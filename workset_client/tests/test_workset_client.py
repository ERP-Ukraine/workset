# -*- coding: utf-8 -*-
"""Тести «Умов клієнтів» (М3, К1): Odoo.sh запускає їх на dev-збірці (локально: odoo-bin -i workset_client --test-enable --test-tags
workset_client). Усі дані — вигадані в самому тесті («Customer K1», «Line K1»; числа не збігаються з даними штабу); «сьогодні» —
31.03.2030 (даних копії в 2030 немає); посередники «Ставок» підмінені порожніми — тест не залежить від домовленостей бази.

Сценарій:
- лінія Line K1, замовник Customer K1 (у «Замовниках» лінії) з контактами Contact K1a / K1b; дві діючі ставки двох працівників —
  «Хто знайшов»: «ми» і не вказано;
- загальна ставка лінії: мінімум частки 3, оренда авто 0,5; житло — правило лінії (платимо ми з буфера);
- «Хто платить» паливо: правило лінії — ми, правило замовника — партнер (замовник точніший); перевезення — правила немає;
- картка DKV на лінію; рядки «лінія × місяць» лютого (10 год) і березня (5 год) 2030 — для «Місяць | Квартал»."""
from contextlib import ExitStack
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.workset_rates.models import agent_deals

from ..models import calc

M_LAST = '2030-03-31'
JAN = '2030-01-01'
FEB = '2030-02-01'
MAR = '2030-03-01'
OLD = '2029-01-01'
PAY_KEYS = ['housing', 'transport', 'fuel', 'tool', 'auto', 'car_rent', 'fund']


@tagged('post_install', '-at_install')
class TestWorksetClient(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.eur = env.ref('base.EUR')
        cls.eur.active = True
        cls.co = env['res.company'].create({'name': 'Test firm K1', 'currency_id': cls.eur.id})
        cls.env = env(context=dict(env.context, allowed_company_ids=[cls.co.id, env.company.id], rates_today=M_LAST, profit_today=M_LAST))
        env = cls.env
        # правила бонусів бази (порожні «Замовники» — усі) дали б тестовим ставкам свій бонус
        env['workset.office.bonus'].sudo().search([]).write({'active': False})
        Partner = env['res.partner']
        cls.Partner = Partner
        cls.cust = Partner.create({'name': 'Customer K1', 'is_company': True})
        cls.contact_a = Partner.create({'name': 'Contact K1a', 'parent_id': cls.cust.id})
        cls.contact_b = Partner.create({'name': 'Contact K1b', 'parent_id': cls.cust.id})
        cls.other = Partner.create({'name': 'Company K1 other', 'is_company': True})
        cls.line = env['workset.line'].create({'name': 'Line K1', 'customer_ids': [(6, 0, [cls.cust.id])]})
        cls.rule = env['workset.rate.rule'].create({'line_id': cls.line.id, 'anchor': 'markup', 'share_line_found': 4, 'share_we_found': 5,
                                                    'min_share': 3, 'car_rent': 0.5, 'date_from': OLD, 'source': 'owner',
                                                    'source_note': 'test rule'})
        cls.housing = env['workset.line.housing'].create({'line_id': cls.line.id, 'kind': 'payment_buffer', 'payer': 'we_buffer',
                                                          'date_from': OLD, 'source': 'owner', 'source_note': 'test housing'})
        Cost = env['workset.line.cost.rule']
        cls.fuel_line = Cost.create({'line_id': cls.line.id, 'kind': 'fuel', 'payer': 'we', 'date_from': OLD, 'note': 'line fuel'})
        cls.fuel_cust = Cost.create({'line_id': cls.line.id, 'customer_id': cls.cust.id, 'kind': 'fuel', 'payer': 'partner',
                                     'date_from': '2029-06-01', 'note': 'customer fuel'})
        cls.card = env['workset.dkv.card'].create({'number': 'TEST-K1-CARD', 'seq': 1,
                                                   'period_ids': [(0, 0, {'target': 'line', 'line_id': cls.line.id, 'date_from': OLD})]})
        Emp = env['hr.employee'].with_company(cls.co)
        cls.e1, cls.e2 = [Emp.create({'name': 'Worker K1 %d' % i, 'company_id': cls.co.id}) for i in (1, 2)]
        Rate = env['workset.rate']
        with cls._deals():
            cls.r1 = Rate.create({'person_id': cls.e1.work_contact_id.id, 'customer_id': cls.cust.id, 'found_by': 'us', 'wage': 14,
                                  'source': 'other', 'date_from': OLD})
            cls.r2 = Rate.create({'person_id': cls.e2.work_contact_id.id, 'customer_id': cls.cust.id, 'wage': 16, 'source': 'other',
                                  'date_from': OLD})
        # рядки «лінія × місяць» пише перерахунок «Прибутковості»; тут — готові, щоб перевірити лише підсумок періоду картки
        Month = env['workset.profit.month'].sudo()
        for month, hours, share, housing, net in ((FEB, 10, 60, -10, 50), (MAR, 5, 40, 0, 40)):
            Month.create({'month': month, 'row_kind': 'line', 'line_id': cls.line.id, 'name': 'Line K1', 'hours_total': hours,
                          'hours_rate': hours, 'share': share, 'housing': housing, 'net': net})

    @classmethod
    def _deals(cls):
        # посередників і агентів людей «Ставок» немає — частки ставок не залежать від домовленостей бази
        stack = ExitStack()
        stack.enter_context(patch.object(agent_deals, 'read_deals', return_value=[]))
        stack.enter_context(patch.object(agent_deals, 'read_people', return_value=[]))
        return stack

    def _user(self, login, groups):
        return new_test_user(self.env, login=login, groups=groups)

    def _as(self, rec, user):
        # фірми контексту класу — не для користувача: лише його фірми (як у тестах «Прибутковості»)
        return rec.with_user(user).with_context(allowed_company_ids=user.company_ids.ids)

    # ——— екран: рядки й описи кліку ———
    def test_screen_rows_and_opens(self):
        data = self.Partner.workset_client_card(self.contact_a.id)
        p = data['partner']
        self.assertEqual(p['id'], self.cust.id, 'контакт замовника — картка його фірми (комерційний контрагент)')
        self.assertEqual([x['name'] for x in p['lines']], ['Line K1'])
        m = data['money']
        self.assertEqual(m['count'], 2)
        self.assertEqual((m['price']['min'], m['price']['max']), (min(self.r1.price, self.r2.price), max(self.r1.price, self.r2.price)))
        self.assertEqual((m['wage']['min'], m['wage']['max']), (14, 16))
        self.assertAlmostEqual(m['share'], round((self.r1.our_share + self.r2.our_share) / 2, 2))
        self.assertEqual(m['min_share'], 3, 'мінімум — із загальної ставки лінії × замовника')
        self.assertEqual((m['rule_open']['model'], m['rule_open']['res_id']), ('workset.rate.rule', self.rule.id))
        self.assertEqual(m['open']['model'], 'workset.rate')
        self.assertIn(('state', '=', 'current'), m['open']['domain'])
        self.assertIsNone(m['withdraw'], 'відсотка за виведення немає — «—», не 0')

        self.assertEqual([r['key'] for r in data['pays']], PAY_KEYS)
        pays = {r['key']: r for r in data['pays']}
        h = pays['housing']
        labels = dict(self.env['workset.line.housing']._fields['payer']._description_selection(self.env))
        self.assertEqual((h['payer'], h['level'], h['tone']), (labels['we_buffer'], 'line', ''))
        self.assertEqual((h['open']['model'], h['open']['res_id']), ('workset.line.housing', self.housing.id), 'житло — форма правила лінії')
        fuel = pays['fuel']
        self.assertEqual(fuel['open']['res_id'], self.fuel_cust.id, 'правило замовника точніше за правило лінії')
        self.assertEqual((fuel['level'], fuel['source']), ('customer', 'customer fuel'))
        self.assertEqual(fuel['cards'], [self.card.display_name])
        self.assertEqual(fuel['cards_open']['domain'], [('id', 'in', [self.card.id])])
        tr = pays['transport']
        self.assertEqual(tr['tone'], 'muted', 'правила немає — «Ми (правила немає)» сірим')
        self.assertNotIn('res_id', tr['open'])
        self.assertEqual(tr['open']['domain'], [('line_id', '=', self.line.id), ('kind', '=', 'transport')])
        self.assertEqual((pays['car_rent']['amount'], pays['car_rent']['open']['res_id']), (0.5, self.rule.id))
        self.assertEqual(pays['fund']['tone'], 'muted', 'фонду немає — «не діє»')

        a = data['agent']
        self.assertEqual((a['found']['us'], a['found']['line'], a['found']['none'], a['found']['total']), (1, 0, 1, 2))
        self.assertTrue(a['found']['missing'], '«не вказано у 1 з 2» — жовтим')
        self.assertIn(('found_by', '=', False), a['missing_open']['domain'])
        self.assertEqual(a['installed'], 'workset.agent.deal.client' in self.env, 'агенти — м\'яко: моделі немає — «не встановлено»')
        self.assertEqual(a['items'], [])

    # ——— «Договір і оплата»: порожнє — жовте, «Заповнити», «Позначити перевіреним» ———
    def test_contract_empty_yellow_then_fill_and_check(self):
        editor = self._user('ws_client_editor', 'base.group_user,workset_client.group_editor')
        Card = self._as(self.Partner, editor)
        data = Card.workset_client_card(self.cust.id)
        items = {i['key']: i for i in data['contract']['items']}
        self.assertEqual(list(items), ['contract', 'scan', 'payment_term', 'sign', 'site_contact', 'site_address', 'note'])
        self.assertTrue(all(i['empty'] for i in items.values()), 'порожні поля договору — жовті ознаки')
        self.assertEqual(data['contract']['missing'], 7)
        self.assertTrue(items['contract']['empty_text'] and items['payment_term']['empty_text'])
        self.assertFalse(data['partner']['checked']['ok'], 'не перевірено — жовтий чип')
        self.assertTrue(data['can_edit'])

        # свій строк оплати без фірми (рядок «100 % одразу» Odoo ставить сам) — не залежить від строків бази
        term = self.env['account.payment.term'].create({'name': 'Test term K1'})
        wizard = self._as(self.env['workset.client.terms'], editor).with_context(default_partner_id=self.cust.id).create({
            'contract_no': 'K1-2030', 'contract_date': '2030-01-15', 'sign_id': self.contact_a.id, 'site_contact_id': self.contact_b.id,
            'site_address': 'Test street K1', 'withdraw_pct': 2.5, 'note': 'test note', 'payment_term_id': term.id})
        self.assertEqual(wizard.partner_id, self.cust, 'вікно — для замовника з контексту')
        wizard.action_save()
        self._as(self.cust, editor).workset_client_mark_checked()
        data = Card.workset_client_card(self.cust.id)
        items = {i['key']: i for i in data['contract']['items']}
        self.assertEqual(data['contract']['missing'], 1, 'лишився лише скан')
        self.assertTrue(items['scan']['empty'])
        self.assertIn('K1-2030', items['contract']['value'])
        self.assertIn('15.01.2030', items['contract']['value'])
        self.assertEqual(items['payment_term']['value'], term.display_name, 'строк оплати — стандартне поле контакту')
        self.assertEqual((items['sign']['value'], items['site_contact']['value']), ('Contact K1a', 'Contact K1b'))
        self.assertEqual(data['money']['withdraw'], 2.5)
        self.assertTrue(data['partner']['checked']['ok'])
        self.assertEqual(data['partner']['checked']['who'], editor.name)
        self.assertEqual(self.cust.sudo().workset_client_checked_by_id, editor)

    # ——— «Картка лінії ›» — лише групі «Прибутковість: Перегляд» ———
    def test_line_link_only_profit_viewer(self):
        plain = self._user('ws_client_plain', 'base.group_user')
        profit = self._user('ws_client_profit', 'base.group_user,workset_profit.group_viewer')
        self.assertTrue(plain.has_group('workset_client.group_viewer'), 'на час збирання «Перегляд» — у кожного внутрішнього користувача')
        d_plain = self._as(self.Partner, plain).workset_client_card(self.cust.id)
        d_profit = self._as(self.Partner, profit).workset_client_card(self.cust.id)
        self.assertFalse(d_plain['profit']['link'], 'без групи «Прибутковості» — без посилання «Картка лінії»')
        self.assertEqual(d_profit['profit']['link']['line_id'], self.line.id)
        self.assertEqual(d_plain['money']['count'], 2, 'цифри — sudo: «всі, з часткою» на час збирання')
        self.assertFalse(d_plain['money']['open'], 'відкрити ставки — лише за правами самого користувача')
        self.assertFalse(d_plain['can_edit'])

    # ——— «Що з цього виходить»: Місяць | Квартал ———
    def test_profit_period_month_and_quarter(self):
        d = self.Partner.workset_client_card(self.cust.id)
        self.assertEqual(d['period']['months'], [FEB], 'за замовчуванням — останній закритий місяць')
        rows = {r['key']: r for r in d['profit']['rows']}
        self.assertEqual((rows['share']['ph'], rows['housing']['ph'], rows['net']['ph']), (6.0, -1.0, 5.0))
        q = self.Partner.workset_client_card(self.cust.id, 'quarter', FEB)
        self.assertEqual(q['period']['months'], [JAN, FEB, MAR])
        rows = {r['key']: r for r in q['profit']['rows']}
        self.assertEqual(rows['share']['amount'], 100)
        self.assertEqual(rows['share']['ph'], round(100 / 15.0, 2), 'на години зі ставкою всього періоду, не середнє місяців')
        self.assertEqual(rows['net']['ph'], 6.0)
        self.assertEqual(q['profit']['open']['domain'], [('month', 'in', [JAN, FEB, MAR]), ('row_kind', '=', 'line'), ('line_id', '=', self.line.id)])
        if 'remain' in self.env['workset.profit.month']._fields:
            self.assertTrue(q['profit']['oh_missing'], 'місяці без «ZUS і загальні» — «не перераховано», а не 0')
            self.assertNotIn('remain', rows)

    # ——— права ———
    def test_rights_viewer_editor(self):
        clerk = self._user('ws_client_clerk', 'base.group_user,base.group_partner_manager')
        portal = self._user('ws_client_portal', 'base.group_portal')
        editor = self._user('ws_client_editor2', 'base.group_user,workset_client.group_editor')
        with self.assertRaises(AccessError):
            self._as(self.cust, clerk).write({'workset_client_contract_no': 'K1-X'})
        self._as(self.cust, clerk).write({'comment': 'test comment K1'})  # інші поля контакту — як і раніше
        with self.assertRaises(AccessError):
            self._as(self.cust, clerk).workset_client_mark_checked()
        with self.assertRaises(AccessError):
            self._as(self.env['workset.client.terms'], clerk).create({'partner_id': self.cust.id})
        with self.assertRaises(AccessError):
            self._as(self.Partner, portal).workset_client_card(self.cust.id)
        self._as(self.cust, editor).workset_client_mark_checked()
        self.assertTrue(self.cust.sudo().workset_client_checked_date)
        self.assertFalse(self.cust.sudo().workset_client_contract_no, 'правку без групи не записано')

    # ——— список «Умови клієнтів» ———
    def test_list_customers_and_columns(self):
        ids = self.Partner.search([('workset_client_is_customer', '=', True)]).ids
        self.assertIn(self.cust.id, ids)
        self.assertNotIn(self.other.id, ids)
        c = self.cust
        self.assertEqual((c.workset_client_line_text, c.workset_client_rate_count), ('Line K1', 2))
        self.assertFalse(c.workset_client_has_contract)
        self.assertTrue(c.workset_client_contract_text and c.workset_client_term_text and c.workset_client_checked_text)
        labels = dict(self.env['workset.line.housing']._fields['payer']._description_selection(self.env))
        self.assertEqual(c.workset_client_housing_text, labels['we_buffer'])
        act = c.action_workset_client_card()
        self.assertEqual((act['tag'], act['params']), ('workset_client.card', {'partner_id': c.id}))

    # ——— «Останні зміни» ———
    def test_changes_tracking_and_rule_creation(self):
        self.cust.write({'workset_client_contract_no': 'K1-A'})
        self.env.flush_all()
        self.env.cr.precommit.run()
        d = self.Partner.workset_client_card(self.cust.id)
        self.assertLessEqual(len(d['changes']), calc.CHANGES_MAX)
        self.assertEqual([c['at'] for c in d['changes']], sorted((c['at'] for c in d['changes']), reverse=True), 'найсвіжіші — першими')
        # усі події до обрізання (у тій самій секунді їх може бути більше десяти — порядок між ними не перевіряємо)
        with patch.object(calc, 'changes_top', side_effect=lambda items, limit=calc.CHANGES_MAX: items):
            full = self.Partner.workset_client_card(self.cust.id)['changes']
        own = [c for c in full if c['new'] == 'K1-A']
        self.assertEqual(len(own), 1, 'трекінг свого поля — «було → стало»')
        self.assertEqual(own[0]['old'], '—')
        self.assertIn('Номер договору', own[0]['what'])
        self.assertTrue(any(c['open'] and c['open']['model'] == 'workset.line.housing' and c['open'].get('res_id') == self.housing.id for c in full),
                        'створення правила житла лінії')
        self.assertTrue(any(c['open'] and c['open']['model'] == 'workset.dkv.card.period' for c in full), 'період картки DKV лінії')
