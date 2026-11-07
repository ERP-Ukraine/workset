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
  транзакцій −4, стаття без розмітки −2,5 (підказка «інструмент»);
- круг 4: картка офісу −3 — загальні фірм березня, діляться за годинами табеля (A 20, B 16); рахунки ZUS / PIT-4 — лише в своєму тесті."""
import re
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

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

    def _housing_by_customer(self, unit):
        # травень: замовник лінії A вираховує житло 30 в одиниці unit; людей із годинами — 2 (працівник 1 зі ставкою, 3 — без)
        may = '2030-05-01'
        self.env['workset.line.housing'].create({'line_id': self.line_a.id, 'customer_id': self.cust1.id, 'kind': 'customer_deducts', 'amount': 30,
                                                 'amount_unit': unit, 'payer': 'customer', 'date_from': may, 'source': 'owner',
                                                 'source_note': 'test'})
        AAL = self.env['account.analytic.line'].with_company(self.co)
        for emp, day, hours in ((self.e1, '2030-05-05', 8), (self.e1, '2030-05-07', 8), (self.e3, '2030-05-06', 4)):
            AAL.create({'name': '/', 'project_id': self.p1.id, 'employee_id': emp.id, 'date': day, 'unit_amount': hours})
        res = self._run(may, may)
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])
        return self._rows(may, row_kind='line', line_id=self.line_a.id)

    def test_housing_deducted_by_customer(self):
        # «€ за місяць (об'єкт)» (month) — одна сума за місяць на «лінія × замовник», не з кожної людини (calc.MONTH_UNIT = 'object')
        a = self._housing_by_customer('month')
        self.assertAlmostEqual(a.housing_customer, -30, msg="month — на об'єкт: 30 раз за місяць, хоч людей 2")
        self.assertAlmostEqual(a.housing, 0)
        self.assertAlmostEqual(a.net, 18, msg='частка 16 × 2,5 + оренда авто 8 − житло, яке вирахував замовник, 30')

    def test_housing_deducted_by_customer_per_person(self):
        # «€ за місяць за особу» (month_person, «Ставки» 06.11) — 30 з кожної людини з годинами в місяці. Значення є лише в «Ставках» із ним
        field = self.env['workset.line.housing']._fields['amount_unit']
        if 'month_person' not in dict(field._description_selection(self.env)):
            self.skipTest('у «Ставках» цієї збірки ще немає одиниці month_person')
        a = self._housing_by_customer('month_person')
        self.assertAlmostEqual(a.housing_customer, -60, msg='30 × 2 людей з годинами в місяці в цього замовника')
        self.assertAlmostEqual(a.net, -12)

    def test_housing_rule_kind_none_goes_to_fund_or_partner(self):
        # правило житла «Житло не наше» (kind none) з «Хто платить» = фонд працівників / партнер: витрата житла лінії — у «З фонду» / «З
        # грошей лінії», не в наше «чисто» (правила копії: лінія офісу — фонд, лінія партнера — партнер)
        Housing = self.env['workset.line.housing']
        Housing.search([('line_id', 'in', [self.line_a.id, self.line_b.id])]).write({'date_to': '2030-03-31'})
        Housing.create({'line_id': self.line_a.id, 'kind': 'none', 'payer': 'worker_fund', 'date_from': APR, 'source': 'owner', 'source_note': 'test'})
        Housing.create({'line_id': self.line_b.id, 'kind': 'none', 'payer': 'partner', 'date_from': APR, 'source': 'owner', 'source_note': 'test'})
        self._aal(self.a_house_a, -12, '2030-04-10', self.g_exp)
        self._aal(self.a_house_b, -7, '2030-04-10', self.g_exp)
        AAL = self.env['account.analytic.line'].with_company(self.co)
        AAL.create({'name': '/', 'project_id': self.p1.id, 'employee_id': self.e1.id, 'date': '2030-04-03', 'unit_amount': 8})
        AAL.create({'name': '/', 'project_id': self.p2.id, 'employee_id': self.e2.id, 'date': '2030-04-03', 'unit_amount': 8})
        res = self._run(APR, '2030-04-30')
        a = self._rows(APR, row_kind='line', line_id=self.line_a.id)
        b = self._rows(APR, row_kind='line', line_id=self.line_b.id)
        self.assertEqual((a.housing, a.fund_paid), (0, -12), 'фонд працівників — «З фонду», не наше житло')
        self.assertEqual((b.housing, b.partner_paid), (0, -7), 'партнер — «З грошей лінії»')
        self.assertAlmostEqual(a.net, 8 * 2.5 + 8 * 0.5, msg='у «чисто» житла немає')
        self.assertTrue(res['verify'][0]['ok'], res['verify'][0]['text'])
        with self._deals():
            card = self.Month.with_context(profit_today='2030-05-15').profit_line_card(self.line_a.id, APR)
        self.assertNotIn('housing', [s['key'] for s in card['waterfall']['bars']], 'нульового кроку «Житло» у водоспаді немає')
        self.assertEqual([x['amount'] for x in card['aside'] if x['key'] == 'fund_paid'], [-12])
        self.assertIn('фонд', [p for p in card['pays'] if p['key'] == 'housing'][0]['payer'].lower())

    # ——— круг 2: екрани ———
    def _screen_rows(self):
        # синтетичні рядки підсумку (пише лише код — тут суперкористувач): 2025-12 (до 2026 — не показуються), травень–червень 2030
        # лінії A — чисто на годину падає з 10 до 7 (жовте), липень — чисто нижче нуля (червоне)
        Month = self.Month.sudo()
        line = {'row_kind': 'line', 'line_id': self.line_a.id, 'name': 'Line A'}
        for month, hours, share, net in (('2025-12-01', 10, 50, 50), ('2030-05-01', 10, 120, 100), ('2030-06-01', 10, 90, 70),
                                         ('2030-07-01', 10, 30, -20)):
            Month.create(dict(line, month=month, hours_total=hours, hours_rate=hours, share=share, housing=net - share, net=net,
                              net_per_hour=net / hours, aal_amount=net - share))

    def test_screen_overview_sums_match_month_rows(self):
        self._run()
        rows = self._rows(row_kind='line')
        with self._deals():
            data = self.Month.with_context(profit_today='2030-04-20').profit_overview(M)
        self.assertEqual(data['month']['value'], M)
        tiles = {t['id']: t for t in data['lines']}
        self.assertEqual(set(tiles), {self.line_a.id, self.line_b.id})
        for r in rows:
            t = tiles[r.line_id.id]
            self.assertEqual((t['net'], t['net_ph'], t['hours']), (r.net, r.net_per_hour, r.hours_total), 'плитка = рядок підсумку')
            self.assertAlmostEqual(t['share_ph'], round(r.share / r.hours_rate, 2))
        tot = data['totals']
        self.assertEqual((tot['net'], tot['hours'], tot['hours_rate']), (sum(rows.mapped('net')), sum(rows.mapped('hours_total')),
                                                                          sum(rows.mapped('hours_rate'))))
        self.assertAlmostEqual(tot['share'] + tot['costs'], tot['net'], msg='частка + витрати = чисто')
        alerts = {a['key']: a for a in data['alerts']}
        un = self._rows(row_kind='unallocated')
        self.assertAlmostEqual(alerts['unallocated']['amount'], sum(un.mapped('net')))
        self.assertEqual(self.Month.search_count(alerts['unallocated']['open']['domain']), len(un), 'число на чипі — рядки списку')
        self.assertEqual(alerts['no_rate']['hours'], 12, 'години без ставки: A 4, B 8')
        self.assertEqual(alerts['check']['state'], 'ok', 'звірка з «Звірки місяця», яку записав перерахунок')
        # круг 3а: власники — один нейтральний чип (сума й кожна стаття), клік — рядки власників місяця
        owners = alerts['owners']
        self.assertEqual(([(p['code'], p['amount']) for p in owners['parts']], owners['amount'], owners['tone']), ([('Owner X', -10)], -10, ''))
        self.assertEqual(self.Month.search(owners['open']['domain']).mapped('row_kind'), ['owner'])
        self.assertNotIn('owner', alerts, 'чипа на кожну статтю власника більше немає')
        nets = [t['net_ph'] for t in data['lines']]
        self.assertEqual(nets, sorted(nets), 'плитки — від збиткових (за чисто € год)')
        self.assertNotIn('2030-04-01', [m['value'] for m in data['series']['months']], 'графік — без поточного (неповного) місяця')
        self.assertEqual(set(data['series']['axis']), {'min', 'max', 'out'})
        split = tiles[self.line_a.id]['split']
        self.assertAlmostEqual(sum(p['pct'] for p in split), 100, places=6)
        self.assertEqual([p['key'] for p in split], ['net', 'housing', 'tool', 'transport'])
        self.assertEqual(data['split_keys'], calc.split_legend([t['split'] for t in data['lines']]), 'легенда — частини з помітним сегментом (круг 3б)')
        self.assertIsInstance(alerts['no_rate']['hours'], int, 'години на чипі — цілі')
        self.assertEqual(data['series']['months'][0]['value'], '2026-01-01', 'ряди — з січня 2026')
        i = [m['value'] for m in data['series']['months']].index(M)
        self.assertAlmostEqual(data['series']['total'][i], round(sum(rows.mapped('net')) / sum(rows.mapped('hours_rate')), 2))
        with self._deals():
            default = self.Month.with_context(profit_today='2030-04-20').profit_overview()
        self.assertEqual(default['month']['value'], M, 'за замовчуванням — останній закритий місяць')

    def test_screen_line_card_chain_closes_to_net(self):
        self._run()
        row = self._rows(row_kind='line', line_id=self.line_a.id)
        with self._deals():
            card = self.Month.with_context(profit_today='2030-04-20').profit_line_card(self.line_a.id, M)
        bars = card['waterfall']['bars']
        # круг 4: після «чисто» — − Загальні фірм (картка офісу −3 за годинами) → = Залишається; ZUS у березні немає — кроку немає
        self.assertEqual([b['key'] for b in bars], ['share', 'car_rent', 'housing', 'tool', 'transport', 'net', 'office', 'remain'],
                         'нульових кроків немає (паливо лінії A — з фонду, ZUS немає)')
        net_i = [b['key'] for b in bars].index('net')
        self.assertAlmostEqual(sum(b['amount'] for b in bars[:net_i]), bars[net_i]['amount'], msg='ланцюжок сходиться до «чисто»')
        self.assertEqual(bars[net_i]['amount'], row.net)
        self.assertAlmostEqual(bars[net_i]['amount'] + bars[net_i + 1]['amount'], bars[-1]['amount'], msg='чисто − загальні = залишається')
        self.assertEqual(bars[-1]['amount'], row.remain)
        self.assertEqual(card['totals']['net_ph'], row.net_per_hour)
        rows = card['rows']
        chain = sum(r['amount'] for r in rows if r['chain'])
        self.assertAlmostEqual(chain, row.net - row.share, msg='рядки ланцюжка = витрати лінії: оренда 8, житло −10, інструмент −3, перевезення −3')
        house = [r for r in rows if r['title'] == self.a_house_a.display_name]
        self.assertEqual(len(house), 1)
        self.assertEqual(self.env['account.analytic.line'].search(house[0]['open']['domain']).mapped('amount'), [-10],
                         'рядок відкриває свої записи аналітики')
        ts = rows[-1]
        self.assertTrue(ts.get('timesheets'))
        self.assertAlmostEqual(sum(self.env['account.analytic.line'].search(ts['open']['domain']).mapped('unit_amount')), row.hours_total,
                               msg='табелі лінії = «Години» картки')
        pays = {p['key']: p for p in card['pays']}
        self.assertEqual(set(pays), {'housing', 'auto', 'fuel', 'tool', 'transport'})
        self.assertEqual(pays['fuel']['cards'], [self.card_a.name], 'паливо — і картки DKV лінії')
        # круг 3а: «Хто платить» згорнуто — рядок більшості й лише винятки; години без ставки лінії — чип зі списком
        lang = self.Month._lang()
        fold = card['pays_fold']
        self.assertEqual(fold['head'], calc.t('p_all_we', lang), 'житло (буфер — ми), авто, інструмент, перевезення — ми: одним рядком')
        self.assertEqual([r['label'] for r in fold['rows']], [calc.t('s_fuel', lang)], 'нижче — лише паливо (фонд працівників)')
        self.assertEqual(fold['cards'], [self.card_a.name])
        self.assertEqual(card['no_rate']['hours'], 4, 'години без ставки лінії A')
        self.assertEqual(self.Month.search(card['no_rate']['open']['domain']), row, 'клік — рядок лінії з годинами без ставки')
        self.assertEqual({x['key'] for x in card['aside']}, {'fund_in', 'fund_paid', 'fund_left', 'housing_buffer'})
        # круг 3б: місяць усередині фрази («Рядки за березень 2030») — зі строчної, кнопка — з заглавної; буфер у правому блоці — свій підпис
        self.assertEqual(card['month']['title_in'], calc.month_info(M, lang)['label'])
        self.assertTrue(card['month']['title_in'][0].islower() and card['month']['title'][0].isupper(), card['month'])
        self.assertEqual(card['month']['title_in'][1:], card['month']['title'][1:])
        self.assertEqual([x['label'] for x in card['aside'] if x['key'] == 'housing_buffer'], [calc.t('b_housing_buffer', lang)])
        self.assertEqual((card['prev']['value'], card['next']['value']), ('2030-02-01', APR))
        self.assertFalse(card['stale'], 'одразу після перерахунку рядки = ланцюжку: попередження немає')
        self.assertTrue(card['recomputed'])
        self._aal(self.a_house_a, -4, '2030-03-12', self.g_exp)  # житло лінії A з'явилось після перерахунку
        with self._deals():
            later = self.Month.with_context(profit_today='2030-04-20').profit_line_card(self.line_a.id, M)
        self.assertTrue(later['stale'], 'аналітику змінили після перерахунку — «рядки змінилися після перерахунку»')
        self.assertEqual([b['amount'] for b in later['waterfall']['bars'] if b['key'] == 'net'], [row.net], 'ланцюжок — досі збережений рядок')

    def test_screen_matrix_tones_and_only_2026(self):
        self._screen_rows()
        Month = self.Month.with_context(profit_today='2030-08-10')
        with self._deals():
            data = Month.profit_matrix('2030-05-01', '2030-07-31')
            early = Month.profit_matrix('2025-06-01', '2026-03-31')
            over = Month.profit_overview('2025-12-01')
            card = Month.profit_line_card(self.line_a.id, '2025-12-01')
        self.assertEqual([m['value'] for m in data['months']], ['2030-05-01', '2030-06-01', '2030-07-01'])
        cells = data['lines'][0]['cells']
        self.assertEqual([c['tone'] for c in cells], ['', 'drop', 'neg'], 'падіння на 3 за годину > порогу 1,5 — жовте; чисто < 0 — червоне')
        self.assertEqual(data['lines'][0]['total']['net'], 150)
        self.assertEqual(data['lines'][0]['total']['net_ph'], 5)
        self.assertEqual(data['lines'][0]['total']['tone'], '', 'підсумок періоду вище нуля — без кольору (круг 3б)')
        self.env['ir.config_parameter'].sudo().set_param(calc.P_DROP, '3')
        with self._deals():
            relaxed = Month.profit_matrix('2030-05-01', '2030-07-31')
        self.assertEqual([c['tone'] for c in relaxed['lines'][0]['cells']], ['', '', 'neg'], 'поріг — параметр: рівно 3 — не жовте')
        self.assertEqual([m['value'] for m in early['months']], ['2026-01-01', '2026-02-01', '2026-03-01'], 'до січня 2026 — не віддається')
        self.assertFalse(early['has'], 'рядок 2025-12 у таблицю не потрапляє')
        self.assertEqual(over['month']['value'], '2026-01-01')
        self.assertNotIn('2025-12-01', [m['value'] for m in over['months']])
        self.assertEqual(card['month']['value'], '2026-01-01')
        self.assertFalse(card['prev'], 'стрілки «назад» з січня 2026 немає')

    def test_screen_no_hours_zero_chips_and_stale_rows(self):
        # червень 2030: у лінії B лише витрати без годин зі ставкою; «Не розподілено» і «Власник» на 0 €; травень — з годинами (для стрілки)
        Month = self.Month.sudo()
        Month.create({'row_kind': 'line', 'line_id': self.line_b.id, 'name': 'Line B', 'month': '2030-05-01', 'hours_total': 10,
                      'hours_rate': 10, 'share': 60, 'housing': -10, 'net': 50, 'net_per_hour': 5, 'aal_amount': -10})
        Month.create({'row_kind': 'line', 'line_id': self.line_b.id, 'name': 'Line B', 'month': '2030-06-01', 'hours_total': 4,
                      'hours_no_rate': 4, 'housing': -20, 'net': -20, 'net_per_hour': 0, 'aal_amount': -20})
        Month.create({'row_kind': 'unallocated', 'name': 'Buffer without line', 'month': '2030-06-01', 'housing_buffer': 15, 'amount': 15,
                      'net': 0, 'aal_amount': 15})
        Month.create({'row_kind': 'owner', 'analytic_id': self.a_owner.id, 'name': 'Owner X', 'month': '2030-06-01', 'net': 0})
        Screen = self.Month.with_context(profit_today='2030-07-10')
        with self._deals():
            over = Screen.profit_overview('2030-06-01')
            card = Screen.profit_line_card(self.line_b.id, '2030-06-01')
            junk = Screen.profit_line_card('abc', '2030-06-01')
            junk_list = Screen.profit_line_card([1], '2030-06-01')
        tile = [t for t in over['lines'] if t['id'] == self.line_b.id][0]
        self.assertIsNone(tile['net_ph'], 'годин зі ставкою немає — «—», а не 0,00 € год')
        self.assertIsNone(tile['share_ph'])
        self.assertIsNone(tile['trend'], 'стрілки від нуля немає')
        self.assertEqual(tile['net'], -20)
        self.assertIsNone(over['totals']['net_ph'], 'підсумок місяця без годин зі ставкою — теж «—»')
        self.assertNotIn('unallocated', [a['key'] for a in over['alerts']], '«Не розподілено» на 0 € — без чипа')
        self.assertNotIn('owners', [a['key'] for a in over['alerts']], '«Власники» на 0 € — без чипа')
        self.assertTrue(card['has'])
        self.assertIsNone(card['totals']['net_ph'])
        self.assertIsNone(card['totals']['share_ph'])
        self.assertTrue(all(b['ph'] is None for b in card['waterfall']['bars']))
        self.assertTrue(card['stale'], 'рядків розкладу немає, а «Чисто» −20 — рядки змінилися після перерахунку')
        self.assertTrue(card['recomputed'])
        for j in (junk, junk_list):
            self.assertEqual(j['line']['id'], self.line_b.id, 'нечисловий line_id — як «не знайдено»: перша лінія місяця, не помилка')
        # той самий рядок лінії з годинами (травень) — € год є: «—» лише без годин зі ставкою
        with self._deals():
            may = Screen.profit_overview('2030-05-01')
        self.assertEqual([t['net_ph'] for t in may['lines'] if t['id'] == self.line_b.id], [5])
        # круг 3а: клітинка C без годин зі ставкою не заливається — чисто місяця нижче нуля лише червоним текстом ('neg_text')
        with self._deals():
            data = Screen.profit_matrix('2030-05-01', '2030-06-30')
        cells = [x for x in data['lines'] if x['id'] == self.line_b.id][0]['cells']
        self.assertEqual([(c['net_ph'], c['tone']) for c in cells], [(5, ''), (None, 'neg_text')], 'без годин зі ставкою — без заливки, не «neg»')
        # круг 3б: підсумок періоду нижче нуля (лише червень: −20) — червоним, як клітинки; травень + червень (+30) — без кольору
        with self._deals():
            june = Screen.profit_matrix('2030-06-01', '2030-06-30')
        total = [x for x in june['lines'] if x['id'] == self.line_b.id][0]['total']
        self.assertEqual((total['net'], total['net_ph'], total['tone']), (-20, None, 'neg'))
        self.assertEqual([x['total']['tone'] for x in data['lines'] if x['id'] == self.line_b.id], [''])

    def test_check_one_per_month(self):
        # «Звірка місяця»: плановий перерахунок і «Звірити» за той самий місяць — один запис (оновити, якщо є); другий запис база не пустить
        Check = self.env['workset.profit.check'].sudo()
        self._run()
        self.assertEqual(Check.search_count([('month', '=', M)]), 1)
        self.Month._store_check(M, {'ok': False, 'text': 'second'})
        self.Month.search([('month', '=', M)], limit=1).action_verify()
        recs = Check.search([('month', '=', M)])
        self.assertEqual(len(recs), 1, 'перерахунок, ще один запис і «Звірити» — той самий запис')
        self.assertTrue(recs.ok, '«Звірити» оновило запис')
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            Check.create({'month': M, 'ok': True})
            self.env.flush_all()

    def test_check_store_when_record_unseen(self):
        # «Звірити» чекав замка, поки перерахунок створив запис місяця: перший пошук його не бачить (знімок до замка) — створення
        # впирається в унікальність, і _store_check оновлює запис, а не падає «Звірка цього місяця вже є»
        Check = self.env['workset.profit.check'].sudo()
        self._run()
        real = type(Check).search
        calls = []

        def stale_first(me, domain, *args, **kwargs):
            calls.append(domain)
            return me.browse() if len(calls) == 1 else real(me, domain, *args, **kwargs)
        with patch.object(type(Check), 'search', stale_first):
            self.Month._store_check(M, {'ok': False, 'text': 'unseen'})
        recs = Check.search([('month', '=', M)])
        self.assertEqual(len(recs), 1, 'одна звірка на місяць')
        self.assertEqual(recs.text, 'unseen', 'запис, якого не було видно, — оновлено')
        self.assertGreaterEqual(len(calls), 2, 'після відмови унікальності — ще один пошук')

    def test_recompute_reads_housing_rules_once(self):
        # правила житла «Ставок» — одне читання на перерахунок місяця (не _housing_at на кожну «лінію × замовника × день»)
        Housing = type(self.env['workset.line.housing'])
        real = Housing._rules
        n = []

        def counted(me):
            n.append(1)
            return real(me)
        with patch.object(Housing, '_rules', counted), patch.object(Housing, '_housing_at', side_effect=AssertionError('_housing_at')):
            self._run()
        self.assertEqual(len(n), 1)

    def test_open_card_only_from_2026(self):
        self._screen_rows()
        old = self._rows('2025-12-01', row_kind='line', line_id=self.line_a.id)
        res = old.action_open_card()
        self.assertEqual(res['tag'], 'display_notification', 'рядок до 2026 — повідомлення, а не картка січня 2026 мовчки')
        new = self._rows('2030-05-01', row_kind='line', line_id=self.line_a.id)
        res = new.action_open_card()
        self.assertEqual((res['tag'], res['params']['month']), ('workset_profit.line_card', '2030-05-01'))

    # ——— круг 3в: кожне число відкриває те, з чого воно складене ———
    def test_screen_every_number_opens_records(self):
        self._run()
        rows = self._rows(row_kind='line')
        row_a = self._rows(row_kind='line', line_id=self.line_a.id)
        lang = self.Month._lang()
        Screen = self.Month.with_context(profit_today='2030-04-20')
        with self._deals():
            over = Screen.profit_overview(M)
            card = Screen.profit_line_card(self.line_a.id, M)
            data = Screen.profit_matrix(M, M_LAST)

        def got(spec, what):
            # опис списку: модель, домен, заголовок, без «Створити» → записи списку
            self.assertTrue(spec and spec['model'] and spec['domain'] and spec['title'], what)
            self.assertEqual(spec['context'], {'create': False}, what)
            return self.env[spec['model']].search(spec['domain'])
        # A: п'ять підсумків — табелі місяця, рядки ліній місяця, рядки ліній з витратами
        op = over['opens']
        self.assertEqual(set(op), {'hours', 'share', 'costs', 'net', 'net_month', 'overhead', 'remain', 'remain_month'})
        for k in ('share', 'net', 'net_month', 'remain', 'remain_month'):
            self.assertIn(('month', '=', M), op[k]['domain'], k)
            self.assertEqual(got(op[k], k), rows, 'A %s — рядки ліній місяця' % k)
        # круг 4 (ревю 3в): «Житло, авто, паливо, інструмент» — записи витрат ліній (аналітика: житло лінії A −10, інструмент −3; житло B
        # платить партнер — не витрата «чисто»), не рядки ліній
        self.assertEqual(op['costs']['model'], 'account.analytic.line')
        self.assertEqual(sorted(got(op['costs'], 'A витрати').mapped('amount')), [-10, -3], 'A витрати — записи витрат місяця')
        # круг 5: «ZUS і загальні» — вікно «Що входить» (вміст — у відповіді, нового виклику немає); у вікні — рахунки й рядки «Офіс»
        dialog = {'dialog': 'overhead', 'ref': 'span', 'hint': calc.t('oh_open', lang)}
        self.assertEqual(op['overhead'], dialog)
        win = over['oh_details']['span']
        self.assertFalse(win['zus']['total']['open'], 'рахунків ZUS у березні немає — без кліку')
        self.assertEqual(got(win['office']['total']['open'], 'A загальні').mapped('row_kind'), ['office'], 'загальні — рядки «Офіс» місяця')
        self.assertEqual(op['hours']['model'], 'account.analytic.line')
        self.assertAlmostEqual(sum(got(op['hours'], 'A години').mapped('unit_amount')), sum(rows.mapped('hours_total')),
                               msg='табелі місяця = «Годин» «Огляду»')
        # B: п'ять підсумків — домен з місяцем і лінією; години — табелі лінії
        cop = card['opens']
        for k in ('share', 'costs', 'net', 'net_month'):
            self.assertIn(('month', '=', M), cop[k]['domain'], k)
            self.assertIn(('line_id', '=', self.line_a.id), cop[k]['domain'], k)
            self.assertEqual(got(cop[k], k), row_a, 'B %s — рядок лінії' % k)
        self.assertAlmostEqual(sum(got(cop['hours'], 'B години').mapped('unit_amount')), row_a.hours_total, msg='табелі лінії = «Години»')
        self.assertEqual(cop['hours']['title'], calc.t('o_ts', lang) % (self.line_a.display_name, card['month']['title_in']))
        # B: кожен крок водоспаду — свої записи; житло й інструмент — та сама аналітика, перевезення — транзакції картки «перевезення»
        bars = {b['key']: b for b in card['waterfall']['bars']}
        for k, b in bars.items():
            if k != 'office':
                got(b['open'], 'крок %s' % k)
        for k in ('share', 'car_rent', 'net', 'remain'):
            self.assertEqual(got(bars[k]['open'], k), row_a, '%s — рядок лінії' % k)
        # круг 5: крок «Загальні фірм» — вікно «Що входить»: частка лінії = крок, рядки «Офіс» — ті, що в числі
        self.assertEqual(bars['office']['open'], dialog, 'крок «Загальні фірм» — вікно «Що входить»')
        cwin = card['oh_details']['span']
        self.assertEqual(cwin['how']['part']['office'], bars['office']['amount'])
        self.assertEqual(got(cwin['office']['total']['open'], 'office').mapped('row_kind'), ['office'], 'у вікні — рядки «Офіс»')
        for k in ('housing', 'tool'):
            self.assertEqual(bars[k]['open']['model'], 'account.analytic.line', k)
            self.assertAlmostEqual(sum(got(bars[k]['open'], k).mapped('amount')), bars[k]['amount'], msg='%s: записи = сума кроку' % k)
        self.assertEqual(bars['transport']['open']['model'], 'workset.dkv.txn')
        self.assertEqual(got(bars['transport']['open'], 'transport').card_id, self.card_tr)
        # B: «Хто платить» — правило, за яким визначено платника; згорнутий рядок — правила лінії
        fold = card['pays_fold']
        self.assertEqual(got(fold['open'], 'правила лінії'), self.env['workset.line.cost.rule'].search([('line_id', '=', self.line_a.id)]))
        fuel = [r for r in fold['rows'] if r['label'] == calc.t('s_fuel', lang)][0]['open']
        rule = self.env['workset.line.cost.rule'].browse(fuel['res_id'])
        self.assertEqual((fuel['model'], rule.line_id, rule.kind, rule.payer), ('workset.line.cost.rule', self.line_a, 'fuel', 'worker_fund'))
        pays = {p['key']: p for p in card['pays']}
        house = pays['housing']['open']
        self.assertEqual(house['model'], 'workset.line.housing')
        self.assertEqual(self.env['workset.line.housing'].browse(house['res_id']).line_id, self.line_a, 'житло — правило житла «Ставок» лінії')
        self.assertNotIn('res_id', pays['auto']['open'], 'правила авто немає — список правил лінії цього виду')
        self.assertFalse(got(pays['auto']['open'], 'авто'), 'порожній список — «правил немає → ми»')
        # A: сегмент «Куди йде частка» — ті самі записи, що крок водоспаду; «чисто» — рядок лінії
        split = {p['key']: p for p in [t for t in over['lines'] if t['id'] == self.line_a.id][0]['split']}
        for k in ('housing', 'tool', 'transport'):
            self.assertEqual((split[k]['open']['model'], split[k]['open']['domain']), (bars[k]['open']['model'], bars[k]['open']['domain']), k)
        self.assertEqual(got(split['net']['open'], 'net'), row_a)
        # C: «N місяців» лінії, «Не розподілено» і «Власник» по місяцях і за період, «Звірка» — форма запису місяця
        line_c = [x for x in data['lines'] if x['id'] == self.line_a.id][0]
        self.assertEqual(got(line_c['total']['open'], 'C лінія'), row_a)
        un = self._rows(row_kind='unallocated')
        cell = data['unallocated']['cells'][0]
        self.assertIn(('month', '=', M), cell['open']['domain'])
        self.assertEqual(got(cell['open'], 'C не розподілено'), un)
        self.assertEqual(got(data['unallocated']['open'], 'C не розподілено за період'), un)
        owner = [o for o in data['owners'] if o['code'] == 'Owner X'][0]
        self.assertEqual(got(owner['cells'][0]['open'], 'C власник').mapped('row_kind'), ['owner'])
        self.assertAlmostEqual(sum(got(owner['open'], 'C власник за період').mapped('net')), owner['total'])
        chk = data['checks'][0]['open']
        self.assertEqual(chk['model'], 'workset.profit.check')
        self.assertEqual(calc.iso(self.env['workset.profit.check'].browse(chk['res_id']).month), M, '«Звірка» — запис свого місяця (форма)')

    # ——— круг 4: «− ZUS − загальні = Залишається WorkSET» ———
    def _contrib_bill(self, partner, day, text, amount, analytic=None):
        # рахунок постачальника (проведений) — як рахунки ZUS / PIT-4 польських фірм; податків у тестової фірми немає
        line = {'name': text, 'account_id': self.g_exp.id, 'quantity': 1, 'price_unit': amount, 'tax_ids': [(6, 0, [])]}
        if analytic:
            line['analytic_distribution'] = {str(analytic.id): 100}
        move = self.env['account.move'].with_company(self.co).create({
            'move_type': 'in_invoice', 'journal_id': self.purchase.id, 'partner_id': partner.id, 'invoice_date': day, 'date': day,
            'ref': text, 'invoice_line_ids': [(0, 0, line)]})
        move.action_post()
        return move

    def _contrib_setup(self):
        # партнери ZUS і податкової з рахунком кредиторки, журнал покупок, стаття «офіс» (розмітка office) → (zus, tax, стаття)
        Account = self.env['account.account'].with_company(self.co)
        payable = Account.create({'name': 'Test payable bills', 'code': '499903', 'account_type': 'liability_payable', 'reconcile': True})
        Partner = self.env['res.partner']
        zus = Partner.create({'name': 'Zakład Ubezpieczeń Społecznych', 'is_company': True})
        tax = Partner.create({'name': 'Urząd Skarbowy Test', 'is_company': True})
        for p in (zus, tax):
            p.with_company(self.co).property_account_payable_id = payable
        self.purchase = self.env['account.journal'].with_company(self.co).create({'name': 'Test purchase P', 'code': 'TPUP', 'type': 'purchase',
                                                                                  'company_id': self.co.id})
        office_acc = self.env['account.analytic.account'].create({'name': 'Office costs', 'plan_id': self.node_office.id, 'company_id': False})
        self.env['workset.profit.analytic.map'].create({'analytic_id': office_acc.id, 'kind': 'office'})
        return zus, tax, office_acc

    def test_overhead_zus_by_work_month_and_office_by_hours(self):
        # березень 2030: ZUS «03/2030» 12 (проведено 10.04, аналітика — у статтю «офіс»), PIT-4 без мітки 2,5 (проведено 20.03 — березень,
        # «без мітки»), CIT податкової 7 — не наше; загальні фірм — картка офісу −3. Години табеля: A 20, B 16 → частки за годинами
        zus, tax, office_acc = self._contrib_setup()
        bill_zus = self._contrib_bill(zus, '2030-04-10', 'ZUS 03/2030', 12, office_acc)
        bill_pit = self._contrib_bill(tax, '2030-03-20', 'PIT-4', 2.5)
        self._contrib_bill(tax, '2030-03-21', 'CIT-8 03/2030', 7)
        res = self._run(M, '2030-04-30')
        for check in res['verify']:
            self.assertTrue(check['ok'], check['text'])
        mar = res['verify'][0]['text']
        self.assertIn('ZUS і PIT-4 −14,50', mar, 'ZUS за міткою (проведено у квітні) і PIT-4 без мітки — у березні; CIT — ні')
        self.assertIn('ZUS без мітки місяця: 1, сума −2,50', mar)
        oh = self.env['workset.profit.overhead'].search([('month', '=', M)])
        self.assertEqual((oh.zus, oh.pit4, oh.office, oh.hours), (-14.5, -2.5, -3, 36))
        self.assertEqual((oh.move_ids, oh.unlabeled_ids), (bill_zus | bill_pit, bill_pit))
        a = self._rows(row_kind='line', line_id=self.line_a.id)
        b = self._rows(row_kind='line', line_id=self.line_b.id)
        self.assertEqual((a.zus_amount, b.zus_amount, a.office_amount, b.office_amount), (-8.06, -6.44, -1.67, -1.33),
                         '−14,50 і −3 за годинами 20 / 16 — до цента, остаток округлення — найбільшій')
        self.assertEqual((a.net, a.remain, b.remain), (40, 30.27, 32.23), '«чисто» не змінилось; залишається = чисто + частки')
        self.assertAlmostEqual(a.remain_per_hour, round(30.27 / 16, 2))
        # квітень: аналітика рахунку ZUS лягла в «Офіс» (−12) — у загальні не входить (уже ZUS березня): подвійного рахунку немає
        apr = self.env['workset.profit.overhead'].search([('month', '=', APR)])
        self.assertEqual((self._rows(APR, row_kind='office').mapped('amount'), apr.office_dup, apr.office, apr.zus), ([-12], -12, 0, 0))
        # звірка ловить частку, якої немає в рахунках
        a.sudo().write({'zus_amount': a.zus_amount - 0.01})
        bad = self.Month._verify(M)
        self.assertFalse(bad['ok'])
        self.assertIn('ZUS і PIT-4: у рядках −14,51, за рахунками −14,50', bad['text'])
        a.sudo().write({'zus_amount': a.zus_amount + 0.01})
        # екрани: A — «ZUS і загальні» і «Залишається», клік — рахунки / рядки «Офіс», чип «без мітки»; B — водоспад далі «чисто»; C — рядки компанії
        Screen = self.Month.with_context(profit_today='2030-05-20')
        with self._deals():
            over = Screen.profit_overview(M)
            card = Screen.profit_line_card(self.line_a.id, M)
            data = Screen.profit_matrix(M, M_LAST)
        tot = over['totals']
        self.assertEqual((tot['overhead'], tot['remain']), (-17.5, 62.5))
        self.assertAlmostEqual(tot['net'] + tot['overhead'], tot['remain'])
        self.assertEqual(tot['remain_ph'], round(62.5 / 24, 2))
        # круг 5: склад місяця (пише перерахунок) і вікно «Що входить»: ZUS — рахунки за фірмою, частка ліній = число «ZUS і загальні»
        mar = self.env['workset.profit.overhead'].search([('month', '=', M)])
        self.assertEqual(mar.breakdown, {'moves': sorted([[bill_zus.id, 'ZUS', -12.0], [bill_pit.id, 'PIT-4', -2.5]]), 'office_dup': []})
        self.assertEqual(apr.breakdown['office_dup'], [[office_acc.id, -1200]], 'квітень: аналітика рахунку ZUS у «Офісі» — за статтею')
        win = over['oh_details']['span']
        self.assertEqual(self.env['account.move'].search(win['zus']['total']['open']['domain']), bill_zus | bill_pit)
        self.assertEqual([(r['name'], r['zus'], r['pit4'], r['total'], r['n']) for r in win['zus']['rows']], [(self.co.name, -12, -2.5, -14.5, 2)])
        self.assertEqual(self.env['account.move'].search(win['zus']['rows'][0]['pit4_open']['domain']), bill_pit, 'клітинка PIT-4 — рахунки PIT-4 фірми')
        self.assertEqual((win['zus']['unlabeled']['n'], win['zus']['unlabeled']['amount']), (1, -2.5))
        self.assertEqual((win['how']['part']['total'], win['how']['total'], win['how']['hours']), (tot['overhead'], -17.5, 36),
                         'частка ліній = число, по якому клікнули; ZUS і загальні компанії — усі години')
        alerts = {x['key']: x for x in over['alerts']}
        self.assertEqual(alerts['zus_unlabeled']['count'], 1)
        self.assertEqual(self.env['account.move'].search(alerts['zus_unlabeled']['open']['domain']), bill_pit)
        tile = [t for t in over['lines'] if t['id'] == self.line_a.id][0]
        self.assertEqual((tile['remain'], tile['remain_ph']), (30.27, a.remain_per_hour))
        bars = {x['key']: x for x in card['waterfall']['bars']}
        self.assertEqual([x['key'] for x in card['waterfall']['bars']][-3:], ['zus', 'office', 'remain'])
        self.assertEqual((bars['zus']['amount'], bars['office']['amount'], bars['remain']['amount']), (-8.06, -1.67, 30.27))
        self.assertEqual((bars['zus']['open']['dialog'], bars['zus']['open']['ref']), ('overhead', 'span'), 'крок ZUS — вікно «Що входить»')
        part = card['oh_details']['span']['how']['part']
        self.assertEqual((part['hours'], part['zus'], part['office'], part['total']), (20, -8.06, -1.67, -9.73), 'частка лінії у вікні = кроки')
        self.assertTrue(part['exact'], '20 год × −0,4865 = −9,73 до цента')
        self.assertEqual(card['totals']['remain'], 30.27)
        rows = [r for r in card['rows'] if r.get('overhead')]
        self.assertEqual([r['amount'] for r in rows], [-8.06, -1.67], 'група «ZUS і загальні (за годинами)»')
        self.assertTrue(card['rows'][-1].get('timesheets'), 'табелі — як і раніше останні')
        self.assertEqual(card['series']['remain'][card['series']['months'].index(card['month'])], a.remain_per_hour)
        self.assertEqual((data['zus']['cells'][0]['amount'], data['office']['cells'][0]['amount']), (-14.5, -3))
        self.assertEqual(data['zus']['cells'][0]['open']['ref'], M, 'C: клітинка — вікно свого місяця')
        cwin = data['oh_details'][M]
        self.assertEqual((cwin['zus']['total']['total'], cwin['office']['total']['amount']), (-14.5, -3), 'вікно C = клітинки')
        self.assertEqual(self.env['account.move'].search(cwin['zus']['total']['open']['domain']), bill_zus | bill_pit)
        self.assertFalse(cwin['how']['part'], 'C — суми компанії, без частки лінії')
        cell = [x for x in data['lines'] if x['id'] == self.line_a.id][0]['cells'][0]
        self.assertEqual((cell['remain'], cell['remain_ph'], cell['remain_tone']), (30.27, a.remain_per_hour, ''))
        self.assertEqual([s['key'] for s in cell['remain_chain']], ['zus', 'office', 'remain'])

    def test_screen_month_without_overhead_not_recomputed(self):
        # круг 4б: рядки, пораховані до круга 4 (підсумку «ZUS і загальні» місяця немає), — екрани кажуть «не перераховано», а не
        # «Залишається 0 €»: A — признак, «—» і без кліку; B — без кроків ZUS / загальні; C — «—» без тону й без «N місяців»
        self._screen_rows()
        Screen = self.Month.with_context(profit_today='2030-08-10')
        with self._deals():
            over = Screen.profit_overview('2030-06-01')
            card = Screen.profit_line_card(self.line_a.id, '2030-06-01')
            data = Screen.profit_matrix('2030-05-01', '2030-07-31')
        self.assertTrue(over['oh_missing'])
        self.assertEqual(over['oh_hint'], calc.t('oh_missing', self.Month._lang()))
        self.assertEqual((over['totals']['remain'], over['totals']['remain_ph'], over['totals']['overhead_ph']), (None, None, None))
        self.assertEqual(over['totals']['net'], 70, '«чисто» — як було')
        self.assertFalse(over['opens']['remain'])
        tile = [t for t in over['lines'] if t['id'] == self.line_a.id][0]
        self.assertEqual((tile['remain'], tile['remain_ph']), (None, None))
        self.assertTrue(card['oh_missing'])
        self.assertEqual(card['waterfall']['bars'][-1]['key'], 'net', 'кроків ZUS / загальні немає')
        self.assertIsNone(card['totals']['remain_ph'])
        self.assertTrue(all(v is None for v in card['series']['remain']), 'точок «Залишається» немає')
        cells = [x for x in data['lines'] if x['id'] == self.line_a.id][0]['cells']
        self.assertEqual([(c['remain'], c['remain_ph'], c['remain_tone'], c['oh_missing']) for c in cells], [(None, None, '', True)] * 3)
        self.assertEqual([c['tone'] for c in cells], ['', 'drop', 'neg'], '«чисто» — тони як були')
        total = [x for x in data['lines'] if x['id'] == self.line_a.id][0]['total']
        self.assertEqual((total['remain'], total['remain_ph'], total['oh_missing']), (None, None, True))
        # після перерахунку (березень) підсумок є — признака немає
        self._run()
        with self._deals():
            done = self.Month.with_context(profit_today='2030-04-20').profit_overview(M)
        self.assertFalse(done['oh_missing'])
        self.assertIsNotNone(done['totals']['remain'])

    # ——— круг 5: період і рік; «Що входить у ZUS і загальні» ———
    def _two_months(self, outside=True):
        # березень і квітень 2030: квітень — табелі A 8 і B 8 год, житло A −5, картка офісу −2 (без рахунку в Odoo); ZUS «03/2030» 12
        # (проведено 10.04, аналітика — у статтю «офіс» квітня), ZUS «04/2030» 6 (проведено 05.05), PIT-4 без мітки 2,5 (20.04 — квітень);
        # outside — ще ZUS з міткою «01/2029», проведений 15.04, — поза вікном (не входить у жоден місяць). Перерахунок обох місяців (звірки
        # сходяться) → (стаття «офіс», рахунки березня, квітня, PIT-4, поза вікном або None)
        zus, tax, office_acc = self._contrib_setup()
        AAL = self.env['account.analytic.line'].with_company(self.co)
        for emp, proj, day in ((self.e1, self.p1, '2030-04-07'), (self.e2, self.p2, '2030-04-08')):
            AAL.create({'name': '/', 'project_id': proj.id, 'employee_id': emp.id, 'date': day, 'unit_amount': 8})
        self._aal(self.a_house_a, -5, '2030-04-10', self.g_exp)
        self.env['workset.dkv.txn'].create({'card_id': self.card_office.id, 'date': '2030-04-12', 'amount_gross': 2, 'amount_net': 2,
                                            'city': 'City', 'source': 'pdf'})
        bill_mar = self._contrib_bill(zus, '2030-04-10', 'ZUS 03/2030', 12, office_acc)
        bill_apr = self._contrib_bill(zus, '2030-05-05', 'ZUS 04/2030', 6)
        bill_pit = self._contrib_bill(tax, '2030-04-20', 'PIT-4', 2.5)
        bill_out = self._contrib_bill(zus, '2030-04-15', 'ZUS 01/2029', 1) if outside else None
        res = self._run(M, '2030-04-30')
        for check in res['verify']:
            self.assertTrue(check['ok'], check['text'])
        return office_acc, bill_mar, bill_apr, bill_pit, bill_out

    def test_screen_period_sums_months_and_overhead_window(self):
        # березень і квітень 2030 (_two_months): ZUS березня й квітня, PIT-4 без мітки, ZUS з міткою поза вікном
        office_acc, bill_mar, bill_apr, bill_pit, bill_out = self._two_months()
        lang = self.Month._lang()
        rows = {(r.month.isoformat(), r.line_id.id): r for r in self.Month.search([('month', 'in', [M, APR]), ('row_kind', '=', 'line')])}
        a = [rows[(M, self.line_a.id)], rows[(APR, self.line_a.id)]]
        both = self.Month.browse([r.id for r in rows.values()])
        ohs = self.env['workset.profit.overhead'].search([('month', 'in', [M, APR])])
        self.assertEqual(self.env['workset.profit.overhead'].search([('month', '=', APR)]).breakdown,
                         {'moves': sorted([[bill_apr.id, 'ZUS', -6.0], [bill_pit.id, 'PIT-4', -2.5]]), 'office_dup': [[office_acc.id, -1200]]},
                         'склад квітня пише перерахунок: рахунки місяця роботи й аналітика рахунку ZUS березня в «Офісі» квітня')
        Screen = self.Month.with_context(profit_today='2030-05-20')
        with self._deals():
            per = Screen.profit_overview(M, 'range', '2030-04-30')
            year = Screen.profit_overview(APR, 'year')
            quarter = Screen.profit_overview(APR, 'quarter')
            # круг 5б: рік і квартал від поточного (неповного) травня — теж до квітня; «Рік → Місяць» — останній закритий
            year_cur = Screen.profit_overview('2030-05-01', 'year')
            quarter_cur = Screen.profit_overview('2030-05-01', 'quarter')
            month_back = Screen.profit_overview(year['span']['to'], 'month')
            may = Screen.profit_overview('2030-05-01', 'month')
            card = Screen.profit_line_card(self.line_a.id, M, 'range', '2030-04-30')
            data = Screen.profit_matrix(M, '2030-04-30')
        # A: період = сума місяців; € год — середньозважене (сума ÷ години зі ставкою), підпис «за період»
        self.assertEqual(per['span']['months'], [M, APR])
        self.assertEqual(per['span']['text'], calc.t('span_text', lang) % calc.range_text(M, APR, lang))
        tot = per['totals']
        net = round(sum(both.mapped('net')), 2)
        self.assertEqual((tot['net'], tot['hours'], tot['hours_rate']), (net, 52, round(sum(both.mapped('hours_rate')), 2)))
        self.assertEqual(tot['net_ph'], round(net / tot['hours_rate'], 2), '€ год за період — середньозважене')
        self.assertEqual(tot['remain'], round(sum(both.mapped('remain')), 2))
        self.assertAlmostEqual(tot['net'] + tot['overhead'], tot['remain'])
        # круг 5б: рік і квартал — до останнього закритого місяця (квітень), поточного неповного травня в них немає
        self.assertEqual((year['totals']['net'], year['span']['months']), (net, ['2030-0%d-01' % i for i in range(1, 5)]),
                         'рік 2030 — січень … квітень (останній закритий), сума — березень і квітень')
        self.assertEqual(year['span']['text'], calc.t('span_text', lang) % calc.range_text('2030-01-01', APR, lang), 'підпис — до квітня')
        self.assertEqual((quarter['span']['months'], quarter['span']['title']), ([APR], calc.t('span_quarter', lang) % ('II', '2030')))
        self.assertEqual(quarter['totals']['net'], round(sum(r.net for (m, _l), r in rows.items() if m == APR), 2), 'II квартал — квітень')
        self.assertEqual((year_cur['span']['months'], quarter_cur['span']['months']), (year['span']['months'], [APR]),
                         'від поточного травня — так само до квітня')
        self.assertEqual((month_back['span']['kind'], month_back['span']['months']), ('month', [APR]), '«Рік → Місяць» — квітень, не травень')
        self.assertEqual(may['span']['months'], ['2030-05-01'], 'поточний місяць — лише явно, «Місяць»')
        tile = [t for t in per['lines'] if t['id'] == self.line_a.id][0]
        self.assertEqual((tile['net'], tile['hours']), (round(a[0].net + a[1].net, 2), 28))
        self.assertEqual(tile['net_ph'], round((a[0].net + a[1].net) / (a[0].hours_rate + a[1].hours_rate), 2))
        # open періоду — домен за датами: рядки обох місяців, табелі обох місяців
        op = per['opens']
        self.assertIn(('month', '>=', M), op['share']['domain'])
        self.assertIn(('month', '<=', APR), op['share']['domain'])
        self.assertEqual(self.Month.search(op['share']['domain']), both)
        self.assertAlmostEqual(sum(self.env['account.analytic.line'].search(op['hours']['domain']).mapped('unit_amount')), 52)
        alerts = {x['key']: x for x in per['alerts']}
        self.assertEqual((alerts['check']['summary'], alerts['check']['state']), (calc.t('v_period', lang) % (2, 2), 'ok'))
        self.assertEqual(len(self.env['workset.profit.check'].search(alerts['check']['open']['domain'])), 2, 'клік — звірки обох місяців')
        self.assertEqual(year['alerts'][[x['key'] for x in year['alerts']].index('check')]['summary'], calc.t('v_period', lang) % (2, 4),
                         'рік: місяці без звірки (січень, лютий) — не зійшлися; неповного травня в «Звірці» немає')
        self.assertEqual((per['prev']['from'], per['prev']['to'], per['next']), ('2030-01-01', '2030-02-01', False),
                         'стрілки «з–по» — на свою довжину; травень–червень — не весь у межах')
        self.assertEqual(per['series']['band'], [[m['value'] for m in per['series']['months']].index(M),
                                                 [m['value'] for m in per['series']['months']].index(APR)], 'графік — смуга періоду')
        # вікно A: ZUS за фірмою (березень 12 + квітень 6 і PIT-4 2,5), загальні — картки офісу −3 і −2 (стаття «офіс» квітня — уся з
        # рахунку ZUS: не входить), частка ліній = «ZUS і загальні» підсумків
        win = per['oh_details']['span']
        self.assertEqual([(r['name'], r['zus'], r['pit4'], r['total'], r['n']) for r in win['zus']['rows']], [(self.co.name, -18, -2.5, -20.5, 3)])
        self.assertEqual(win['zus']['total']['total'], round(sum(ohs.mapped('zus')), 2), 'ZUS вікна = ZUS підсумків місяців')
        self.assertEqual(self.env['account.move'].search(win['zus']['total']['open']['domain']), bill_mar | bill_apr | bill_pit)
        self.assertEqual((win['zus']['outside']['n'], win['zus']['outside']['amount']), (1, -1))
        self.assertEqual(self.env['account.move'].search(win['zus']['outside']['open']['domain']), bill_out)
        self.assertEqual([(r['key'], r['amount'], r['n']) for r in win['office']['rows']], [('dkv', -5, 2)], 'DKV · офіс — одним рядком')
        self.assertEqual(win['office']['total']['amount'], round(sum(ohs.mapped('office')), 2), 'загальні вікна = загальні підсумків')
        self.assertEqual(win['office']['dup'], -12)
        office_rows = self.Month.search(win['office']['rows'][0]['open']['domain'])
        self.assertEqual((office_rows.mapped('row_kind'), sorted(office_rows.mapped('amount'))), (['office', 'office'], [-3, -2]),
                         'клік — рядки «Офіс», що ввійшли в число (без статті, цілком з рахунку ZUS)')
        self.assertEqual((win['how']['hours'], win['how']['total']), (52, -25.5))
        self.assertEqual(win['how']['part']['total'], tot['overhead'], 'частка ліній = число «ZUS і загальні», по якому клікнули')
        # круг 5б: «На годину зі ставкою (як на екрані)» — рівно «ZUS і загальні € год» A до цента (40 год зі ставкою, не 52 табеля)
        wp = win['how']['part']
        self.assertIsNotNone(tot['overhead_ph'])
        self.assertEqual((wp['rate_ph'], wp['hours_rate'], wp['screen']), (tot['overhead_ph'], tot['hours_rate'], True))
        self.assertNotEqual(round(wp['ph'], 2), wp['rate_ph'], 'рядок «на годину табеля» лишається своїм числом')
        # B: картка за період — водоспад до «чисто» й «Залишається» сумами місяців; «Рядки» — за статтею з числом записів
        self.assertEqual((card['span']['kind'], card['totals']['net'], card['totals']['hours']), ('range', round(a[0].net + a[1].net, 2), 28))
        bars = {b['key']: b for b in card['waterfall']['bars']}
        self.assertAlmostEqual(sum(b['amount'] for b in card['waterfall']['bars'][:list(bars).index('net')]), bars['net']['amount'])
        self.assertEqual((bars['zus']['amount'], bars['office']['amount']), (-10.92, -2.67), 'березень 20 з 36 год, квітень 8 з 16')
        self.assertEqual(bars['remain']['amount'], round(a[0].remain + a[1].remain, 2))
        part = card['oh_details']['span']['how']['part']
        self.assertEqual((part['hours'], part['zus'], part['office']), (28, bars['zus']['amount'], bars['office']['amount']), 'вікно B = кроки')
        self.assertEqual((part['rate_ph'], part['screen']), (card['totals']['overhead_ph'], False),
                         'B: на годину зі ставкою лінії; одним числом на екрані B його немає — без «як на екрані»')
        house =[r for r in card['rows'] if r['title'] == self.a_house_a.display_name][0]
        self.assertEqual(house['amount'], -15)
        self.assertTrue(house['sub'].endswith(calc.records_text(2, lang)), house['sub'])
        self.assertEqual(sorted(self.env['account.analytic.line'].search(house['open']['domain']).mapped('amount')), [-10, -5],
                         'група — записи статті за обидва місяці')
        self.assertAlmostEqual(sum(self.env['account.analytic.line'].search(card['rows'][-1]['open']['domain']).mapped('unit_amount')), 28)
        self.assertEqual(self.Month.search(card['opens']['share']['domain']), self.Month.browse([r.id for r in a]))
        # C: вікно місяця = клітинка, вікно періоду = «N місяців»
        self.assertEqual([c['amount'] for c in data['zus']['cells']], [-12, -8.5])
        self.assertEqual(data['oh_details'][APR]['zus']['total']['total'], -8.5)
        self.assertEqual(data['oh_details']['period']['office']['total']['amount'], data['office']['total'])
        self.assertEqual(data['office']['open']['ref'], 'period')

    # ——— круг 5б: картка за період зі складу місяців; вікно «Що входить» до перерахунку ———
    def test_screen_card_period_rows_sum_of_months(self):
        # картка лінії за період складає склад «Рядків» місяців (card_groups — пише перерахунок), а не розкладає кожен місяць: «Рядки» за
        # березень — квітень = «Рядки» березня + «Рядки» квітня (кожен окремо — розклад наново) до цента й до запису; рядок без складу
        # (перераховано до 19.0.5, одразу після -u) — розклад місяця, те саме
        self._two_months(outside=False)
        Model = type(self.Month)
        real = Model._month_parts
        seen = []

        def counted(me, month, hr=None, with_ts=True):
            seen.append(month)
            return real(me, month, hr, with_ts)
        Screen = self.Month.with_context(profit_today='2030-05-20')
        line_rows = self.Month.search([('month', 'in', [M, APR]), ('row_kind', '=', 'line')])
        self.assertTrue(all(isinstance(r.card_groups, dict) and r.card_groups.get('groups') for r in line_rows.filtered(
            lambda r: r.line_id == self.line_a)), 'перерахунок пише склад «Рядків» у рядок лінії')
        with self._deals():
            mar = Screen.profit_line_card(self.line_a.id, M)
            apr = Screen.profit_line_card(self.line_a.id, APR)
            with patch.object(Model, '_month_parts', counted):
                per = Screen.profit_line_card(self.line_a.id, M, 'range', '2030-04-30')
            self.assertEqual(seen, [], 'склад обох місяців є — місяці не розкладаються')
            line_rows.sudo().write({'card_groups': False})
            with patch.object(Model, '_month_parts', counted):
                bare = Screen.profit_line_card(self.line_a.id, M, 'range', '2030-04-30')
        self.assertEqual(sorted(seen), [M, APR], 'складу немає — розклад кожного місяця, як до 19.0.5')

        def groups(card):
            # «Рядки» без табелів і часток ZUS / загальних: (назва без лічильника, підпис без числа записів) → (центи, записи списку)
            out = {}
            for r in card['rows']:
                if r.get('timesheets') or r.get('overhead'):
                    continue
                key = (r['title'].split(' — ')[0], re.sub(r' · \d+ \S+$', '', r['sub']))
                ids = self.env[r['open']['model']].search(r['open']['domain']).ids if r['open'] else []
                c, got = out.get(key, (0, []))
                out[key] = (c + calc.cents(r['amount']), sorted(set(got) | set(ids)))
            return out
        want = groups(mar)
        for key, (c, ids) in groups(apr).items():
            c0, ids0 = want.get(key, (0, []))
            want[key] = (c0 + c, sorted(set(ids0) | set(ids)))
        self.assertTrue(len(want) >= 4, want)
        self.assertEqual(groups(per), want, '«Рядки» за два місяці = сума «Рядків» двох місяців по одному')
        self.assertEqual((per['rows'], per['waterfall'], per['stale']), (bare['rows'], bare['waterfall'], False),
                         'склад і розклад наново — ті самі рядки, суми, кроки водоспаду й записи кліків')
        house = [r for r in per['rows'] if r['title'] == self.a_house_a.display_name][0]
        self.assertEqual(house['amount'], -15, 'житло A: березень −10 + квітень −5')

    def test_overhead_window_without_breakdown_before_recalc(self):
        # стан одразу після -u до перерахунку: підсумок місяця є, складу breakdown немає (місяць перераховано до 19.0.4) — вікно «Що
        # входить» складає запасна гілка (рахунки — _contrib_moves за рахунками підсумку, статті «Офісу» — розклад місяця, бо в квітні
        # office_dup ≠ 0) і сходиться з числом, по якому клікнули, — так само, як зі складом
        self._two_months(outside=False)
        Model = type(self.Month)
        real_moves, real_rows = Model._contrib_moves, Model._contrib_in_rows
        calls = []

        def moves(me, dom):
            calls.append('moves')
            return real_moves(me, dom)

        def in_rows(me, entries, refs):
            calls.append('rows')
            return real_rows(me, entries, refs)
        Screen = self.Month.with_context(profit_today='2030-05-20')
        ohs = self.env['workset.profit.overhead'].search([('month', 'in', [M, APR])])
        with self._deals(), patch.object(Model, '_contrib_moves', moves), patch.object(Model, '_contrib_in_rows', in_rows):
            full = Screen.profit_overview(M, 'range', '2030-04-30')
            self.assertEqual(calls, [], 'склад є — рахунки й розклад місяця не читаються')
            ohs.sudo().write({'breakdown': False})
            bare = Screen.profit_overview(M, 'range', '2030-04-30')
        self.assertIn('moves', calls, 'без складу — рахунки ZUS / PIT-4 підсумку місяця (запасна гілка)')
        self.assertIn('rows', calls, 'без складу — аналітика рахунків ZUS у «Офісі» квітня з розкладу місяця')
        a, b = full['oh_details']['span'], bare['oh_details']['span']
        self.assertEqual((b['zus'], b['office'], b['how']), (a['zus'], a['office'], a['how']), 'запасна гілка = склад: рядки, суми, записи')
        tot = bare['totals']
        self.assertEqual(b['zus']['total']['total'], round(sum(ohs.mapped('zus')), 2), 'ZUS вікна = ZUS підсумків місяців')
        self.assertEqual(b['office']['total']['amount'], round(sum(ohs.mapped('office')), 2), 'загальні вікна = загальні підсумків')
        self.assertEqual((b['office']['dup'], [r['key'] for r in b['office']['rows']]), (-12, ['dkv']), 'стаття «офіс» квітня — уся з ZUS')
        self.assertEqual((b['how']['part']['total'], b['how']['part']['rate_ph']), (tot['overhead'], tot['overhead_ph']),
                         'частка ліній і на годину зі ставкою = число «ZUS і загальні»')
        self.assertTrue(b['zus']['rows'] and b['zus']['unlabeled'], 'рахунки й «без мітки» — є')

    def test_screen_access_only_viewer_group(self):
        viewer = new_test_user(self.env, login='ws_profit_screen_viewer', groups='base.group_user,workset_profit.group_viewer')
        plain = new_test_user(self.env, login='ws_profit_screen_plain', groups='base.group_user')
        admin = new_test_user(self.env, login='ws_profit_screen_admin', groups='base.group_user,base.group_system')
        self._run()

        def as_user(user):
            return self.Month.with_user(user).with_context(allowed_company_ids=user.company_ids.ids, profit_today=False)
        for user in (plain, admin):
            for call in (lambda m: m.profit_overview(M), lambda m: m.profit_line_card(self.line_a.id, M), lambda m: m.profit_matrix(M, M_LAST)):
                with self.assertRaises(AccessError):
                    call(as_user(user))
        # «сьогодні» перегляду — справжнє (підміна — лише адміністратору), тож синтетика — у січні 2026 (він завжди між MIN_MONTH і
        # поточним місяцем): перегляд читає рядки, line_id / analytic_id (search_read його правами) і всі три екрани з даними
        Month = self.Month.sudo()
        jan = calc.MIN_MONTH
        Month.create({'row_kind': 'line', 'line_id': self.line_a.id, 'name': 'Line A', 'month': jan, 'hours_total': 10, 'hours_rate': 10,
                      'share': 50, 'housing': -20, 'net': 30, 'net_per_hour': 3, 'aal_amount': -20})
        Month.create({'row_kind': 'owner', 'analytic_id': self.a_owner.id, 'name': 'Owner X', 'month': jan, 'amount': -5, 'net': -5,
                      'aal_amount': -5})
        Month.create({'row_kind': 'unallocated', 'name': 'Unallocated', 'month': jan, 'amount': -2, 'net': -2, 'aal_amount': -2})
        me = as_user(viewer)
        with self._deals():
            over = me.profit_overview(jan)
            card = me.profit_line_card(self.line_a.id, jan)
            data = me.profit_matrix(jan, '2026-01-31')
        self.assertEqual(over['month']['value'], jan)
        tile = [t for t in over['lines'] if t['id'] == self.line_a.id]
        self.assertEqual([(t['name'], t['net'], t['net_ph']) for t in tile], [('Line A', 30, 3)], 'перегляд бачить плитку лінії (line_id)')
        alerts = {a['key']: a for a in over['alerts']}
        self.assertIn(('Owner X', -5), [(p['code'], p['amount']) for a in over['alerts'] if a['key'] == 'owners' for p in a['parts']],
                      'стаття власника — з analytic_id правами перегляду')
        self.assertTrue(alerts['unallocated']['amount'] <= -2)
        self.assertTrue(card['has'], 'картка лінії під переглядом — з даними')
        self.assertEqual((card['line']['id'], card['totals']['net'], card['waterfall']['bars'][-1]['amount']), (self.line_a.id, 30, 30))
        self.assertTrue(card['recomputed'], 'час перерахунку — з write_date рядка')
        self.assertEqual(len(data['months']), 1, 'перегляд бачить екран')
        row = [x for x in data['lines'] if x['id'] == self.line_a.id]
        self.assertEqual([(c['net'], c['net_ph']) for c in row[0]['cells']], [(30, 3)])
        self.assertIn('Owner X', [o['code'] for o in data['owners']])
        menu = self.env.ref('workset_profit.menu_root')
        groups = menu['group_ids' if 'group_ids' in menu._fields else 'groups_id']  # Odoo 19 — group_ids
        self.assertEqual(groups, self.env.ref('workset_profit.group_viewer'), 'меню — лише групі перегляду')


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
