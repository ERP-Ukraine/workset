# -*- coding: utf-8 -*-
"""Тести «Агентів»: чисті функції та вычисляемые поля моделей. Запускає Odoo.sh на dev-збірці (і локально:
odoo-bin -i workset_agents --test-enable --test-tags workset_agents). Усі дані — вигадані в самому тесті."""
from psycopg2 import IntegrityError

from odoo.tests.common import TransactionCase, tagged

from ..models import calc, payplan

TODAY = '2026-11-05'


@tagged('post_install', '-at_install')
class TestAgentsCalc(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.agent = env['workset.agent'].create({'name': 'Тест агент', 'code': 'T-1'})
        cls.worker = env['res.partner'].create({'name': 'Тест людина'})
        cls.cust = env['res.partner'].create({'name': 'Тест замовник', 'is_company': True})

    # ── чисті функції (ті самі, що перевіряє node-тест) ──
    def test_pay_day(self):
        self.assertEqual(calc.pay_day_for('2026-11-05'), '2026-11-10')
        self.assertEqual(calc.pay_day_for('2026-11-08'), '2026-11-25')
        self.assertEqual(calc.pay_day_for('2026-12-23'), '2026-12-28')  # 24–26.12 свята, 27 — неділя
        self.assertFalse(calc.bank_day('2026-11-11'))
        self.assertEqual(payplan.prev_pay_day('2026-11-25'), '2026-11-10')

    def test_compute_minimal(self):
        # 40 год рахунку без табеля: «Діє з» далі NODATE_MARGIN (31 день) від дати рахунку — «Очікується» («Лише звірка» — режим mode, стадія «Діє»);
        # «Діє з» за 19 днів до рахунку — сторож no_timesheet «На перевірці» (так само еталон ручної збірки tools/odoo_agents_calc/calc.py)
        cfg = calc.make_cfg(1, 36)

        def data(date_from):
            return {'deals_people': [], 'deals_client': [{'id': 7, 'agent_id': [71, 'a'], 'kind': 'hour', 'state': 'active', 'mode': 'zvirka', 'date_from': date_from,
                                                          'date_to': False, 'company_id': False, 'rate': 2, 'pay_path': 'personal_36', 'customer_ids': [900], 'line_id': False}],
                    'members': [], 'line_customers': [], 'accruals': [], 'has_line': False, 'emp_contact': [],
                    'moves': [{'id': 5, 'name': 'INV/5', 'invoice_date': '2026-10-20', 'partner_id': [900, 'c'], 'commercial_partner_id': [900, 'c'], 'company_id': [1, 'w'],
                               'state': 'posted', 'payment_state': 'paid', 'pay_date': '2026-11-03'}],
                    'hours': [[5, {'rows': [[0, False, 40]], 'total': 40, 'free': 40}]]}
        res = calc.compute(data('2026-06-01'), TODAY, cfg)
        self.assertEqual([c['key'] for c in res['creates']], ['hour:c7:m5'])
        v = res['creates'][0]['vals']
        self.assertEqual((v['amount'], v['state'], v['company_id'], v['pay_date'], v.get('guard') or False), (80.0, 'expected', 36, '2026-11-10', False))
        near = calc.compute(data('2026-10-01'), TODAY, cfg)['creates'][0]['vals']
        self.assertEqual((near['amount'], near['state'], near['guard']), (80.0, 'review', 'no_timesheet'), 'дата рахунку біля «Діє з» — години без табеля на перевірку')

    # ── вычисляемые поля ──
    def test_pay_path_by_kind(self):
        Deal = self.env['workset.agent.deal.people']
        v17 = Deal.create({'agent_id': self.agent.id, 'kind': 'v17'})
        hour = Deal.create({'agent_id': self.agent.id, 'kind': 'hour'})
        custom = Deal.create({'agent_id': self.agent.id, 'kind': 'v17', 'pay_path': 'custom'})
        self.assertEqual((v17.pay_path, hour.pay_path), ('official_1', 'personal_36'))
        custom.kind = 'hour'
        self.assertEqual(custom.pay_path, 'custom', '«За запитом» вид не перебиває')
        self.assertEqual((v17.state, v17.mode, v17.currency_id), ('draft', 'pay', self.env.ref('base.EUR')))

    def test_agent_kind_state_and_partner_flag(self):
        # позначка контакту — за своїм полем workset_agent_ids (не agent_ids модуля Odoo «ai»); контакт без агента — без позначки
        self.assertEqual(self.agent.partner_id.workset_agent_ids, self.agent)
        self.assertTrue(self.agent.partner_id.is_agent)
        self.assertFalse(self.worker.is_agent)
        self.assertEqual((self.agent.kind, self.agent.state), ('none', 'none'))
        pd = self.env['workset.agent.deal.people'].create({'agent_id': self.agent.id, 'kind': 'hour', 'state': 'active'})
        self.assertEqual((self.agent.kind, self.agent.state), ('people', 'pay'))
        self.env['workset.agent.deal.client'].create({'agent_id': self.agent.id, 'kind': 'hour', 'state': 'active', 'mode': 'zvirka'})
        self.assertEqual((self.agent.kind, self.agent.state), ('both', 'zvirka'), 'найгірший із живих — «лише звірка»')
        pd.state = 'closed'
        self.env['workset.agent.member'].create({'deal_id': pd.id, 'partner_id': self.worker.id, 'date_from': '2026-01-01'})
        self.assertEqual(self.agent.people_n, 1)
        self.assertEqual(self.agent.member_ids.agent_id, self.agent)

    def test_decision_line_amounts_and_totals(self):
        dec = self.env['workset.agent.decision'].create({'pay_day': '2026-11-10', 'section': 'people'})
        line = self.env['workset.agent.decision.line'].create({'decision_id': dec.id, 'agent_id': self.agent.id, 'available': 100, 'pay_now': 60, 'wh_pct': 10})
        zv = self.env['workset.agent.decision.line'].create({'decision_id': dec.id, 'agent_id': self.agent.id, 'state': 'zvirka', 'accrued': 30, 'zv_diff': -5})
        self.assertEqual((line.pay_wh, line.pay_tax, line.pay_net, line.buffer_out), (6.0, 0.0, 54.0, 40.0))
        self.assertEqual(zv.buffer_out, 0.0)
        self.assertEqual((dec.available_total, dec.pay_total, dec.net_total, dec.buffer_total, dec.line_count), (100.0, 60.0, 54.0, 40.0, 1))
        self.assertEqual((dec.zvirka_total, dec.zv_more_total, dec.zv_less_total), (30.0, 5.0, 0.0))
        self.assertEqual(dec.name, 'Рішення · Агенти-кандидати · 10.11.2026')

    # обмеження неповторності — models.Constraint (у 19.0 _sql_constraints не діє): порушення в ORM — IntegrityError бази (UniqueViolation);
    # у ValidationError його перетворює лише шар RPC (odoo/service/model.py). assertRaises Odoo приймає один клас (issubclass), не кортеж.
    # Рядок ERROR «bad query» odoo.sql_db — очікуваний: ловимо assertLogs (у журнал збірки не йде) і за ним перевіряємо, ЯКЕ обмеження спрацювало
    def test_accrual_key_unique(self):
        Acc = self.env['workset.agent.accrual']
        vals = {'section': 'people', 'agent_id': self.agent.id, 'kind': 'oneoff', 'amount': 5, 'key': 'oneoff:test'}
        Acc.create(vals)
        Acc.create(dict(vals, key='oneoff:test2'))
        self.env.flush_all()
        with self.assertRaises(IntegrityError), self.assertLogs('odoo.sql_db', 'ERROR') as logs:
            Acc.create(dict(vals))
            self.env.flush_all()
        self.assertIn('workset_agent_accrual_key_uniq', '\n'.join(logs.output))

    def test_one_agent_per_contact(self):
        with self.assertRaises(IntegrityError), self.assertLogs('odoo.sql_db', 'ERROR') as logs:
            self.env['workset.agent'].create({'partner_id': self.agent.partner_id.id})
            self.env.flush_all()
        self.assertIn('workset_agent_partner_uniq', '\n'.join(logs.output))

    def test_zvirka_label_and_pct(self):
        self.env['ir.config_parameter'].sudo().set_param('workset_agents.card_year', '2026')
        z = self.env['workset.agent.zvirka'].create({'agent_id': self.agent.id, 'month': '2026-10-01', 'calc': 110, 'envelope': 100, 'diff': 10})
        self.assertEqual((z.month_label, z.diff_pct), ('Жовтень 2026', 10.0))
        self.assertEqual(self.agent.calc_year, 110.0)
