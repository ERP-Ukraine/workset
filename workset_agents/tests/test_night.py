# -*- coding: utf-8 -*-
"""Тести ночі «Агентів» на базі: нарахування за оплаченим рахунком, вхолосту нічого не пише, повтор не дублює, сторно, параметри, прод,
порядок і незалежність точок збереження, «Виплачено» за рахунком і рядком WS, права кнопок. Запускає Odoo.sh на dev-збірці.
Усі дані — вигадані в самому тесті; «сьогодні» — аргументом run_now (копія: параметр is_copy = 1)."""
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from ..models import agent_run, common

TODAY = '2026-11-05'
INV_DAY = '2026-10-20'
# логер ночі (getLogger(__name__)): очікувані рядки ERROR — лише під assertLogs з перевіркою тексту, інакше Odoo.sh ставить «Test: Failed»
RUN_LOG = agent_run.__name__


@tagged('post_install', '-at_install')
class TestAgentsNight(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # у 19.0 оточення AccountTestInvoicingCommon — не суперкористувач, а користувач accountman (бухгалтерія, адміністратор, групи
        # get_default_groups): агентів, домовленості, рішення, виплати тестів він створює сам — додаємо групу модуля «Власник».
        # Права кнопок перевіряють окремі користувачі with_user(власник / кадровик)
        return super().get_default_groups() | cls.env.ref('workset_agents.group_owner')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        co = cls.company_data['company']
        cls.co = co
        ICP = env['ir.config_parameter'].sudo()
        for k, v in [(common.P_OFFICIAL, co.id), (common.P_PERSONAL, co.id), (common.P_PERIMETER, co.id), (common.P_IS_COPY, 1),
                     (common.P_WS_CODES, 'TWS'), (common.P_WH_ANALYTIC, 0), (common.P_PROBE_TODAY, '2026-11-08')]:
            ICP.set_param(k, str(v))
        cls.hour = env.ref('uom.product_uom_hour')
        cls.cust = env['res.partner'].create({'name': 'Тест замовник ночі', 'is_company': True})
        cls.agent = env['workset.agent'].create({'name': 'Тест посередник'})
        cls.deal = env['workset.agent.deal.client'].create({'agent_id': cls.agent.id, 'kind': 'hour', 'state': 'active', 'mode': 'zvirka', 'rate': 2,
                                                            'date_from': '2026-06-01', 'customer_ids': [(6, 0, [cls.cust.id])]})
        # «Діє з» далеко від дати рахунку: годин без табеля — без сторожа no_timesheet (табелів у тесті немає)
        cls.Run = env['workset.agent.run']
        cls.Acc = env['workset.agent.accrual']
        cls.owner = new_test_user(env, login='agents_owner_t', groups='base.group_user,workset_agents.group_owner')
        cls.hr = new_test_user(env, login='agents_hr_t', groups='base.group_user,hr.group_hr_user')

    def _paid_invoice(self, hours=10, day=INV_DAY):
        inv = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.cust.id, 'invoice_date': day, 'date': day, 'company_id': self.co.id,
            'invoice_line_ids': [(0, 0, {'name': 'Години', 'quantity': hours, 'price_unit': 30, 'product_uom_id': self.hour.id, 'tax_ids': [(6, 0, [])]})]})
        inv.action_post()
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=inv.ids).create({'payment_date': day})._create_payments()
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))
        return inv

    def _mine(self):
        return self.Acc.search([('client_deal_id', '=', self.deal.id)])

    def test_dry_run_writes_nothing(self):
        inv = self._paid_invoice()
        runs = self.Run.search_count([])
        res = self.Run.run_now(dry=True, today=TODAY)
        self.assertEqual([c['key'] for c in res['creates']], ['hour:c%d:m%d' % (self.deal.id, inv.id)])
        self.assertEqual(res['creates'][0]['vals']['amount'], 20.0)
        self.assertFalse(self._mine(), 'вхолосту — нарахувань немає')
        self.assertEqual(self.Run.search_count([]), runs, 'вхолосту — рядка журналу немає')

    def test_night_writes_once_and_logs(self):
        inv = self._paid_invoice()
        res = self.Run.run_now(dry=False, today=TODAY)
        acc = self._mine()
        self.assertEqual(len(acc), 1)
        self.assertEqual((acc.key, acc.amount, acc.state, acc.hours, acc.agent_id, acc.move_id),
                         ('hour:c%d:m%d' % (self.deal.id, inv.id), 20.0, 'expected', 10.0, self.agent, inv), '«Лише звірка» — «Очікується»')
        log = self.Run.search([], limit=1)
        self.assertEqual((log.result, log.kind, log.created), ('OK', 'manual', 1))
        self.assertIn('accruals', res['order'])
        again = self.Run.run_now(dry=False, today=TODAY)
        self.assertEqual((again['creates'], again['writes'], again['stornos']), ([], [], []))
        self.assertEqual(len(self._mine()), 1, 'повтор не дублює')

    def test_storno_when_invoice_unpaid(self):
        inv = self._paid_invoice()
        self.Run.run_now(dry=False, today=TODAY)
        acc = self._mine()
        inv.line_ids.remove_move_reconcile()
        self.assertEqual(inv.payment_state, 'not_paid')
        self.Run.run_now(dry=False, today=TODAY)
        st = self.Acc.search([('storno_id', '=', acc.id)])
        self.assertEqual((st.kind, st.amount, st.state, acc.state), ('storno', -20.0, 'cancelled', 'cancelled'))
        self.assertTrue(st.key.endswith(':auto'))

    def test_missing_param_error_nothing_written(self):
        self._paid_invoice()
        self.env['ir.config_parameter'].sudo().set_param(common.P_PERIMETER, '')
        with self.assertLogs(RUN_LOG, 'ERROR') as logs:
            res = self.Run.run_now(dry=False, today=TODAY)
        self.assertIn('розрахунок не вдався: Агенти: параметр %s не заданий' % common.P_PERIMETER, '\n'.join(logs.output))
        self.assertIn(common.P_PERIMETER, res['error'])
        self.assertFalse(self._mine())
        log = self.Run.search([], limit=1)
        self.assertEqual(log.result, 'Помилка')
        self.assertIn('не заданий', log.summary)

    def test_prod_night_not_allowed_without_word(self):
        self._paid_invoice()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param(common.P_IS_COPY, '')
        # день проду — за Києвом: фіксуємо його, щоб ніч (крок рішень D−3, минулий день виплати) не залежала від дати прогону збірки
        with patch('odoo.addons.workset_agents.models.common.kyiv_today', return_value=TODAY):
            self.Run._cron_night()
            self.assertFalse(self._mine())
            self.assertEqual(self.Run.search([], limit=1).result, 'Не дозволено')
            # вхолосту — можна; probe_today на проді не діє (день — за Києвом), «сьогодні» аргументом — лише вхолосту
            self.assertTrue(self.Run.run_now(dry=True, today=TODAY)['creates'])
            ICP.set_param(common.P_NIGHT_PROD_OK, '1')
            self.Run._cron_night()
        self.assertEqual(len(self._mine()), 1)
        self.assertEqual(self.Run.search([], limit=1).result, 'OK')

    def test_points_independent_and_ordered(self):
        self._paid_invoice()
        Dec = type(self.env['workset.agent.decision'])
        boom = {'ops': [{'op': 'create', 'model': 'workset.agent.decision', 'ref': 'd1', 'vals': {}}], 'notes': [], 'summary': {}}  # без дня виплати — збій запису
        # поломка НАВМИСНА (підмінений крок рішень, модуль так рішень не створює): NOT NULL pay_day у базі — ERROR odoo.sql_db «bad query»,
        # точка відкочується — ERROR ночі «запис не вдався, відкочено»; обидва рядки очікувані — під assertLogs з текстом
        with patch.object(Dec, '_night_pay_step', lambda self, today, now, dry: boom), \
                self.assertLogs('odoo.sql_db', 'ERROR') as sql_logs, self.assertLogs(RUN_LOG, 'ERROR') as run_logs:
            res = self.Run.run_now(dry=False, today=TODAY)
        self.assertIn('pay_day', '\n'.join(sql_logs.output))
        self.assertIn('запис не вдався, відкочено: виплати:', '\n'.join(run_logs.output))
        self.assertEqual(res['order'][0], 'accruals')
        self.assertEqual(res['order'][-1], 'pay', 'рішення — після нарахувань')
        self.assertEqual(len(self._mine()), 1, 'збій кроку рішень не відкочує нарахування')
        self.assertEqual(self.Run.search([], limit=1).result, 'Частково')
        self.assertTrue(any(e.startswith('виплати') for e in res['errors']))

    def _payout(self, **kw):
        vals = {'agent_id': self.agent.id, 'section': 'client', 'pay_path': 'personal_36', 'state': 'prep', 'net': 36, 'gross': 40, 'pay_day': '2026-11-10'}
        vals.update(kw)
        return self.env['workset.agent.payout'].create(vals)

    def test_paid_by_ws_line(self):
        # переказ шукаємо від дня «Затвердити» рішення виплати
        dec = self.env['workset.agent.decision'].create({'pay_day': '2026-11-10', 'section': 'client', 'state': 'done', 'approved_at': '2026-11-07 10:00:00'})
        po = self._payout(decision_id=dec.id)
        acc = self.Acc.create({'section': 'client', 'agent_id': self.agent.id, 'kind': 'oneoff', 'amount': 40, 'state': 'decided', 'payout_id': po.id})
        journal = self.env['account.journal'].create({'name': 'Тест WS', 'code': 'TWS', 'type': 'general', 'company_id': self.co.id})
        a1 = self.company_data['default_account_revenue']
        a2 = self.company_data['default_account_expense']
        mv = self.env['account.move'].create({'journal_id': journal.id, 'date': '2026-11-08', 'line_ids': [
            (0, 0, {'account_id': a1.id, 'partner_id': self.agent.partner_id.id, 'credit': 36, 'debit': 0, 'name': 'агенту'}),
            (0, 0, {'account_id': a2.id, 'debit': 36, 'credit': 0, 'name': 'агенту'})]})
        mv.action_post()
        line = mv.line_ids.filtered(lambda l: l.credit)
        res = self.Run.run_now(dry=False, today='2026-11-10')
        self.assertEqual(res['errors'], [], 'журнал WS є — крок «Виплачено» без помилок')
        self.assertIn('paid', res['order'])
        self.assertEqual((po.state, po.paid_via, po.move_line_id, po.paid_amount), ('paid', 'ws', line, 36.0))
        self.assertEqual(acc.state, 'paid', 'виплата покрила рядок — нарахування «Виплачено»')
        # повтор — рядок уже прив'язаний, нічого не міняється
        self.Run.run_now(dry=False, today='2026-11-11')
        self.assertEqual(po.state, 'paid')

    def test_paid_no_ws_journal(self):
        # журналу WS з кодом параметра ws_journal_codes у фірмі personal немає: крок «Виплачено» (2б) не рахується — ясна помилка в журналі ночі
        # (рядок ERROR очікуваний — під assertLogs), виплата «особисто» не змінюється
        po = self._payout()
        with self.assertLogs(RUN_LOG, 'ERROR') as logs:
            res = self.Run.run_now(dry=False, today='2026-11-10')
        self.assertTrue(any(e.startswith('виплачено (розрахунок)') and 'TWS' in e for e in res['errors']), res['errors'])
        self.assertIn('журналів WS з кодами TWS у фірмі personal немає', '\n'.join(logs.output))
        self.assertNotIn('paid', res['order'])
        self.assertEqual(po.state, 'prep')
        self.assertNotEqual(self.Run.search([], limit=1).result, 'OK')

    def test_paid_by_vendor_bill(self):
        bill = self.env['account.move'].create({'move_type': 'in_invoice', 'partner_id': self.agent.partner_id.id, 'invoice_date': '2026-11-07',
                                                'company_id': self.co.id, 'invoice_line_ids': [(0, 0, {'name': 'Винагорода', 'quantity': 1, 'price_unit': 36, 'tax_ids': [(6, 0, [])]})]})
        bill.action_post()
        po = self._payout(pay_path='official_1', state='bill', move_id=bill.id)
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=bill.ids).create({'payment_date': '2026-11-08'})._create_payments()
        self.Run.run_now(dry=False, today='2026-11-10')
        self.assertIn(po.state, ('paid', 'paid_wait'))
        if po.state == 'paid':
            self.assertEqual(po.paid_via, 'statement')
        # сума рахунку ≠ «До переказу» — «На перевірці»
        po2 = self._payout(pay_path='official_1', state='bill', move_id=bill.id, net=35)
        self.Run.run_now(dry=False, today='2026-11-10')
        self.assertEqual((po2.state, po2.guard), ('review', 'bill_amount'))

    def test_buttons_rights(self):
        dec = self.env['workset.agent.decision'].create({'pay_day': '2026-11-25', 'section': 'client'})
        line = self.env['workset.agent.decision.line'].create({'decision_id': dec.id, 'agent_id': self.agent.id, 'available': 50, 'pay_now': 50, 'proposal': 30})
        # assertRaises Odoo — один клас (кортеж падає на issubclass): кнопки рішення — UserError «… може лише власник» (check_owner), run_now — AccessError
        with self.assertRaises(UserError):
            dec.with_user(self.hr).action_approve()
        with self.assertRaises(UserError):
            dec.with_user(self.hr).action_apply_advice()
        with self.assertRaises(UserError):
            dec.with_user(self.hr).action_stop()
        with self.assertRaises(AccessError):
            self.Run.with_user(self.hr).run_now(dry=True)
        dec.with_user(self.owner).action_apply_advice()
        self.assertEqual(line.pay_now, 30.0)
        line.pay_now = 60
        with self.assertRaises(UserError):
            dec.with_user(self.owner).action_approve()  # більше за «Разом до рішення»
        line.pay_now = 50
        dec.with_user(self.owner).action_approve()
        # одразу за «Затвердити» ніч готує виплати: у рядка немає нарахувань — «Сплатити зараз» поза 0…0, рішення «На перевірці» (як у ручній збірці)
        self.assertEqual((dec.approved_by, dec.state, line.state, line.guard), (self.owner, 'review', 'review', 'pay_range'))
        # «Сторно» — адміністратори кадрів і власник
        acc = self.Acc.create({'section': 'client', 'agent_id': self.agent.id, 'kind': 'oneoff', 'amount': 7, 'state': 'due', 'key': 'oneoff:t1', 'note': 'тест'})
        with self.assertRaises(UserError):
            acc.with_user(self.hr).action_storno()
        acc.with_user(self.owner).action_storno()
        st = self.Acc.search([('storno_id', '=', acc.id)])
        self.assertEqual((acc.state, st.state, st.amount, st.key), ('cancelled', 'cancelled', -7.0, 'storno:oneoff:t1'))
        with self.assertRaises(UserError):
            acc.with_user(self.owner).action_storno()
        # сторно скасували — повторне «Сторно» дає ключ «#2», а не сиру помилку бази
        st.state = 'cancelled'
        acc.state = 'due'
        acc.with_user(self.owner).action_storno()
        self.assertEqual(self.Acc.search([('storno_id', '=', acc.id), ('state', '=', 'cancelled'), ('id', '!=', st.id)]).key, 'storno:oneoff:t1#2')

    def test_manual_paid_wizard_partial(self):
        po = self._payout()
        dec = self.env['workset.agent.decision'].create({'pay_day': '2026-11-10', 'section': 'client'})
        dl = self.env['workset.agent.decision.line'].create({'decision_id': dec.id, 'agent_id': self.agent.id, 'available': 40, 'pay_now': 40})
        po.write({'pay_line_id': dl.id, 'decision_id': dec.id})
        Wiz = self.env['workset.agent.paid.wizard']
        with self.assertRaises(AccessError):  # кадровику вікна немає (права — бухгалтерія й «Власник»): відмова вже на створенні
            Wiz.with_user(self.hr).create({'payout_ids': [(6, 0, po.ids)], 'note': 'x', 'source': 'other'}).action_confirm()
        w = Wiz.with_user(self.owner).create({'payout_ids': [(6, 0, po.ids)], 'note': 'квитанція', 'source': 'other', 'date': '2026-11-10'})
        w.amount = 18
        w.action_confirm()
        self.assertEqual((po.state, po.paid_amount, po.paid_manual, po.paid_by), ('paid_part', 18.0, True, self.owner))
        self.assertEqual(dl.unpaid, 20.0, 'недоплата брутто: 18 з 36 → половина з 40')
        self.assertEqual(dl.buffer_out, 20.0)
