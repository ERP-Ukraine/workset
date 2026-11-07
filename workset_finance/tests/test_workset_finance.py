# -*- coding: utf-8 -*-
"""Тести «Фінансів» (круг 1): Odoo.sh запускає їх на dev-збірці (локально: odoo-bin -i workset_finance --test-enable --test-tags
workset_finance). Усі дані — вигадані в самому тесті («Customer 1», «Node A»; числа не збігаються з даними штабу); місяць — березень 2030
(даних копії в ньому немає); корінь плану — план проєктів (як план рекрутингу на проді: статті в полі account_id рядка аналітики).

Сценарій березня 2030 (див. setUpClass); дві фірми: A (рекрутинг) і U (Україна), обидві в периметрі:
- рахунок клієнту 100 із розподілом 60/40 (Node A / Node B), неоплачений; рахунок Energy 20 і міжфірмовий 15 (на партнера фірми U) — теж із
  часткою рекрутингу; рахунок 33 без частки рекрутингу (стаття іншого плану); оплачений рахунок 50 без аналітики (кандидат 1г);
- пара проводок «доход по місяцю роботи»: −40 у лютому, +40 у березні; витрати −30 (A) і −5 (U); власник −10 (у ступінь 1 і 5), «частка» −6;
- Україна −12 (фірма U, рахунок ліквідності); «II етап»: рядки аналітики статті без партнера, одержувач у тексті name; рядок статті — додатна
  частка 10 %: +10 («PHRASE A», фраза з карти ліній), +4 (жодної фрази), +6 («Transit one», транзитна фраза, яка теж є в карті ліній) + рядки рахунку 10 і 4
  (проводка 19.03 без рядка статті: з круга 1е вона йде в 4' рядками рахунку — текст «test Stage partner A/B» фраз не має, «не розпізнано»);
  конверт AN −8 (і +3, яке не рахується).
- «Energy» — партнер третьої фірми (res.company.partner_id): його неоплачений рахунок 20 мусить іти в «Energy», а не в «міжфірмові».
Очікується (березень): 1 = 130, 1а = 10, 2 = −12, до часток = 128; 5 = 16 (з них частка 6); 6 = 20 + 14 = 34; за грошима: 128 − (10 × 9 + 8) = 30;
7: замовники 100, Energy 20 (міжфірмовий не показуємо); кандидати 1г: 1 на 50; дохід довідково 175.
Круг 1г: тести з фірмою поза периметром («Test firm Out», дописується в workset_rates.out_of_perimeter_company_ids у самому тесті) — її рядки
в ступені не входять, її партнер — окремий кошик, не «міжфірмові»."""
import json

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import SQL

from ..models import calc

M = '2030-03-01'
M_LAST = '2030-03-31'
FEB = '2030-02-01'
OLD = '2029-01-01'


@tagged('post_install', '-at_install')
class TestWorksetFinance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.eur = env.ref('base.EUR')
        cls.eur.active = True
        Company = env['res.company']
        cls.co = Company.create({'name': 'Test firm A', 'currency_id': cls.eur.id})
        cls.co_ua = Company.create({'name': 'Test firm U', 'currency_id': cls.eur.id})
        cls.co_en = Company.create({'name': 'Test firm Energy', 'currency_id': cls.eur.id})
        cls.cos = [cls.co.id, cls.co_ua.id, cls.co_en.id, env.company.id]
        cls.env = env(context=dict(env.context, allowed_company_ids=cls.cos, finance_today=M_LAST))
        env = cls.env
        # планы: корінь — план проєктів, під ним вузли; окремий план без кореня-рекрутингу
        Plan = env['account.analytic.plan']
        root = Plan.browse(int(env['ir.config_parameter'].sudo().get_param('analytic.project_plan') or 0)).exists()
        if not root and hasattr(Plan, '_get_all_plans'):
            root = Plan._get_all_plans()[0]
        cls.root = root or Plan.create({'name': 'Test recruiting root'})
        cls.node_owners = Plan.create({'name': 'Owners', 'parent_id': cls.root.id})
        cls.node_ua = Plan.create({'name': 'Ukraine', 'parent_id': cls.root.id})
        cls.plan_other = Plan.create({'name': 'Other root'})
        Acc = env['account.analytic.account']

        def acc(name, plan):
            return Acc.create({'name': name, 'plan_id': plan.id, 'company_id': False})
        cls.a_node_a = acc('Node A', cls.root)
        cls.a_node_b = acc('Node B', cls.root)
        cls.a_owner = acc('Owner X', cls.node_owners)
        cls.a_share = acc('Owner X share', cls.node_owners)
        cls.a_ua = acc('Ukraine costs', cls.node_ua)
        cls.a_stage2 = acc('Stage two', cls.root)
        cls.a_an = acc('Envelope AN', cls.root)
        cls.a_other = acc('Other plan', cls.plan_other)
        # бухгалтерські рахунки й журнали (нова фірма без плану рахунків)
        Account = env['account.account']

        def account(co, code, name, kind):
            return Account.with_company(co).create({'name': name, 'code': code, 'account_type': kind})
        cls.income = account(cls.co, '799911', 'Test income', 'income')
        cls.expense = account(cls.co, '699911', 'Test expense', 'expense')
        cls.cash = account(cls.co, '199911', 'Test cash', 'asset_current')
        cls.recv = account(cls.co, '129911', 'Test receivable', 'asset_receivable')
        cls.st2_acc = account(cls.co, '199912', 'Test stage two', 'asset_current')
        cls.expense_u = account(cls.co_ua, '699921', 'Test expense U', 'expense')
        cls.cash_u = account(cls.co_ua, '199921', 'Test cash U', 'asset_current')
        cls.liquidity = account(cls.co_ua, '199922', 'Test liquidity U', 'asset_current')
        Journal = env['account.journal']
        cls.misc = {co.id: Journal.with_company(co).create({'name': 'Test misc', 'code': 'TFM%d' % i, 'type': 'general', 'company_id': co.id})
                    for i, co in enumerate((cls.co, cls.co_ua))}
        cls.sale = Journal.with_company(cls.co).create({'name': 'Test sales', 'code': 'TFS', 'type': 'sale', 'company_id': cls.co.id,
                                                       'default_account_id': cls.income.id})
        Partner = env['res.partner']
        cls.cust1, cls.cust3, cls.part_a, cls.part_b = [Partner.create({'name': n, 'is_company': True}) for n in
                                                        ('Customer 1', 'Customer 3', 'Stage partner A', 'Stage partner B')]
        cls.energy = cls.co_en.partner_id   # Energy — фірма в Odoo: її партнер є і серед «своїх фірм» (перевірка Energy раніше за міжфірмові)
        for p in (cls.cust1, cls.energy, cls.cust3, cls.co_ua.partner_id):
            p.with_company(cls.co).property_account_receivable_id = cls.recv
        cls.line = env['workset.line'].create({'name': 'Line A'})
        # проводки
        d = {str(cls.a_node_a.id): 100}
        cls.inv_cust = cls._invoice(cls.cust1, 100, '2030-03-10', {str(cls.a_node_a.id): 60, str(cls.a_node_b.id): 40})
        cls.inv_energy = cls._invoice(cls.energy, 20, '2030-03-11', d)
        cls.inv_ic = cls._invoice(cls.co_ua.partner_id, 15, '2030-03-12', d)
        cls.inv_noshare = cls._invoice(cls.cust3, 33, '2030-03-13', {str(cls.a_other.id): 100})
        cls.inv_paid = cls._invoice(cls.cust1, 50, '2030-03-12', None)
        pay = cls._entry(cls.co, '2030-03-20', [{'account_id': cls.cash.id, 'debit': 50}, {'account_id': cls.recv.id, 'credit': 50, 'partner_id': cls.cust1.id}])
        (cls.inv_paid.line_ids | pay.line_ids).filtered(lambda x: x.account_id == cls.recv and not x.reconciled).reconcile()
        cls._entry(cls.co, '2030-02-15', [{'account_id': cls.income.id, 'debit': 40, 'analytic_distribution': d}, {'account_id': cls.cash.id, 'credit': 40}])
        cls._entry(cls.co, '2030-03-15', [{'account_id': cls.income.id, 'credit': 40, 'analytic_distribution': d}, {'account_id': cls.cash.id, 'debit': 40}])
        cls._entry(cls.co, '2030-03-16', [{'account_id': cls.expense.id, 'debit': 30, 'analytic_distribution': d}, {'account_id': cls.cash.id, 'credit': 30}])
        cls._entry(cls.co, '2030-03-17', [{'account_id': cls.expense.id, 'debit': 10, 'analytic_distribution': {str(cls.a_owner.id): 100}},
                                          {'account_id': cls.cash.id, 'credit': 10}])
        cls._entry(cls.co_ua, '2030-03-18', [{'account_id': cls.expense_u.id, 'debit': 5, 'analytic_distribution': d}, {'account_id': cls.cash_u.id, 'credit': 5}])
        cls._entry(cls.co, '2030-03-19', [{'account_id': cls.st2_acc.id, 'debit': 10, 'partner_id': cls.part_a.id},
                                          {'account_id': cls.st2_acc.id, 'debit': 4, 'partner_id': cls.part_b.id}, {'account_id': cls.cash.id, 'credit': 14}])
        # рядки аналітики без проводки (не входять у ступінь 1: потрібна проведена проводка)
        cls._aal(cls.co_ua, cls.a_ua, -12, cls.liquidity)
        cls._aal(cls.co_ua, cls.a_ua, -3, cls.cash_u)
        cls._aal(cls.co, cls.a_share, -6, cls.expense)
        # як на проді: партнера в рядках аналітики «II етапу» немає, одержувач — у тексті name
        cls._aal(cls.co, cls.a_stage2, 10, cls.cash, name='Payout PHRASE A to X')
        cls._aal(cls.co, cls.a_stage2, 4, cls.cash, name='Payout to nobody known')
        cls._aal(cls.co, cls.a_stage2, 6, cls.cash, name='Transit one II')
        cls._aal(cls.co, cls.a_an, -8, cls.cash)
        cls._aal(cls.co, cls.a_an, 3, cls.cash)
        # параметри
        P = calc.P_PREFIX
        params = {'recruting_plan_id': cls.root.id, 'owners_plan_id': cls.node_owners.id, 'owners_share_analytic_ids': str(cls.a_share.id),
                  'ukraine_company_id': cls.co_ua.id, 'ukraine_plan_id': cls.node_ua.id, 'liquidity_account_id': cls.liquidity.id,
                  'stage2_analytic_id': cls.a_stage2.id, 'stage2_account_id': cls.st2_acc.id,
                  'stage2_partner_lines': json.dumps([['phrase a', cls.line.id], ['transit one', cls.line.id]]),
                  'stage2_transit_phrases': json.dumps(['TRANSIT ONE']), 'an_envelope_analytic_id': cls.a_an.id,
                  'energy_partner_ids': str(cls.energy.id)}
        for k, v in params.items():
            env['ir.config_parameter'].sudo().set_param(P + k, str(v))
        cls.Month = env['workset.finance.month']
        cls.Adjust = env['workset.finance.adjust']

    @classmethod
    def _entry(cls, co, day, lines):
        move = cls.env['account.move'].with_company(co).create({
            'move_type': 'entry', 'journal_id': cls.misc[co.id].id, 'date': day,
            'line_ids': [(0, 0, dict({'name': 'test', 'debit': 0, 'credit': 0}, **l)) for l in lines]})
        move.action_post()
        return move

    @classmethod
    def _invoice(cls, partner, amount, day, dist, move_type='out_invoice'):
        move = cls.env['account.move'].with_company(cls.co).create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': day, 'date': day, 'journal_id': cls.sale.id,
            'invoice_line_ids': [(0, 0, {'name': 'service', 'quantity': 1, 'price_unit': amount, 'account_id': cls.income.id, 'tax_ids': [(6, 0, [])],
                                         'analytic_distribution': dist or False})]})
        move.action_post()
        return move

    @classmethod
    def _aal(cls, co, analytic, amount, general, day='2030-03-14', name='test'):
        return cls.env['account.analytic.line'].with_company(co).create({
            'name': name, 'account_id': analytic.id, 'amount': amount, 'date': day, 'general_account_id': general.id, 'company_id': co.id})

    def _run(self, months=(M,), model=None):
        return (self.Month if model is None else model).finance_recompute(list(months))

    @staticmethod
    def _r(*values):
        return [round(v, 2) for v in values]

    def _month(self, month=M):
        return self.Month.search([('month', '=', month)])

    def _adjust(self, step, amount, month=M, **kw):
        return self.Adjust.create(dict({'month': month, 'step': step, 'amount': amount, 'basis': 'test basis'}, **kw))

    def _out_company(self):
        """Нова фірма поза периметром «Ставок» (дописана в workset_rates.out_of_perimeter_company_ids) і її рахунок."""
        co = self.env['res.company'].create({'name': 'Test firm Out', 'currency_id': self.eur.id})
        Param = self.env['ir.config_parameter'].sudo()
        key = 'workset_rates.out_of_perimeter_company_ids'
        Param.set_param(key, ','.join(str(x) for x in calc.parse_ids(Param.get_param(key) or '') + [co.id]))
        acc = self.env['account.account'].with_company(co).create({'name': 'Test cash O', 'code': '199931', 'account_type': 'asset_current'})
        return co, acc

    def _pln(self):
        """Злотий з курсом 4,2 за євро у фірмі A (з 2030-01-01)."""
        pln = self.env.ref('base.PLN')
        pln.active = True
        self.env['res.currency.rate'].create({'name': '2030-01-01', 'rate': 4.2, 'currency_id': pln.id, 'company_id': self.co.id})
        return pln

    def _bank_journal(self, code, currency=None):
        """Банківський журнал фірми A (EUR, або валюта currency) з власним рахунком і рахунком очікування."""
        Account = self.env['account.account'].with_company(self.co)
        n = {'TBE': '1', 'TBP': '2'}[code]
        acc = Account.create({'name': 'Test bank ' + code, 'code': '19995' + n, 'account_type': 'asset_cash', 'currency_id': currency.id if currency else False})
        sus = Account.create({'name': 'Test suspense ' + code, 'code': '19996' + n, 'account_type': 'asset_current'})
        return self.env['account.journal'].with_company(self.co).create({
            'name': 'Test bank ' + code, 'code': code, 'type': 'bank', 'company_id': self.co.id, 'currency_id': currency.id if currency else False,
            'default_account_id': acc.id, 'suspense_account_id': sus.id})

    def _st(self, journal, amount, ref, partner=None, day='2030-03-05'):
        """Несвірений рядок виписки (amount — у валюті журналу)."""
        return self.env['account.bank.statement.line'].with_company(self.co).create({
            'journal_id': journal.id, 'date': day, 'payment_ref': ref, 'amount': amount, 'partner_id': partner.id if partner else False})

    # ——— ступені до цента ———
    def test_steps_match_to_the_cent(self):
        res = self._run()
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])
        m = self._month()
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m.s1_docs, 130, msg='доходи 135 + пара DRM 40 − витрати 30 − власник 10 − фірма U 5; стаття іншого плану не рахується')
        self.assertAlmostEqual(m.s1a_owners_in_pl, 10, msg='власник в P&L повертається в прибуток (іде у ступінь 5)')
        self.assertAlmostEqual(m.s1g_no_analytic, 0)
        self.assertAlmostEqual(m.s2_ukraine, -12, msg='лише фірма U, рахунок ліквідності, вузол Ukraine')
        self.assertAlmostEqual(m.base, 128)
        self.assertEqual(self._r(m.s3_shares, m.s4_net, m.s4c_cash), [0, 128, 30], msg='4 = до часток; 4\' = 128 − (10 × 9 + 8)')
        self.assertAlmostEqual(m.s5_owners, 16)
        self.assertAlmostEqual(m.s5_owners_share, 6, msg='«частка» — статті з параметра, решта — витрати компанії')
        self.assertAlmostEqual(m.s6_stage2, 34, msg='аналітика 10 + 4 + 6 + рядки рахунку 10 і 4')
        self.assertAlmostEqual(m.cash_out, 98, msg='рядки аналітики «II етапу»: партнер з лінією 10 × 9 = 90 + конверт AN 8; без лінії (4) і транзит (6) не входять; '
                                                   'рядки рахунку проводки без рядка статті (10 і 4) фраз не мають — «не розпізнано»')
        self.assertEqual(self._r(m.s7_unpaid_customers, m.s7_unpaid_energy), [100, 20], msg='міжфірмовий (15) і рахунок без частки рекрутингу не показуємо')
        self.assertAlmostEqual(m.revenue, 175)
        self.assertEqual(m.g_cand_count, 1)
        self.assertAlmostEqual(m.g_cand_amount, 50, msg='оплачений рахунок без аналітики — кандидат 1г за датою оплати')
        self.assertEqual(self._r(m.q_ic, m.q_unplaced, m.q_ext), [35, 83, 183], msg='міжфірмові: 15 + рахунок Energy 20 (він теж фірма); оборот без своїх фірм')
        self.assertAlmostEqual(m.q_pct, 64.5)
        self.assertEqual((m.s1_docs_src, m.s2_ukraine_src, m.s3_shares_src, m.s4_net_src), ('fact', 'fact', 'calc', 'calc'))

    def test_february_pair_shifts_income_between_months(self):
        self._run((FEB, M))
        self.assertAlmostEqual(self._month(FEB).s1_docs, -40, msg='ліва проводка пари «дохід за місяцем роботи» — у лютому')
        self.assertAlmostEqual(self._month(M).s1_docs, 130)

    def test_items_open_lists(self):
        self._run()
        items = self.env['workset.finance.item'].search([('month', '=', M)])
        kinds = set(items.mapped('kind'))
        self.assertTrue({'step', 'owner', 'owner_share', 'ukraine', 'stage2', 'cash', 'cash_other', 'unpaid', 'candidate', 'quality'} <= kinds, kinds)
        unpaid = items.filtered(lambda i: i.kind == 'unpaid' and i.partner_id == self.cust1)
        self.assertEqual((unpaid.amount, unpaid.count, unpaid.note), (100, 1, 'customer'))
        act = unpaid.action_open()
        self.assertEqual(act['res_model'], 'account.move')
        self.assertEqual(self.env['account.move'].search(act['domain']), self.inv_cust)
        # вид cash — усе, що віднімається в 4': розпізнані рядки партнерів (по рядку на лінію) і конверт AN (теж рядки аналітики, примітка 'AN')
        cash = items.filtered(lambda i: i.kind == 'cash')
        other = items.filtered(lambda i: i.kind == 'cash_other')
        net = cash.filtered(lambda i: i.note != 'AN')
        an = cash.filtered(lambda i: i.note == 'AN')
        self.assertEqual((len(net), len(an)), (1, 1), 'в «за грошима» — один розпізнаний рядок партнера й конверт AN; нерозпізнані й транзит — окремий вид')
        AAL, ML = 'account.analytic.line', 'account.move.line'
        unknown = other.filtered(lambda i: calc.t('unknown', 0) in i.note and i.res_model == AAL)
        self.assertIn(calc.t('not_in_4', 0), unknown.note, 'нерозпізнані й транзит — окремий вид, з позначкою «в 4\' не входить»')
        self.assertEqual((unknown.amount, unknown.count), (36, 1), 'рядок без збігу фрази — «не розпізнано», сума × 9, у переказ не входить')
        # круг 1е: проводка 19.03 без рядка статті — у 4' рядками рахунку (окремий рядок деталі, клік — рядки проводки)
        acc_unknown = other.filtered(lambda i: calc.t('unknown', 0) in i.note and i.res_model == ML)
        self.assertEqual((acc_unknown.amount, acc_unknown.count), (126, 2), '(10 + 4) × 9: тексти «test Stage partner A/B» фраз не мають')
        self.assertIn(calc.t('no_analytic_row', 0), acc_unknown.note)
        self.assertEqual(self.env[ML].search(acc_unknown.action_open()['domain']).account_id, self.st2_acc)
        self.assertEqual((net.amount, net.line_id, net.res_model), (90, self.line, 'account.analytic.line'),
                         'розпізнаний за фразою (без регістру): 10 × 9, лінія з карти; брутто (×10) — у примітці')
        self.assertIn('100,00', net.note)
        self.assertEqual((an.amount, an.line_id.id, an.res_model), (8, False, 'account.analytic.line'), 'конверт AN: −(−8) як є, без лінії; +3 не рахується')
        self.assertAlmostEqual(sum(cash.mapped('amount')), self._month().cash_out, msg='сума виду cash = перекази партнерам нетто (cash_out)')
        self.assertAlmostEqual(self._month().cash_out, 98)
        transit = other.filtered(lambda i: calc.t('transit', 0) in i.note)
        self.assertEqual((transit.amount, transit.count), (54, 1))
        self.assertEqual(self.env['account.analytic.line'].search(unknown.action_open()['domain']).mapped('name'), ['Payout to nobody known'])

    def test_stage2_cash_excludes_transit_and_unknown_phrases(self):
        P = calc.P_PREFIX
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 98)
        self.env['ir.config_parameter'].sudo().set_param(P + 'stage2_transit_phrases', '')
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 152, msg='без фрази транзиту рядок «Transit one» (він у карті ліній) входить: 98 + 6 × 9')
        self.env['ir.config_parameter'].sudo().set_param(P + 'stage2_partner_lines', json.dumps([['phrase a', 999999]]))
        res = self._run()
        self.assertAlmostEqual(self._month().cash_out, 98, msg='лінії 999999 у базі немає — розпізнаний рядок усе одно рахується')
        item = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'cash'), ('note', 'like', calc.t('line_gone', 0))])
        self.assertFalse(item.line_id)
        self.assertEqual(len(item), 1)
        self.assertIn(calc.t('line_missing', 0) % 1, res['verify'][0]['text'], 'пометка про відсутню лінію — у звірці')

    def test_stage2_order_of_pairs_and_partner_name_in_text(self):
        P = calc.P_PREFIX
        self._aal(self.co, self.a_stage2, 3, self.cash, name='nothing here')
        self.env['account.analytic.line'].search([('name', '=', 'nothing here')]).partner_id = self.part_b
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param(P + 'stage2_partner_lines', json.dumps([['stage partner b', self.line.id], ['phrase a', self.line.id]]))
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 161, msg="текст = name + імя партнера рядка: «Stage partner B» розпізнано (3 × 9 = 27) + 90 + AN 8; "
                                                                "і рядок рахунку 4 проводки без рядка статті — партнер B (4 × 9 = 36, круг 1е)")
        other_line = self.env['workset.line'].create({'name': 'Line B'})
        Param.set_param(P + 'stage2_partner_lines', json.dumps([['payout', other_line.id], ['phrase a', self.line.id]]))
        self._run()
        items = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'cash')])
        self.assertEqual(items.filtered(lambda i: i.line_id == other_line).amount, 126, 'перша за порядком масиву пара виграє: «payout» раніше за «phrase a» — обидва «Payout…»-рядки (10 + 4) × 9')
        Param.set_param(P + 'stage2_partner_lines', json.dumps({'phrase a': self.line.id}))
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 8, msg="обєкт замість масиву пар — нічого не розпізнано (посів такий файл відхиляє)")

    def test_stage2_partner_text_is_name_not_display_name(self):
        """Текст рядка «II етапу» бере ім'я партнера (name), а не display_name: у контакту фірми display_name — «Фірма, Людина». JS-драбина
        читає res.partner.name так само (паритет, круг 1г)."""
        P = calc.P_PREFIX
        # фірма-батько — своя (не Stage partner B: його ім'я з круга 1е є в тексті рядків рахунку проводки 19.03)
        parent = self.env['res.partner'].create({'name': 'Parent Firm Q', 'is_company': True})
        contact = self.env['res.partner'].create({'name': 'Contact Person', 'parent_id': parent.id})
        self.assertIn('Parent Firm Q', contact.display_name, 'передумова: ім\'я фірми є лише в display_name контакту')
        row = self._aal(self.co, self.a_stage2, 3, self.cash, name='nothing here')
        row.partner_id = contact
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param(P + 'stage2_partner_lines', json.dumps([['parent firm q', self.line.id], ['phrase a', self.line.id]]))
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 98, msg='фраза з імені фірми-батька контакту не розпізнає рядок: 90 + AN 8')
        Param.set_param(P + 'stage2_partner_lines', json.dumps([['contact person', self.line.id], ['phrase a', self.line.id]]))
        self._run()
        self.assertAlmostEqual(self._month().cash_out, 125, msg='ім\'я самого контакту — розпізнано: 3 × 9 = 27 + 90 + AN 8')

    def test_number_phrase_skipped_with_note(self):
        """Фраза-число (сума, рік, номер до 7 цифр) розпізнала б чужі рядки: розбір її пропускає (як JS-драбина), звірка називає параметр і
        скільки пропущено; хвіст рахунку від 8 цифр — фраза (круг 1д)."""
        P = calc.P_PREFIX
        self._aal(self.co, self.a_stage2, 2, self.cash, name='Payout 2030 to nobody')
        self._aal(self.co, self.a_stage2, 1, self.cash, name='Payout to acc 12345678')
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param(P + 'stage2_partner_lines', json.dumps([['2030', self.line.id], [' 12,5 ', self.line.id], ['12345678', self.line.id],
                                                                 ['phrase a', self.line.id]]))
        res = self._run()
        self.assertAlmostEqual(self._month().cash_out, 107, msg='«2030» пропущено (інакше + 2 × 9 = 125); «12345678» — фраза: 1 × 9 + 90 + AN 8')
        text = res['verify'][0]['text']
        self.assertIn(calc.t('phrase_number', 0) % (P + 'stage2_partner_lines', 2, calc.PHRASE_NUM_MAX_DIGITS), text)
        self.assertTrue(res['verify'][0]['ok'], 'примітка на «зійшлося» не впливає')

    def test_out_of_perimeter_firm_rows_not_in_steps(self):
        """Під sudo видно рядки всіх фірм: рядки аналітики фірми поза периметром (AN, «II етап», власники) у ступені не входять (круг 1г:
        на копії модуль брав AN чужих фірм)."""
        co_out, acc_out = self._out_company()
        self.assertNotIn(co_out.id, self.Month._perimeter())
        self._aal(co_out, self.a_an, -50, acc_out)
        self._aal(co_out, self.a_stage2, 7, acc_out, name='Payout PHRASE A to Y')
        self._aal(co_out, self.a_owner, -9, acc_out)
        res = self._run()
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])
        m = self._month()
        self.assertEqual(self._r(m.cash_out, m.s4c_cash, m.s5_owners, m.s6_stage2), [98, 30, 16, 34],
                         'AN −50 (було б 148), «II етап» 7 × 9 (було б 161), власник −9 і аналітика «II етапу» 7 фірми поза периметром не рахуються')
        items = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'cash'), ('note', '=', 'AN')])
        self.assertNotIn(co_out, self.env['account.analytic.line'].search(json.loads(items.domain)).company_id, 'список кліку — ті самі рядки, без чужої фірми')

    def test_out_of_perimeter_partner_not_intercompany(self):
        """Партнер фірми поза периметром — окремий кошик (як partnerSets закриття місяця): не «міжфірмові», не «без місця», не оборот,
        неоплачене не показуємо; finance_verify віддає його окремим списком."""
        co_out, _acc = self._out_company()
        partner = co_out.partner_id
        partner.with_company(self.co).property_account_receivable_id = self.recv
        self._invoice(partner, 10, '2030-03-12', {str(self.a_node_a.id): 60})
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.s1_docs, 136, msg='дохід за документом фірми периметра лишається: 130 + 60 % від 10')
        self.assertEqual(self._r(m.q_ic, m.q_unplaced, m.q_ext), [35, 83, 183],
                         'не «міжфірмові» (до круга 1г було б 45); 40 % без розподілу — не «без місця» (без окремого кошика було б 87); не оборот (було б 193)')
        self.assertEqual(self._r(m.s7_unpaid_customers, m.s7_unpaid_energy), [100, 20], 'рахунок фірмі поза периметром — не замовник')
        full = self.Month.finance_verify([M])
        self.assertIn(partner.id, full['out_of_perimeter_partner_ids'])
        self.assertNotIn(partner.id, full['intercompany_partner_ids'])
        self.assertIn(self.co_ua.partner_id.id, full['intercompany_partner_ids'], 'фірма периметра — «міжфірмова»')

    def test_out_receipt_counts_in_turnover(self):
        """Оборот із зовнішніми (q_ext) — усі out_*: рахунки, повернення й чеки продажу (out_receipt), як /^out_/ у MC.truthTurnover;
        «не оплачено» (ступінь 7) — лише рахунки й повернення."""
        if 'out_receipt' not in dict(self.env['account.move']._fields['move_type'].selection):
            self.skipTest('у цій збірці Odoo немає out_receipt')
        self._invoice(self.cust3, 9, '2030-03-13', {str(self.a_node_a.id): 100}, move_type='out_receipt')
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.q_ext, 192, msg='чек продажу 9 — в обороті: 183 + 9')
        self.assertAlmostEqual(m.s1_docs, 139, msg='і в ступені 1, як будь-який документ із часткою рекрутингу')
        self.assertEqual(self._r(m.s7_unpaid_customers, m.s7_unpaid_energy), [100, 20], 'чек у «не оплачено» не входить')

    def test_quality_transit_documents_excluded(self):
        """Транзит Energy — як PG.identity закриття місяця: документ транзиту знімає з «без місця» лише нерозподілений залишок; частка статті
        іншого плану лишається «без місця» за будь-якого партнера (круг 1е). Оборот — без транзиту."""
        ids = '%d,%d' % (self.inv_noshare.id, self.inv_paid.id)
        self.env['ir.config_parameter'].sudo().set_param(calc.P_PREFIX + 'quality_transit_move_ids', ids)
        self._run()
        m = self._month()
        self.assertEqual(self._r(m.q_ic, m.q_unplaced, m.q_ext), [35, 33, 100],
                         msg='рахунок 50 без розподілу — транзит, не «без місця»; 33 на статті іншого плану — «без місця» й у транзиті; оборот 183 − 33 − 50')
        self.assertAlmostEqual(m.s1_docs, 130, msg='ступінь 1 транзит не змінює')
        item = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'quality'), ('note', '=', 'unplaced')])
        self.assertEqual(self.env['account.move.line'].search(json.loads(item.domain)).move_id, self.inv_noshare, 'клік «без місця» — рівно рядки з внеском')

    def test_unplaced_click_is_exactly_contributing_lines(self):
        """«Без місця» — сума зі знаком (правило закриття місяця: MC.truthShare бере модуль від суми); клік — рядки, що дали внесок, зокрема
        частки статей іншого плану, а не лише «без розподілу»; витрата без розподілу зменшує суму зі знаком."""
        self._run()
        item = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'quality'), ('note', '=', 'unplaced')])
        lines = self.env['account.move.line'].search(json.loads(item.domain))
        self.assertEqual(lines.move_id, self.inv_noshare | self.inv_paid, '33 (інший план, розподіл є) і 50 (без розподілу)')
        self.assertEqual((item.amount, item.count), (83, 2))
        self._entry(self.co, '2030-03-23', [{'account_id': self.expense.id, 'debit': 13}, {'account_id': self.cash.id, 'credit': 13}])
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.q_unplaced, 70, msg='83 − 13: дохід і витрата без місця гасять одне одного, як у PG.identity і --check')
        self.assertAlmostEqual(m.q_pct, round((35 + 70) / 183 * 100, 1))

    def test_bank_nodoc_like_month_close(self):
        """«Банк без документа» — як PG.bankNoDoc закриття місяця (круг 1е): лише 4 види, сума в EUR (balance рядка на рахунку журналу, не
        amount виписки); переказ своїй фірмі, платіж посереднику й зарплатний лист без партнера — не «без документа»."""
        P = calc.P_PREFIX
        pln = self._pln()
        eur_bank, pln_bank = self._bank_journal('TBE'), self._bank_journal('TBP', pln)
        mediator = self.env['res.partner'].create({'name': 'Mediator M', 'is_company': True})
        self.env['ir.config_parameter'].sudo().set_param(P + 'mediator_partner_ids', str(mediator.id))
        self._st(pln_bank, -420, 'Lista plac 03/2030')
        self._st(eur_bank, -30, 'Transfer to own firm', self.co_ua.partner_id)
        self._st(eur_bank, -20, 'Payment to mediator', mediator)
        row = self._st(pln_bank, -84, 'Payment without document')
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.q_nodoc, 20, msg='лише рядок без партнера, у EUR: 84 / 4,2; код круга 1д дав би 420 + 30 + 20 + 84 = 554')
        items = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'quality'), ('note', 'like', 'nodoc:')])
        self.assertEqual(items.mapped('note'), ['nodoc:noPartner'], 'рядок деталі — на кожен непорожній вид')
        self.assertEqual((items.amount, items.count), (20, 1))
        self.assertEqual(self.env['account.bank.statement.line'].search(json.loads(items.domain)), row, 'клік — рядки виписки цього виду')
        self.assertAlmostEqual(m.q_pct, round((35 + 83 + 20) / 183 * 100, 1))

    def test_transfers_in_eur(self):
        """Перекази на особисті рахунки — у EUR (balance рядка на рахунку журналу): журнал у злотих не дає злотих як євро (круг 1е)."""
        P = calc.P_PREFIX
        pln_bank = self._bank_journal('TBP', self._pln())
        self.env['ir.config_parameter'].sudo().set_param(P + 'transfer_phrases', json.dumps(['na konto osobiste']))
        row = self._st(pln_bank, -210, 'Przelew na konto osobiste')
        self._run()
        item = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'transfer')])
        self.assertEqual((item.amount, item.count, item.note), (50, 1, calc.t('nodoc', 0)), '210 PLN / 4,2 = 50 EUR (код круга 1д дав би 210), не розібрано')
        self.assertEqual(self.env['account.bank.statement.line'].search(json.loads(item.domain)), row)

    def test_stage2_move_without_analytic_row_counts_once(self):
        """4' — по проводках «II етапу» (круг 1е): немає рядка аналітики статті — береться рядок рахунку проводки (знак і сума як у рядка
        статті); проводка з обома — один раз, рядком статті."""
        self._entry(self.co, '2030-03-24', [{'account_id': self.st2_acc.id, 'debit': 5, 'name': 'Payout PHRASE A no analytic'},
                                            {'account_id': self.cash.id, 'credit': 5}])
        both = self._entry(self.co, '2030-03-25', [{'account_id': self.st2_acc.id, 'debit': 7, 'name': 'Payout PHRASE A both'},
                                                   {'account_id': self.cash.id, 'credit': 7, 'name': 'Payout PHRASE A both',
                                                    'analytic_distribution': {str(self.a_stage2.id): 100}}])
        self.assertEqual(both.line_ids.analytic_line_ids.mapped('amount'), [7], 'передумова: рядок статті +7 (−баланс кредиту)')
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.cash_out, 206, msg='98 + 5 × 9 (рядок рахунку без статті) + 7 × 9 (рядок статті, рядок рахунку тієї ж проводки — ні); '
                                                     'код круга 1д дав би 161, подвійний рахунок — 269')
        items = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'cash'), ('line_id', '=', self.line.id)])
        acc = items.filtered(lambda i: i.res_model == 'account.move.line')
        self.assertEqual((acc.amount, acc.count), (45, 1))
        self.assertIn(calc.t('no_analytic_row', 0), acc.note)
        self.assertAlmostEqual(sum(items.mapped('amount')), 90 + 45 + 63)

    def test_stage2_account_lines_only_without_analytic(self):
        """Круг 2 (ревю 1е): у проводці «II етапу» без рядка статті в 4' — лише рядки рахунку БЕЗ аналітики (як документ v2). Пара «+X без
        аналітики / −X з іншою статтею» не гаситься в 0; рядок рахунку з конвертом AN рахується один раз (рядком конверта); рядки з іншою статтею —
        примітка звірки «скільки й на яку суму»."""
        other = self.env['account.analytic.account'].create({'name': 'Stage other', 'plan_id': self.root.id, 'company_id': False})
        self._entry(self.co, '2030-03-26', [{'account_id': self.st2_acc.id, 'debit': 5, 'name': 'Payout PHRASE A pair'},
                                            {'account_id': self.st2_acc.id, 'credit': 5, 'name': 'Payout PHRASE A pair back',
                                             'analytic_distribution': {str(other.id): 100}}])
        self._entry(self.co, '2030-03-27', [{'account_id': self.st2_acc.id, 'debit': 7, 'name': 'Payout PHRASE A envelope',
                                             'analytic_distribution': {str(self.a_an.id): 100}}, {'account_id': self.cash.id, 'credit': 7}])
        res = self._run()
        m = self._month()
        self.assertAlmostEqual(m.cash_out, 98 + 45 + 7, msg='+5 без аналітики × 9 = 45 (пара з −5 зі статтею — не 0); конверт AN −7 — лише рядком конверта (7), '
                                                          'не ще 7 × 9; код круга 1е дав би 98 + 0 + 63 + 7 = 168')
        self.assertIn(calc.t('stage2_other', 0) % (1, '−5,00'), res['verify'][0]['text'], 'рядок −5 з іншою статтею — примітка; рядок конверта — ні')
        self.assertTrue(res['verify'][0]['ok'], 'примітка на «зійшлося» не впливає')
        self.assertAlmostEqual(m.s6_stage2, 34 + 7, msg='ступінь 6 — як раніше: увесь баланс рахунку «II етапу» (+5 − 5 + 7)')

    def test_mediators_not_seeded_note(self):
        """Круг 2 (ревю 1е): посередників не посіяно — примітка звірки (забутий --seed інакше мовчки пускає їх в оборот); посіяно — примітки немає."""
        note = calc.t('mediator_missing', 0) % (calc.P_PREFIX + 'mediator_partner_ids')
        self.assertIn(note, self._run()['verify'][0]['text'])
        mediator = self.env['res.partner'].create({'name': 'Mediator N', 'is_company': True})
        self.env['ir.config_parameter'].sudo().set_param(calc.P_PREFIX + 'mediator_partner_ids', str(mediator.id))
        self.assertNotIn(note, self._run()['verify'][0]['text'])

    # ——— поправки ———
    def test_adjust_changes_step3_and_step4_and_marks_adjust(self):
        self._run()
        self.assertEqual(self._month().s4_net_src, 'calc')
        adj = self._adjust('s3', 20, line_id=self.line.id)
        self._adjust('s1g', 5)
        self._adjust('s3', 999).active = False
        self._run()
        m = self._month()
        self.assertAlmostEqual(m.s3_shares, 20)
        self.assertAlmostEqual(m.s1g_no_analytic, 5)
        self.assertAlmostEqual(m.base, 133)
        self.assertAlmostEqual(m.s4_net, 113, msg='4 = до часток 133 − частки 20; поправка в архіві не входить')
        self.assertAlmostEqual(m.s4c_cash, 35)
        self.assertEqual((m.s3_shares_src, m.s1g_no_analytic_src, m.s4_net_src, m.base_src), ('adjust', 'adjust', 'adjust', 'adjust'))
        self.assertEqual(m.s1_docs_src, 'fact')
        item = self.env['workset.finance.item'].search([('month', '=', M), ('kind', '=', 'adjust'), ('note', '=', 's3')])
        self.assertEqual((item.amount, item.line_id), (20, self.line))
        adj.active = False
        self._run()
        self.assertAlmostEqual(self._month().s4_net, 133, msg='поправку в архів — частки зникли, ступінь 1г лишилась')

    def test_adjust_needs_basis_and_month_2026(self):
        with self.assertRaises(ValidationError):
            self._adjust('s3', 1, basis='   ')
        with self.assertRaises(ValidationError):
            self._adjust('s3', 1, month='2025-12-01')
        with self.assertRaises(ValidationError):
            self._adjust('s3', 1, month='2030-03-15')

    # ——— пересчёт ———
    def test_recompute_twice_does_not_multiply_rows(self):
        self._run()
        n_items = self.env['workset.finance.item'].search_count([('month', '=', M)])
        self._run()
        self.assertEqual(self.Month.search_count([('month', '=', M)]), 1)
        self.assertEqual(self.env['workset.finance.item'].search_count([('month', '=', M)]), n_items)
        self.assertEqual(self.env['workset.finance.check'].search_count([('month', '=', M)]), 1)

    def test_months_before_2026_refused(self):
        with self.assertRaises(ValidationError):
            self.Month.finance_recompute(['2025-12-01'])
        with self.assertRaises(ValidationError):
            self.Month.create({'month': '2025-12-01'})
        self.assertTrue(all(m >= calc.MIN_MONTH for m in self.Month._months(None)))

    def test_missing_param_is_clear_error(self):
        self.env['ir.config_parameter'].sudo().set_param(calc.P_PREFIX + 'stage2_account_id', '')
        with self.assertRaises(UserError) as cm:
            self._run()
        self.assertIn(calc.P_PREFIX + 'stage2_account_id', str(cm.exception))

    # ——— звірка ———
    def test_verify_returns_fresh_steps_and_flags_stale(self):
        self._run()
        full = self.Month.finance_verify([M])
        got = full['months'][0]
        self.assertTrue(got['ok'], got['text'])
        self.assertFalse(got['stale'])
        self.assertEqual(sorted(full['companies']), sorted(self.Month._perimeter()), 'периметр фірм віддає сам модуль')
        self.assertTrue({self.co.id, self.co_ua.id, self.co_en.id} <= set(full['companies']))
        self.assertTrue({self.energy.id, self.co_ua.partner_id.id, self.co.partner_id.id} <= set(full['intercompany_partner_ids']), 'партнери всіх фірм, і Energy')
        self.assertAlmostEqual(got['steps']['s4_net'], 128)
        self._entry(self.co, '2030-03-21', [{'account_id': self.expense.id, 'debit': 7, 'analytic_distribution': {str(self.a_node_a.id): 100}},
                                            {'account_id': self.cash.id, 'credit': 7}])
        later = self.Month.finance_verify([M])['months'][0]
        self.assertTrue(later['stale'], 'в книгах з’явилось після перерахунку')
        self.assertAlmostEqual(later['steps']['s1_docs'], 123)

    def _entry_25(self):
        """Проведена витрата 25 на Node A (100 %) 22.03.2030 → її рядок P&L."""
        move = self._entry(self.co, '2030-03-22', [{'account_id': self.expense.id, 'debit': 25, 'analytic_distribution': {str(self.a_node_a.id): 100}},
                                                   {'account_id': self.cash.id, 'credit': 25}])
        line = move.line_ids.filtered(lambda x: x.account_id == self.expense)
        self.assertTrue(line.analytic_line_ids, 'передумова: проведення створило рядок аналітики')
        return line

    def test_verify_control_catches_lost_analytic_line(self):
        """Рядок аналітики зник ПОВЗ ORM (SQL, міграція 16 → 19, код із контекстом skip_analytic_sync), розподіл у проводці лишився: другий
        спосіб бачить 25, перший — ні. Через ORM так не буває: account.analytic.line.unlink() в Odoo 19 зове
        move_line_id._update_analytic_distribution() і переписує розподіл із рядків, що лишилися (addons/account/models/account_analytic_line.py)
        — це перевіряє test_verify_orm_unlink_moves_amount_to_unplaced."""
        self._run()
        line = self._entry_25()
        self.env.flush_all()
        self.env.cr.execute(SQL('DELETE FROM account_analytic_line WHERE id = ANY(%s)', line.analytic_line_ids.ids))
        self.env.invalidate_all()
        self.assertFalse(line.analytic_line_ids, 'рядка аналітики немає')
        self.assertIn(str(self.a_node_a.id), line.analytic_distribution or {}, 'а розподіл у проводці лишився')
        got = self.Month.finance_verify([M])['months'][0]
        self.assertFalse(got['ok'], 'проводка з розподілом без рядка аналітики: другий спосіб бачить 25, перший — ні')
        self.assertIn(calc.t('v_control', 0) % ('130,00', '105,00', '25,00'), got['text'])
        self.assertAlmostEqual(got['steps']['s1_docs'], 130)

    def test_verify_orm_unlink_moves_amount_to_unplaced(self):
        """Odoo 19 тримає розподіл проводки в лад із рядками аналітики: видалення рядка через ORM стирає й розподіл (_update_analytic_distribution),
        тож обидва способи ступеня 1 сходяться, а 25 не зникає мовчки — рядок без розподілу йде в «без місця» якості даних."""
        self._run()
        line = self._entry_25()
        line.analytic_line_ids.unlink()
        self.assertFalse(line.analytic_distribution, 'передумова (Odoo 19): розподіл стерто разом із рядком аналітики')
        got = self.Month.finance_verify([M])['months'][0]
        self.assertTrue(got['ok'], got['text'])
        self.assertAlmostEqual(got['steps']['s1_docs'], 130, msg='витрата без розподілу у ступінь 1 не входить — ні першим, ні другим способом')
        self.assertAlmostEqual(got['steps']['q_unplaced'], 58, msg='83 + (−25): рядок без розподілу — «без місця»')

    # ——— права ———
    def test_rights(self):
        base = 'base.group_user'
        # всім тестовим користувачам — усі фірми теста: відмова має бути саме від перевірки групи модуля, а не від чужих фірм
        co = {'company_id': self.co.id, 'company_ids': [(6, 0, self.cos)]}
        none = new_test_user(self.env, login='fin_none', groups=base, **co)
        owner = new_test_user(self.env, login='fin_owner', groups=base + ',workset_finance.group_owner', **co)
        editor = new_test_user(self.env, login='fin_editor', groups=base + ',workset_finance.group_editor', **co)
        admin = new_test_user(self.env, login='fin_admin', groups=base + ',base.group_system', **co)
        self._run()
        only_group = r'Цей екран бачить лише група|Этот экран видит только группа'   # calc.TX['only_group'] (ключ only_group)
        only_editor = r'Перераховувати може лише редактор|Пересчитывать может только редактор'   # calc.TX['only_editor']
        for user in (none, admin):
            Model = self.Month.with_user(user)
            with self.assertRaisesRegex(AccessError, only_group):
                Model.finance_owner(M)
            with self.assertRaisesRegex(AccessError, only_group):
                Model.finance_verify([M])
        with self.assertRaisesRegex(AccessError, only_editor):
            self.Month.with_user(none).finance_recompute([M])
        with self.assertRaisesRegex(AccessError, only_editor):
            self.Month.with_user(owner).finance_recompute([M])
        got = self.Month.with_user(owner).finance_owner(M)
        self.assertTrue(got['has'])
        self.assertAlmostEqual({x['key']: x['value'] for x in got['tiles']}['s4_net'], 128)
        self.assertEqual({r['key']: r['cells'][-1]['src'] for r in got['ladder']['rows']}['s1_docs'], 'fact')
        self.assertEqual(got['ladder']['rows'][4]['cells'][-1]['open']['res_id'], self._month().id, 'власник відкриває картку місяця своїми правами')
        self.assertTrue(self.Month.with_user(owner).finance_verify([M])['months'][0]['ok'])
        with self.assertRaises(ValidationError):
            self.Month.with_user(owner).finance_owner('2025-12-01')
        self.assertTrue(self.Month.with_user(editor).finance_recompute([M])['verify'][0]['ok'])
        self.assertTrue(self.Month.with_user(admin).finance_recompute([M])['verify'][0]['ok'], 'адміністратор перераховує, але екрани поза групою не бачить')
        with self.assertRaises(AccessError):
            self._month().with_user(owner).write({'s4_net': 1})
        with self.assertRaises(AccessError):
            self.Adjust.with_user(owner).create({'month': M, 'step': 's3', 'amount': 1, 'basis': 'x'})
        self.assertTrue(self.Adjust.with_user(editor).create({'month': M, 'step': 's3', 'amount': 1, 'basis': 'x'}))

    def test_cron_recompute_uses_last_months(self):
        res = self.Month._cron_recompute()
        self.assertEqual(res['months'], ['2029-12-01', '2030-01-01', '2030-02-01', '2030-03-01'], '3 останні місяці й поточний (сьогодні підмінено)')

    def test_too_many_months_is_clear_error(self):
        many = calc.month_list('2026-01-01', calc.add_months('2026-01-01', calc.MAX_MONTHS))   # MAX_MONTHS + 1 місяців
        with self.assertRaisesRegex(ValidationError, r'Забагато місяців за раз|Слишком много месяцев за раз'):
            self.Month.finance_recompute(many)
        self.assertEqual(len(self.Month._months(calc.month_list('2026-01-01', calc.add_months('2026-01-01', calc.MAX_MONTHS - 1)))), calc.MAX_MONTHS)

    # ——— круг 2: екран «Власник» ———
    def _owner(self, month=M, today='2030-04-10'):
        return self.Month.with_context(finance_today=today).finance_owner(month)

    def _records(self, spec):
        """Записи, які відкриває клік (опис сервера: модель і домен; res_id — один запис)."""
        self.assertTrue(spec, 'у числа є опис списку')
        Model = self.env[spec['model']].with_context(active_test=True)
        return Model.browse(spec['res_id']) if spec.get('res_id') else Model.search(spec['domain'])

    def test_owner_screen_tiles_ytd_waterfall_and_ladder(self):
        """Плитки = полям місяця; «з початку року» = Σ місяців року до обраного; водоспад сходиться до s4_net (нульові ступені сховано);
        таблиця = полям місяців (частки — мінусом), «Разом» = Σ; колонки — лише пораховані місяці; ряд графіка — ті самі колонки."""
        self._adjust('s3', 20, line_id=self.line.id)
        self._adjust('s1g', 5)
        self._run((FEB, M))
        feb, mar = self._month(FEB), self._month(M)
        data = self._owner()
        self.assertTrue(data['has'])
        self.assertEqual((data['month']['value'], data['year']), (M, '2030'))
        tiles = {x['key']: x for x in data['tiles']}
        self.assertEqual([x['key'] for x in data['tiles']], list(calc.TILES))
        self.assertEqual(self._r(tiles['s4_net']['value'], tiles['s4c_cash']['value'], tiles['recruiting']['value'], tiles['s2_ukraine']['value'],
                                 tiles['s3_shares']['value']),
                         self._r(mar.s4_net, mar.s4c_cash, mar.s1_docs + mar.s1a_owners_in_pl + mar.s1g_no_analytic, mar.s2_ukraine, -mar.s3_shares))
        self.assertEqual(self._r(mar.s4_net, mar.s4c_cash), [113, 35], 'передумова: 133 − 20; 133 − 98')
        for key in ('s4_net', 's4c_cash', 's2_ukraine'):
            self.assertAlmostEqual(tiles[key]['ytd'], feb[key] + mar[key], msg='з початку року = лютий + березень: %s' % key)
        self.assertAlmostEqual(tiles['s3_shares']['ytd'], -(feb.s3_shares + mar.s3_shares))
        self.assertAlmostEqual(self._owner(FEB)['tiles'][0]['ytd'], feb.s4_net, msg='з початку року — лише до обраного місяця')
        # водоспад: 1 → 1а → 1г → 2 → до часток → частки → чистий прибуток; сходиться до цента
        fall = data['waterfall']
        bars = fall['bars']
        self.assertEqual([b['key'] for b in bars], ['s1_docs', 's1a_owners_in_pl', 's1g_no_analytic', 's2_ukraine', 'base', 's3_shares', 's4_net'])
        cents = [calc.cents(b['amount']) for b in bars]
        self.assertEqual(sum(cents[:4]), cents[4], 'доданки дають «до часток»')
        self.assertEqual(cents[4] + cents[5], cents[6])
        self.assertEqual(cents[6], calc.cents(mar.s4_net), 'останній стовпець — s4_net до цента')
        self.assertTrue(fall['ok'])
        # таблиця «Драбина»: колонки — пораховані місяці (січня немає), клітинки = поля місяців, «Разом» = Σ
        lad = data['ladder']
        self.assertEqual([m['value'] for m in lad['months']], [FEB, M], 'непорахованих колонок немає')
        self.assertEqual(lad['selected'], 1)
        self.assertEqual([r['key'] for r in lad['rows']], [k for k, *_x in calc.LADDER_ROWS])
        for r in lad['rows']:
            sign = -1 if r['key'] == 's3_shares' else 1
            self.assertEqual([c['value'] for c in r['cells']], self._r(sign * feb[r['key']], sign * mar[r['key']]), r['key'])
            self.assertEqual(calc.cents(r['total']['value']), sum(calc.cents(c['value']) for c in r['cells']), '«Разом» = Σ: %s' % r['key'])
            self.assertEqual([c['text'] for c in r['cells']], [calc.k_text(c['value']) for c in r['cells']])
        rows = {r['key']: r for r in lad['rows']}
        self.assertEqual(rows['s3_shares']['cells'][1]['src'], 'adjust')
        self.assertIn('test basis', rows['s3_shares']['cells'][1]['hint'], 'поправка — підказка з підставою')
        self.assertEqual(rows['base']['cells'][0]['hint'], calc.t('h_calc', self.Month._lang()), 'без поправки — «розрахунок штабу»')
        self.assertEqual(rows['s1_docs']['cells'][1]['hint'], '', 'факт — без підказки')
        self.assertEqual(rows['s1g_no_analytic']['cells'][0]['text'], '—', 'нуль — «—»')
        self.assertEqual(data['series']['months'], [FEB, M])
        self.assertEqual(data['series']['net'], [calc.k_value(feb.s4_net), calc.k_value(mar.s4_net)])
        self.assertEqual(data['series']['selected'], 1)
        # круг 2б: «Разом» = «з початку року» плиток (той самий набір місяців)
        self.assertEqual(calc.cents(rows['s4_net']['total']['value']), calc.cents(tiles['s4_net']['ytd']))
        self.assertEqual(calc.cents(rows['s4c_cash']['total']['value']), calc.cents(tiles['s4c_cash']['ytd']))
        self.assertEqual(calc.cents(rows['s2_ukraine']['total']['value']), calc.cents(tiles['s2_ukraine']['ytd']))
        self.assertEqual(calc.cents(rows['s3_shares']['total']['value']), calc.cents(tiles['s3_shares']['ytd']), 'частки — мінусом і там, і там')
        self.assertEqual(sum(calc.cents(rows[k]['total']['value']) for k in calc.RECRUITING), calc.cents(tiles['recruiting']['ytd']))
        # місяць після обраного — ні в таблиці, ні в «Разом», ні в графіку, ні в «з початку року»
        early = self._owner(FEB)
        self.assertEqual([m['value'] for m in early['ladder']['months']], [FEB], 'березень (після обраного лютого) не показується')
        self.assertEqual(early['series']['months'], [FEB])
        for r in early['ladder']['rows']:
            sign = -1 if r['key'] == 's3_shares' else 1
            self.assertEqual(calc.cents(r['total']['value']), calc.cents(sign * feb[r['key']]), '«Разом» лютого — лише лютий: %s' % r['key'])
        self.assertEqual(calc.cents({x['key']: x for x in early['tiles']}['s4_net']['ytd']), calc.cents(feb.s4_net))
        # «Разом» ступеня 1 за два місяці — записи ступеня обох місяців (домени через «АБО»): сума списку = число
        total = rows['s1_docs']['total']
        self.assertEqual(total['open']['model'], 'account.analytic.line')
        self.assertAlmostEqual(sum(self._records(total['open']).mapped('amount')), feb.s1_docs + mar.s1_docs, msg='«Разом» = Σ записів')
        self.assertTrue(feb.s1_docs and mar.s1_docs, 'передумова: обидва місяці не нульові')
        # видимі позначки: плитки й стовпці водоспаду з поправкою чи розрахунком штабу; «Разом» — найсильніша позначка рядка
        self.assertEqual({k: x['src'] for k, x in tiles.items()}, {'s4_net': 'adjust', 's4c_cash': 'adjust', 'recruiting': 'adjust', 's2_ukraine': '',
                                                                    's3_shares': 'adjust'})
        self.assertEqual({b['key']: b['src'] for b in bars}, {'s1_docs': '', 's1a_owners_in_pl': '', 's1g_no_analytic': 'adjust', 's2_ukraine': '',
                                                              'base': 'adjust', 's3_shares': 'adjust', 's4_net': 'adjust'})
        self.assertEqual((rows['s1_docs']['total']['src'], rows['base']['total']['src']), ('', 'adjust'), 'лютий — розрахунок, березень — поправка')
        self.assertIn('test basis', rows['s3_shares']['total']['hint'])
        self.assertEqual(data['month']['partial'], '', 'березень завершено (сьогодні 10.04)')
        # неповний місяць: сьогодні 20.03 — березень «неповний, по 20.03» у шапці й колонці; лютий — повний
        part = self._owner(M, today='2030-03-20')
        self.assertEqual(part['month']['partial'], '20.03')
        self.assertEqual([m['partial'] for m in part['ladder']['months']], ['', '20.03'])
        # без поправок нульові ступені 1г і 3 сховано
        self.Adjust.search([]).write({'active': False})
        self._run((M,))
        self.assertEqual([b['key'] for b in self._owner()['waterfall']['bars']], ['s1_docs', 's1a_owners_in_pl', 's2_ukraine', 'base', 's4_net'])

    def test_owner_screen_months_default_and_before_2026(self):
        """Місяць за замовчуванням — останній закритий, закритих немає — останній завершений календарний; вибір — лише місяці з записом;
        до 2026 — відмова; місяць без запису — «немає даних», таблиця року лишається."""
        self._run((FEB, M))
        self.assertEqual(self._owner(None)['month']['value'], M, 'закритих немає — останній завершений місяць (сьогодні 10.04)')
        self.assertEqual(self._owner(None, today='2030-03-31')['month']['value'], FEB, 'березень ще не завершено')
        self._month(FEB).write({'state': 'closed'})
        self.assertEqual(self._owner(None)['month']['value'], FEB, 'є закритий — він')
        data = self._owner()
        self.assertEqual([c['value'] for c in data['choices']], [M, FEB], 'вибір — лише місяці з записом, від останнього')
        self.assertTrue(all(c['value'] >= calc.MIN_MONTH for c in data['choices']))
        with self.assertRaises(ValidationError):
            self._owner('2025-12-01')
        empty = self._owner('2030-05-01')
        self.assertFalse(empty['has'])
        self.assertEqual((empty['tiles'], empty['chips'], empty['owners'], empty['transfers'], empty['aside']), ([], [], False, False, False))
        self.assertEqual([m['value'] for m in empty['ladder']['months']], [FEB, M], 'таблиця року — є')
        self.assertEqual(empty['ladder']['selected'], -1)

    def test_owner_screen_chips_only_when_something_to_say(self):
        self._run()
        lang = self.Month._lang()
        chips = {c['key']: c for c in self._owner()['chips']}
        self.assertEqual(set(chips), {'open', 'check', 'quality', 'stamp'}, 'поправок немає — чипа «Поправки» немає')
        self.assertTrue(chips['check']['ok'])
        self.assertEqual(self._records(chips['check']['open']), self.env['workset.finance.check'].search([('month', '=', M)]), 'клік — звірка місяця')
        q = chips['quality']
        m = self._month()
        self.assertEqual((q['pct'], q['bad'], q['parts'], q['ext'], q['threshold']), (m.q_pct, True, 118, 183, calc.TRUTH_PCT),
                         'доля як у закриття місяця; не розібрано 35 + 83 із обороту 183')
        self.assertEqual(q['open']['dialog'], 'quality', 'клік — три кошики з тієї самої відповіді')
        self.assertEqual([b['key'] for b in q['baskets']], ['ic', 'unplaced'], 'нульові кошики (банк без документа) — без рядка')
        self.assertEqual([b['label'] for b in q['baskets']], [calc.t('q_ic', lang), calc.t('q_unplaced', lang)], 'підпис — за кодом примітки')
        unplaced = self._records(q['baskets'][1]['open'])
        self.assertEqual(unplaced.move_id, self.inv_noshare | self.inv_paid, 'кошик — свої записи')
        self.assertRegex(chips['stamp']['text'], r'^\d{2}\.\d{2} \d{2}:\d{2}$', '«Перераховано ДД.ММ ГГ:ХХ»')
        # поправка є — чип з кількістю (живий підрахунок), місяць закрито — чипа немає, кошики порожні — чипа якості немає, звірка не зійшлась
        self._adjust('s3', 3)
        m.write({'state': 'closed', 'q_ic': 0, 'q_unplaced': 0, 'q_nodoc': 0})
        self.env['workset.finance.check'].search([('month', '=', M)]).write({'ok': False})
        chips = {c['key']: c for c in self._owner()['chips']}
        self.assertEqual(set(chips), {'check', 'adjust', 'stamp'})
        self.assertEqual(chips['adjust']['count'], 1)
        self.assertEqual(len(self._records(chips['adjust']['open'])), 1)
        self.assertFalse(chips['check']['ok'], 'не зійшлася — червоним')
        m.write({'q_ext': 0, 'q_ic': 5})
        self.assertNotIn('quality', [c['key'] for c in self._owner()['chips']], 'частка не порахована (оборот 0) — чипа немає')

    def test_owner_screen_clicks_open_own_records(self):
        """Кожне число відкриває свої записи: клітинки таблиці, плитки, стовпці водоспаду, «Власники», «Перекази», «Окремо від прибутку»."""
        P = calc.P_PREFIX
        self._adjust('s3', 20, line_id=self.line.id)
        eur_bank = self._bank_journal('TBE')
        self.env['ir.config_parameter'].sudo().set_param(P + 'transfer_phrases', json.dumps(['na konto osobiste']))
        row = self._st(eur_bank, -50, 'Przelew na konto osobiste')
        self._run()
        m = self._month()
        data = self._owner()
        AAL = 'account.analytic.line'
        lad = {r['key']: r for r in data['ladder']['rows']}
        cell = {k: r['cells'][-1] for k, r in lad.items()}
        self.assertAlmostEqual(sum(self._records(cell['s1_docs']['open']).mapped('amount')), m.s1_docs, msg='1 — рядки аналітики ступеня')
        self.assertAlmostEqual(-sum(self._records(cell['s1a_owners_in_pl']['open']).mapped('amount')), m.s1a_owners_in_pl)
        self.assertAlmostEqual(sum(self._records(cell['s2_ukraine']['open']).mapped('amount')), m.s2_ukraine)
        self.assertEqual(cell['s1_docs']['open']['model'], AAL)
        self.assertEqual(self._records(cell['s3_shares']['open']).mapped('amount'), [20], '3 — поправки місяця')
        self.assertEqual(self._records(cell['base']['open']), m, '«до часток» — картка місяця')
        self.assertEqual(cell['s4_net']['open']['res_id'], m.id)
        cash = self._records(cell['s4c_cash']['open'])
        self.assertEqual(set(cash.mapped('kind')), {'cash', 'cash_other'}, "4' — що віднімається і що не входить")
        self.assertEqual(cell['s4c_cash']['open']['context']['group_by'], ['kind', 'line_id'], 'одна лінія — одна група, не дробимо')
        self.assertFalse(cell['s1g_no_analytic']['open'], 'нуль — без кліку')
        # «Разом» — записи ступеня за місяці колонок (січень … обраний); тут порахований лише березень — ті самі записи, що в клітинці (круг 2б:
        # раніше чекали один рядок деталі, а клік відкривав 8 рядків аналітики — dev-збірка 07.11)
        tot = self._records(lad['s1_docs']['total']['open'])
        self.assertEqual(tot, self._records(cell['s1_docs']['open']))
        self.assertAlmostEqual(sum(tot.mapped('amount')), lad['s1_docs']['total']['value'], msg='сума списку = «Разом»')
        # плитки: число = список
        tiles = {x['key']: x for x in data['tiles']}
        rec = self._records(tiles['recruiting']['open'])
        self.assertAlmostEqual(sum(rec.mapped('amount')), tiles['recruiting']['value'], msg='рекрутинг за документами = 1 + 1а + 1г у списку')
        self.assertEqual(self._records(tiles['s4_net']['open']), m)
        # водоспад: стовпець — свої записи
        bars = {b['key']: b for b in data['waterfall']['bars']}
        self.assertAlmostEqual(sum(self._records(bars['s2_ukraine']['open']).mapped('amount')), m.s2_ukraine)
        # «Власники»: рядок на власника (стаття прямо у вузлі «власники» — сама стаття), частка окремо; клік — рядки аналітики
        own = data['owners']
        self.assertAlmostEqual(own['total'], m.s5_owners)
        self.assertEqual([(o['code'], o['total'], o['share'], o['company']) for o in own['rows']],
                         [(self.a_owner.display_name, 10, 0, 10), (self.a_share.display_name, 6, 6, 0)])
        self.assertEqual(self._records(own['rows'][0]['open']).mapped('amount'), [-10])
        self.assertEqual(set(self._records(own['open']).mapped('kind')), {'owner', 'owner_share'})
        # «Перекази»: одержувач × сума × статус; клік — рядки виписки
        tr = data['transfers']
        self.assertEqual([(r['amount'], r['status']) for r in tr['rows']], [(50, 'nodoc')])
        self.assertEqual(self._records(tr['rows'][0]['open']), row)
        # «Окремо від прибутку»: II етап, не оплачено замовниками й Energy; клік — рахунки
        aside = {a['key']: a for a in data['aside']['rows']}
        self.assertEqual(self._r(aside['stage2']['amount'], aside['customers']['amount'], aside['energy']['amount']), [34, 100, 20])
        self.assertEqual(self._records(aside['customers']['open']), self.inv_cust)
        self.assertEqual(self._records(aside['energy']['open']), self.inv_energy)
        self.assertTrue(aside['energy']['warn'])
        self.assertEqual(aside['energy']['firm'], self.co_en.name, 'назва групи Energy — з Odoo (фірма, чий партнер у параметрі), не з коду')
        self.assertIn(self.co_en.name, aside['energy']['open']['title'])
        self.assertEqual(set(self._records(aside['stage2']['open']).mapped('kind')), {'stage2'})

    def _specs(self, node):
        """Усі описи кліку ('open' з моделлю) у відповіді екрана."""
        if isinstance(node, dict):
            if node.get('model') and 'domain' in node:
                yield node
            for v in node.values():
                yield from self._specs(v)
        elif isinstance(node, list):
            for v in node:
                yield from self._specs(v)

    def test_owner_screen_click_context_companies(self):
        """Клік відкриває список по фірмах периметра, доступних користувачу (allowed_company_ids у контексті дії), а не лише по відмічених у
        перемикачі: фірма поза периметром — ні, фірма, якої в користувача немає, — ні; поточна фірма — першою."""
        out_co, _acc = self._out_company()
        self._adjust('s3', 20, line_id=self.line.id)
        self._run()
        base = 'base.group_user,workset_finance.group_owner'
        user = new_test_user(self.env, login='fin_owner_cos', groups=base, company_id=self.co_ua.id,
                             company_ids=[(6, 0, [self.co.id, self.co_ua.id, out_co.id])])
        perim = set(self.Month._perimeter())
        self.assertTrue({self.co.id, self.co_ua.id} <= perim and out_co.id not in perim, 'передумова: периметр')
        data = self.Month.with_user(user).with_context(allowed_company_ids=[self.co_ua.id]).finance_owner(M)
        specs = list(self._specs(data))
        self.assertTrue(len(specs) > 10, 'описів кліку багато: таблиця, плитки, водоспад, чипи')
        for spec in specs:
            self.assertEqual(spec['context'].get('allowed_company_ids'), [self.co_ua.id, self.co.id],
                             'периметр ∩ фірми користувача, поточна — першою; поза периметром і чужих фірм немає: %s' % spec['title'])
            self.assertFalse(spec['context'].get('create', True))
        self.assertEqual(calc.click_companies([1, 2, 3], [2, 3, 9], 3), [3, 2])
        self.assertEqual(calc.click_companies([1, 2], [2, 9], 9), [2], 'поточна поза периметром — не в списку')

    def test_closed_takes_parent_company_lock_date(self):
        Company = self.env['res.company']
        if 'hard_lock_date' not in Company._fields:
            self.skipTest('у цій збірці Odoo немає hard_lock_date')
        parent = Company.create({'name': 'Test parent', 'currency_id': self.eur.id})
        child = Company.create({'name': 'Test child', 'currency_id': self.eur.id, 'parent_id': parent.id})
        self.assertFalse(self.Month._closed([child.id], M_LAST), 'блокувань немає')
        parent.hard_lock_date = M_LAST
        self.assertTrue(self.Month._closed([child.id], M_LAST), 'дата блокування батьківської фірми діє на дочірню')
