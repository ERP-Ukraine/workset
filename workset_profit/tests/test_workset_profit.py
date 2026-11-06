# -*- coding: utf-8 -*-
"""Тести «Прибутковості ліній»: Odoo.sh запускає їх на dev-збірці (локально: odoo-bin -i workset_profit --test-enable --test-tags
workset_profit). Усі дані — вигадані в самому тесті («Line A», «Customer 1»; числа не збігаються з даними штабу); місяць — 2030 (даних
копії в ньому немає); корінь плану аналітики — план проєктів (параметр calc.P_ROOT_PLANS), вузли й статті — свої; бонуси офісу бази —
в архіві, посередники «Ставок» — підмінені (фонд працівників лінії A — 1 за годину).

Сценарій березня 2030 (див. setUpClass):
- лінія A (Customer 1, вузол Node A): працівник 1 — по 8 год 4-го і 20-го, ставка змінилась 16-го (частка 3,5 → 2,5 після фонду 1 і оренди
  авто 0,5); працівник 3 — 4 год без ставки; житло −10 (платимо ми з буфера), буфер житла +15, інструмент −3, паливо в зарплаті −6 і
  картка DKV лінії −4,5 (обидва — з фонду працівників за «Хто платить»), виручка +100 — вузол «ігнор»;
- лінія B (Customer 2): працівник 2 — 8 год за ставкою 5, працівник 4 — 8 год без ставки; житло −20 платить партнер; перевезення — партнер;
- перевезення (картка «перевезення», 9) — за прибуттями: працівник 1 не прибув (табель 10.02, розрив 22 дні), працівник 3 — у A,
  працівники 2 і 4 — у B (у працівника 4 розрив 48 днів) → A 3, B 6;
- компанія: власник −10, картка офісу −3, картка без періоду −1,2, рахунок DKV з транзакціями −17,7 (розкладено), рахунок DKV без
  транзакцій −4, стаття без розмітки −2,5 (підказка «інструмент»)."""
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.workset_rates.models import agent_deals

from ..models import calc

M = '2030-03-01'
M_LAST = '2030-03-31'
APR = '2030-04-01'
OLD = '2029-01-01'


@tagged('post_install', '-at_install')
class TestWorksetProfit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.eur = env.ref('base.EUR')
        cls.eur.active = True
        cls.co = env['res.company'].create({'name': 'Test firm P', 'currency_id': cls.eur.id})
        cls.env = env(context=dict(env.context, allowed_company_ids=[cls.co.id, env.company.id], rates_today=M_LAST, profit_today=M_LAST))
        env = cls.env
        # правила бонусів бази (порожні «Замовники» — усі) дали б тестовим ставкам свій бонус
        env['workset.office.bonus'].sudo().search([]).write({'active': False})
        Partner = env['res.partner']
        cls.cust1 = Partner.create({'name': 'Customer 1', 'is_company': True})
        cls.cust2 = Partner.create({'name': 'Customer 2', 'is_company': True})
        cls.vendor = Partner.create({'name': 'Fuel card vendor', 'is_company': True})
        # корінь — план проєктів (як план рекрутингу на проді: статті в полі account_id рядка аналітики); вузли — свої, під ним.
        # Плану проєктів немає (параметра analytic.project_plan немає) — свій корінь
        Plan = env['account.analytic.plan']
        project_plan = Plan.browse(int(env['ir.config_parameter'].sudo().get_param('analytic.project_plan') or 0)).exists()
        if not project_plan and hasattr(Plan, '_get_all_plans'):
            project_plan = Plan._get_all_plans()[0]
        cls.root = project_plan or Plan.create({'name': 'Test recruiting root'})
        cls.node_a = Plan.create({'name': 'Node A', 'parent_id': cls.root.id})
        cls.node_b = Plan.create({'name': 'Node B', 'parent_id': cls.root.id})
        cls.node_owners = Plan.create({'name': 'Owners', 'parent_id': cls.root.id})
        cls.node_office = Plan.create({'name': 'Office', 'parent_id': cls.root.id})
        env['ir.config_parameter'].sudo().set_param(calc.P_ROOT_PLANS, str(cls.root.id))
        Line = env['workset.line']
        cls.line_a = Line.create({'name': 'Line A', 'customer_ids': [(6, 0, [cls.cust1.id])], 'plan_ids': [(6, 0, [cls.node_a.id])]})
        cls.line_b = Line.create({'name': 'Line B', 'customer_ids': [(6, 0, [cls.cust2.id])], 'plan_ids': [(6, 0, [cls.node_b.id])]})
        Rule = env['workset.rate.rule']
        Rule.create({'line_id': cls.line_a.id, 'anchor': 'markup', 'share_line_found': 4, 'share_we_found': 5, 'car_rent': 0.5,
                     'date_from': OLD, 'source': 'owner', 'source_note': 'test'})
        Rule.create({'line_id': cls.line_b.id, 'anchor': 'markup', 'share_line_found': 4, 'share_we_found': 5, 'date_from': OLD,
                     'source': 'owner', 'source_note': 'test'})
        Housing = env['workset.line.housing']
        Housing.create({'line_id': cls.line_a.id, 'kind': 'payment_buffer', 'payer': 'we_buffer', 'date_from': OLD, 'source': 'owner',
                        'source_note': 'test'})
        Housing.create({'line_id': cls.line_b.id, 'kind': 'payment_buffer', 'payer': 'partner', 'date_from': OLD, 'source': 'owner',
                        'source_note': 'test'})
        Emp = env['hr.employee'].with_company(cls.co)
        cls.e1, cls.e2, cls.e3, cls.e4 = [Emp.create({'name': 'Worker %d' % i, 'company_id': cls.co.id}) for i in (1, 2, 3, 4)]
        Project = env['project.project'].with_company(cls.co)
        cls.p1 = Project.create({'name': 'Site 1', 'partner_id': cls.cust1.id, 'company_id': cls.co.id, 'allow_billable': True})
        cls.p2 = Project.create({'name': 'Site 2', 'partner_id': cls.cust2.id, 'company_id': cls.co.id, 'allow_billable': True})
        Rate = env['workset.rate']
        with cls._deals():
            cls.r1 = Rate.create({'person_id': cls.e1.work_contact_id.id, 'customer_id': cls.cust1.id, 'found_by': 'us', 'wage': 14,
                                  'source': 'other', 'date_from': OLD})
            cls.r2 = Rate.create({'person_id': cls.e1.work_contact_id.id, 'customer_id': cls.cust1.id, 'found_by': 'line', 'wage': 15,
                                  'source': 'other', 'date_from': '2030-03-16'})
            cls.r3 = Rate.create({'person_id': cls.e2.work_contact_id.id, 'customer_id': cls.cust2.id, 'found_by': 'us', 'wage': 14,
                                  'source': 'other', 'date_from': OLD})
        AAL = env['account.analytic.line'].with_company(cls.co)
        for emp, proj, day, hours in ((cls.e1, cls.p1, '2030-02-10', 8), (cls.e1, cls.p1, '2030-03-04', 8), (cls.e1, cls.p1, '2030-03-20', 8),
                                      (cls.e3, cls.p1, '2030-03-05', 4), (cls.e2, cls.p2, '2030-03-06', 8),
                                      (cls.e4, cls.p2, '2030-01-20', 8), (cls.e4, cls.p2, '2030-03-09', 8)):
            AAL.create({'name': '/', 'project_id': proj.id, 'employee_id': emp.id, 'date': day, 'unit_amount': hours})
        Acc = env['account.analytic.account']

        def acc(name, plan, partner=None):
            return Acc.create({'name': name, 'plan_id': plan.id, 'partner_id': partner.id if partner else False, 'company_id': False})
        cls.a_house_a = acc('Housing | Expense | Customer 1', cls.node_a, cls.cust1)
        cls.a_buffer_a = acc('Housing | Buffer | Customer 1', cls.node_a, cls.cust1)
        cls.a_cust_a = acc('Customer 1', cls.node_a, cls.cust1)
        cls.a_tool_a = acc('Tools | Expense | Customer 1', cls.node_a, cls.cust1)
        cls.a_house_b = acc('Housing | Expense | Customer 2', cls.node_b, cls.cust2)
        cls.a_owner = acc('Owner X', cls.node_owners)
        cls.a_transport = acc('Transport', cls.node_office)
        cls.a_misc = acc('Інструмент різне', cls.root)
        Account = env['account.account'].with_company(cls.co)
        cls.g_exp = Account.create({'name': 'Test expense', 'code': '699901', 'account_type': 'expense'})
        cls.g_fuel = Account.create({'name': 'Test payroll fuel', 'code': '699902', 'account_type': 'expense'})
        cls.g_income = Account.create({'name': 'Test income', 'code': '799901', 'account_type': 'income'})
        cls.g_payable = Account.create({'name': 'Test payable', 'code': '499901', 'account_type': 'liability_current'})
        cls.journal = env['account.journal'].with_company(cls.co).create({'name': 'Test misc P', 'code': 'TPRF', 'type': 'general',
                                                                           'company_id': cls.co.id})
        cls.bill_cov = cls._bill(17.7)
        cls.bill_unc = cls._bill(4)
        cls._aal(cls.a_house_a, -10, '2030-03-10', cls.g_exp)
        cls._aal(cls.a_house_b, -20, '2030-03-10', cls.g_exp)
        cls._aal(cls.a_buffer_a, 15, '2030-03-11', cls.g_income)
        cls._aal(cls.a_cust_a, 100, '2030-03-15', cls.g_income)
        cls._aal(cls.a_cust_a, -6, '2030-03-31', cls.g_fuel)
        cls._aal(cls.a_tool_a, -3, '2030-03-18', cls.g_exp)
        cls._aal(cls.a_owner, -10, '2030-03-05', cls.g_exp)
        cls._aal(cls.a_transport, -17.7, '2030-03-15', cls.g_exp, cls.vendor, cls.bill_cov)
        cls._aal(cls.a_transport, -4, '2030-03-30', cls.g_exp, cls.vendor, cls.bill_unc)
        cls._aal(cls.a_misc, -2.5, '2030-03-20', cls.g_exp)
        Map = env['workset.profit.analytic.map']
        Map.create({'analytic_id': cls.a_house_a.id, 'kind': 'housing'})
        Map.create({'analytic_id': cls.a_house_b.id, 'kind': 'housing'})
        Map.create({'analytic_id': cls.a_buffer_a.id, 'kind': 'housing_buffer'})
        Map.create({'analytic_id': cls.a_tool_a.id, 'kind': 'tool'})
        Map.create({'plan_id': cls.node_a.id, 'kind': 'ignore', 'note': 'виручка й зарплата — у частці ставок'})
        Map.create({'account_id': cls.g_fuel.id, 'kind': 'fuel', 'note': 'паливо в зарплаті: рахунок точніший за вузол'})
        Map.create({'plan_id': cls.node_owners.id, 'kind': 'owner'})
        Map.create({'partner_id': cls.vendor.id, 'kind': 'dkv_bill'})
        Cost = env['workset.line.cost.rule']
        Cost.create({'line_id': cls.line_a.id, 'kind': 'fuel', 'payer': 'worker_fund', 'date_from': OLD})
        Cost.create({'line_id': cls.line_b.id, 'kind': 'transport', 'payer': 'partner', 'date_from': OLD})
        Card = env['workset.dkv.card']
        cls.card_a = Card.create({'number': 'TEST-CARD-A', 'seq': 1, 'period_ids': [(0, 0, {'target': 'line', 'line_id': cls.line_a.id, 'date_from': OLD})]})
        cls.card_office = Card.create({'number': 'TEST-CARD-O', 'seq': 2, 'period_ids': [(0, 0, {'target': 'office', 'date_from': OLD})]})
        cls.card_tr = Card.create({'number': 'TEST-CARD-T', 'seq': 3, 'period_ids': [(0, 0, {'target': 'transport', 'date_from': OLD})]})
        cls.card_none = Card.create({'number': 'TEST-CARD-N', 'seq': 4})
        for card, day, gross in ((cls.card_a, '2030-03-08', 4.5), (cls.card_office, '2030-03-10', 3), (cls.card_tr, '2030-03-12', 9),
                                 (cls.card_none, '2030-03-14', 1.2)):
            env['workset.dkv.txn'].create({'card_id': card.id, 'date': day, 'amount_gross': gross, 'amount_net': gross, 'city': 'City',
                                           'move_id': cls.bill_cov.id, 'source': 'pdf'})
        cls.Month = env['workset.profit.month']

    @classmethod
    def _bill(cls, amount, day='2030-03-15', ref=False):
        # чернетка проводки — лише щоб рядок аналітики мав рахунок (move_line_id → move_id), до якого прив'язані транзакції DKV
        return cls.env['account.move'].with_company(cls.co).create({
            'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day, 'partner_id': cls.vendor.id, 'ref': ref,
            'line_ids': [(0, 0, {'account_id': cls.g_exp.id, 'debit': amount, 'credit': 0, 'name': 'fuel'}),
                         (0, 0, {'account_id': cls.g_payable.id, 'debit': 0, 'credit': amount, 'name': 'fuel'})]})

    @classmethod
    def _aal(cls, account, amount, day, general, partner=None, move=None):
        ml = move.line_ids.filtered(lambda x: x.account_id == cls.g_exp)[:1] if move else None
        return cls.env['account.analytic.line'].with_company(cls.co).create({
            'name': 'test', 'account_id': account.id, 'amount': amount, 'date': day, 'general_account_id': general.id,
            'partner_id': partner.id if partner else False, 'move_line_id': ml.id if ml else False, 'company_id': cls.co.id})

    @classmethod
    def _deals(cls):
        # фонд працівників лінії A (замовник Customer 1) — 1 за годину; посередників немає
        deals = [{'id': 1, 'kind': 'worker_fund', 'rate': 1.0, 'rate_full': None, 'state': 'active', 'customer_ids': [cls.cust1.id],
                  'line_id': 0, 'date_from': '', 'date_to': '', 'agent': 'Test fund'}]
        return _Both(patch.object(agent_deals, 'read_deals', return_value=deals), patch.object(agent_deals, 'read_people', return_value=[]))

    def _run(self, date_from=M, date_to=M_LAST, model=None):
        # model — модель від імені іншого користувача (порожній набір записів — «хибний», тому не «or»)
        with self._deals():
            return (self.Month if model is None else model).profit_recompute(date_from, date_to)

    def _rows(self, month=M, **kw):
        dom = [('month', '=', month)] + [(k, '=', v) for k, v in kw.items()]
        return self.Month.search(dom)

    # ——— години, частка, оренда авто, фонд ———
    def test_rate_change_inside_month_and_hours_without_rate(self):
        self._run()
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        self.assertEqual(len(a), 1)
        self.assertEqual((a.hours_total, a.hours_rate, a.hours_no_rate), (20, 16, 4), 'години без ставки — окремо, не вгадуються')
        self.assertAlmostEqual(a.share, 48, msg='8 год × 3,5 (ставка до 16-го) + 8 год × 2,5 (нова ставка з 16-го)')
        b = self._rows(row_kind='line', line_id=self.line_b.id)
        self.assertEqual((b.hours_total, b.hours_rate, b.hours_no_rate), (16, 8, 8))
        self.assertAlmostEqual(b.share, 40)

    def test_car_rent_and_worker_fund(self):
        self._run()
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        self.assertAlmostEqual(a.car_rent, 8, msg='оренда авто 0,5 × 16 год — наш дохід, у «чисто»')
        self.assertAlmostEqual(a.fund_in, 16, msg='фонд 1 × 16 год — у колонку фонду, не наше')
        self.assertAlmostEqual(a.fund_paid, -10.5, msg='паливо в зарплаті −6 і картка лінії −4,5 — з фонду за «Хто платить»')
        self.assertAlmostEqual(a.fund_left, 5.5)
        self.assertAlmostEqual(a.fuel, 0, msg='паливо фонду не в нашому «чисто»')

    # ——— житло, буфер, чисто ———
    def test_housing_we_vs_partner_and_buffer_not_in_net(self):
        self._run()
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        b = self._rows(row_kind='line', line_id=self.line_b.id)
        self.assertAlmostEqual(a.housing, -10, msg='житло лінії A платимо ми — у «чисто»')
        self.assertAlmostEqual(b.housing, 0)
        self.assertAlmostEqual(b.partner_paid, -26, msg='житло −20 і перевезення −6 лінії B платить партнер — «з грошей лінії»')
        self.assertAlmostEqual(a.housing_buffer, 15, msg='буфер житла — довідково')
        self.assertAlmostEqual(a.tool, -3)
        self.assertAlmostEqual(a.net, 40, msg='48 + 8 − 10 − 3 − 3: буфер, фонд і виручка (ігнор) не входять')
        self.assertAlmostEqual(a.net_per_hour, 2.5, msg='чисто ÷ години зі ставкою')
        self.assertAlmostEqual(b.net, 40)
        self.assertAlmostEqual(b.net_per_hour, 5)

    # ——— DKV і перевезення ———
    def test_dkv_card_line_office_transport_by_arrivals(self):
        self._run()
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        self.assertAlmostEqual(a.transport, -3, msg='9 за прибуттями: A — 1 прибуття, B — 2')
        office = self._rows(row_kind='office', card_id=self.card_office.id)
        self.assertAlmostEqual(office.amount, -3)
        self.assertIn('Офіс', office.name)
        none = self._rows(row_kind='unallocated', card_id=self.card_none.id)
        self.assertEqual((none.tag, none.amount), ('dkv_card', -1.2), 'картка без періоду — «Не розподілено»')
        dkv = self._rows(row_kind='dkv')
        self.assertAlmostEqual(dkv.amount, -17.7, msg='рахунок DKV із транзакціями — окремо, не вдруге')
        self.assertAlmostEqual(dkv.net, 0)
        unc = self._rows(row_kind='unallocated', tag='dkv_bill')
        self.assertAlmostEqual(unc.amount, -4, msg='рахунок DKV без транзакцій — «Не розподілено: DKV»')
        self.assertIn('DKV', unc.name)

    def test_transport_without_arrivals_unallocated(self):
        self.env['workset.dkv.txn'].create({'card_id': self.card_tr.id, 'date': '2030-04-05', 'amount_gross': 5, 'source': 'api'})
        res = self._run(APR, APR)
        row = self._rows(APR, row_kind='unallocated', tag='transport_none')
        self.assertAlmostEqual(row.amount, -5, msg='прибуттів у квітні немає — «Перевезення без прибуттів»')
        self.assertIn('прибуттів', row.name)
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])

    # ——— компанія: власник, без розмітки, ігнор ———
    def test_owner_block_and_unmapped_with_hint(self):
        self._run()
        owner = self._rows(row_kind='owner')
        self.assertEqual((owner.analytic_id, owner.amount), (self.a_owner, -10), 'вузол Owners — блок «Власник» за статтею')
        self.assertIn('Owner X', owner.name)
        self.assertFalse(owner.line_id)
        misc = self._rows(row_kind='unallocated', analytic_id=self.a_misc.id)
        self.assertEqual((misc.amount, misc.hint_kind), (-2.5, 'tool'), 'без розмітки — «Не розподілено» з підказкою, не лінія')
        self.assertIn('підказка', misc.name)
        ignore = self._rows(row_kind='ignore')
        self.assertEqual((ignore.amount, ignore.net), (100, 0), 'виручка (вузол «ігнор») — у звірці, не в «чисто»')

    # ——— звірка ———
    def test_verify_matches_and_catches_mismatch(self):
        res = self._run()
        check = res['verify'][0]
        self.assertTrue(check['ok'], check['text'])
        self.assertEqual((check['aal'], check['dkv']), (41.8, -17.7))
        self.assertIn('до цента', check['text'])
        self._aal(self.a_misc, -7, '2030-03-21', self.g_exp)  # з'явилось після перерахунку
        bad = self.Month._verify(M)
        self.assertFalse(bad['ok'])
        self.assertIn('34,80', bad['text'])
        self.assertIn('7,00', bad['text'])
        self._run()
        self.assertTrue(self.Month._verify(M)['ok'], 'після перерахунку знову сходиться')
        row = self._rows(row_kind='line', line_id=self.line_a.id)
        row.sudo().write({'housing': row.housing - 1})
        broken = self.Month._verify(M)
        self.assertFalse(broken['ok'])
        self.assertIn('Line A', broken['text'], 'рядок, де стовпці не дають суми, — названо')

    def test_recompute_replaces_month_and_cron_months(self):
        self._run()
        n = self.Month.search_count([('month', '=', M)])
        self._run()
        self.assertEqual(self.Month.search_count([('month', '=', M)]), n, 'перерахунок місяць заміщає, не множить')
        with self._deals():
            res = self.Month.with_context(profit_today='2030-04-15')._cron_recompute()
        self.assertEqual(res['months'], ['2030-02-01', M, APR], 'плановий — 3 останні місяці')
        feb = self._rows('2030-02-01', row_kind='line', line_id=self.line_a.id)
        self.assertEqual(feb.hours_rate, 8)
        self.assertFalse(self._rows('2030-01-01'), 'січень плановий не чіпає')
        cron = self.env.ref('workset_profit.cron_recompute')
        self.assertTrue(cron.active)
        self.assertEqual(cron.code.strip(), 'model._cron_recompute()')

    # ——— правила й заборони ———
    def test_rules_validation(self):
        Cost = self.env['workset.line.cost.rule']
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Cost.create({'line_id': self.line_a.id, 'kind': 'fuel', 'payer': 'we', 'date_from': '2030-01-01'})
        Cost.create({'line_id': self.line_a.id, 'customer_id': self.cust1.id, 'kind': 'fuel', 'payer': 'we', 'date_from': '2030-01-01'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.profit.analytic.map'].create({'kind': 'fuel'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.profit.analytic.map'].create({'analytic_id': self.a_house_a.id, 'kind': 'auto'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.dkv.card.period'].create({'card_id': self.card_a.id, 'target': 'office', 'date_from': '2030-01-01'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.dkv.card.period'].create({'card_id': self.card_none.id, 'target': 'line', 'date_from': '2030-01-01'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['workset.dkv.card'].create({'number': 'TEST-CARD-A'})

    def test_customer_rule_beats_line_rule(self):
        # правило замовника лінії точніше за правило лінії: паливо Customer 1 — платимо ми
        self.env['workset.line.cost.rule'].create({'line_id': self.line_a.id, 'customer_id': self.cust1.id, 'kind': 'fuel', 'payer': 'we',
                                                   'date_from': OLD})
        self._run()
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        self.assertAlmostEqual(a.fuel, -6, msg='паливо в зарплаті на статті Customer 1 — наше')
        self.assertAlmostEqual(a.fund_paid, -4.5, msg='картка лінії без замовника в періоді — за правилом лінії (фонд)')

    # ——— права ———
    def test_rights(self):
        viewer = new_test_user(self.env, login='ws_profit_viewer', groups='base.group_user,workset_profit.group_viewer')
        editor = new_test_user(self.env, login='ws_profit_editor', groups='base.group_user,workset_profit.group_editor')
        plain = new_test_user(self.env, login='ws_profit_plain', groups='base.group_user')

        def as_user(rec, user):
            return rec.with_user(user).with_context(allowed_company_ids=user.company_ids.ids)
        self._run()
        self.assertTrue(as_user(self.Month, viewer).search_count([('month', '=', M)]), 'перегляд бачить підсумок')
        self.assertTrue(as_user(self.Month, viewer).search_read([('month', '=', M), ('row_kind', '=', 'line')], ['name', 'line_id', 'net']))
        with self.assertRaises(AccessError):
            as_user(self.Month, plain).search_count([])
        self.assertTrue(as_user(self.Month, viewer).profit_verify(M, M_LAST)[0]['ok'], 'звірку (лише читання) може й перегляд')
        with self.assertRaises(AccessError):
            as_user(self.Month, plain).profit_verify(M, M_LAST)
        with self.assertRaises(AccessError):
            as_user(self.Month, viewer).profit_recompute(M, M_LAST)
        with self.assertRaises(AccessError):
            as_user(self._rows(row_kind='line', line_id=self.line_a.id), editor).write({'net': 1})
        with self.assertRaises(AccessError):
            as_user(self.env['workset.line.cost.rule'], viewer).create({'line_id': self.line_a.id, 'kind': 'tool', 'payer': 'partner',
                                                                       'date_from': OLD})
        rule = as_user(self.env['workset.line.cost.rule'], editor).create({'line_id': self.line_a.id, 'kind': 'tool', 'payer': 'partner',
                                                                          'date_from': OLD})
        self.assertTrue(rule.id, 'редактор править «Хто платить»')
        res = self._run(model=as_user(self.Month, editor))
        self.assertTrue(res['verify'][0]['ok'], 'редактор перераховує (sudo лише всередині перерахунку)')
        self.assertAlmostEqual(self._rows(row_kind='line', line_id=self.line_a.id).partner_paid, -3, msg='інструмент лінії A тепер — партнер')

    # ——— круг 1б ———
    def test_service_period_split_and_deferral_moves(self):
        # рядок рахунку з періодом послуги 16.06–15.07: ділиться навпіл; у рахунку є проводки відстрочки Odoo — не ділиться (вони вже
        # розносять суму своїми датами, поділ дав би подвійний рахунок); параметр «ділити за періодом» = ні — не ділиться
        jun = '2030-06-01'
        mv = self._bill(60, '2030-06-10')
        line = self._aal(self.a_house_a, -60, '2030-06-10', self.g_exp, None, mv)
        Model = type(self.Month)
        period = {line.move_line_id.id: ('2030-06-16', '2030-07-15')}
        with patch.object(Model, '_service_periods', lambda me, mls: period):
            self._run(jun, jun)
            split = self._rows(jun, row_kind='line', line_id=self.line_a.id).housing
            check = self.Month._verify(jun)
            with patch.object(Model, '_deferral_moves', lambda me, moves: {mv.id}):
                self._run(jun, jun)
                deferral = self._rows(jun, row_kind='line', line_id=self.line_a.id).housing
            self.env['ir.config_parameter'].sudo().set_param(calc.P_SPLIT, 'ні')
            self._run(jun, jun)
            off = self._rows(jun, row_kind='line', line_id=self.line_a.id).housing
        self.assertEqual((split, deferral, off), (-30, -60, -60))
        self.assertTrue(check['ok'], check['text'])
        self.assertIn('переніс', check['text'], 'звірка називає, скільки періоди послуги перенесли між місяцями')

    def test_dkv_bill_difference_and_txns_without_bill(self):
        # липень: рахунок DKV 20, транзакцій завантажено на 15 (частково) — різниця 5 «Не розподілено: DKV різниця»; рахунок 4 без поля
        # «Рахунок постачальника», але номер рахунку DKV = «Референс» — покритий; рахунок 3 без транзакцій; транзакція API без рахунку — по
        # лінії й текстом у звірці (разом із рахунком без транзакцій — попередження про можливий подвійний рахунок)
        jul = '2030-07-01'
        part = self._bill(20, '2030-07-15')
        by_no = self._bill(4, '2030-07-16', ref='DKV-TEST-77')
        bare = self._bill(3, '2030-07-17')
        self._aal(self.a_transport, -20, '2030-07-15', self.g_exp, self.vendor, part)
        self._aal(self.a_transport, -4, '2030-07-16', self.g_exp, self.vendor, by_no)
        self._aal(self.a_transport, -3, '2030-07-17', self.g_exp, self.vendor, bare)
        Txn = self.env['workset.dkv.txn']
        Txn.create({'card_id': self.card_a.id, 'date': '2030-07-08', 'amount_gross': 6, 'move_id': part.id, 'source': 'pdf'})
        Txn.create({'card_id': self.card_office.id, 'date': '2030-07-10', 'amount_gross': 9, 'move_id': part.id, 'source': 'pdf'})
        Txn.create({'card_id': self.card_office.id, 'date': '2030-07-21', 'amount_gross': 4, 'invoice_no': 'DKV-TEST-77', 'source': 'api'})
        Txn.create({'card_id': self.card_a.id, 'date': '2030-07-20', 'amount_gross': 2, 'source': 'api'})
        res = self._run(jul, jul)['verify'][0]
        self.assertAlmostEqual(self._rows(jul, row_kind='dkv').amount, -19, msg='рахунок 20 мінус різниця 5 і рахунок 4 за номером')
        diff = self._rows(jul, row_kind='unallocated', tag='dkv_diff')
        self.assertEqual((diff.amount, diff.analytic_id), (-5, self.a_transport), 'часткове завантаження — різниця видна, не губиться')
        self.assertIn('DKV різниця', diff.name)
        self.assertAlmostEqual(self._rows(jul, row_kind='unallocated', tag='dkv_bill').amount, -3)
        self.assertAlmostEqual(self._rows(jul, row_kind='line', line_id=self.line_a.id).fund_paid, -8, msg='картка лінії: 6 + 2 (без рахунку)')
        self.assertAlmostEqual(self._rows(jul, row_kind='office', card_id=self.card_office.id).amount, -13)
        self.assertTrue(res['ok'], res['text'])
        self.assertIn('без рахунку в Odoo: 1, сума 2,00', res['text'], 'транзакція за номером рахунку — не «без рахунку»')
        self.assertIn('подвійний рахунок', res['text'])

    def test_verify_independent_sum_catches_lost_line(self):
        # окремий підрахунок аналітики за датою (_read_group) бачить рядок, який розклад загубив: звірка «рядки = розклад» його не бачить
        self._run()
        lost = self._aal(self.a_misc, -7, '2030-03-21', self.g_exp)
        orig = type(self.Month)._analytic_items

        def drop(me, first, last):
            return [i for i in orig(me, first, last) if i['aal_id'] != lost.id]
        with patch.object(type(self.Month), '_analytic_items', drop):
            res = self.Month._verify(M)
        self.assertEqual((res['aal'], res['aal_expected']), (41.8, 41.8), 'рядки й розклад сходяться — загублене не видно')
        self.assertFalse(res['ok'])
        self.assertEqual([(x['currency'], x['books'], x['calc']) for x in res['raw_bad']], [('EUR', 34.8, 41.8)])
        self.assertIn('окремий підрахунок', res['text'])
        self.assertEqual(self.Month.profit_verify(M, M_LAST)[0]['raw_bad'], [], 'без підміни окремий підрахунок сходиться')

    def test_arrivals_glue_cards_of_one_person(self):
        # людина з двома картками у двох фірмах («Worker 5» і «Worker 5 | PA»): у лютому — картка іншої фірми, у березні — ця; розрив 10
        # днів — не нове прибуття. Без склейки прибуттів у A стало б 2 і перевезення A — 4,5
        co2 = self.env['res.company'].create({'name': 'Test firm P2', 'currency_id': self.eur.id})
        env = self.env(context=dict(self.env.context, allowed_company_ids=list(self.env.context['allowed_company_ids']) + [co2.id]))
        e5 = env['hr.employee'].with_company(self.co).create({'name': 'Worker 5', 'company_id': self.co.id})
        e5b = env['hr.employee'].with_company(co2).create({'name': 'Worker 5 | PA', 'company_id': co2.id})
        p1b = env['project.project'].with_company(co2).create({'name': 'Site 1b', 'partner_id': self.cust1.id, 'company_id': co2.id,
                                                               'allow_billable': True})
        env['account.analytic.line'].with_company(co2).create({'name': '/', 'project_id': p1b.id, 'employee_id': e5b.id, 'date': '2030-02-25',
                                                               'unit_amount': 8})
        env['account.analytic.line'].with_company(self.co).create({'name': '/', 'project_id': self.p1.id, 'employee_id': e5.id,
                                                                   'date': '2030-03-07', 'unit_amount': 4})
        self.assertNotEqual(e5.work_contact_id, e5b.work_contact_id, 'контакти різні — склеює ім\'я без приписки фірми')
        self._run()
        self.assertAlmostEqual(self._rows(row_kind='line', line_id=self.line_a.id).transport, -3)

    def test_housing_deducted_by_customer(self):
        # травень: замовник лінії A вираховує житло 30 за людину на місяць; людей із годинами — 2 (працівник 1 зі ставкою, 3 — без)
        may = '2030-05-01'
        self.env['workset.line.housing'].create({'line_id': self.line_a.id, 'customer_id': self.cust1.id, 'kind': 'customer_deducts', 'amount': 30,
                                                 'amount_unit': 'month', 'payer': 'customer', 'date_from': may, 'source': 'owner',
                                                 'source_note': 'test'})
        AAL = self.env['account.analytic.line'].with_company(self.co)
        for emp, day, hours in ((self.e1, '2030-05-05', 8), (self.e1, '2030-05-07', 8), (self.e3, '2030-05-06', 4)):
            AAL.create({'name': '/', 'project_id': self.p1.id, 'employee_id': emp.id, 'date': day, 'unit_amount': hours})
        res = self._run(may, may)
        a = self._rows(may, row_kind='line', line_id=self.line_a.id)
        self.assertAlmostEqual(a.housing_customer, -60, msg='30 × 2 людей з годинами в місяці в цього замовника')
        self.assertAlmostEqual(a.housing, 0)
        self.assertAlmostEqual(a.net, -12, msg='частка 16 × 2,5 + оренда авто 8 − житло, яке вирахував замовник, 60')
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])


class _Both:
    """Два patch.object одним with (у setUpClass і тестах)."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def __enter__(self):
        self.a.__enter__()
        self.b.__enter__()
        return self

    def __exit__(self, *exc):
        self.b.__exit__(*exc)
        self.a.__exit__(*exc)
        return False
