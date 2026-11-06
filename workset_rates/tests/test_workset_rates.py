# -*- coding: utf-8 -*-
"""Тести «Ставок»: запускаються Odoo.sh на dev-збірці (і локально: odoo-bin -i workset_rates --test-enable --test-tags workset_rates).
Усі дані — вигадані в самому тесті; «сьогодні» підміняється контекстом rates_today (лише адміністратору)."""
import ast
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..models import agent_deals, calc

DAY = '2026-03-02'   # понеділок
NEXT = '2026-03-03'
DAY3 = '2026-03-04'
OLD = '2025-01-01'   # «Ставка з» загальної ставки лінії в тестах
LATER = '2026-04-01'


@tagged('post_install', '-at_install')
class TestWorksetRates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.eur = env.ref('base.EUR')
        cls.eur.active = True
        cls.uah = env.ref('base.UAH')
        cls.uah.active = True
        Company = env['res.company']
        cls.co_a = Company.create({'name': 'Тест фірма А', 'currency_id': cls.eur.id})
        cls.co_b = Company.create({'name': 'Тест фірма Б', 'currency_id': cls.eur.id})
        cls.co_uah = Company.create({'name': 'Тест фірма гривня', 'currency_id': cls.uah.id})
        cls.co_out = Company.create({'name': 'Тест фірма поза периметром', 'currency_id': cls.eur.id})
        mine = cls.co_a | cls.co_b | cls.co_uah | cls.co_out
        others = Company.search([('id', 'not in', mine.ids)])
        # поза периметром — усі чужі фірми бази (демо-дані не заважають звірці) і одна тестова
        env['ir.config_parameter'].sudo().set_param(calc.P_OUT, ','.join(str(i) for i in (others | cls.co_out).ids))
        env['ir.config_parameter'].sudo().set_param(calc.P_ASSIGNEE, str(env.ref('base.user_admin').id))
        cls.env = env(context=dict(env.context, allowed_company_ids=mine.ids + [env.company.id], rates_today=DAY))
        env = cls.env
        cls.cust = env['res.partner'].create({'name': 'Тест замовник', 'is_company': True})
        cls.cust2 = env['res.partner'].create({'name': 'Тест замовник 2', 'is_company': True})
        cls.line = env['workset.line'].create({'name': 'Тест лінія', 'code': 'TL', 'customer_ids': [(6, 0, [cls.cust.id])]})
        # частки лінії — із загальної ставки рівня лінії (М2): надбавка «знайшла лінія 4 / знайшли ми 5». Усі числа тестів — вигадані
        # (публічний репозиторій: не збігаються з правилами й тарифами штабу)
        cls.line_rule = env['workset.rate.rule'].create({'line_id': cls.line.id, 'anchor': 'markup', 'share_line_found': 4, 'share_we_found': 5,
                                                         'date_from': OLD, 'source': 'owner', 'source_note': 'тест'})
        # М2c: у лінії-надбавки є правило житла («житло не наше») — інакше кожна звірка мала б розділ «Лінія з надбавкою без правила житла»
        cls.line_housing = env['workset.line.housing'].create({'line_id': cls.line.id, 'kind': 'none', 'date_from': OLD, 'source': 'owner',
                                                               'source_note': 'тест'})
        Emp = env['hr.employee']
        cls.emp_a = Emp.with_company(cls.co_a).create({'name': 'Тест працівник', 'company_id': cls.co_a.id, 'hourly_cost': 10})
        cls.person = cls.emp_a.work_contact_id
        cls.emp_b = Emp.with_company(cls.co_b).create({'name': 'Тест працівник', 'company_id': cls.co_b.id, 'hourly_cost': 10, 'work_contact_id': cls.person.id})
        cls.emp_uah = Emp.with_company(cls.co_uah).create({'name': 'Тест працівник', 'company_id': cls.co_uah.id, 'hourly_cost': 300, 'work_contact_id': cls.person.id})
        cls.emp_out = Emp.with_company(cls.co_out).create({'name': 'Тест працівник', 'company_id': cls.co_out.id, 'hourly_cost': 10, 'work_contact_id': cls.person.id})
        # проєкти — «Billable», як усі проєкти з картою на проді: у неоплачуваного проєкту sale_project (Odoo 18–19, _compute_partner_id)
        # стирає замовника, щойно змінюється карта, — пара «людина × замовник» зникає, і А1 / А2 не бачать рядків (перша збірка Odoo.sh 06.11)
        Project = env['project.project']
        cls.p_a = Project.with_company(cls.co_a).create({'name': 'Тест об’єкт А', 'partner_id': cls.cust.id, 'company_id': cls.co_a.id, 'allow_billable': True})
        cls.p_b = Project.with_company(cls.co_b).create({'name': 'Тест об’єкт Б', 'partner_id': cls.cust.id, 'company_id': cls.co_b.id, 'allow_billable': True})
        cls.p_uah = Project.with_company(cls.co_uah).create({'name': 'Тест об’єкт гривня', 'partner_id': cls.cust.id, 'company_id': cls.co_uah.id,
                                                            'allow_billable': True})
        cls.p_out = Project.with_company(cls.co_out).create({'name': 'Тест об’єкт поза', 'partner_id': cls.cust.id, 'company_id': cls.co_out.id,
                                                            'allow_billable': True})
        Map = env['project.sale.line.employee.map']
        cls.m_a = Map.create({'project_id': cls.p_a.id, 'employee_id': cls.emp_a.id})
        cls.m_b = Map.create({'project_id': cls.p_b.id, 'employee_id': cls.emp_b.id})
        cls.m_uah = Map.create({'project_id': cls.p_uah.id, 'employee_id': cls.emp_uah.id})
        cls.m_out = Map.create({'project_id': cls.p_out.id, 'employee_id': cls.emp_out.id})
        cls.R = env['workset.rate']

    def _rate(self, **kw):
        vals = {'person_id': self.person.id, 'customer_id': self.cust.id, 'found_by': 'us', 'wage': 14, 'source': 'other', 'date_from': DAY}
        vals.update(kw)
        return self.R.create(vals)

    def _set_contract(self, wage):
        # без hr_payroll поля немає — договір не звіряється взагалі (contract_ok = True), тест іде далі
        if 'hourly_wage' in self.env['hr.version']._fields:
            vals = {'hourly_wage': wage}
            if 'wage_type' in self.env['hr.version']._fields:
                vals['wage_type'] = 'hourly'  # М2b: не погодинний договір — окремий розділ звірки
            (self.emp_a | self.emp_b | self.emp_uah | self.emp_out).sudo().current_version_id.write(vals)

    def _isolate(self, drop=False):
        """Справжні ставки бази (staging-копія з даними) — з поля зору тесту всередині транзакції: в архів (drop — видалити), без автоматики."""
        others = self.env['workset.rate'].sudo().with_context(active_test=False, workset_rates_no_sync=True).search([])
        if drop:
            others.unlink()
        else:
            others.filtered('active').write({'active': False})
        self.env['workset.rate.check'].sudo().search([]).unlink()
        self.env['mail.activity'].sudo().search([('res_model', '=', 'workset.rate.check')]).unlink()

    # ——— А1 ———
    def test_a1_two_firms_eur_perimeter(self):
        r = self._rate()
        self.assertEqual(r.line_id, self.line, 'лінія сама за замовником')
        self.assertAlmostEqual(r.price, 19, msg='ціна = на руки + правило лінії «знайшли ми»')
        self.assertAlmostEqual(r.our_share, 5)
        self.assertTrue(r.rule_ok)
        self.assertEqual(r.state, 'current')
        for m in (self.m_a, self.m_b):
            self.assertAlmostEqual(m.cost, 14, msg='вартість години = на руки в обох фірмах')
            self.assertTrue(m.is_cost_changed, 'прапорець ручної правки стоїть — синхрон hourly_cost не перетре')
        self.assertAlmostEqual(self.m_uah.cost, 300, msg='рядок в іншій валюті автоматика не чіпає')
        self.assertAlmostEqual(self.m_out.cost, 10, msg='фірма поза периметром (параметр) не чіпається')
        self.assertEqual(r.map_count, 2)
        self.assertEqual(r.company_ids, self.co_a | self.co_b | self.co_uah)
        self.assertTrue(any('Вартість години' in str(b) and 'рядків: 2' in str(b) for b in r.message_ids.mapped('body')), 'запис в історії ставки')

    def test_pair_without_current_back_to_hourly_cost(self):
        r = self._rate()
        self.assertAlmostEqual(self.m_a.cost, 14)
        r.active = False
        self.assertEqual(r.state, 'past', 'архівна — «Минула»')
        for m in (self.m_a, self.m_b):
            self.assertAlmostEqual(m.cost, 10, msg='пара без чинної — назад на hourly_cost картки')
            self.assertFalse(m.is_cost_changed)

    def test_customer_change_reverts_old_pair_and_clears_line(self):
        r = self._rate()
        r.customer_id = self.cust2
        self.assertFalse(r.line_id, 'замовника немає в жодній лінії — лінія порожня')
        self.assertAlmostEqual(self.m_a.cost, 10, msg='рядки колишньої пари — назад на картку')

    def test_unlink_set_reverts(self):
        r1 = self._rate()
        r2 = self._rate(date_from=calc.add_days(DAY, -10), wage=12)
        self.assertEqual(r2.state, 'past')
        (r1 | r2).unlink()
        self.assertAlmostEqual(self.m_a.cost, 10, msg='видалили всю пару одним набором — рядки на картку')
        self.assertFalse(self.m_a.is_cost_changed)

    # ——— майбутня ставка → А3 ———
    def test_future_rate_then_daily(self):
        r1 = self._rate()
        r2 = self._rate(date_from=NEXT, wage=15)
        self.assertEqual(r2.state, 'future')
        self.assertEqual(r1.state, 'current')
        self.assertEqual(calc.iso(r1.date_to), DAY)
        self.assertAlmostEqual(self.m_a.cost, 14, msg='майбутня ставка рядки ще не змінює')
        res = self.R.with_context(rates_today=NEXT).rates_daily()
        self.assertEqual(r2.state, 'current')
        self.assertEqual(r1.state, 'past')
        self.assertAlmostEqual(self.m_a.cost, 15, msg='А3: нова чинна — у рядках карти')
        self.assertEqual(res['moved'], 1)

    def test_dry_daily_writes_nothing(self):
        self._rate()
        r2 = self._rate(date_from=NEXT, wage=15)
        self.R.with_context(rates_today=NEXT, rates_dry=True).rates_daily()
        self.assertEqual(r2.state, 'future', 'rates_dry — лише розрахунок')

    # ——— А2 ———
    def test_a2_new_map_line_and_employee_change(self):
        self._rate()
        p2 = self.env['project.project'].with_company(self.co_a).create({'name': 'Тест об’єкт А2', 'partner_id': self.cust.id, 'company_id': self.co_a.id,
                                                                         'allow_billable': True})
        self.assertEqual(self.p_a.partner_id, self.cust, 'замовник проєкту з картою — на місці (Billable)')
        m2 = self.env['project.sale.line.employee.map'].create({'project_id': p2.id, 'employee_id': self.emp_a.id})
        self.assertAlmostEqual(m2.cost, 14, msg='А2: новий рядок одразу за ставкою')
        self.assertTrue(m2.is_cost_changed)
        other = self.env['hr.employee'].with_company(self.co_a).create({'name': 'Тест інший', 'company_id': self.co_a.id, 'hourly_cost': 9})
        m2.employee_id = other
        self.assertAlmostEqual(m2.cost, 9, msg='перенесли до пари без ставки — назад на hourly_cost')
        self.assertFalse(m2.is_cost_changed)

    # ——— заборони ———
    def test_forbid_duplicate_and_zero_wage(self):
        self._rate()
        with self.assertRaises(ValidationError):
            self._rate()
        with self.assertRaises(ValidationError):
            self._rate(date_from=NEXT, wage=0)

    # ——— звірка ———
    def test_check_one_open_activity(self):
        self._isolate()
        self._set_contract(14)
        self.m_uah.unlink()  # рядок у гривні — окремий розділ «інша валюта»; тут перевіряємо лише правило й задачі
        # ціна ≠ правилу → «Своя»; частка ≤ 0 → «не за правилом» (М2b: «Своя» з додатною часткою без зміни загальної — не шум звірки)
        r = self._rate(price=13, own_reason='тест: ціна з листа')
        self.assertTrue(r.is_own)
        self.assertFalse(r.rule_ok)
        self.assertTrue(r.contract_ok, 'договір 14 = ставка або модуля зарплат немає — «Договір ≠ ставка» не горить')
        todo = self.env.ref('mail.mail_activity_data_todo')
        Act = self.env['mail.activity']
        res1 = self.R.rates_check()
        self.assertTrue(res1['created'])
        self.assertEqual(res1['sections']['rule'], 1)
        self.assertEqual(res1['sections']['contract'], 0)
        self.assertEqual(res1['sections']['agent'], 0, 'лінія не чекає посередника — розділу посередників немає')
        res1b = self.R.rates_check()
        self.assertEqual(res1b['reused'], res1['created'], 'повтор того ж дня — той самий запис')
        opened = Act.search([('res_model', '=', 'workset.rate.check'), ('activity_type_id', '=', todo.id)])
        self.assertEqual(len(opened), 1, 'відкрита завжди одна задача')
        res2 = self.R.with_context(rates_today=NEXT).rates_check()
        self.assertTrue(res2['created'] and res2['created'] != res1['created'])
        self.assertEqual(res2['closed'], 1, 'минула задача закрита «замінено новою звіркою»')
        opened = Act.search([('res_model', '=', 'workset.rate.check'), ('activity_type_id', '=', todo.id)])
        self.assertEqual(opened.res_id, res2['created'])
        r.price = 19
        res3 =self.R.with_context(rates_today=DAY3).rates_check()
        self.assertEqual(res3['issues'], 0)
        self.assertFalse(res3['created'], 'розбіжностей немає — запису немає')
        self.assertFalse(self.env['workset.rate.check'].search([('date', '=', DAY3)]))

    def test_check_without_rates(self):
        self._isolate(drop=True)
        res = self.R.rates_check()
        self.assertEqual(res['rates'], 0)
        self.assertFalse(res['created'])

    # ——— права ———
    def test_rights(self):
        r = self._rate()
        hr_user = new_test_user(self.env, login='ws_rates_hr', groups='base.group_user,hr.group_hr_user')
        editor = new_test_user(self.env, login='ws_rates_editor', groups='base.group_user,workset_rates.group_editor')
        admin = new_test_user(self.env, login='ws_rates_admin', groups='base.group_user,base.group_system')

        def as_(user, rec):
            # тестові фірми користувачам не видані: фірми контексту — лише його власна
            return rec.with_user(user).with_context(allowed_company_ids=[user.company_id.id])

        self.assertEqual(as_(hr_user, r).read(['wage'])[0]['wage'], 14, 'кадри читають')
        with self.assertRaises(AccessError):
            as_(hr_user, r).write({'note': 'x'})
        as_(editor, r).write({'note': 'редактор пише'})
        self.assertEqual(r.note, 'редактор пише')
        with self.assertRaises(AccessError):
            as_(editor, r).unlink()
        with self.assertRaises(AccessError):
            as_(editor, self.env['workset.rate.check']).create({'name': 'x'})
        chk = self.env['workset.rate.check'].sudo().create({'name': 'Тест звірка', 'date': DAY, 'issue_count': 1})
        as_(editor, chk).write({'done': True})
        self.assertTrue(chk.done, 'редактор ставить «Розібрано»')
        with self.assertRaises(AccessError):
            as_(editor, chk).write({'issue_count': 0})
        with self.assertRaises(AccessError):
            as_(editor, self.R).rates_daily()
        as_(admin, r).unlink()
        self.assertFalse(r.exists())

    # ——— середні ———
    def test_averages_in_groups_and_summary(self):
        cust3 = self.env['res.partner'].create({'name': 'Тест замовник 3', 'is_company': True})
        self.line.customer_ids = [(4, cust3.id)]
        self._rate(wage=14)
        self._rate(customer_id=cust3.id, wage=18)
        groups = self.R._read_group([('line_id', '=', self.line.id)], ['line_id'], ['wage:avg', 'our_share:avg'])
        self.assertAlmostEqual(groups[0][1], 16, msg='у рядку групи — середнє, не сума')
        web = self.R.formatted_read_group([('line_id', '=', self.line.id)], ['line_id'], ['wage:avg'])
        self.assertAlmostEqual(web[0]['wage:avg'], 16)
        self.assertEqual(self.R._fields['wage'].aggregator, 'avg')
        self.assertEqual(self.R._fields['price'].aggregator, 'avg')
        self.assertEqual(self.R._fields['our_share'].aggregator, 'avg')
        rates = self.R.search([('line_id', '=', self.line.id)])
        rates.write({'work': 'work', 'work_group': 'g1_work'})
        s = self.R.rates_summary([('line_id', '=', self.line.id)])
        self.assertEqual(s['working'], 2)
        self.assertAlmostEqual(s['avg_wage'], 16)
        self.env.flush_all()  # «Середні» — представлення SQL: читає таблицю ставок, не кеш
        avg = self.env['workset.rate.avg'].search([('dim', '=', 'line'), ('key', '=', self.line.id)])
        self.assertEqual(avg.work_count, 2, '«Середні» — представлення по тих, хто працює')
        self.assertAlmostEqual(avg.avg_wage, 16)

    def test_panel_without_empty_values(self):
        empty = self.env['workset.line'].create({'name': 'Тест порожня лінія'})
        self._rate()
        res = self.R.search_panel_select_range('line_id', enable_counters=True, search_domain=[], category_domain=[], filter_domain=[('state', '=', 'past')])
        ids = [v['id'] for v in res['values']]
        self.assertNotIn(empty.id, ids, 'лінія без ставок не показується')
        self.assertNotIn(self.line.id, ids, 'лічильник 0 під поточним фільтром — значення прибрано')
        res = self.R.search_panel_select_range('work_group', enable_counters=True, expand=True, search_domain=[], category_domain=[], filter_domain=[])
        self.assertEqual(len(res['values']), 3, '«Робота» — завжди всі значення')

    def test_panel_lines_ordered_by_working(self):
        # без переопределення панель дала б порядок лінії (sequence): «перша» раніше «другої»
        first = self.env['workset.line'].create({'name': 'Тест лінія перша', 'sequence': 1})
        second = self.env['workset.line'].create({'name': 'Тест лінія друга', 'sequence': 2})
        c1 = self.env['res.partner'].create({'name': 'Тест замовник П1', 'is_company': True})
        c2 = self.env['res.partner'].create({'name': 'Тест замовник П2', 'is_company': True})
        r1 = self._rate(customer_id=c1.id, line_id=first.id)
        r2 = self._rate(customer_id=c2.id, line_id=second.id)
        r1.write({'work': 'z_idle', 'work_group': 'g2_idle'})
        r2.write({'work': 'work', 'work_group': 'g1_work'})
        res = self.R.search_panel_select_range('line_id', enable_counters=True, search_domain=[('id', 'in', (r1 | r2).ids)], category_domain=[], filter_domain=[])
        ids = [v['id'] for v in res['values'] if v['id'] in (first.id, second.id)]
        self.assertEqual(ids, [second.id, first.id], 'лінія з тими, хто працює, — вище')

    # ——— М2: ланцюжок цін (розділ 8 спеціфікації М2; номери — пункти розділу) ———
    def _person(self, name):
        return self.env['res.partner'].create({'name': name})

    def _cust(self, name, line=None):
        c = self.env['res.partner'].create({'name': name, 'is_company': True})
        (line or self.line).customer_ids = [(4, c.id)]
        return c

    def _rule(self, **kw):
        vals = {'line_id': self.line.id, 'anchor': 'markup', 'date_from': OLD, 'source': 'owner', 'source_note': 'тест'}
        vals.update(kw)
        return self.env['workset.rate.rule'].create(vals)

    def _quiet_bonuses(self):
        # правила бонусів бази (порожні «Замовники» — усі) дали б тестовим ставкам свій бонус
        self.env['workset.office.bonus'].sudo().search([]).write({'active': False})

    def _no_agents(self):
        # домовленості бази тестових замовників не стосуються, але адаптер — підмінений, щоб тест не залежав від даних копії
        return patch.object(agent_deals, 'read_deals', return_value=[]), patch.object(agent_deals, 'read_people', return_value=[])

    def test_m2_01_markup_found_by(self):
        a, b = self._no_agents()
        with a, b:
            us = self._rate()
            line = self._rate(person_id=self._person('Тест П лінія').id, found_by='line')
            none = self._rate(person_id=self._person('Тест П без').id, found_by=False)
        self.assertEqual(us.rule_id, self.line_rule, 'загальна ставка лінії на «Ставка з»')
        self.assertEqual(us.anchor, 'markup')
        self.assertEqual((us.price, us.our_share), (19, 5), 'надбавка, «знайшли ми»: Р 14 + 5')
        self.assertEqual((line.price, line.our_share), (18, 4), '«знайшла лінія»: Р 14 + 4')
        self.assertEqual((none.price, none.our_share), (18, 4), '«Хто знайшов» порожньо — за «знайшла лінія»')
        self.assertTrue(none.found_missing)
        self.assertFalse(us.found_missing)
        self.assertEqual(none.missing_text, 'Хто знайшов не вказано')
        self.assertNotIn('хто знайшов', none.issues or '', '«Перевірити» не повторює «Бракує» (ревю М2c-1, № 2)')
        self.assertFalse(us.is_own)

    def test_m2_02_tariff_and_start_price(self):
        lt = self.env['workset.line'].create({'name': 'Тест лінія тариф'})
        self._rule(line_id=lt.id, anchor='tariff', share_we_found=5)
        ct = self._cust('Тест замовник тариф', lt)
        cu = self._cust('Тест замовник без тарифу', lt)
        self._rule(line_id=lt.id, customer_id=ct.id, anchor='tariff', price=31)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4.5')
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            r = self._rate(customer_id=ct.id)
            s = self._rate(customer_id=cu.id)
            s2 = self._rate(customer_id=cu.id, date_from=NEXT, wage=15)
        self.assertEqual((r.price, r.our_share), (31, 17), 'тариф замовника: Ц 31, W = 31 − 14')
        self.assertEqual((s.price, s.our_share), (19, 5), 'тарифу замовника немає — стартова Ц = Р + частка лінії')
        self.assertEqual((s2.price, s2.our_share), (19, 4), 'тариф: росте на руки — ціна та сама (попередньої ставки), частка падає')
        # поріг тарифу (мінімум — параметр 4,5): W 5 — не низька; W 4 — нижче мінімуму, і частка, і чиста (бонусів немає)
        self.assertNotEqual(s.share_level, 'low', 'W 5 ≥ мінімуму 4,5')
        self.assertNotEqual(r.share_level, 'low')
        self.assertEqual((s2.share_level, s2.net_level), ('low', 'low'), 'W 4 < мінімуму 4,5 — червона')

    def test_m2_03_inheritance_and_dates(self):
        a, b = self._no_agents()
        with a, b:
            cust_rule = self._rule(customer_id=self.cust.id, share_line_found=5.75, share_we_found=7.5, date_from=NEXT)
            self.assertEqual(cust_rule.state, 'future', 'А7: правило з майбутньою датою — «Майбутня»')
            today = self._rate()
            later = self._rate(date_from=NEXT, wage=14)
            self.assertEqual(today.rule_id, self.line_rule, 'правило з майбутньою датою не впливає на сьогоднішню ставку')
            self.assertEqual(later.rule_id, cust_rule, 'правило замовника перекриває правило лінії')
            self.assertEqual(later.price, 21.5)
            # А7: нове правило лінії з минулою датою — ставки самі не змінюються (лише «Застосувати з дати»)
            newer = self._rule(share_line_found=4.5, share_we_found=5.5, date_from=calc.add_days(DAY, -1))
            self.assertEqual(calc.iso(self.line_rule.date_to), calc.add_days(DAY, -2), '«Ставка до» попереднього правила пари')
            self.assertEqual(self.line_rule.state, 'past')
            self.assertEqual(newer.state, 'current')
            self.assertEqual((today.price, today.rule_id), (19, self.line_rule), 'ставка сама не змінилась')
            self.line.invalidate_recordset()  # частки лінії — нехранимі: рахуються при читанні
            self.assertEqual(self.line.share_we_found, 5.5, 'частки лінії — з діючого правила лінії')

    def test_m2_rule_checks(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(anchor='markup', date_from=NEXT)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(anchor='tariff', date_from=NEXT)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(price_mode='fixed_month', anchor='tariff', date_from=NEXT)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(share_we_found=5, client_price=43, date_from=NEXT)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(share_we_found=5, date_from=OLD)  # дубль «лінія × замовник × Ставка з»
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._rule(share_we_found=-1, date_from=NEXT)

    def test_m2_04_own_and_back_to_rule(self):
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                r.write({'price': 26})
            r.invalidate_recordset()
            self.assertFalse(r.is_own)
            r.write({'price': 26, 'own_reason': 'тест: домовились окремо'})
            self.assertTrue(r.is_own, 'ручна Ц ≠ правилу — «Своя»')
            self.assertEqual((r.price, r.our_share, r.rule_price), (26, 12, 19))
            self.assertFalse(r.rule_ok)
            r.write({'wage': 15})
            self.assertEqual(r.price, 26, '«Своя»: зміна на руки ціну не чіпає')
            r.action_back_to_rule()
            self.assertFalse(r.is_own)
            self.assertEqual(r.price, 20, '«Повернути до загальної» — числа правила (Р 15 + 5)')
            r.write({'price': 20})
            self.assertFalse(r.is_own, 'ручна Ц = правилу — не «Своя»')

    def test_m2_05_apply_from_date(self):
        self._isolate()
        a, b = self._no_agents()
        with a, b:
            rates = [self._rate(person_id=self._person('Тест П%d' % i).id) for i in range(3)]
            own = self._rate(person_id=self._person('Тест П своя').id, price=32, own_reason='тест: своя')
            new_rule = self._rule(share_line_found=5.5, share_we_found=6.5, date_from=LATER)
            self.assertEqual((new_rule.inherit_count, new_rule.own_count), (3, 1), 'А7: «Наслідують 3; своїх 1»')
            wiz = self.env['workset.rate.rule.apply'].create({'rule_id': new_rule.id, 'date': LATER})
            self.assertEqual((wiz.inherit_count, wiz.own_count, wiz.same_count, wiz.late_count), (3, 1, 0, 0), 'майстер показав 3 / 1')
            self.assertEqual(wiz.governed_count, new_rule.inherit_count, 'майстер і смарт-кнопка рахують однаково')
            self.assertIn('19 / 5', wiz.preview_html)
            self.assertIn('20,5 / 6,5', wiz.preview_html)
            wiz.action_apply()
            for r in rates:
                new = self.R.search([('person_id', '=', r.person_id.id), ('date_from', '=', LATER)])
                self.assertEqual(len(new), 1, 'нова ставка з дати застосування')
                self.assertEqual((new.price, new.state, new.source, new.rule_id), (20.5, 'future', 'rule', new_rule))
                self.assertEqual(r.price, 19, 'попередня ставка не переписана')
            self.assertFalse(self.R.search([('person_id', '=', own.person_id.id), ('date_from', '=', LATER)]), '«Своя» не змінена')
            self.assertIn('вирішіть', new_rule.message_ids[0].body)
            self.R.with_context(rates_today=LATER).rates_daily()
            new = self.R.search([('person_id', '=', rates[0].person_id.id), ('date_from', '=', LATER)])
            self.assertEqual(new.state, 'current', 'А3 з підміною дати — «Діє»')
            self.assertEqual(rates[0].state, 'past')
            self.assertEqual(calc.iso(rates[0].date_to), calc.add_days(LATER, -1))

    def test_m2_05b_apply_rates_started_after_rule(self):
        # правило з минулою датою R; ставка, що почалась після R (до появи правила), — перераховується на місці; давня — нова ставка з дати
        self._isolate()
        a, b = self._no_agents()
        with a, b:
            old = self._rate(person_id=self._person('Тест П давня').id, date_from=calc.add_days(DAY, -10))
            late = self._rate(person_id=self._person('Тест П пізня').id, date_from=calc.add_days(DAY, -2))
            self.assertEqual((old.price, late.price, late.rule_id), (19, 19, self.line_rule))
            rule = self._rule(share_line_found=5.5, share_we_found=6.5, date_from=calc.add_days(DAY, -5))
            self.assertEqual(rule.inherit_count, 2, 'смарт-кнопка: давня + та, що почалась після правила')
            res = rule.action_apply_wizard()
            self.assertEqual(res['context']['default_date'], DAY, 'дата майстра = дата смарт-кнопки (правило в минулому — сьогодні)')
            wiz = self.env['workset.rate.rule.apply'].create({'rule_id': rule.id, 'date': DAY})
            self.assertEqual((wiz.governed_count, wiz.inherit_count, wiz.late_count), (2, 1, 1))
            self.assertEqual(wiz.governed_count, rule.inherit_count, 'майстер і смарт-кнопка рахують однаково')
            self.assertIn('Почалися після правила', wiz.preview_html)
            wiz.action_apply()
            self.assertEqual((late.price, late.rule_id, late.source), (20.5, rule, 'rule'), 'перераховано на місці')
            self.assertFalse(self.R.search([('person_id', '=', late.person_id.id), ('date_from', '=', DAY)]), 'нової ставки для пізньої немає')
            new = self.R.search([('person_id', '=', old.person_id.id), ('date_from', '=', DAY)])
            self.assertEqual((len(new), new.price, old.price), (1, 20.5, 19), 'давня — нова ставка з дати, сама не переписана')

    def test_m2_fixed_month(self):
        lf = self.env['workset.line'].create({'name': 'Тест лінія фікс'})
        cf = self._cust('Тест замовник фікс', lf)
        self._rule(line_id=lf.id, customer_id=cf.id, anchor='tariff', price_mode='fixed_month', fixed_month=840)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MONTH_HOURS, '')
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b, patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
            r = self._rate(customer_id=cf.id, wage=12)
        self.assertEqual((r.price_mode, r.price, r.fixed_month), ('fixed_month', 12, 840), 'фікс за місяць: Ц = Р')
        self.assertEqual(r.our_share, 5, 'W = фікс ÷ норма годин (табелів немає)')
        self.assertIn('фікс за місяць', r.formula)

    def test_m2_14_raise_wage_now(self):
        r = self._rate()
        Raise = self.env['workset.rate.raise']
        self.assertTrue(Raise._fields['date_to'].required, 'без «До» не зберегти')
        x = Raise.create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2, 'date_from': DAY, 'date_to': LATER,
                          'payer': 'workset', 'source': 'other', 'source_note': 'тест'})
        self.assertEqual(x.rate_id, r)
        self.assertEqual(x.state, 'current')
        r.invalidate_recordset()
        self.assertEqual((r.raise_now, r.wage_now), (2, 16), 'на руки зараз = Р + Н')
        self.assertEqual(r.with_context(rates_today=calc.add_days(LATER, 1)).wage_now, 14, 'поза датами — лише Р')
        net = r.net_share
        x.write({'amount': 3})
        self.assertEqual((self.m_a.cost, r.our_share, r.net_share), (14, 5, net), 'у карту — база, W і Ч зміна не чіпає')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Raise.create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1, 'date_from': NEXT, 'date_to': NEXT,
                          'payer': 'line', 'source': 'other', 'source_note': 'тест'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Raise.create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'lump', 'amount': 5, 'date_from': NEXT, 'date_to': DAY,
                          'payer': 'line', 'source': 'other', 'source_note': 'тест'})

    def test_m2_15_payout_one_task(self):
        Raise = self.env['workset.rate.raise'].with_context(rates_today='2026-11-15')
        Month = self.env['workset.rate.raise.month']
        p2 = self._person('Тест П доплата')
        hours = [['2026-11-10', 10], ['2026-11-20', 10], ['2026-11-30', 1], ['2026-12-05', 8]]
        with patch.object(type(self.R), '_pair_hour_rows', return_value=hours):
            x = Raise.create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2, 'date_from': '2026-11-10',
                              'date_to': '2026-12-31', 'payer': 'workset', 'source': 'other', 'source_note': 'тест'})
            self.assertEqual(x.month_ids.mapped('state'), ['open'], 'місяць іде — не до доплати')
            self.assertFalse(Raise._open_tasks())
            Raise.with_context(rates_today='2026-12-01')._raises_daily('2026-12-01')
            nov = x.month_ids.filtered(lambda m: calc.iso(m.month) == '2026-11-01')
            self.assertEqual((nov.hours, nov.total, nov.state), (21, 42, 'task'), '1 грудня: години 10–30 листопада × сума — у задачі')
            task = Raise._open_tasks()
            self.assertEqual(len(task), 1)
            self.assertEqual(task.user_id, self.env.ref('base.user_admin'), 'виконавець — з параметра звірки')
            self.assertEqual(x.state, 'task')
            self._created(task, '2026-12-01')  # «сьогодні» тесту підмінене — день створення задачі теж
            y = Raise.with_context(rates_today='2026-12-02').create({'person_id': p2.id, 'customer_id': self.cust.id, 'kind': 'lump', 'amount': 50,
                                                                     'date_from': '2026-11-01', 'date_to': '2026-11-20', 'payer': 'line',
                                                                     'source': 'other', 'source_note': 'тест'})
            task2 = Raise._open_tasks()
            self.assertEqual(len(task2), 1, 'друга зміна — і далі одна відкрита задача')
            self.assertNotEqual(task2, task, 'наступного дня — минула задача закрита «замінено новою», поставлено нову')
            self.assertEqual(y.month_ids.state, 'task')
            self.assertIn('2 рядки', task2.summary)
            self._created(task2, '2026-12-02')
            nov.with_context(rates_today='2026-12-02').action_paid()
            task3 = Raise._open_tasks()
            self.assertEqual(len(task3), 1)
            self.assertEqual(task3, task2, '«Виплачено» того ж дня — та сама задача переписана, нової немає (одна задача на день)')
            self.assertIn('1 рядок', task3.summary, 'у задачі — без рядка «Виплачено»')
            self.assertEqual(Month.search([('activity_id', '=', task3.id)]), y.month_ids)
            self.assertEqual(nov.state, 'paid')
            self.assertFalse(self._added_notes(task3), '«Виплачено» (рядків поменшало) — без повідомлення')
            # нова зміна того ж дня — та сама задача, а про новий рядок виконавцю приходить повідомлення (4.5, замічання 8 ревю)
            Raise.with_context(rates_today='2026-12-02').create({'person_id': self._person('Тест П доплата 2').id, 'customer_id': self.cust.id,
                                                                'kind': 'lump', 'amount': 45, 'date_from': '2026-11-01', 'date_to': '2026-11-15',
                                                                'payer': 'line', 'source': 'other', 'source_note': 'тест'})
            task4 = Raise._open_tasks()
            self.assertEqual(task4, task3, 'того ж дня — та сама задача')
            self.assertIn('2 рядки', task4.summary)
            notes = self._added_notes(task4)
            self.assertEqual(len(notes), 1, 'про новий рядок — одне повідомлення')
            self.assertIn(self.env.ref('base.user_admin').partner_id, notes.partner_ids, 'адресат — виконавець задачі')
            # «сьогоднішня» — за днем створення, не за строком: задача п'ятниці (04.12, строк — вівторок 08.12) у суботу 05.12 має той самий
            # строк «сьогодні + 2 робочі дні», але вона вчорашня — нова зміна суботи ставить нову задачу
            Raise.with_context(rates_today='2026-12-04').create({'person_id': self._person('Тест П доплата 3').id, 'customer_id': self.cust.id,
                                                                'kind': 'lump', 'amount': 45, 'date_from': '2026-11-01', 'date_to': '2026-11-15',
                                                                'payer': 'line', 'source': 'other', 'source_note': 'тест'})
            fri = Raise._open_tasks()
            self.assertNotEqual(fri, task4, 'задача середи в п’ятницю — не сьогоднішня')
            self._created(fri, '2026-12-04')
            Raise.with_context(rates_today='2026-12-05').create({'person_id': self._person('Тест П доплата 4').id, 'customer_id': self.cust.id,
                                                                'kind': 'lump', 'amount': 45, 'date_from': '2026-11-01', 'date_to': '2026-11-15',
                                                                'payer': 'line', 'source': 'other', 'source_note': 'тест'})
            sat = Raise._open_tasks()
            self.assertEqual(len(sat), 1)
            self.assertNotEqual(sat, fri, 'строк той самий (вівторок), але задачу створено вчора — нова')

    def _created(self, act, day):
        # день створення задачі (create_date пише лише база) — під «сьогодні» тесту
        self.env.flush_all()
        self.env.cr.execute('UPDATE mail_activity SET create_date = %s WHERE id = %s', ('%s 10:00:00' % day, act.id))
        act.invalidate_recordset(['create_date'])

    def _added_notes(self, act):
        return self.env['workset.rate.raise'].browse(act.res_id).message_ids.filtered(lambda m: 'доповнено' in str(m.body or ''))

    def test_m2_15b_late_timesheets_still_pay(self):
        # місяць закінчився, табелів ще немає (0 €) — зміна не «Виплачено», а «Нічого доплачувати» й щоночі перераховується;
        # табель внесли 3-го — рядок «До доплати» (і в задачі); через 45 днів після «До» без годин — «Без годин», далі не перераховується
        Raise = self.env['workset.rate.raise']
        self.env['ir.config_parameter'].sudo().set_param(calc.P_RAISE_GRACE, '')
        with patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
            x = Raise.with_context(rates_today='2026-12-01').create({
                'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2, 'date_from': '2026-11-10',
                'date_to': '2026-11-30', 'payer': 'workset', 'source': 'other', 'source_note': 'тест'})
            self.assertEqual((x.month_ids.state, x.state), ('skip', 'nothing'), '0 годин на 1-е число — не «Виплачено»')
        with patch.object(type(self.R), '_pair_hour_rows', return_value=[['2026-11-20', 8]]):
            x._refresh('2026-12-03')
            self.assertEqual((x.month_ids.state, x.month_ids.total, x.state), ('due', 16, 'due'), 'табель внесли 3-го — до доплати')
            Raise._raises_daily('2026-12-03')
            self.assertEqual(x.month_ids.state, 'task', 'нічна перевірка бере «Нічого доплачувати» й ставить задачу')
        y = Raise.with_context(rates_today='2026-12-01').create({
            'person_id': self._person('Тест П без годин').id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2, 'date_from': '2026-11-10',
            'date_to': '2026-11-30', 'payer': 'line', 'source': 'other', 'source_note': 'тест'})
        with patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
            Raise._raises_daily('2027-01-14')
            self.assertEqual(y.state, 'nothing', '45-й день після «До» — ще чекаємо табелі')
            Raise._raises_daily('2027-01-15')
            self.assertEqual(y.state, 'nohours', 'строк минув — «Без годин»')
        with patch.object(type(self.R), '_pair_hour_rows', return_value=[['2026-11-20', 8]]):
            Raise._raises_daily('2027-01-20')
            self.assertEqual(y.state, 'nohours', '«Без годин» більше не перераховується')

    def test_m2_16_agents_from_deals(self):
        deals = [{'id': 1, 'rate': 2.5, 'state': 'active', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': '',
                  'agent': 'Тест посередник А'},
                 {'id': 2, 'rate': 1.5, 'state': 'zvirka', 'kind': 'hour', 'customer_ids': [], 'line_id': self.line.id, 'date_from': OLD, 'date_to': '',
                  'agent': 'Тест посередник Б'},
                 {'id': 3, 'rate': 9, 'state': 'draft', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': ''},
                 {'id': 4, 'rate': 9, 'state': 'closed', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': ''},
                 {'id': 5, 'rate': 9, 'state': 'active', 'kind': 'fixed', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': ''},
                 {'id': 6, 'rate': 9, 'state': 'active', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': OLD}]
        with patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]):
            r = self._rate()
        self.assertEqual(r.agent_client_share, 4, 'дві діючі (2,5 + 1,5); чернетка, закрита, фікс, поза датами — ні')
        self.assertEqual((r.price, r.our_share), (19, 1), 'W = Ц − Р − посередник')
        self.assertIn('Тест посередник А · 2,5', r.agent_client_note)
        with patch.object(agent_deals, 'read_deals', return_value=None), patch.object(agent_deals, 'read_people', return_value=None):
            n = self._rate(date_from=NEXT)
        self.assertEqual((n.agent_client_share, n.our_share), (0, 5), 'моделей посередників немає — 0, без помилки')
        self.assertEqual(n.agent_client_note, 'посередники не підключені')

    def test_m2_16b_deal_by_customer_before_line(self):
        # домовленість із замовниками підходить лише за замовником ставки, без замовників — за лінією: дві домовленості одного посередника
        # на одній лінії з різними замовниками не додаються (було: кожна ставка лінії отримувала суму обох)
        other = self._cust('Тест замовник Б лінії')
        far = self.env['workset.line'].create({'name': 'Тест лінія далека'})
        far_cust = self._cust('Тест замовник далекої лінії', far)
        deal = {'state': 'active', 'kind': 'hour', 'date_from': '', 'date_to': '', 'agent': 'Тест посередник А'}
        deals = [dict(deal, **{'id': 1, 'rate': 2.5, 'customer_ids': [self.cust.id], 'line_id': self.line.id}),
                 dict(deal, **{'id': 2, 'rate': 1.5, 'customer_ids': [other.id], 'line_id': self.line.id}),
                 dict(deal, **{'id': 3, 'rate': 0.7, 'customer_ids': [self.cust2.id], 'line_id': far.id}),
                 dict(deal, **{'id': 4, 'rate': 0.4, 'customer_ids': [], 'line_id': far.id})]
        with patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]):
            a = self._rate()
            b = self._rate(customer_id=other.id)
            c = self._rate(customer_id=self.cust2.id)
            d = self._rate(customer_id=far_cust.id)
        self.assertEqual((a.line_id, b.line_id, c.line_id, d.line_id), (self.line, self.line, self.env['workset.line'], far))
        self.assertEqual((a.agent_client_share, b.agent_client_share), (2.5, 1.5), 'кожній ставці — домовленість її замовника, не сума по лінії')
        self.assertEqual(c.agent_client_share, 0.7, 'замовник у домовленості іншої лінії — підходить за замовником')
        self.assertEqual(d.agent_client_share, 0.4, 'домовленість без замовників — за лінією; чужий замовник тієї лінії — ні')

    def test_m2_17_people_agent(self):
        people = [{'id': 5, 'partner_id': self.person.id, 'rate': 1.5, 'state': 'active', 'kind': 'hour', 'date_from': OLD, 'date_to': '',
                   'deal_from': '', 'deal_to': ''}]
        with patch.object(agent_deals, 'read_deals', return_value=[]), patch.object(agent_deals, 'read_people', return_value=people):
            r = self._rate()
            self.assertEqual((r.agent_people_share, r.people_agent_payer, r.our_share), (1.5, 'line', 5), 'платить лінія — W не зменшується')
            self.assertFalse(r.people_agent_payer_manual)
            r.write({'people_agent_payer': 'workset'})
            self.assertEqual((r.our_share, r.people_agent_payer_manual), (3.5, True), 'платить WorkSET (вручну) — W = 5 − 1,5')
            r.write({'wage': 15})
            self.assertEqual(r.people_agent_payer, 'workset', '«вручну» тримається при перерахунку')
            # правило змінило платника: ставка, яку не правили руками, іде за правилом; повернули те саме, що в правилі, — знову не «вручну»
            self.line_rule.people_agent_payer = 'workset'
            n = self._rate(date_from=NEXT)
            self.assertEqual((n.people_agent_payer, n.people_agent_payer_manual, n.our_share), ('workset', False, 3.5))
            n.write({'people_agent_payer': 'workset'})
            self.assertFalse(n.people_agent_payer_manual, 'те саме, що в правилі, — за правилом')
            n.write({'people_agent_payer': 'line'})
            self.assertTrue(n.people_agent_payer_manual)
            self.line_rule.people_agent_payer = 'client'
            n._rechain()
            self.assertEqual(n.people_agent_payer, 'line', 'правило змінилось — «вручну» не чіпає')

    def test_m2_18_client_price(self):
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
            self.assertEqual((r.client_price, r.line_share), (0, 0), 'К невідома — П порожньо (0)')
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                r.write({'client_price': 43})
            r.invalidate_recordset()
            r.write({'client_price': 43, 'client_price_source': 'тест: лист', 'client_price_date': DAY})
            self.assertEqual(r.line_share, 24, 'П = К 43 − Ц 19')
            self.assertEqual(r.our_share, 5, 'К у нашу частку не входить')
            self.assertTrue(r.client_price_manual)

    def test_m2_19_office_bonus(self):
        self._quiet_bonuses()
        Bonus = self.env['workset.office.bonus']
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4')
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MONTH_HOURS, '')
        Bonus.create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 1.6, 'customer_ids': [(6, 0, [self.cust.id])], 'date_from': OLD, 'source': 'other'})
        Bonus.create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 4, 'customer_ids': [(6, 0, [self.cust2.id])], 'date_from': OLD, 'source': 'other'})
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
            self.assertEqual((r.office_bonus, r.net_share), (1.6, 3.4), '«€ за годину» замовника: Б 1,6, Ч = 5 − 1,6; бонус іншого замовника — ні')
            self.assertEqual(r.net_level, 'low', 'Ч 3,4 < мінімум 4')
            Bonus.create({'employee_id': self.emp_a.id, 'kind': 'worker_month', 'amount': 50, 'date_from': OLD, 'source': 'other'})
            with patch.object(type(self.R), '_pair_hour_rows', return_value=[['2026-02-10', 170]]):
                self.R.rates_daily()
            self.assertEqual((r.month_hours, r.office_bonus, r.bonus_norm), (170, 1.89, False), 'бонус за працівника ÷ 170 год минулого місяця')
            with patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
                self.R.rates_daily()
            self.assertEqual((r.office_bonus, r.bonus_norm), (1.9, True), 'годин немає — ÷ 168 і позначка «за нормою»')
            self.assertEqual(r.net_share, 3.1)
            self.assertEqual(r.share_level, 'mid', 'W 5 не нижче мінімуму 4')

    def test_c_office_bonus_without_card(self):
        # людина офісу без картки працівника (на проді — частина офісу; картки без користувача не додаємо): правило за іменем, бонус у
        # ставці — той самий (calc.office_bonus картку не читає). Ім'я — вигадане
        self._quiet_bonuses()
        Bonus = self.env['workset.office.bonus']
        b = Bonus.create({'employee_label': 'Тест офіс без картки', 'kind': 'hour', 'amount': 0.6, 'customer_ids': [(6, 0, [self.cust.id])],
                          'date_from': OLD, 'source': 'other'})
        self.assertEqual((b.employee_id, b.employee_label, b.has_card), (self.env['hr.employee'], 'Тест офіс без картки', False))
        self.assertTrue(b.name.startswith('Тест офіс без картки · '), b.name)
        a, c = self._no_agents()
        with a, c:
            r = self._rate()
        self.assertEqual((r.office_bonus, r.net_share), (0.6, 4.4), 'бонус від картки не залежить')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Bonus.create({'kind': 'hour', 'amount': 0.9, 'date_from': OLD, 'source': 'other'})  # ні картки, ні імені
        # з карткою — ім'я з картки; «Бонуси офісу — перегляд» без прав кадрів читає обидва (ім'я — хранимий рядок)
        b2 = Bonus.create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 0.3, 'customer_ids': [(6, 0, [self.cust2.id])], 'date_from': OLD,
                           'source': 'other'})
        self.assertEqual((b2.employee_label, b2.has_card), (self.emp_a.name, True))
        viewer = new_test_user(self.env, login='ws_c_bonus_viewer', groups='base.group_user,workset_rates.group_bonus_viewer')
        rows = Bonus.with_user(viewer).with_context(allowed_company_ids=viewer.company_ids.ids).search_read([('id', 'in', (b | b2).ids)],
                                                                                                            ['employee_label', 'has_card'])
        self.assertEqual(sorted((x['employee_label'], x['has_card']) for x in rows), sorted([('Тест офіс без картки', False), (self.emp_a.name, True)]))

    def test_m2_20_rights(self):
        lt = self.env['workset.line'].create({'name': 'Тест лінія права'})
        line_rule = self._rule(line_id=lt.id, anchor='tariff', share_we_found=5)
        ct = self._cust('Тест замовник права', lt)
        editor = new_test_user(self.env, login='ws_m2_editor', groups='base.group_user,workset_rates.group_editor')
        owner = new_test_user(self.env, login='ws_m2_owner', groups='base.group_user,workset_rates.group_rule_editor')
        viewer = new_test_user(self.env, login='ws_m2_viewer', groups='base.group_user,workset_rates.group_bonus_viewer')
        hr_user = new_test_user(self.env, login='ws_m2_hr', groups='base.group_user,hr.group_hr_user')

        def as_(user, rec):
            return rec.with_user(user).with_context(allowed_company_ids=[user.company_id.id])

        Rule = self.env['workset.rate.rule']
        tariff = as_(editor, Rule).create({'line_id': lt.id, 'customer_id': ct.id, 'price': 31, 'date_from': DAY, 'source': 'call', 'source_note': 'тест'})
        self.assertEqual(tariff.anchor, 'tariff', 'якір тарифу — з лінії')
        as_(editor, tariff).write({'price': 33, 'client_price': 43, 'client_price_source': 'тест', 'client_price_date': DAY})
        self.assertEqual(tariff.price, 33, 'редактор править тариф')
        for vals in ({'min_share': 5}, {'price_mode': 'fixed_month', 'fixed_month': 100}, {'share_we_found': 8.5}, {'anchor': 'markup'}):
            with self.assertRaisesRegex(UserError, 'власник'), self.env.cr.savepoint():
                as_(editor, tariff).write(vals)
        with self.assertRaisesRegex(UserError, 'власник'), self.env.cr.savepoint():
            as_(editor, line_rule).write({'note': 'x'})
        with self.assertRaisesRegex(UserError, 'власник'), self.env.cr.savepoint():
            as_(editor, Rule).create({'line_id': lt.id, 'anchor': 'tariff', 'share_we_found': 9, 'date_from': DAY, 'source': 'call', 'source_note': 'тест'})
        as_(owner, tariff).write({'min_share': 5})
        self.assertEqual(tariff.min_share, 5, 'група «правила ліній» — так')
        self.assertEqual(as_(hr_user, tariff).read(['price'])[0]['price'], 33, 'кадри читають')
        with self.assertRaises(AccessError):
            as_(hr_user, tariff).write({'note': 'x'})
        with self.assertRaises(AccessError):
            as_(editor, tariff).unlink()
        bonus = self.env['workset.office.bonus'].create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 1.2, 'date_from': OLD, 'source': 'other'})
        with self.assertRaises(AccessError):
            as_(editor, bonus).read(['amount'])
        self.assertEqual(as_(viewer, bonus).read(['amount'])[0]['amount'], 1.2, '«бонуси офісу — перегляд» читає')
        with self.assertRaises(AccessError):
            as_(viewer, bonus).write({'amount': 2.4})
        as_(owner, bonus).write({'amount': 2.4})
        r = self._rate()
        self.assertTrue(as_(editor, r).read(['office_bonus', 'net_share']), 'на ставці редактор бачить підсумок')
        # видаляє лише системний адміністратор (права моделі, як до круга 06.11): tariff створено редактором — запис несе його оточення,
        # тож видалення — явно від імені кожного (збірка Odoo.sh c326b874c: tariff.unlink() ішов від редактора)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            as_(owner, tariff).unlink()
        admin = new_test_user(self.env, login='ws_m2_admin', groups='base.group_user,base.group_system')
        as_(admin, tariff).unlink()
        self.assertFalse(tariff.exists(), 'видаляє системний адміністратор')

    def test_m2_21_averages(self):
        for name in ('net_share', 'office_bonus', 'line_share', 'agent_client_share', 'agent_people_share', 'client_price', 'fixed_month'):
            self.assertEqual(self.R._fields[name].aggregator, 'avg', name)
        Rule = self.env['workset.rate.rule']
        for name in ('share_line_found', 'share_we_found', 'price', 'fixed_month', 'min_share', 'client_price'):
            self.assertEqual(Rule._fields[name].aggregator, 'avg', 'правило: %s' % name)
        a, b = self._no_agents()
        with a, b:
            self._rate(wage=14)
            self._rate(person_id=self._person('Тест П середні').id, wage=14, found_by='line')
        groups = self.R._read_group([('line_id', '=', self.line.id)], ['line_id'], ['net_share:avg', 'our_share:avg'])
        self.assertAlmostEqual(groups[0][2], 4.5, msg='у рядку групи — середнє частки (5 і 4), не сума')

    def test_m2_22_summary(self):
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4.5')
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            us = self._rate()                                                                # W 5 — не нижче 4,5
            ln = self._rate(person_id=self._person('Тест П полоса').id, found_by='line')    # W 4 — нижче
        (us | ln).write({'work': 'work', 'work_group': 'g1_work'})
        self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1, 'date_from': DAY,
                                               'date_to': NEXT, 'payer': 'workset', 'source': 'other', 'source_note': 'тест'})
        s = self.R.rates_summary([('id', 'in', (us | ln).ids)])
        self.assertEqual((s['working'], s['avg_net'], s['below_min'], s['raises']), (2, 4.5, 1, 1),
                         'полоса: сер. чиста (5 і 4), нижче мінімуму — одна, тимчасова зміна — в однієї пари')
        # М2c: числа — за доменом пошуку (полоса над списком), а не за всіма ставками
        s = self.R.rates_summary([('id', '=', ln.id)])
        self.assertEqual((s['working'], s['avg_net'], s['below_min'], s['raises']), (1, 4, 1, 0), 'лише ставка «знайшла лінія»: без тимчасової зміни')
        s = self.R.rates_summary([('id', '=', us.id)])
        self.assertEqual((s['avg_net'], s['below_min'], s['raises']), (5, 0, 1))

    def test_m2_23_plan_at(self):
        self._quiet_bonuses()
        self.env['workset.office.bonus'].create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 0.7, 'date_from': OLD,
                                                 'date_to': calc.add_days(DAY, -3), 'source': 'other'})
        deals = [{'id': 7, 'rate': 2.5, 'state': 'active', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': OLD,
                  'date_to': calc.add_days(DAY, -3), 'agent': 'Тест посередник'}]
        with patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]):
            old = self._rate(date_from=calc.add_days(DAY, -10), wage=12)
            cur = self._rate()
            self.assertEqual((cur.agent_client_share, cur.office_bonus), (0, 0), 'на сьогодні домовленість і бонус уже не діють')
            plan = self.R._plan_at([(self.person.id, self.cust.id)], calc.add_days(DAY, -5))[(self.person.id, self.cust.id)]
            none = self.R._plan_at([(self.person.id, self.cust.id)], '2020-01-01')[(self.person.id, self.cust.id)]
        self.assertEqual((plan['rate_id'], plan['wage'], plan['price']), (old.id, 12, 17), 'числа ставки тієї дати')
        self.assertEqual((plan['agent_client'], plan['office_bonus'], plan['our_share'], plan['net_share']), (2.5, 0.7, 2.5, 1.8), 'А_з і Б — на ту дату')
        self.assertIsNone(none, 'ставки на дату немає')

    # ——— М2b: А6 «договір → база», страж, розділи звірки (розділ 8 п. 6–13, 4.7) ———
    def _a6(self, mode):
        # вимикач А6 (параметр workset_rates.a6_mode): False — параметра немає (вимкнено), 'dry', 'on'
        self.env['ir.config_parameter'].sudo().set_param(calc.P_A6_MODE, mode)

    def _contracts(self, wages):
        """Ставка договору версій — зі словника {(картка, «РРРР-ММ-ДД»): €/год або (€/год, погодинна)}: м'яке читання модуля зарплат
        підмінене, тож тест однаковий з hr_payroll і без нього (версії справжні — hr.version, А6 бачить їх створення й правку).
        А6 — увімкнено (параметр 'on'; за замовчуванням вимкнено — test_m2b_a6_mode_off_dry_on)."""
        self._a6('on')
        def contract_of(_rate, version):
            w = wages.get((version.employee_id.id, calc.iso(version.date_version))) if version else None
            if w is None:
                return {'wage': 0.0, 'hourly': False}
            return {'wage': float(w[0]), 'hourly': w[1]} if isinstance(w, tuple) else {'wage': float(w), 'hourly': True}
        Rate = type(self.R)
        return patch.object(Rate, '_has_contract', return_value=True), patch.object(Rate, '_contract_of', contract_of)

    def _base_version(self, wages, wage=14):
        # перша версія картки договору (emp_a: рядок карти в замовника, менший id) — з давньою датою: поточна — вона, а не «сьогодні» прогону
        v = self.emp_a.version_ids.sorted('date_version')[:1]
        wages[(self.emp_a.id, OLD)] = wage
        v.write({'date_version': OLD})
        self.emp_a._compute_current_version_id()
        return v

    def _version(self, wages, day, wage, emp=None):
        emp = emp or self.emp_a
        wages[(emp.id, day)] = wage
        return emp.create_version({'date_version': day})

    def _pair(self, day, cust=None):
        return self.R.search([('person_id', '=', self.person.id), ('customer_id', '=', (cust or self.cust).id), ('date_from', '=', day)])

    def _bodies(self, rec):
        return ' '.join(str(b) for b in rec.message_ids.mapped('body'))

    def test_m2_06_a6_markup(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate()
            v = self._version(wages, LATER, 15)
            new = self._pair(LATER)
            self.assertEqual(len(new), 1, 'нова версія договору — нова ставка з її дати')
            self.assertEqual((new.wage, new.price, new.our_share, new.state), (15, 20, 5, 'future'), 'надбавка: Ц = 15 + 5, W та сама')
            self.assertEqual((new.wage_source, new.source, new.contract_version_id), ('contract', 'contract', v))
            self.assertIn('з договору', self._bodies(new))
            self.assertIn('надбавка — платять нам 19 → 20', self._bodies(new))
            self.assertEqual((r.wage, r.price), (14, 19), 'попередня ставка не змінена')
            self.assertEqual(calc.iso(r.date_to), calc.add_days(LATER, -1))
            self.R.with_context(rates_today=LATER).rates_daily()
            self.assertEqual(new.state, 'current')
            self.assertEqual((self.m_a.cost, self.m_a.is_cost_changed), (15, True), 'у карту — нова база з прапорцем')

    def test_m2_07_a6_tariff_below_min(self):
        self._isolate()
        lt = self.env['workset.line'].create({'name': 'Тест лінія тариф А6'})
        self._rule(line_id=lt.id, anchor='tariff', share_we_found=5)
        ct = self._cust('Тест замовник тариф А6', lt)
        self._rule(line_id=lt.id, customer_id=ct.id, anchor='tariff', price=31)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '16.5')
        self._quiet_bonuses()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate(customer_id=ct.id)
            self.assertEqual((r.price, r.our_share), (31, 17))
            self.assertNotEqual(r.share_level, 'low', 'W 17 ≥ мінімуму 16,5')
            self._version(wages, LATER, 15)
            new = self._pair(LATER, ct)
            self.assertEqual((new.wage, new.price, new.our_share, new.share_level), (15, 31, 16, 'low'), 'тариф: ціна та сама, частка падає нижче мінімуму')
            self.assertIn('тариф — платять нам те саме, частка 17 → 16', self._bodies(new))
            self.assertIn('Нижче мінімуму', self._bodies(new))
            res = self.R.with_context(rates_today=LATER).rates_check()
        self.assertEqual(res['sections']['low'], 1, 'розділ «Нижче мінімуму»')
        self.assertFalse(self.env['mail.activity'].search([('res_model', '=', 'workset.rate'), ('res_id', '=', new.id)]),
                         '«нижче мінімуму» — лише розділ звірки, задач модуль не ставить (10а.2)')

    def test_m2_08_a6_fixed_month(self):
        lf = self.env['workset.line'].create({'name': 'Тест лінія фікс А6'})
        cf = self._cust('Тест замовник фікс А6', lf)
        self._rule(line_id=lf.id, customer_id=cf.id, anchor='tariff', price_mode='fixed_month', fixed_month=840)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MONTH_HOURS, '')
        self._quiet_bonuses()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c, patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
            self._base_version(wages, 12)
            self._rate(customer_id=cf.id, wage=12)
            self._version(wages, LATER, 13)
            new = self._pair(LATER, cf)
        self.assertEqual((new.price_mode, new.price, new.our_share), ('fixed_month', 13, 5), 'фікс: Ц = нова Р, W = фікс ÷ годин')
        self.assertIn('Фікс за місяць — платять нам = зарплата працівника (12 → 13)', self._bodies(new))

    def test_m2_09_a6_temporary_version(self):
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        Raise = self.env['workset.rate.raise']
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate()
            x = Raise.create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2, 'date_from': LATER,
                              'date_to': '2026-04-30', 'payer': 'workset', 'source': 'other', 'source_note': 'тест'})
            # у формі: нова версія з дати (копія — та сама ставка), далі зберегли ставку й позначку разом
            v = self._version(wages, LATER, 14)
            self.assertFalse(self._pair(LATER), 'копія версії з тією самою ставкою — нової ставки немає')
            wages[(self.emp_a.id, LATER)] = 16
            v.write({'workset_temporary': True})
            self.assertFalse(self._pair(LATER), 'версія «Тимчасово» — ставка не змінюється')
            self.assertEqual(x.version_id, v, 'тимчасова зміна пари на ці дати — зв’язана з версією')
            self.assertEqual(r.wage, 14)
            # друга тимчасова версія — тимчасової зміни на її дату немає → розділ звірки
            v2 = self._version(wages, '2026-06-01', 14)
            wages[(self.emp_a.id, '2026-06-01')] = 17
            v2.write({'workset_temporary': True})
            self.assertFalse(self._pair('2026-06-01'))
            res = self.R.with_context(rates_today='2026-06-02').rates_check()
        self.assertEqual(res['sections']['tempver'], 1, 'тимчасова версія без тимчасової зміни — у звірці; зв’язана — ні')

    def test_m2_10_a6_backdated_same_date_and_bad_wage(self):
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            r1 = self._rate(date_from=calc.add_days(DAY, -30))
            r2 = self._rate(date_from=LATER, wage=16)
            v = self._version(wages, DAY, 15)
            mid = self._pair(DAY)
            self.assertEqual(len(mid), 1, 'версія задним числом між двома ставками — вставлена')
            self.assertEqual((calc.iso(r1.date_to), calc.iso(mid.date_to)), (calc.add_days(DAY, -1), calc.add_days(LATER, -1)), '«до» сусідів перераховано')
            self.assertEqual(r2.wage, 16)
            wages[(self.emp_a.id, DAY)] = 15.5
            v.write({'workset_temporary': False})
            self.assertEqual((len(self._pair(DAY)), mid.wage), (1, 15.5), 'та сама дата — змінено «На руки», дубля немає')
            self._version(wages, '2026-03-20', (15.8, False))
            self.assertFalse(self._pair('2026-03-20'), 'не погодинний договір — ставка не тронута')
            self._version(wages, '2026-05-01', 140)
            self.assertFalse(self._pair('2026-05-01'), 'вище за поріг — ставка не тронута')
            self.emp_a._compute_current_version_id()
            res = self.R.with_context(rates_today='2026-05-02').rates_check()
        self.assertEqual(res['sections']['wagebad'], 1, 'розділ «не погодинний або вищий за поріг»')
        self.assertEqual(res['sections']['contract'], 0, 'та сама пара не дублюється в «Договір ≠ база»')

    def test_m2_11_a6_perimeter_and_card(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            self._version(wages, LATER, 15, self.emp_out)
            self.assertFalse(self._pair(LATER), 'картка фірми поза периметром — нічого')
            self._version(wages, LATER, 15, self.emp_b)
            self.assertFalse(self._pair(LATER), 'картка не договірна для пари — нічого')

    def test_m2_12_a6_catchup(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            wages[(self.emp_a.id, LATER)] = 15
            self.emp_a.with_context(workset_rates_no_sync=True).create_version({'date_version': LATER})  # «повз А6», як імпорт чи SQL
            self.assertFalse(self._pair(LATER))
            dry = self.R.with_context(rates_today=DAY, rates_dry=True).rates_daily()
            self.assertEqual(dry['contract']['created'], 1, 'лише розрахунок: скільки ставок створить догон')
            self.assertFalse(self._pair(LATER))
            res = self.R.with_context(rates_today=DAY).rates_daily()
            self.assertEqual(res['contract']['created'], 1)
            self.assertEqual(self._pair(LATER).wage, 15, 'нічний догон створив ставку з версії')

    def test_m2_13_up_and_back_guard(self):
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            va = self._version(wages, LATER, 16)
            vb = self._version(wages, '2026-05-10', 14)
            up, back = self._pair(LATER), self._pair('2026-05-10')
            self.assertEqual((up.wage, back.wage), (16, 14), 'А6 узяв обидві версії')
            res = self.R.with_context(rates_today='2026-05-11').rates_daily()
            self.assertEqual(res['updown']['marked'], 2)
            for r in (up, back):
                self.assertTrue(r.to_check, 'обидві ставки — «Перевірити»')
                self.assertIn('схоже на тимчасове підвищення', r.check_reason)
            chk = self.R.with_context(rates_today='2026-05-11').rates_check()
            self.assertEqual((chk['sections']['updown'], chk['sections']['check']), (1, 0), 'свій розділ, не «Перевірити після переносу»')
            va.write({'workset_temporary': True})
            self.assertFalse(up.active, 'ставка з версії, позначеної «Тимчасово», — в архіві (замічання 2 ревю)')
            res = self.R.with_context(rates_today='2026-05-11').rates_daily()
            self.assertEqual((res['updown']['marked'], res['updown']['cleared']), (0, 2), 'з позначкою — тиша, позначку стража знято')
            self.assertFalse(up.to_check or back.to_check)
            va.write({'workset_temporary': False})
            wages[(self.emp_a.id, '2026-07-30')] = 14
            vb.write({'date_version': '2026-07-30'})  # повернення через 120 днів
            self.assertFalse(back.active, 'дату версії перенесли — ставка зі старою датою в архіві')
            self.assertEqual(self._pair('2026-07-30').wage, 14, '… і є з нової дати версії')
            res = self.R.with_context(rates_today='2026-07-31').rates_daily()
            self.assertEqual(res['updown']['marked'], 0, 'повернення пізніше за вікно — тиша')

    def test_m2_contract_excused_by_raise_or_temporary(self):
        # «Договір ≠ база» мовчить, поки в пари діє тимчасова зміна або поточну версію позначено «Тимчасово» (розділ 5.5)
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            base = self._base_version(wages)
            r = self._rate()
            self.assertTrue(r.contract_ok, 'договір 14 = база')
            wages[(self.emp_a.id, OLD)] = 16  # версію змінили повз А6
            r.action_apply_now()
            self.assertFalse(r.contract_ok)
            x = self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2,
                                                       'date_from': calc.add_days(DAY, -1), 'date_to': calc.add_days(DAY, 5), 'payer': 'line',
                                                       'source': 'other', 'source_note': 'тест'})
            r.action_apply_now()
            self.assertTrue(r.contract_ok, 'діє тимчасова зміна, договір 16 = Р 14 + Н 2 — мовчить')
            x.write({'amount': 1})
            r.action_apply_now()
            self.assertFalse(r.contract_ok, 'договір 16 ≠ Р 14 + Н 1 — не мовчить, хоч зміна й діє')
            x.write({'amount': 2})
            x.action_cancel()
            r.action_apply_now()
            self.assertFalse(r.contract_ok)
            base.with_context(workset_rates_no_sync=True).write({'workset_temporary': True})
            r.action_apply_now()
            self.assertTrue(r.contract_ok, 'версію позначено «Тимчасово» — мовчить')

    def test_m2_47_check_sections(self):
        self._isolate()
        self._quiet_bonuses()
        self.env['workset.rate.raise'].sudo().search([]).write({'state': 'cancel'})  # зміни бази (staging-копія) — поза тестом
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '3')
        a, b = self._no_agents()
        with a, b:
            # «Своя» з давньою загальною ставкою: правило лінії змінилось 20 днів тому → у «не за правилом»; «Своя» на новому правилі — ні
            stale = self._rate(person_id=self._person('Тест П стара своя').id, date_from=calc.add_days(DAY, -40), price=31, own_reason='тест: стара')
            self._rule(share_line_found=4.25, share_we_found=5.25, date_from=calc.add_days(DAY, -20))
            self._rate(person_id=self._person('Тест П нова своя').id, price=26, own_reason='тест: нова')
            found = self._rate(person_id=self._person('Тест П хто').id, found_by=False)
            lt = self.env['workset.line'].create({'name': 'Тест лінія тариф звірка'})
            ct = self._cust('Тест замовник тариф звірка', lt)
            self._rule(line_id=lt.id, customer_id=ct.id, anchor='tariff', price=16)
            low = self._rate(person_id=self._person('Тест П низька').id, customer_id=ct.id)
            loose = self.env['res.partner'].create({'name': 'Тест замовник без лінії', 'is_company': True})
            norule = self._rate(person_id=self._person('Тест П без правила').id, customer_id=loose.id, price=20)
            self.env['workset.rate.raise'].create({'person_id': found.person_id.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1,
                                                   'date_from': calc.add_days(DAY, -5), 'date_to': calc.add_days(DAY, 2), 'payer': 'line',
                                                   'source': 'other', 'source_note': 'тест'})
            res = self.R.rates_check()
        self.assertTrue(stale.is_own and low.share_level == 'low' and not norule.rule_id)
        s = res['sections']
        self.assertEqual((s['rule'], s['low'], s['found'], s['norule'], s['raise']), (1, 1, 1, 1, 1),
                         'своя з давньою загальною; нижче мінімуму; хто знайшов; загальної немає; зміна закінчується за ≤ 3 дні')
        body = self.env['workset.rate.check'].search([('date', '=', DAY)]).body
        self.assertIn('Нижче мінімуму — 1', body)
        rule_part = body.split('Частка WorkSET не за правилом')[1].split('<h3>')[0]
        self.assertIn('Тест П стара своя', rule_part)
        self.assertNotIn('Тест П нова своя', rule_part, '«Своя» без зміни загальної — не шум звірки')

    # ——— М2b, правки ревю (11-review-m2b.md): вимикач, ставка йде за версією, догон, форма версії, помилка, страж, мова «Контролю» ———
    def test_m2_11b_only_card_out_of_perimeter(self):
        # у пари єдина картка — у фірмі поза периметром: вона й картка договору (calc.pick_card), але А6 її не бере
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            emp = self.env['hr.employee'].with_company(self.co_out).create({'name': 'Тест П лише поза', 'company_id': self.co_out.id})
            v = emp.version_ids.sorted('date_version')[:1]
            wages[(emp.id, OLD)] = 14
            v.write({'date_version': OLD})
            emp._compute_current_version_id()
            person = emp.work_contact_id
            self._rate(person_id=person.id)
            self.assertEqual(self.R._contract_card(person.id, self.cust.id), emp, 'єдина картка пари — картка договору')
            self._version(wages, LATER, 15, emp)
            self.assertFalse(self.R.search([('person_id', '=', person.id), ('date_from', '=', LATER)]), 'картка поза периметром — ставки немає')

    def test_m2b_a6_mode_off_dry_on(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            self._a6(False)  # параметра немає — вимкнено (так після оновлення модуля)
            self._version(wages, LATER, 15)
            self.assertFalse(self._pair(LATER), 'А6 вимкнено — збереження договору ставку не створює')
            res = self.R.with_context(rates_today=DAY).rates_daily()
            self.assertEqual((res['a6_mode'], res['contract']['created'], res['a6_would']), ('off', 0, 0), 'і ніч не догонить')
            self.assertFalse(self._pair(LATER))
            self._a6('dry')
            self._version(wages, '2026-05-01', 16)
            self.assertFalse(self._pair('2026-05-01'), 'dry — збереження договору нічого не робить')
            res = self.R.with_context(rates_today=DAY).rates_daily()
            self.assertEqual((res['a6_mode'], res['a6_would']), ('dry', 2), 'ніч лише рахує: створив би дві ставки')
            self.assertIn('створив би ставок 2', res['message'])
            self.assertFalse(self._pair(LATER) or self._pair('2026-05-01'), 'dry нічого не пише')
            self._a6('on')
            res = self.R.with_context(rates_today=DAY).rates_daily()
            self.assertEqual((res['a6_mode'], res['contract']['created'], res['a6_would']), ('on', 2, 0))
            self.assertEqual((self._pair(LATER).wage, self._pair('2026-05-01').wage), (15, 16), 'увімкнули — догон створив обидві')

    def test_m2b_rate_follows_version(self):
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        # дати версій — до «сьогодні» тесту (DAY): ставки з них чинні, їхнє «На руки» стоїть у карті проєктів, і видно, що після архіву
        # карта повертається до попередньої ставки 14 (ревю № 2, п. 5)
        cost = lambda: (self.m_a.cost, self.m_b.cost)  # noqa: E731
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate(date_from='2026-02-01')
            self.assertEqual(cost(), (14, 14))
            # відмітили «Тимчасово» після збереження → ставки з версії немає, база — попередня
            v = self._version(wages, '2026-02-20', 16)
            new = self._pair('2026-02-20')
            self.assertEqual((new.wage, new.state, cost()), (16, 'current', (16, 16)))
            v.write({'workset_temporary': True})
            self.assertFalse(self._pair('2026-02-20'), 'позначили після збереження — ставки з версії немає')
            self.assertFalse(new.active)
            self.assertIn('позначено «Тимчасово (не база)»', self._bodies(new))
            self.assertFalse(r.date_to, 'база — знову попередня ставка, «до» порожньо')
            self.assertEqual(cost(), (14, 14), 'карта проєктів — знову 14')
            # дату перенесли (у майбутнє) → ставка переїхала, сьогодні база — знову 14
            v2 = self._version(wages, '2026-02-25', 15)
            old = self._pair('2026-02-25')
            self.assertEqual((old.contract_version_id, cost()), (v2, (15, 15)))
            wages[(self.emp_a.id, LATER)] = 15
            v2.write({'date_version': LATER})
            self.assertFalse(self._pair('2026-02-25'))
            self.assertFalse(old.active)
            self.assertIn('Дату версії договору перенесено', self._bodies(old))
            moved = self._pair(LATER)
            self.assertEqual((moved.wage, moved.contract_version_id, moved.state), (15, v2, 'future'), 'ставка переїхала на нову дату версії')
            self.assertEqual(cost(), (14, 14), 'перенесли — до нової дати в карті знову 14')
            # архівували версію → ставка в архіві
            v3 = self._version(wages, '2026-02-27', 15)
            third = self._pair('2026-02-27')
            self.assertEqual(cost(), (15, 15))
            v3.write({'active': False})
            self.assertFalse(self._pair('2026-02-27'))
            self.assertFalse(third.active, 'архівували версію — ставка з неї в архіві')
            self.assertIn('архівовано', self._bodies(third))
            self.assertEqual(cost(), (14, 14), 'архівували — у карті знову 14')
            self.assertTrue(r.active and r.wage == 14, 'ставку, внесену не з договору, не чіпаємо')

    def test_m2b_follow_keeps_rate_of_older_version(self):
        # ревю № 2, п. 1: А6 створив ставку по v1; кадрова правка дала v2 з тим самим «На руки» пізніше — ставка лишається за v1,
        # ночі її не знімають («дату перенесли» тут хибне)
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            v1 = self._version(wages, '2026-05-10', 16)
            mine = self._pair('2026-05-10')
            self.assertEqual((mine.wage, mine.source, mine.contract_version_id), (16, 'contract', v1))
            self._version(wages, '2026-06-01', 16)
            self.assertEqual(mine.contract_version_id, v1, 'ставка «з договору» з іншою датою — за своєю версією')
            for _night in range(2):
                res = self.R.with_context(rates_today='2026-06-02').rates_daily()
                self.assertEqual((res['contract']['archived'], res['contract']['linked'], res['contract']['created']), (0, 0, 0))
            self.assertTrue(mine.active, 'ставка з 10.05 — діюча')
            self.assertEqual(mine.contract_version_id, v1)
            gone = self.R.with_context(active_test=False).search([('person_id', '=', self.person.id), ('customer_id', '=', self.cust.id),
                                                                  ('active', '=', False)])
            self.assertFalse(gone, 'в архіві нічого')

    def test_m2b_follow_restores_rate_rewritten_by_contract(self):
        # ревю № 2, п. 2: ставку внесли руками, версія з тієї самої дати переписала її «На руки» (update); договір перестав бути базою —
        # ставка не в архів, а назад прежнє «На руки» (найгірший випадок — перша ставка пари); прежнього числа немає — «Перевірити»
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            first = self._rate(date_from='2026-02-01')
            v = self._version(wages, '2026-02-01', 15)
            self.assertEqual((first.wage, first.wage_source, first.contract_version_id, first.wage_before_contract), (15, 'contract', v, 14))
            self.assertEqual(self.m_a.cost, 15)
            v.write({'workset_temporary': True})
            self.assertTrue(first.active, 'перша ставка пари не в архіві')
            self.assertEqual((first.wage, first.wage_source, first.contract_version_id.id, first.wage_before_contract), (14, 'manual', False, 0))
            self.assertEqual(self.m_a.cost, 14, 'карта — прежнє «На руки»')
            self.assertIn('повернуто до внесеного раніше', self._bodies(first))
            # прежнього числа немає (ставку переписав договір до того, як модуль його запам'ятовував) — «Перевірити» з причиною
            v.write({'workset_temporary': False})
            self.assertEqual((first.wage, first.contract_version_id), (15, v))
            first.write({'wage_before_contract': 0})
            v.write({'active': False})
            self.assertTrue(first.active)
            self.assertEqual((first.wage, first.contract_version_id.id), (15, False), 'число не вгадуємо')
            self.assertTrue(first.to_check)
            self.assertIn('перевірте «Зарплата працівника»', first.check_reason)

    def test_m2b_temporary_row_not_in_nested_form(self):
        # ревю № 2, п. 3: рядок — лише в sheet самої форми версії, не у вкладеній формі поля x2many (інша модель)
        from lxml import etree
        from ..models.hr_version import add_temporary_row
        arch = etree.fromstring('<form><field name="lines"><form><sheet><group><field name="workset_temporary"/></group>'
                                '<notebook><page name="n"/></notebook></sheet></form></field>'
                                '<sheet><group><field name="date_version"/></group><notebook><page name="p"/></notebook></sheet></form>')
        add_temporary_row(arch)
        rows = arch.xpath("//group[@name='workset_rates_temporary']")
        self.assertEqual(len(rows), 1, 'поле з тим самим ім’ям у вкладеній формі не рахується')
        self.assertIs(rows[0].getparent(), arch.xpath('./sheet')[0], 'рядок — у sheet самої форми')
        self.assertEqual(rows[0].getnext().tag, 'notebook')

    def test_m2b_catchup_links_once(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate()
            self._version(wages, DAY3, 14)
            v2 = self._version(wages, LATER, 14)
            self.assertEqual(r.contract_version_id, v2, 'та сама «На руки» — лише позначити новішу версію')
            for _night in range(2):
                res = self.R.with_context(rates_today=DAY).rates_daily()
                self.assertEqual(res['contract']['linked'], 0, 'ніч без змін — «пов’язано 0»')
            self.assertEqual(r.contract_version_id, v2)

    def test_m2b_version_form_row(self):
        # форма самої версії договору (не лише вкладка працівника): рядок «Тимчасово (не база)» — у sheet перед вкладками
        view = self.env['ir.ui.view'].create({'name': 'test.hr.version.form.workset', 'model': 'hr.version', 'type': 'form', 'priority': 1,
                                              'arch': '<form><sheet><group><field name="date_version"/></group>'
                                                      '<notebook><page string="Тест" name="test_page"/></notebook></sheet></form>'})
        arch = self.env['hr.version'].with_user(self.env.ref('base.user_admin')).get_view(view.id, 'form')['arch']
        self.assertIn('name="workset_temporary"', arch)
        self.assertLess(arch.index('workset_temporary'), arch.index('<notebook'), 'рядок — у sheet перед вкладками')
        self.assertEqual(arch.count('name="workset_temporary"'), 1)
        self.assertEqual(arch.count('name="workset_full_rate"'), 1, '«Повна ставка» — ставки договору у формі немає, тож у тому самому рядку')

    def test_c_full_rate_next_to_hourly_wage(self):
        # форма версії з погодинною ставкою (модуль зарплат): «Повна ставка» — одразу після рядка групи зі ставкою (div-рядок із підписом)
        from ..models.hr_version import add_temporary_row
        arch = etree.fromstring('<form><sheet><group><label for="hourly_wage"/><div class="o_row" name="w"><field name="hourly_wage"/><span>h</span></div>'
                                '<field name="date_version"/></group><notebook><page name="p"/></notebook></sheet></form>')
        add_temporary_row(arch)
        full = arch.xpath("//field[@name='workset_full_rate']")
        self.assertEqual(len(full), 1)
        self.assertEqual(full[0].getprevious().get('name'), 'w', 'після div-рядка ставки, у тій самій групі')
        self.assertEqual(full[0].getparent().tag, 'group')
        add_temporary_row(arch)
        self.assertEqual(len(arch.xpath("//field[@name='workset_full_rate']")), 1, 'повторний виклик — без дубля')

    def test_m2b_a6_error_wrapped(self):
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            with patch.object(type(self.R), '_contract_to_base', side_effect=ValidationError('тест: дубль ставки')):
                with self.assertRaisesRegex(UserError, 'договір не збережено') as cm, self.env.cr.savepoint():
                    self._version(wages, LATER, 15)
        self.assertIn('тест: дубль ставки', str(cm.exception), 'причина з перевірки ставки — у тексті, не проковтнута')
        self.assertNotIsInstance(cm.exception, ValidationError)

    def test_m2b_guard_old_and_foreign(self):
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            # давнє «вгору і назад» до першої ставки пари — не шум звірки (рядок без ставки й без імені)
            self._version(wages, '2025-06-01', 16)
            self._version(wages, '2025-07-01', 14)
            self._rate()
            chk = self.R.rates_check()
            self.assertEqual(chk['sections']['updown'], 0, 'повернення раніше першої ставки — тиша')
            # чужа причина «Перевірити» — страж її не переписує й не знімає
            va = self._version(wages, LATER, 16)
            self._version(wages, '2026-05-10', 14)
            up, back = self._pair(LATER), self._pair('2026-05-10')
            back.write({'to_check': True, 'check_reason': 'тест: перевірити після переносу'})
            res = self.R.with_context(rates_today='2026-05-11').rates_daily()
            self.assertEqual(res['updown']['marked'], 1, 'позначено лише ставку без чужої причини')
            self.assertEqual(back.check_reason, 'тест: перевірити після переносу')
            self.assertIn('схоже на тимчасове підвищення', up.check_reason)
            va.write({'workset_temporary': True})
            res = self.R.with_context(rates_today='2026-05-11').rates_daily()
            self.assertEqual(res['updown']['cleared'], 1, 'знято лише свою позначку')
            self.assertTrue(back.to_check, 'чужа позначка лишилась')

    def test_m2b_guard_quiet_with_raise(self):
        # тимчасова зміна пари на дні між підвищенням і поверненням — страж мовчить (виняток _updown_hits)
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate()
            self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 2,
                                                   'date_from': LATER, 'date_to': '2026-05-09', 'payer': 'workset', 'source': 'other',
                                                   'source_note': 'тест'})
            self._version(wages, LATER, 16)
            self._version(wages, '2026-05-10', 14)
            res = self.R.with_context(rates_today='2026-05-11').rates_daily()
            self.assertEqual(res['updown']['marked'], 0, 'є тимчасова зміна — «Перевірити» не ставиться')
            chk = self.R.with_context(rates_today='2026-05-11').rates_check()
        self.assertEqual(chk['sections']['updown'], 0, 'і розділу «Вгору і назад» немає')

    def test_m2b_control_html_by_lang(self):
        # вкладка «Контроль» — мовою того, хто дивиться, а не виконавця звірки, що вів ніч
        for code in ('uk_UA', 'ru_RU'):
            self.env['res.lang']._activate_lang(code)
        r = self._rate()
        self.assertIn('Договір у картках', r.control_html_uk)
        self.assertIn('Договор в карточках', r.control_html_ru)
        self.assertIn('Договор в карточках', r.with_context(lang='ru_RU').control_html)
        self.assertIn('Договір у картках', r.with_context(lang='uk_UA').control_html)
        admin = self.env.ref('base.user_admin')
        admin.lang = 'ru_RU'  # виконавець звірки — російською
        r.write({'control_html_uk': False, 'control_html_ru': False, 'control_sig': False})
        self.R.rates_daily()
        self.assertIn('Договір у картках', r.control_html_uk, 'ніч мовою виконавця — українська вкладка все одно українська')
        self.assertIn('Договор в карточках', r.control_html_ru)
        # змінився лише російський текст (правка словника) — відбиток від обох мов, російська вкладка переписується (ревю № 2, п. 4)
        orig = calc.control_html

        def ru_changed(cards, maps, other, wage, price, lang):
            return orig(cards, maps, other, wage, price, lang) + ('<p>тест: новий словник</p>' if lang == 'ru' else '')
        with patch.object(calc, 'control_html', ru_changed):
            r.action_apply_now()
        self.assertIn('тест: новий словник', r.control_html_ru)
        self.assertNotIn('тест: новий словник', r.control_html_uk)

    def test_unlink_dry_and_no_sync_only_admin(self):
        r = self._rate()
        r.with_context(rates_dry=True).unlink()
        self.assertTrue(r.exists(), 'сухий режим нічого не видаляє')
        self.assertAlmostEqual(self.m_a.cost, 14)
        editor = new_test_user(self.env, login='ws_rates_editor2', groups='base.group_user,workset_rates.group_editor')
        as_editor = r.with_user(editor).with_context(allowed_company_ids=[editor.company_id.id], workset_rates_no_sync=True)
        as_editor.write({'wage': 15})
        self.assertAlmostEqual(self.m_a.cost, 15, msg='не адміністратору «без автоматики» не діє — А1 спрацювала')

    # ——— М2b, ревю № 3: повернення й «Перевірити» — окремі лічильники ночі; інша діюча версія на ту саму дату ———
    def test_m2b_daily_counts_restored_and_check(self):
        # позначку «Тимчасово» поставили повз ORM (імпорт, SQL) — догон уночі: «На руки» назад рахується як restored / check, не archived
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)

        def mark_temporary_by_sql(version):
            self.env.flush_all()
            self.env.cr.execute('UPDATE hr_version SET workset_temporary = TRUE WHERE id = %s', [version.id])
            version.invalidate_recordset(['workset_temporary'])
        with a, b, h, c:
            self._base_version(wages)
            first = self._rate(date_from='2026-02-01')
            v = self._version(wages, '2026-02-01', 15)
            self.assertEqual((first.wage, first.wage_before_contract), (15, 14))
            mark_temporary_by_sql(v)
            res = self.R.rates_daily()
            self.assertEqual((res['contract']['restored'], res['contract']['check'], res['contract']['archived']), (1, 0, 0))
            self.assertIn('повернуто «Зарплата працівника» 1, «Перевірити» 0', res['message'])
            self.assertEqual((first.wage, first.active), (14, True))
            # прежнього числа немає — «Перевірити», і це теж не архів
            v.write({'workset_temporary': False})
            self.assertEqual(first.wage, 15)
            first.write({'wage_before_contract': 0})
            mark_temporary_by_sql(v)
            res = self.R.rates_daily()
            self.assertEqual((res['contract']['restored'], res['contract']['check'], res['contract']['archived']), (0, 1, 0))
            self.assertIn('«Перевірити» 1', res['message'])
            self.assertTrue(first.to_check and first.active)

    def test_m2b_follow_falls_back_to_other_version(self):
        # ставку А6 створив за v1 (картка А); картка договору пари стала Б, її версія v2 на ту саму дату переписала «На руки» (update);
        # v2 архівували — на цю дату діє v1: ставка не в архів, а назад до v1
        self._isolate()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            self._rate(date_from='2026-02-01')
            v1 = self._version(wages, '2026-02-10', 15)
            mine = self._pair('2026-02-10')
            self.assertEqual((mine.wage, mine.source, mine.contract_version_id), (15, 'contract', v1))
            emp_b = self.emp_b
            with patch.object(type(self.R), '_contract_card', lambda _self, person, cust: emp_b):
                v2 = self._version(wages, '2026-02-10', 16, emp_b)
                self.assertEqual((mine.wage, mine.contract_version_id), (16, v2), 'версія картки Б переписала ту саму ставку')
                v2.write({'active': False})
            self.assertTrue(mine.active, 'на ту саму дату діє інша версія — ставка не в архіві')
            self.assertEqual((mine.wage, mine.contract_version_id, mine.wage_source), (15, v1, 'contract'))
            self.assertIn('діє інша версія договору', self._bodies(mine))
            self.assertEqual(self.m_a.cost, 15, 'карта проєктів — «На руки» версії v1')

    # ——— М2c: екрани (розділ 6), житло лінії (3.13), полоса ———
    def _views(self):
        x = lambda n: self.env.ref('workset_rates.' + n).id  # noqa: E731
        return {
            'workset.rate': [(x('view_rate_list'), 'list'), (x('view_rate_form'), 'form'), (x('view_rate_kanban'), 'kanban'),
                             (x('view_rate_search'), 'search'), (x('view_rate_pivot'), 'pivot'), (x('view_rate_graph'), 'graph')],
            'workset.rate.rule': [(x('view_rule_list'), 'list'), (x('view_rule_form'), 'form'), (x('view_rule_search'), 'search')],
            'workset.rate.rule.apply': [(x('view_rule_apply_form'), 'form')],
            'workset.rate.raise': [(x('view_raise_list'), 'list'), (x('view_raise_form'), 'form'), (x('view_raise_search'), 'search')],
            'workset.line': [(x('view_line_list'), 'list'), (x('view_line_form'), 'form'), (x('view_line_search'), 'search')],
            'workset.line.housing': [(x('view_housing_list'), 'list'), (x('view_housing_form'), 'form'), (x('view_housing_search'), 'search')],
        }

    def _web_read_form(self, user, rec, view_xmlid):
        """Як клієнт відкриває форму: поля виду цього користувача (groups уже прибрані) → web_read; m2o — з display_name (читання моделі)."""
        res = rec.env[rec._name].with_user(user).get_views([(self.env.ref('workset_rates.' + view_xmlid).id, 'form')])
        m = res['models'][rec._name]
        names = [f for f in m.get('fields', m) if f in rec._fields]
        spec = {f: ({'fields': {'display_name': {}}} if rec._fields[f].type == 'many2one' else {}) for f in names}
        return rec.with_user(user).with_context(allowed_company_ids=user.company_ids.ids).web_read(spec)

    def test_m2c_views_and_menus(self):
        # редактор — як справжні редактори: бачить фірми ставок (фірма договору у вкладці «Контроль»), але без прав кадрів
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_m2c_editor', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        viewer = new_test_user(self.env, login='ws_m2c_viewer', groups='base.group_user,workset_rates.group_bonus_viewer')
        hr_user = new_test_user(self.env, login='ws_m2c_hr', groups='base.group_user,hr.group_hr_user')
        admin = self.env.ref('base.user_admin')
        for user in (admin, editor):
            for model, views in self._views().items():
                res = self.env[model].with_user(user).get_views(views)
                self.assertEqual(sorted(res['views']), sorted({t for _v, t in views}), '%s: %s' % (user.login, model))
        bonus = [(self.env.ref('workset_rates.view_bonus_list').id, 'list'), (self.env.ref('workset_rates.view_bonus_search').id, 'search')]
        for user in (admin, viewer):
            self.assertIn('list', self.env['workset.office.bonus'].with_user(user).get_views(bonus)['views'])
        # редактор ставок без прав кадрів: у формі ставки немає поля версії договору (hr.version), і форма читається без помилки доступу
        form = self.env['workset.rate'].with_user(editor).get_views([(self.env.ref('workset_rates.view_rate_form').id, 'form')])['views']['form']['arch']
        self.assertNotIn('name="contract_version_id"', form)
        self.assertIn('name="wage_note"', form)
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
        self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1.5, 'date_from': DAY,
                                               'date_to': NEXT, 'payer': 'line', 'source': 'other', 'source_note': 'тест'})
        r.sudo().write({'contract_version_id': self.emp_a.version_ids[:1].id})
        r.invalidate_recordset(['raise_ids'])
        self.assertTrue(r.raise_ids)
        row = self._web_read_form(editor, r, 'view_rate_form')[0]
        self.assertEqual(row['id'], r.id)
        self.assertIn('Тест фірма А · діє', row['employee_summary'], 'картки у фірмах — і без прав кадрів (без поля hr.employee)')
        self.assertNotIn('employee_ids', row)
        self.assertTrue(self._web_read_form(editor, self.line_rule, 'view_rule_form'))
        self.assertTrue(self._web_read_form(editor, r.raise_ids[:1], 'view_raise_form'))
        # меню: «Бонуси офісу» — лише «перегляд» і вище; решта нових — усім, хто бачить «Ставки»
        Menu = self.env['ir.ui.menu']
        ref = lambda n: self.env.ref('workset_rates.' + n).id  # noqa: E731
        for user, sees, hidden in ((editor, ['menu_rules', 'menu_raises', 'menu_settings', 'menu_lines', 'menu_housing'], ['menu_bonuses']),
                                   (hr_user, ['menu_rules', 'menu_raises', 'menu_housing'], ['menu_bonuses']),
                                   (viewer, ['menu_bonuses', 'menu_rules'], []), (admin, ['menu_bonuses', 'menu_housing'], [])):
            visible = Menu.with_user(user)._visible_menu_ids()
            for n in sees:
                self.assertIn(ref(n), visible, '%s бачить %s' % (user.login, n))
            for n in hidden:
                self.assertNotIn(ref(n), visible, '%s не бачить %s' % (user.login, n))

    def test_m2c_housing_rules(self):
        H = self.env['workset.line.housing']
        lt = self.env['workset.line'].create({'name': 'Тест лінія житло'})
        ct = self._cust('Тест замовник житло', lt)
        other = self._cust('Тест замовник житло 2', lt)
        vals = {'line_id': lt.id, 'source': 'owner', 'source_note': 'тест'}
        base = H.create(dict(vals, kind='hour_deduction', amount=11, amount_unit='hour', date_from=OLD))
        # назва: «лінія · уся лінія · години житла × сума €/год з дати» (суму в тексті тесту не пишемо поруч зі знаком євро — перевірка публічності)
        self.assertTrue(base.name.startswith('Тест лінія житло · уся лінія · години житла × 11 '), base.name)
        self.assertTrue(base.name.endswith('/год з 01.01.2025'), base.name)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            H.create(dict(vals, kind='none', date_from=DAY))  # перетин з правилом без кінця
        base.date_to = calc.add_days(DAY, -1)
        later = H.create(dict(vals, kind='none', date_from=DAY))
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            later.date_from = calc.add_days(DAY, -3)
        mine = H.create(dict(vals, customer_id=ct.id, kind='payment_buffer', amount=4, amount_unit='pct', date_from=OLD))
        self.assertEqual(H._housing_at(lt, ct, DAY), mine, 'правило замовника перекриває правило лінії')
        self.assertEqual(H._housing_at(lt, other, DAY), later)
        self.assertEqual(H._housing_at(lt, other, calc.add_days(DAY, -5)), base, 'на минулу дату — правило, що діяло тоді')
        self.assertFalse(H._housing_at(lt, other, '2020-01-01'))
        mine.active = False
        self.assertEqual(H._housing_at(lt, ct, DAY), later, 'архівне правило не діє')
        # права — як у правил ліній: редактор лише читає, «правила ліній» — створює
        editor = new_test_user(self.env, login='ws_m2c_h_editor', groups='base.group_user,workset_rates.group_editor')
        owner = new_test_user(self.env, login='ws_m2c_h_owner', groups='base.group_user,workset_rates.group_rule_editor')
        hr_user = new_test_user(self.env, login='ws_m2c_h_hr', groups='base.group_user,hr.group_hr_user')

        def as_(user, rec):
            return rec.with_user(user).with_context(allowed_company_ids=[user.company_id.id])
        self.assertEqual(as_(editor, base).read(['kind'])[0]['kind'], 'hour_deduction')
        self.assertTrue(as_(hr_user, base).read(['kind']))
        with self.assertRaises(AccessError):
            as_(editor, H).create(dict(vals, customer_id=other.id, kind='none', date_from=DAY))
        with self.assertRaises(AccessError):
            as_(editor, base).write({'note': 'x'})
        as_(owner, H).create(dict(vals, customer_id=other.id, kind='customer_deducts', date_from=DAY))
        with self.assertRaises(AccessError):
            as_(owner, base).unlink()

    def test_m2c_check_housing_section(self):
        self._isolate()
        self._quiet_bonuses()
        self.line_housing.active = False
        lt = self.env['workset.line'].create({'name': 'Тест тарифна житло'})
        ct = self._cust('Тест замовник тарифна житло', lt)
        self._rule(line_id=lt.id, anchor='tariff', share_we_found=5)
        a, b = self._no_agents()
        with a, b:
            self._rate()
            self._rate(person_id=self._person('Тест П тарифна').id, customer_id=ct.id)
            res = self.R.rates_check()
        self.assertEqual(res['sections']['housing'], 1, 'лінія-надбавка без правила житла — у звірці, тарифна — ні')
        part = self.env['workset.rate.check'].search([('date', '=', DAY)]).body.split('Лінія з надбавкою без правила житла')[1].split('<h3>')[0]
        self.assertIn('Тест лінія', part)
        self.assertIn('Тест замовник', part, 'рядок — пара «лінія × замовник»')
        self.assertNotIn('Тест тарифна житло', part)
        # ревю М2c-1, № 6: правило житла ІНШОГО замовника лінії пару не закриває (раніше закривало всю лінію)
        other = self._cust('Тест замовник житло інший')
        self.env['workset.line.housing'].create({'line_id': self.line.id, 'customer_id': other.id, 'kind': 'none', 'date_from': DAY, 'source': 'owner',
                                                 'source_note': 'тест'})
        res = self.R.with_context(rates_today=NEXT).rates_check()
        self.assertEqual(res['sections']['housing'], 1, 'правило іншого замовника — пара без житла лишається в звірці')
        self.env['workset.line.housing'].create({'line_id': self.line.id, 'kind': 'invoice_buffer', 'date_from': DAY, 'source': 'owner',
                                                 'source_note': 'тест'})
        res = self.R.with_context(rates_today=DAY3).rates_check()
        self.assertEqual(res['sections']['housing'], 0, 'правило житла лінії є — розділ мовчить')
        # «Годин житла на особу» — лише при «Вирахування: години житла × €» (ревю М2c-1, № 8)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.line.housing'].create({'line_id': lt.id, 'kind': 'payment_buffer', 'hours_per_person': 10, 'date_from': DAY,
                                                     'source': 'chat', 'source_note': 'тест'})
        ok = self.env['workset.line.housing'].create({'line_id': lt.id, 'kind': 'hour_deduction', 'hours_per_person': 10, 'date_from': DAY,
                                                      'source': 'odoo', 'source_note': 'тест'})
        self.assertEqual((ok.hours_per_person, ok.source), (10, 'odoo'), 'джерела «Переписка» / «Odoo» — у загальному списку')

    def test_m2c1b_review_access(self):
        """Ревю М2c-1, № 3–5: редактор без прав кадрів підбирає «Працівник» (поле workset_is_worker, а не employee_ids); «Бонуси офісу —
        перегляд» без прав кадрів читає список (ім'я — через sudo); «Нова з дати» правила лінії редактору не видно."""
        # редактор — як справжні (фірми ставок у нього є): контекст тесту несе тестові фірми, а фірма контексту, якої в користувача немає,
        # — «Access to unauthorized or invalid companies» на будь-якому пошуку з правилом фірм (збірка Odoo.sh c326b874c, name_search контактів)
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_m2c1b_editor', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        viewer = new_test_user(self.env, login='ws_m2c1b_viewer', groups='base.group_user,workset_rates.group_bonus_viewer')
        owner = new_test_user(self.env, login='ws_m2c1b_owner', groups='base.group_user,workset_rates.group_rule_editor')
        self.assertFalse(editor.has_group('hr.group_hr_user'))
        # № 3: домен поля «Працівник» форм ставки й тимчасової зміни — від редактора без прав кадрів, без AccessError
        for xmlid, model in (('view_rate_form', 'workset.rate'), ('view_raise_form', 'workset.rate.raise')):
            arch = self.env[model].with_user(editor).get_views([(self.env.ref('workset_rates.' + xmlid).id, 'form')])['views']['form']['arch']
            self.assertIn("('workset_is_worker', '=', True)", arch, xmlid)
            self.assertNotIn('employee_ids', arch, xmlid)
        stranger = self._person('Тест контакт без картки')
        found = [i for i, _n in self.env['res.partner'].with_user(editor).name_search('Тест', [('workset_is_worker', '=', True)], limit=1000)]
        self.assertIn(self.person.id, found, 'працівник з карткою — є')
        self.assertNotIn(stranger.id, found, 'контакт без картки — ні')
        self.assertNotIn(self.person.id, self.env['res.partner'].with_user(editor).search([('workset_is_worker', '=', False), ('id', '=', self.person.id)]).ids)
        # № 4: список «Бонуси офісу» так, як його читає клієнт: поля виду цього користувача → web_search_read
        bonus = self.env['workset.office.bonus'].create({'employee_id': self.emp_a.id, 'kind': 'hour', 'amount': 1.2, 'date_from': OLD, 'source': 'other'})
        view = self.env.ref('workset_rates.view_bonus_list').id

        def list_fields(user):
            m = self.env['workset.office.bonus'].with_user(user).get_views([(view, 'list')])['models']['workset.office.bonus']
            return [f for f in m.get('fields', m) if f in bonus._fields]
        names = list_fields(viewer)
        self.assertIn('employee_label', names)
        self.assertNotIn('employee_id', names, 'поле картки (hr.employee) — лише «правила ліній»')
        self.assertIn('employee_id', list_fields(owner), '«правила ліній» правлять «Хто отримує» в списку')
        spec = {f: ({'fields': {'display_name': {}}} if bonus._fields[f].type in ('many2one', 'many2many') else {}) for f in names}
        rows = self.env['workset.office.bonus'].with_user(viewer).with_context(allowed_company_ids=viewer.company_ids.ids).web_search_read(
            [('id', '=', bonus.id)], spec)['records']
        self.assertEqual(rows[0]['employee_label'], self.emp_a.name, 'ім\'я картки в чужій фірмі — без AccessError')
        # № 5: «Нова з дати» правила лінії — лише «правила ліній» (редактор отримав би UserError)
        self.assertFalse(self.line_rule.with_user(editor).can_edit_line)
        self.assertTrue(self.line_rule.with_user(owner).can_edit_line)
        form = self.env['workset.rate.rule'].with_user(editor).get_views([(self.env.ref('workset_rates.view_rule_form').id, 'form')])['views']['form']['arch']
        self.assertIn("level == 'line' and not can_edit_line", form)
        # у перемикачі фірм обрано не всі фірми користувача — ставка читається без помилки, «Картки у фірмах» — лише обраних
        r = self._rate()
        only_b = r.with_user(editor).with_context(allowed_company_ids=[self.co_b.id])
        self.assertEqual(only_b.read(['employee_summary', 'wage'])[0]['employee_summary'], 'Тест фірма Б · діє')

    def test_m2c_rate_screen_texts(self):
        self._quiet_bonuses()
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '3')  # частки тесту (4 і 5) — не нижче мінімуму
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
            none = self._rate(person_id=self._person('Тест П бракує').id, found_by=False)
        self.assertFalse(r.missing_text)
        self.assertEqual(none.missing_text, 'Хто знайшов не вказано')
        self.assertFalse(r.raise_note or r.raise_badge or r.has_raise)
        self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1.5, 'date_from': DAY,
                                               'date_to': '2026-03-20', 'payer': 'line', 'source': 'other', 'source_note': 'тест'})
        r.invalidate_recordset(['raise_note', 'raise_badge', 'has_raise', 'wage_now', 'raise_ids'])
        self.assertEqual((r.wage_now, r.raise_note, r.raise_badge, r.has_raise), (15.5, 'зараз 15,5 до 20.03', '+1,5 до 20.03', True))
        pair = (r | none).ids
        self.assertEqual(self.R.search([('has_raise', '=', True), ('id', 'in', pair)]), r, 'фільтр «Є тимчасова зміна»')
        self.assertEqual(self.R.search([('has_raise', '=', False), ('id', 'in', pair)]), none)
        # Odoo 19 приносить у метод пошуку «in» / «not in» з OrderedSet — той самий результат (перша збірка Odoo.sh: unhashable)
        self.assertEqual(self.R.search([('has_raise', 'not in', [False]), ('id', 'in', pair)]), r)
        self.assertEqual(self.R.search([('has_raise', 'in', [True, False]), ('id', 'in', pair)]), r | none)
        self.assertIn('після бонусів 5', r.card_line)
        act = r.action_new_raise()
        self.assertEqual((act['res_model'], act['context']['default_person_id'], act['context']['default_customer_id']),
                         ('workset.rate.raise', self.person.id, self.cust.id))
        # смарт-кнопки й «Нова з дати» загальної ставки — ті самі ставки, що рахує майстер
        self.assertIn(r.id, self.line_rule.action_open_inherit()['domain'][0][2])
        self.assertNotIn(r.id, self.line_rule.action_open_own()['domain'][0][2])
        new = self.line_rule.action_new_from_date()
        self.assertEqual((new['context']['default_line_id'], new['context']['default_share_we_found'], new['context']['default_date_from']),
                         (self.line.id, 5, DAY))

    # ——— рівень B (розділ 6б): «Огляд ставок» і «Стрічка» ———
    def test_b_overview_tiles_series_lines(self):
        self._isolate()
        self._quiet_bonuses()
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4.5')
        a, b = self._no_agents()
        with a, b:
            us = self._rate()                                                               # W 5
            ln = self._rate(person_id=self._person('Тест П огляд').id, found_by='line')     # W 4 — нижче 4,5
            old = self._rate(date_from='2026-01-21', wage=12)                               # минула ставка пари us: діяла 1 лютого й 1 березня
            fut = self._rate(person_id=self._person('Тест П огляд майбутня').id, date_from=DAY3)
            nol = self._rate(person_id=self._person('Тест П огляд без лінії').id, customer_id=self.cust2.id)
        self.assertEqual((old.state, fut.state, nol.line_id.id), ('past', 'future', False))
        (us | ln | nol).write({'work': 'work', 'work_group': 'g1_work'})
        dom = [('line_id', '=', self.line.id)]
        res = self.R.rates_overview(dom, 12)
        s = self.R.rates_summary(dom + [('state', '=', 'current')])
        for k in ('working', 'avg_wage', 'avg_price', 'avg_share', 'avg_net', 'below_min', 'raises', 'contract_diff'):
            self.assertEqual(res['tiles'][k], s[k], 'плитка %s = полоса підсумків' % k)
        # прямий підрахунок: працюють us і ln, частки 5 і 4, на руки 14
        self.assertEqual((res['tiles']['working'], res['tiles']['below_min'], res['tiles']['no_line']), (2, 1, 0))
        self.assertAlmostEqual(res['tiles']['avg_share'], 4.5)
        self.assertAlmostEqual(res['tiles']['avg_wage'], 14)
        # ряди: точок стільки, скільки місяців; 1 березня діяла лише минула ставка пари (us і ln — з 2 березня), 1 січня — жодної
        series = res['series']
        self.assertEqual((len(series['days']), len(series['total'])), (12, 12))
        self.assertEqual(series['days'][-1], '2026-03-01')
        self.assertEqual((series['total'][-1]['count'], series['total'][-1]['avg_wage']), (1, 12))
        self.assertEqual(series['total'][-3]['count'], 0)
        self.assertEqual(len(self.R.rates_overview(dom, 6)['series']['total']), 6)
        self.assertEqual(len(self.R.rates_overview(dom, 7)['series']['total']), 12, 'період не 6 / 12 / 24 — 12')
        card = [c for c in res['lines'] if c['id'] == self.line.id][0]
        self.assertEqual((card['working'], card['below_min'], card['agent']), (2, 1, 'no'))
        self.assertEqual(len(card['spark_values']), calc.SPARK_MONTHS)
        self.assertEqual([x['key'] for x in res['alerts']], ['raise_soon', 'future', 'no_line', 'to_check', 'no_rate', 'talk'])
        self.assertEqual([x for x in res['alerts'] if x['key'] == 'future'][0]['ids'], fut.ids, 'майбутня ставка лінії — у тривозі «на 7 днів»')
        self.assertEqual(sum(x['count'] for x in res['buckets']), 2)
        self.assertEqual(res['found'], {'us': 1, 'line': 1, 'none': 0})
        # домен звужує: інша лінія — нулі; «Без лінії» — ставка замовника поза лініями
        other = self.env['workset.line'].create({'name': 'Тест лінія огляд'})
        empty = self.R.rates_overview([('line_id', '=', other.id)], 12)
        self.assertEqual((empty['tiles']['working'], empty['lines']), (0, []))
        res_all = self.R.rates_overview([('id', 'in', (us | ln | nol | fut).ids)], 12, only_working=False)
        self.assertEqual(res_all['tiles']['no_line'], 1)
        self.assertEqual([x for x in res_all['alerts'] if x['key'] == 'no_line'][0]['ids'], nol.ids)
        # «тільки працюють»: увімкнено — середні по тих, хто працює (= полоса); вимкнено — по всіх діючих, «Працюють» — як було
        ln.write({'work': 'z_idle', 'work_group': 'g2_idle'})
        self.env['ir.config_parameter'].sudo().set_param(calc.P_GOOD_SHARE, '5')
        on = self.R.rates_overview(dom, 12)['tiles']
        off = self.R.rates_overview(dom, 12, only_working=False)['tiles']
        self.assertEqual((on['working'], on['avg_share'], on['below_min'], on['share_tone']), (1, 5, 0, 'good'))
        self.assertEqual((off['working'], off['total'], off['avg_share'], off['below_min'], off['share_tone']), (1, 2, 4.5, 1, ''))
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4.6')
        self.assertEqual(self.R.rates_overview(dom, 12, only_working=False)['tiles']['share_tone'], 'low', 'середня нижче мінімуму — червона')
        # «Договір ≠ база» — той самий підрахунок calc.summary, що полоса: увімкнено — серед тих, хто працює, вимкнено — по всіх діючих
        (us | ln).sudo().with_context(workset_rates_internal=True).write({'contract_ok': False})
        on = self.R.rates_overview(dom, 12)['tiles']
        off = self.R.rates_overview(dom, 12, only_working=False)['tiles']
        self.assertEqual((on['contract_diff'], off['contract_diff']), (1, 2))
        self.assertEqual(on['contract_diff'], self.R.rates_summary(dom + [('state', '=', 'current'), ('work_group', '=', 'g1_work')])['contract_diff'])

    def _open_count(self, spec):
        """Скільки рядків покаже список з опису «Огляду» (calc.ov_list) так, як його відкриє клієнт: прихований домен дії + фільтри пошуку
        виду «Ставок» (search_default_*; фільтри однієї групи між роздільниками — «або», групи — «і») + вибір панелі зліва
        (searchpanel_default_*). Опис JS виконує як є (openList), тож розбіжність числа й списку тут і ловиться."""
        act = self.env.ref(spec['action'])
        self.assertEqual(act.res_model, 'workset.rate', spec['action'])
        arch = etree.fromstring(self.R.get_views([(act.search_view_id.id, 'search')])['views']['search']['arch'])
        filters, group = {}, 0
        for el in arch:
            if el.tag == 'separator':
                group += 1
            elif el.tag == 'filter' and el.get('domain'):
                filters[el.get('name')] = (group, el.get('domain'))
        panel = {f.get('name') for sp in arch.iter('searchpanel') for f in sp}
        recs = self.R.search(spec['domain'])
        groups = {}
        for key, value in spec['context'].items():
            if key.startswith('search_default_') and value:
                g, dom = filters[key[len('search_default_'):]]
                groups[g] = groups.get(g, self.R.browse()) | self.R.search(ast.literal_eval(dom))
            elif key.startswith('searchpanel_default_'):
                field = key[len('searchpanel_default_'):]
                self.assertIn(field, panel, 'у панелі зліва є %s' % field)
                recs &= self.R.search([(field, '=', value)])
        for found in groups.values():
            recs &= found
        return len(recs)

    def test_b_overview_clicks_match_lists(self):
        # клік по числу «Огляду» відкриває список за описом сервера (open): число на елементі = рядки списку так, як його відкриє клієнт, — в
        # обох положеннях «Тільки працюють» і з фільтром сторінки. Картка лінії: увімкнено — ті, хто працює, вимкнено — усі діючі лінії (раніше
        # картка показувала «Працюють», а список відкривав усі ставки лінії — цей тест таке ловить)
        self._isolate()
        self._quiet_bonuses()
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '4.5')
        deals = [{'id': 1, 'rate': 2.5, 'state': 'active', 'kind': 'hour', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': '',
                  'agent': 'Тест посередник А'}]
        with patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]):
            us = self._rate()
            ln = self._rate(person_id=self._person('Тест П клік').id, found_by='line')
            idle = self._rate(person_id=self._person('Тест П клік не працює').id)
            nobody = self._rate(person_id=self._person('Тест П клік хто знайшов').id, found_by=False)
            nol = self._rate(person_id=self._person('Тест П клік без лінії').id, customer_id=self.cust2.id)
        self.env['workset.rate.raise'].create({'person_id': self.person.id, 'customer_id': self.cust.id, 'kind': 'hour', 'amount': 1.5, 'date_from': DAY,
                                               'date_to': NEXT, 'payer': 'line', 'source': 'other', 'source_note': 'тест'})
        (us | ln | nobody | nol).write({'work': 'work', 'work_group': 'g1_work'})
        idle.write({'work': 'z_idle', 'work_group': 'g2_idle', 'to_check': True, 'check_reason': 'тест'})
        ln.write({'to_check': True, 'check_reason': 'тест'})
        # «Договір ≠ база»: з модулем зарплат ставка без картки теж «≠» — тут лише одна, явно
        (ln | idle | nobody | nol).sudo().with_context(workset_rates_internal=True).write({'contract_ok': True})
        us.sudo().with_context(workset_rates_internal=True).write({'contract_ok': False})
        for only in (True, False):
            for base in ([], [('line_id', 'in', [self.line.id])], [('customer_id', '=', self.cust.id)]):
                res = self.R.rates_overview(base, 12, only_working=only)
                tiles, opens = res['tiles'], res['open']
                msg = 'тільки працюють: %s, фільтр: %s' % (only, base)
                for key in ('working', 'all', 'below_min', 'raises', 'contract_diff', 'no_line', 'to_check'):
                    self.assertEqual(self._open_count(opens['tiles'][key]), tiles[key], '%s — %s' % (msg, key))
                self.assertEqual(self._open_count(opens['tiles']['avg']), tiles['working'] if only else tiles['total'], msg + ' — середні')
                for b in res['buckets']:
                    self.assertEqual(self._open_count(b['open']), b['count'], '%s — стовпець %s' % (msg, b['label']))
                for kind in ('us', 'line', 'none'):
                    self.assertEqual(self._open_count(opens['found'][kind]), res['found'][kind], '%s — хто знайшов %s' % (msg, kind))
                for card in res['lines']:
                    self.assertEqual(self._open_count(card['open']), card['count'], '%s — картка %s' % (msg, card['name']))
                    self.assertEqual(card['count'], card['working'] if only else card['total'], msg + ' — число картки')
        # дані справді розрізняють обидва положення: «Ставок» — усі діючі; картка лінії — 3 працюють із 4 діючих; тривоги не порожні
        on = self.R.rates_overview([], 12)
        off = self.R.rates_overview([], 12, only_working=False)
        card_on = [c for c in on['lines'] if c['id'] == self.line.id][0]
        card_off = [c for c in off['lines'] if c['id'] == self.line.id][0]
        self.assertEqual((card_on['count'], card_off['count']), (3, 4))
        self.assertEqual((on['tiles']['all'], on['tiles']['working'], off['tiles']['all']), (5, 4, 5))
        self.assertEqual((on['tiles']['to_check'], off['tiles']['to_check'], on['tiles']['raises'], on['tiles']['contract_diff']), (1, 2, 1, 1))
        self.assertEqual((on['found']['none'], on['tiles']['no_line']), (1, 1))
        # картка лінії: ім'я й сума посередника з тих самих даних, що вже віднімаються від частки
        self.assertEqual(card_on['agent'], 'yes')
        self.assertEqual(card_on['agents'], [{'name': 'Тест посередник А', 'min': 2.5, 'max': 2.5}])
        for key in ('found_empty', 'agent_from'):
            self.assertTrue(on['labels'][key], key)

    def test_b_agent_deal_button(self):
        # «Домовленість» біля «Посередник»: моделі посередників немає (як на проді) — форма читається, кнопки не видно, дія — повідомлення;
        # домовленість є (адаптер підмінено) — кнопка видна, дія — картка (одна) або список (кілька)
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_b_deal_editor', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        form = self.R.with_user(editor).get_views([(self.env.ref('workset_rates.view_rate_form').id, 'form')])['views']['form']['arch']
        self.assertIn('name="action_open_agent_deal"', form)
        self.assertIn('name="agent_deal_ok"', form)
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
        if agent_deals.DEAL not in self.env:
            self.assertIsNone(agent_deals.deal_ids(self.env, self.cust.id, self.line.id), 'моделі немає — None, без помилки')
            self.assertFalse(r.agent_deal_ok)
            self.assertEqual(r.action_open_agent_deal()['tag'], 'display_notification')
            self.assertEqual(self._web_read_form(editor, r, 'view_rate_form')[0]['agent_deal_ok'], False, 'форма читається без моделі посередників')
        with patch.object(agent_deals, 'deal_ids', return_value=[5]):
            r.invalidate_recordset(['agent_deal_ok'])
            self.assertTrue(r.agent_deal_ok)
            act = r.action_open_agent_deal()
            self.assertEqual((act['res_model'], act['res_id'], act['views']), (agent_deals.DEAL, 5, [[False, 'form']]))
        with patch.object(agent_deals, 'deal_ids', return_value=[5, 6]) as ids:
            act = r.action_open_agent_deal()
            self.assertEqual((act['domain'], act['view_mode']), ([('id', 'in', [5, 6])], 'list,form'))
            self.assertEqual(ids.call_args[0][1:], (self.cust.id, self.line.id), 'замовник (комерційний) і лінія ставки')
        with patch.object(agent_deals, 'deal_ids', return_value=[]):
            r.invalidate_recordset(['agent_deal_ok'])
            self.assertFalse(r.agent_deal_ok)
            self.assertEqual(r.action_open_agent_deal()['params']['type'], 'warning')

    def test_b_overview_empty(self):
        self._isolate()
        res = self.R.rates_overview([], 24)
        t = res['tiles']
        self.assertEqual(res['months'], 24)
        self.assertEqual((t['working'], t['total'], t['avg_wage'], t['avg_share'], t['avg_net'], t['below_min'], t['contract_diff'], t['no_line'],
                          t['to_check']), (0, 0, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual((res['lines'], res['buckets'], res['found'], res['check']), ([], [], {'us': 0, 'line': 0, 'none': 0}, False))
        self.assertEqual(len(res['series']['total']), 24)
        self.assertTrue(all(p['count'] == 0 and p['avg_share'] == 0 for p in res['series']['total']))
        self.assertEqual([x['count'] for x in res['alerts'] if x['model'] == 'workset.rate'], [0, 0, 0, 0], 'майбутні, без лінії, перевірити, переговорити')

    def test_b_overview_rights(self):
        hr_user = new_test_user(self.env, login='ws_b_hr', groups='base.group_user,hr.group_hr_user')
        plain = new_test_user(self.env, login='ws_b_plain', groups='base.group_user')
        self._rate()
        res = self.R.with_user(hr_user).with_context(allowed_company_ids=[hr_user.company_id.id]).rates_overview([], 6)
        self.assertEqual(res['months'], 6, 'кадри (читають ставки) бачать «Огляд»')
        with self.assertRaises(AccessError):
            self.R.with_user(plain).with_context(allowed_company_ids=[plain.company_id.id]).rates_overview([], 6)

    def test_b_gantt_views_stop_and_move(self):
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_b_editor', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        admin = self.env.ref('base.user_admin')
        views = [(self.env.ref('workset_rates.view_rate_gantt').id, 'gantt'), (self.env.ref('workset_rates.view_rate_search').id, 'search')]
        for user in (admin, editor):
            res = self.R.with_user(user).get_views(views)
            self.assertIn('gantt', res['views'], user.login)
            self.assertIn('gantt_stop', res['views']['gantt']['arch'])
        # кольори смуг залежать від рівня частки: мінімум і бонуси — свої, як у сусідніх тестах (частка 5 — не нижче мінімуму)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MIN_SHARE, '3')
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            past = self._rate(date_from='2026-01-21', wage=12)
            cur = self._rate()
            fut = self._rate(date_from=LATER, wage=15)
            alone = self._rate(person_id=self._person('Тест П стрічка').id)
        self.assertEqual([r.state for r in (past, cur, fut, alone)], ['past', 'current', 'future', 'current'])
        self.assertEqual(calc.iso(past.gantt_stop), '2026-03-01', 'минула — «Ставка до»')
        self.assertEqual(calc.iso(cur.gantt_stop), '2026-03-31', 'чинна з наступною — «Ставка до»')
        self.assertEqual(calc.iso(fut.gantt_stop), calc.add_days(LATER, 30), 'майбутня без «до» — «Ставка з» + 30')
        self.assertEqual(calc.iso(alone.gantt_stop), calc.add_days(DAY, 30), 'чинна без «до» — сьогодні + 30')
        ids = (past | cur | fut | alone).ids
        self.assertEqual(self.R.search([('id', 'in', ids), ('gantt_stop', '>', '2026-03-31')]), fut | alone, 'пошук «Смуга до» — тими самими правилами')
        self.assertEqual(self.R.search([('id', 'in', ids), ('gantt_stop', '<', DAY)]), past)
        self.assertEqual((past.gantt_color, cur.gantt_color, fut.gantt_color), (0, 10, 4))
        label = cur.with_context(workset_rates_gantt=1).display_name
        self.assertTrue(label.startswith('Діє · ') and cur.formula in label, label)
        self.assertEqual(cur.display_name, cur.name, 'поза «Стрічкою» — звичайна назва')

        def as_(rec):
            return rec.with_user(editor).with_context(allowed_company_ids=editor.company_ids.ids)

        # «сьогодні» редактора — справжнє (підміна rates_today — лише адміністратору), тож дати переносу — далеко в майбутньому.
        # Редактор пересуває майбутню в «Стрічці»: «Ставка з» змінилась, А1 перерахував пару («Ставка до» попередньої — день перед новою)
        as_(fut).write({'date_from': '2099-04-10', 'gantt_stop': '2099-05-10'})
        self.assertEqual(calc.iso(fut.date_from), '2099-04-10')
        self.assertEqual(calc.iso(cur.date_to), '2099-04-09')
        # gantt присилає й незмінні поля рядка (та сама лінія й працівник) — це не перенос у інший рядок ([id, назва] — node-тест gantt_moved)
        as_(fut).write({'date_from': '2099-04-11', 'gantt_stop': '2099-05-11', 'line_id': fut.line_id.id, 'person_id': fut.person_id.id})
        self.assertEqual(calc.iso(fut.date_from), '2099-04-11')
        for rec, day in ((cur, '2026-03-05'), (past, '2026-01-25')):
            with self.assertRaisesRegex(UserError, 'не пересуваються'), self.env.cr.savepoint():
                as_(rec).write({'date_from': day, 'gantt_stop': '2099-01-01'})
        with self.assertRaisesRegex(UserError, 'після сьогодні'), self.env.cr.savepoint():
            as_(fut).write({'date_from': '2026-01-10', 'gantt_stop': '2026-02-10'})
        with self.assertRaisesRegex(UserError, 'лише пересунути'), self.env.cr.savepoint():
            as_(fut).write({'date_from': '2099-04-12', 'gantt_stop': '2099-05-12', 'person_id': alone.person_id.id})
        # у контексті «Стрічки» без «Смуга до»: растяжка за лівий край (лише «Ставка з») у чинної й перенос униз (лише працівник) — ні;
        # інше поле (наприклад, «На руки» з картки, відкритої зі «Стрічки») — так
        gantt = lambda rec: as_(rec).with_context(workset_rates_gantt=1)  # noqa: E731
        with self.assertRaisesRegex(UserError, 'не пересуваються'), self.env.cr.savepoint():
            gantt(cur).write({'date_from': '2026-03-01'})
        with self.assertRaisesRegex(UserError, 'лише пересунути'), self.env.cr.savepoint():
            gantt(fut).write({'person_id': alone.person_id.id})
        gantt(fut).write({'note': 'з картки «Стрічки»'})
        self.assertEqual(fut.note, 'з картки «Стрічки»')
        # форма ставки поза «Стрічкою» (без «Смуга до» й контексту) — як до рівня B: редактор виправляє «Ставка з» діючої
        # («завела з сьогодні, треба з 1-го»)
        as_(cur).write({'date_from': '2026-03-01'})
        self.assertEqual(calc.iso(cur.date_from), '2026-03-01')
        self.assertEqual(calc.iso(past.date_to), '2026-02-28', 'А1: «Ставка до» минулої — день перед виправленою датою')
        # системний адміністратор у «Стрічці» переносить і чинну
        cur.write({'date_from': NEXT, 'gantt_stop': '2026-05-01'})
        self.assertEqual(calc.iso(cur.date_from), NEXT)
        self.assertEqual(calc.iso(past.date_to), DAY)
        self.assertEqual(self.R.search([('id', 'in', ids), ('gantt_stop', '!=', False)]), past | cur | fut | alone, '«встановлено» — без помилки')
        self.assertFalse(self.R.search([('id', 'in', ids), ('gantt_stop', '=', False)]))

    def test_m2c1b_internal_context_only_superuser(self):
        """Контекст workset_rates_internal (службові поля ланцюжка без автоматики) — лише суперкористувачу: редактор через RPC з ним
        не обходить ланцюжок і А1 — усе як без контексту (ревю рівня B)."""
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_m2c1b_internal', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
            self.assertEqual((r.price, self.m_a.cost), (19, 14))
            r.with_user(editor).with_context(workset_rates_internal=True, allowed_company_ids=firms).write({'wage': 16})
            self.env.invalidate_all()
            self.assertEqual((r.wage, r.price, r.our_share), (16, 21, 5), 'ланцюжок перераховано: 16 + 5, як без контексту')
            self.assertEqual((self.m_a.cost, self.m_b.cost), (16, 16), 'А1: «Вартість години» в карті обох фірм — як без контексту')
            r.with_user(editor).with_context(workset_rates_internal=True, allowed_company_ids=firms).write({'price': 24, 'own_reason': 'тест'})
            self.env.invalidate_all()
            self.assertTrue(r.is_own, 'ціна ≠ правилу від редактора — «Своя», як без контексту')
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                r.with_user(editor).with_context(workset_rates_internal=True, allowed_company_ids=firms).write({'own_reason': False})
            # суперкористувач (код модуля) — пише службове поле без ланцюжка
            r.sudo().with_context(workset_rates_internal=True).write({'check_reason': 'службове'})
            self.assertEqual(r.check_reason, 'службове')

    def test_m2c2_link_rules_outside_migration(self):
        """rates_link_rules поза міграцією (посів штабу --seed-m2): сухий режим нічого не пише; ціна 0 (перенесено з рядка карти без ціни) —
        ціна з правила службовим записом, не «Своя»; ціна ≠ правилу — «Своя» з причиною, ціна та сама; «На руки з» у ставок, внесених
        руками, не чіпається ні в 'transfer'; карта проєктів (cost, прапорець) — без змін: А1 не запускається."""
        self._quiet_bonuses()
        self.line_rule.active = False
        a, b = self._no_agents()
        with a, b:
            zero = self._rate(price=0)
            own = self._rate(person_id=self._person('Тест П привязка').id, price=23)
            moved = self._rate(person_id=self._person('Тест П перенос карти').id, price=19, source='migration')
        self.assertFalse(zero.rule_id or own.rule_id or moved.rule_id, 'правило лінії в архіві — ставки без правила')
        self.assertEqual((zero.price, own.price, zero.wage_source), (0, 23, 'manual'))
        self.m_a.sudo().write({'cost': 11})
        before = [(m.cost, m.is_cost_changed) for m in (self.m_a, self.m_b)]
        self.line_rule.active = True
        with a, b:
            dry = self.R.with_context(rates_dry=True).rates_link_rules(migration='transfer')
            self.assertTrue(dry['dry'] and dry['linked'] >= 3)
            self.env.invalidate_all()
            self.assertEqual((zero.rule_id.id, zero.price, own.is_own, moved.wage_source), (False, 0, False, 'manual'), 'сухий режим нічого не пише')
            res = self.R.rates_link_rules(migration='transfer')
        self.env.invalidate_all()
        self.assertFalse(res['dry'])
        self.assertEqual((zero.rule_id, zero.price, zero.is_own), (self.line_rule, 19, False), 'ціна 0 — з правила (14 + 5), не «Своя»')
        self.assertEqual((own.rule_id, own.price, own.is_own), (self.line_rule, 23, True), 'ціна ≠ правилу — «Своя», ціна та сама')
        self.assertIn('за правилом 19', own.own_reason)
        self.assertEqual((zero.wage_source, own.wage_source, moved.wage_source), ('manual', 'manual', 'migration'),
                         "'transfer': «Перенос» — лише ставкам з джерелом «Перенесено з проєкту»")
        self.assertEqual([(m.cost, m.is_cost_changed) for m in (self.m_a, self.m_b)], before, 'карта проєктів не змінилась (А1 не запускався)')
        with a, b:
            again = self.R.rates_link_rules(migration='transfer')
        self.assertFalse({zero.id, own.id, moved.id} & set(self.R.search([('rule_id', '=', False)]).ids), 'повтор: усі прив\'язані')
        self.assertEqual(again['linked'], 0, 'повтор нічого не прив\'язує')

    def test_m2c2_migration_from_m1(self):
        """Перенос 19.0.1 → 19.0.2 (migrations/19.0.2.0.0/post-migrate.py) на вигаданих даних: частки лінії М1 з тимчасової таблиці → правило
        лінії «Надбавка» з найранішої «Ставка з»; ставка з ціною за правилом — не «Своя», «На руки з» — «Перенос»; ручна частка агента М1 —
        «Своя» з причиною; тимчасова таблиця прибрана; параметра А6 немає (off)."""
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', '19.0.2.0.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('workset_rates_post_migrate_test', path)
        post = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(post)
        self._quiet_bonuses()
        self.env['ir.config_parameter'].sudo().set_param(calc.P_A6_MODE, '')
        lt = self.env['workset.line'].create({'name': 'Тест лінія перенос'})
        ct = self._cust('Тест замовник перенос', lt)
        a, b = self._no_agents()
        with a, b:
            same = self._rate(customer_id=ct.id, price=19)
            agent = self._rate(person_id=self._person('Тест П перенос').id, customer_id=ct.id, price=21, date_from=NEXT)
            agent.with_context(workset_rates_internal=True).write({'agent_share': 2, 'agent_source': 'manual'})
            self.assertFalse(lt.rule_id, 'до переносу правила лінії немає')
            self.env.cr.execute('CREATE TABLE workset_rates_m1_line_shares AS SELECT %s::int AS line_id, 4.0::float AS share_line_found, '
                                '5.0::float AS share_we_found, 0.0::float AS min_share', (lt.id,))
            post.migrate(self.env.cr, '19.0.1.0.0')
        self.env.invalidate_all()
        rule = self.env['workset.rate.rule'].search([('line_id', '=', lt.id), ('customer_id', '=', False)])
        self.assertEqual(len(rule), 1)
        self.assertEqual((rule.anchor, rule.share_line_found, rule.share_we_found, calc.iso(rule.date_from), rule.source, rule.source_note),
                         ('markup', 4, 5, DAY, 'migration', calc.MIGRATION_NOTE), 'надбавка з часток М1, «Ставка з» — найраніша ставка лінії')
        self.assertEqual((same.rule_id, same.is_own, same.wage_source, same.price), (rule, False, 'migration', 19), 'Ц = Р 14 + 5 — за правилом')
        self.assertEqual((agent.is_own, agent.own_reason, agent.price), (True, 'було: частка агента 2 (до посередників)', 21),
                         'ручна частка агента М1 — «Своя» з причиною, ціна як була')
        self.env.cr.execute("SELECT to_regclass('workset_rates_m1_line_shares')")
        self.assertIsNone(self.env.cr.fetchone()[0], 'тимчасову таблицю прибрано')
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(calc.P_A6_MODE), 'a6_mode не створюється — А6 вимкнено')
        post.migrate(self.env.cr, '19.0.1.0.0')
        self.assertEqual(self.env['workset.rate.rule'].search_count([('line_id', '=', lt.id)]), 1, 'повторний запуск правил не множить')

    # ——— круг 06.11 (c): фонд працівників, оренда авто, правка зі списку, «Переговорити», «працюють без ставки», зарплата за договором ———
    def _fund_deals(self, rate_full=1.5):
        # вигадані суми: фонд 2,5 (неповна ставка) / 1,5 (повна), посередник 0,75 — домовленості того самого замовника
        deal = {'state': 'active', 'customer_ids': [self.cust.id], 'line_id': 0, 'date_from': '', 'date_to': ''}
        return [dict(deal, **{'id': 21, 'kind': 'worker_fund', 'rate': 2.5, 'rate_full': rate_full, 'agent': 'Тест фонд'}),
                dict(deal, **{'id': 22, 'kind': 'hour', 'rate': 0.75, 'rate_full': None, 'agent': 'Тест посередник'})]

    def test_c_worker_fund_and_car_rent(self):
        self._quiet_bonuses()
        self.line_rule.car_rent = 0.5
        v = self.emp_a.version_ids.sorted('date_version')[:1]
        v.write({'date_version': OLD})
        self.emp_a._compute_current_version_id()
        with patch.object(agent_deals, 'read_deals', return_value=self._fund_deals()), patch.object(agent_deals, 'read_people', return_value=[]):
            r = self._rate()
            self.assertEqual((r.price, r.agent_client_share, r.worker_fund_share, r.car_rent_share), (19, 0.75, 2.5, 0.5),
                             'фонд — окремо від посередника; оренда авто — з загальної ставки')
            self.assertEqual(r.our_share, 1.25, 'частка = 19 − 14 − посередник 0,75 − фонд 2,5 − оренда 0,5')
            self.assertIn('14 + 1,25 + 0,75 + 2,5 + 0,5 = 19', r.formula)
            v.write({'workset_full_rate': True})
            self.assertEqual((r.worker_fund_share, r.our_share), (1.5, 2.25), 'повна ставка в договорі — сума фонду «на повну ставку», одразу')
            plan = self.R._plan_at([(self.person.id, self.cust.id)], DAY)[(self.person.id, self.cust.id)]
            self.assertEqual((plan['worker_fund_share'], plan['car_rent_share'], plan['our_share']), (1.5, 0.5, 2.25),
                             '«Прибутковість»: фонд і оренда поруч із часткою (частка — уже без них)')
            # нова версія з дати (неповна ставка) і перенос її дати — фонд одразу, не чекаючи ночі
            v2 = self.emp_a.create_version({'date_version': calc.add_days(DAY, -5), 'workset_full_rate': False})
            self.assertEqual((r.worker_fund_share, r.our_share), (2.5, 1.25), 'на сьогодні діє нова версія без «Повної ставки» — фонд неповної')
            v2.write({'date_version': LATER})
            self.assertEqual(r.worker_fund_share, 1.5, 'версію перенесли на потім — на сьогодні знову повна ставка')
        with patch.object(agent_deals, 'read_deals', return_value=self._fund_deals(rate_full=None)), patch.object(agent_deals, 'read_people', return_value=[]):
            r._chain(full=False)
            self.assertEqual(r.worker_fund_share, 2.5, 'поля «на повну ставку» в домовленості немає — завжди основна сума')
        # оренду авто в тарифі замовника змінює лише власник (захищене поле, як частки); правило лінії редактор не змінює й так — тому тариф
        editor = new_test_user(self.env, login='ws_c_fund_editor', groups='base.group_user,workset_rates.group_editor')
        ct = self._cust('Тест замовник оренда')
        tariff = self._rule(customer_id=ct.id, anchor='tariff', price=23, date_from=DAY)
        as_editor = tariff.with_user(editor).with_context(allowed_company_ids=[editor.company_id.id])
        with self.assertRaisesRegex(UserError, 'Оренда авто'), self.env.cr.savepoint():
            as_editor.write({'car_rent': 1.5})
        as_editor.write({'price': 24})
        self.assertEqual((tariff.price, tariff.car_rent), (24, 0), 'ціну тарифу редактор править, оренду — ні')

    def test_c_fixed_month_without_fund_and_car(self):
        # «Фікс за місяць»: фонд і оренда авто з частки не віднімаються (calc.shares) — і в ставці 0, щоб стовпець, «Переговорити» й
        # «Прибутковість» не показували суму, якої в частці немає
        self._quiet_bonuses()
        self.env['ir.config_parameter'].sudo().set_param(calc.P_MONTH_HOURS, '')
        lf = self.env['workset.line'].create({'name': 'Тест лінія фікс'})
        cf = self._cust('Тест замовник фікс', lf)
        self._rule(line_id=lf.id, price_mode='fixed_month', fixed_month=336, car_rent=0.5)
        deals = [dict(d, customer_ids=[cf.id]) for d in self._fund_deals()]
        with patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]), \
                patch.object(type(self.R), '_pair_hour_rows', return_value=[]):
            r = self._rate(customer_id=cf.id)
            self.assertEqual((r.price_mode, r.price, r.agent_client_share), ('fixed_month', 14, 0.75))
            self.assertEqual((r.worker_fund_share, r.car_rent_share, r.our_share), (0, 0, 2), 'фікс 336 ÷ 168 год; фонд 2,5 й оренда 0,5 — не в ставці')
            plan = self.R._plan_at([(self.person.id, cf.id)], DAY)[(self.person.id, cf.id)]
            self.assertEqual((plan['worker_fund_share'], plan['car_rent_share'], plan['our_share']), (0, 0, 2))
            self.assertEqual(calc.talk_text(r._talk_numbers(), 'uk'), 'зарплата 14, платять 14, посередник 0,75, частка 2', 'без фонду й авто')

    def test_c_edit_from_list(self):
        self._quiet_bonuses()
        a, b = self._no_agents()
        Edit = self.env['workset.rate.edit']
        with a, b:
            r = self._rate()
            act = r.action_edit_wage()
            # дата за замовчуванням — сьогодні, але ставка почалась сьогодні — завтра (нова ставка з того самого дня — відмова)
            self.assertEqual((act['res_model'], act['context']['default_value'], act['context']['default_date']), ('workset.rate.edit', 14, NEXT))
            # «Нова ставка з дати»: нова ставка пари з дати, попередня закінчується напередодні, частка — одразу
            Edit.create({'rate_id': r.id, 'what': 'wage', 'value': 15, 'date': NEXT, 'reason': 'тест: розмова', 'source': 'call'}).action_new_from_date()
            new = self.R.search([('person_id', '=', self.person.id), ('customer_id', '=', self.cust.id), ('date_from', '=', NEXT)])
            self.assertEqual((len(new), new.wage, new.price, new.our_share, new.state, new.source), (1, 15, 20, 5, 'future', 'call'))
            self.assertEqual((r.wage, calc.iso(r.date_to)), (14, DAY), 'попередня не переписана, закінчується напередодні')
            with self.assertRaisesRegex(UserError, 'уже є'), self.env.cr.savepoint():
                Edit.create({'rate_id': r.id, 'what': 'wage', 'value': 16, 'date': DAY}).action_new_from_date()
            # дата раніше «Ставка з»: нова ставка встала б перед цією й укоротила б попередню — минулі місяці змінились би
            with self.assertRaisesRegex(UserError, 'пізніше'), self.env.cr.savepoint():
                Edit.create({'rate_id': new.id, 'what': 'wage', 'value': 16, 'date': DAY}).action_new_from_date()
            self.assertEqual((calc.iso(r.date_to), self.R.search_count([('person_id', '=', self.person.id), ('customer_id', '=', self.cust.id)])), (DAY, 2))
            # «Виправити помилку»: без причини — ні; з причиною — число в цій самій ставці й причина в історії
            fix = Edit.create({'rate_id': r.id, 'what': 'price', 'value': 21, 'date': DAY})
            with self.assertRaises(UserError), self.env.cr.savepoint():
                fix.action_fix()
            self.assertEqual(r.price, 19)
            fix.reason = 'тест: помилка введення'
            fix.action_fix()
            self.assertEqual((r.price, r.is_own, r.own_reason, r.our_share), (21, True, 'тест: помилка введення', 7))
            self.assertTrue(any('Виправлено помилку' in str(m.body) and 'тест: помилка введення' in str(m.body) for m in r.message_ids))
            # посередник лише цій людині — своє значення з причиною замість домовленостей; «За домовленістю» — назад
            Edit.create({'rate_id': r.id, 'what': 'agent', 'value': 0.5, 'date': DAY, 'agent_scope': 'person', 'reason': 'тест: домовились'}).action_fix()
            self.assertEqual((r.agent_own, r.agent_client_share, r.our_share), (True, 0.5, 6.5))
            self.assertIn('тест: домовились', r.agent_client_note)
            r.action_agent_by_deal()
            self.assertEqual((r.agent_own, r.agent_client_share, r.our_share), (False, 0, 7))
            # минула ставка — історія пари: олівці сховані, а сервер відмовляє й при виклику повз список
            old = self._rate(date_from=calc.add_days(DAY, -10), wage=12)
            self.assertEqual(old.state, 'past')
            with self.assertRaisesRegex(UserError, 'минула'), self.env.cr.savepoint():
                old.action_edit_wage()
            for method in ('action_new_from_date', 'action_fix'):
                with self.assertRaisesRegex(UserError, 'минула'), self.env.cr.savepoint():
                    getattr(Edit.create({'rate_id': old.id, 'what': 'wage', 'value': 13, 'date': DAY3, 'reason': 'тест'}), method)()
            self.assertEqual((old.wage, self.R.search_count([('person_id', '=', self.person.id), ('customer_id', '=', self.cust.id)])), (12, 3))
        # те саме — від редактора без прав кадрів (як справжні редактори: фірми ставок у нього є): відкрити майстер, прочитати його форму
        # так, як клієнт, нова ставка з дати, виправлення, «Переговорити» — без помилки доступу (прошла збірка Odoo.sh падала саме на
        # полі hr.employee у формі для такого користувача). «Сьогодні» в редактора — справжнє (rates_today — лише адміністратору), тож
        # тут перевіряються числа й межі ставок, а не статуси
        # усі фірми контексту тесту: self.env.company тут — «Тест фірма А» (перша в allowed_company_ids), а головна фірма бази теж у
        # контексті — без неї в користувача «Access to unauthorized or invalid companies» (збірка Odoo.sh 641665177)
        firms = list(self.env.context['allowed_company_ids'])
        editor = new_test_user(self.env, login='ws_c_edit_editor', groups='base.group_user,workset_rates.group_editor',
                               company_id=self.env.company.id, company_ids=[(6, 0, firms)])
        self.assertFalse(editor.has_group('hr.group_hr_user'))
        a, b = self._no_agents()
        with a, b:
            act = new.with_user(editor).action_edit_wage()
            self.assertEqual(act['context']['default_rate_id'], new.id)
            w = Edit.with_user(editor).create({'rate_id': new.id, 'what': 'wage', 'value': 16, 'date': LATER, 'reason': 'тест: редактор', 'source': 'call'})
            self.assertEqual(self._web_read_form(editor, w, 'view_rate_edit_form')[0]['old_value'], 15)
            w.action_new_from_date()
            later = self.R.search([('person_id', '=', self.person.id), ('customer_id', '=', self.cust.id), ('date_from', '=', LATER)])
            self.assertEqual((len(later), later.wage, later.price, later.source), (1, 16, 21, 'call'))
            self.assertEqual(calc.iso(new.date_to), calc.add_days(LATER, -1), 'попередня закінчується напередодні')
            Edit.with_user(editor).create({'rate_id': later.id, 'what': 'price', 'value': 22, 'date': LATER, 'reason': 'тест: редактор виправив'}).action_fix()
            self.assertEqual((later.price, later.is_own, later.our_share), (22, True, 6))
            talk = self.env['workset.rate.talk'].with_user(editor).create({'rate_id': later.id})
            self.assertEqual(talk.user_id, editor, '«Кому» за замовчуванням — сам')
            self.assertTrue(self._web_read_form(editor, talk, 'view_rate_talk_form'))
            talk.action_create()
        # права: кадри читають ставки, але майстер правки не відкривають
        hr_user = new_test_user(self.env, login='ws_c_edit_hr', groups='base.group_user,hr.group_hr_user')
        with self.assertRaises(AccessError):
            Edit.with_user(hr_user).with_context(allowed_company_ids=[hr_user.company_id.id]).create({'rate_id': r.id, 'what': 'wage', 'value': 15, 'date': DAY})
        with self.assertRaises(AccessError):
            r.with_user(hr_user).with_context(allowed_company_ids=[hr_user.company_id.id]).action_talk()

    def test_c_talk_task_and_overview_alert(self):
        self._isolate()
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            r = self._rate()
        admin = self.env.ref('base.user_admin')
        # назва задачі рахується на сервері й без форми (precompute): обов'язкове поле не йде в базу порожнім (збірка Odoo.sh c326b874c)
        talk = self.env['workset.rate.talk'].create({'rate_id': r.id, 'user_id': admin.id})
        self.assertEqual(talk.summary, 'Переговорити: зарплата 14, платять 19, частка 5')
        talk.action_create()
        acts = self.env['mail.activity'].search([('res_model', '=', 'workset.rate'), ('res_id', '=', r.id)])
        self.assertEqual((len(acts), acts.user_id, acts.summary, acts.activity_type_id), (1, admin, talk.summary, self.env.ref('workset_rates.mail_activity_talk')),
                         'задача типу «Переговорити» на ставці для обраного користувача')
        # задача іншого типу на ставці (з чаттера) — не «Переговорити»
        r.activity_schedule('mail.mail_activity_data_todo', summary='Тест інша задача', user_id=admin.id)
        alert = [x for x in self.R.rates_overview([], 6)['alerts'] if x['key'] == 'talk'][0]
        self.assertEqual((alert['count'], alert['ids'], alert['rows'][0]['id']), (1, r.ids, r.id), '«Тривоги» «Огляду» — рядок «Переговорити»')
        # «Кому» — лише ті, хто читає ставки: у виборі немає, а переданий повз вибір — пояснення, а не помилка доступу Odoo
        plain = new_test_user(self.env, login='ws_c_talk_plain', groups='base.group_user')
        editor = new_test_user(self.env, login='ws_c_talk_editor', groups='base.group_user,workset_rates.group_editor')
        bad = self.env['workset.rate.talk'].create({'rate_id': r.id, 'user_id': plain.id})
        self.assertIn(editor, bad.reader_ids)
        self.assertIn(admin, bad.reader_ids)
        self.assertNotIn(plain, bad.reader_ids)
        with self.assertRaisesRegex(UserError, 'не бачить ставок'), self.env.cr.savepoint():
            bad.action_create()
        self.assertEqual(len(self.env['mail.activity'].search([('res_model', '=', 'workset.rate'), ('res_id', '=', r.id)])), 2)

    def test_c_norate_pairs_without_map_row(self):
        # людина з табелями в проєкті замовника, а рядка карти й ставки немає — раніше звірка її не бачила (лише рядки карти)
        self._isolate()
        self._quiet_bonuses()
        a, b = self._no_agents()
        with a, b:
            self._rate()
        other = self.env['hr.employee'].with_company(self.co_a).create({'name': 'Тест без карти', 'company_id': self.co_a.id})
        groups = [(other, self.p_a, 16.0, calc.to_date(DAY), 77), (self.emp_a, self.p_a, 8.0, calc.to_date(DAY), 78)]
        Aal = type(self.env['account.analytic.line'])
        with patch.object(Aal, '_read_group', return_value=groups):
            pairs = self.R._norate_pairs(DAY)
            res = self.R.rates_check()
            ov = self.R.rates_overview([], 6)
            ov_other = self.R.rates_overview([('customer_id', '=', self.cust2.id)], 6)
            # без права читати табелі: заголовок — не посилання; рядок — нова ставка пари, а без права створювати ставки — без кліку
            with patch.object(Aal, 'has_access', return_value=False):
                no_ts = [x for x in self.R.rates_overview([], 6)['alerts'] if x['key'] == 'no_rate'][0]
                with patch.object(type(self.R), 'has_access', return_value=False):
                    reader = [x for x in self.R.rates_overview([], 6)['alerts'] if x['key'] == 'no_rate'][0]
        self.assertEqual((no_ts['count'], no_ts['link'], no_ts['rows'][0]['click'], bool(no_ts['rows'][0]['new'])), (1, False, True, True))
        self.assertEqual((reader['link'], reader['rows'][0]['click'], reader['rows'][0]['new']), (False, False, False))
        self.assertEqual([(p['person'], p['cust'], p['hours']) for p in pairs], [(other.work_contact_id.id, self.cust.id, 16)], 'пара зі ставкою — ні')
        self.assertEqual(res['sections']['nomap'], 1)
        body = self.env['workset.rate.check'].search([('date', '=', DAY)]).body
        self.assertIn('Тест без карти', body)
        self.assertIn('немає в карті проєкту', body)
        alert = [x for x in ov['alerts'] if x['key'] == 'no_rate'][0]
        self.assertEqual((alert['count'], alert['model'], alert['rows'][0]['new']['default_person_id']), (1, 'account.analytic.line', other.work_contact_id.id),
                         'тривога «Працюють без ставки»: рядок — нова ставка цієї пари')
        self.assertEqual((alert['link'], alert['rows'][0]['click']), (True, True), 'з правом на табелі — заголовок і рядок клікаються')
        self.assertEqual([x for x in ov_other['alerts'] if x['key'] == 'no_rate'][0]['count'], 0, 'фільтр сторінки за іншим замовником — ні')

    def test_c_wage_from_contract(self):
        self._isolate()
        self._quiet_bonuses()
        wages = {}
        a, b = self._no_agents()
        h, c = self._contracts(wages)
        with a, b, h, c:
            self._base_version(wages)
            r = self._rate()
            self._a6(False)  # А6 вимкнено (як на проді після переносу): нова версія договору ставку не змінює
            self._version(wages, LATER, 15)
            self.emp_a._compute_current_version_id()
            self.assertFalse(self._pair(LATER))
            plan = self.R.rates_wage_from_contract()
            self.assertEqual([(x['rate_id'], x['date'], x['wage_was'], x['wage_new'], x['price_was'], x['price_new']) for x in plan['rows']],
                             [(r.id, LATER, 14, 15, 19, 20)], 'план: було → стало з дати версії; надбавка — + різниця')
            self.assertFalse(self._pair(LATER), 'план нічого не пише')
            done = self.R.rates_wage_from_contract([r.id], apply=True)
            new = self._pair(LATER)
            self.assertEqual((done['created'], new.wage, new.price, new.wage_source, new.source), (1, 15, 20, 'contract', 'contract'))
            self.assertEqual(calc.iso(r.date_to), calc.add_days(LATER, -1))
            self.assertFalse(self.R.rates_wage_from_contract()['rows'], 'повтор — нічого')
