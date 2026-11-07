# -*- coding: utf-8 -*-
"""Тести кроку рішень «Агентів» на базі (круг 2): ніч D−3 — чернетка рішення; «Затвердити» → підготовка (виплата personal, чернетка рахунку
постачальника official з payment_reference = номер виплати, нарахування «Закрито рішенням», список офісу активністю); буфер у наступне рішення;
«Пропущено»; автозатвердження вимкнено → тривога «ask» власнику; «Зупинити»; права. Запускає Odoo.sh на dev-збірці. Дані вигадані."""
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from ..models import common

D = '2026-11-10'


@tagged('post_install', '-at_install')
class TestAgentsDecision(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # оточення AccountTestInvoicingCommon у 19.0 — користувач accountman, не суперкористувач: дані тестів (агент, домовленості, нарахування,
        # рішення, виплати) він створює з групою модуля «Власник»; права кнопок — окремі користувачі with_user(власник / кадровик)
        return super().get_default_groups() | cls.env.ref('workset_agents.group_owner')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        co = cls.company_data['company']
        cls.co = co
        cls.owner = new_test_user(env, login='agents_dec_owner', groups='base.group_user,workset_agents.group_owner')
        cls.hr = new_test_user(env, login='agents_dec_hr', groups='base.group_user,hr.group_hr_user')
        plan = env['account.analytic.plan'].create({'name': 'Тест план агентів'})
        cls.analytic = env['account.analytic.account'].create({'name': 'Тест агенти', 'plan_id': plan.id, 'company_id': False})
        purchase = cls.company_data['default_journal_purchase']
        expense = cls.company_data['default_account_expense']
        todo = env.ref('mail.mail_activity_data_todo')
        ICP = env['ir.config_parameter'].sudo()
        for k, v in [(common.P_OFFICIAL, co.id), (common.P_PERSONAL, co.id), (common.P_PERIMETER, co.id), (common.P_IS_COPY, 1), (common.P_WS_CODES, 'TWS'),
                     (common.P_WH_ANALYTIC, 0), (common.P_BILL_JOURNAL, purchase.code), (common.P_FEE_ACCOUNT, expense.code), (common.P_TAX_ACCOUNT, expense.code),
                     (common.P_ANALYTIC, cls.analytic.id), (common.P_OWNER_UID, cls.owner.id), (common.P_OFFICE_TYPE, todo.id),
                     (common.P_OFFICE_TO, 'Офіс'), (common.P_CARD_YEAR, 2026), (common.P_PROBE_TODAY, '2026-11-08')]:
            # probe_today — «сьогодні» кнопок «Затвердити» / «Зупинити» на копії (інакше тест залежав би від дати прогону)
            ICP.set_param(k, str(v))
        # журнал WS фірми personal з кодом параметра ws_journal_codes: без нього крок «Виплачено» (2б) ночі з підготовленою виплатою «особисто»
        # відкочувався б з помилкою (dev-збірка 03c9252ef, test_buffer_to_next_decision), а тести йшли б без цього кроку
        cls.ws_journal = env['account.journal'].create({'name': 'Тест WS рішень', 'code': 'TWS', 'type': 'general', 'company_id': co.id})
        cls.agent = env['workset.agent'].create({'name': 'Тест агент рішень'})
        cls.bank = env['res.partner.bank'].create({'partner_id': cls.agent.partner_id.id, 'acc_number': 'TESTAGENTIBAN0001', 'allow_out_payment': True})
        cls.pd_personal = env['workset.agent.deal.people'].create({'agent_id': cls.agent.id, 'kind': 'oneoff', 'state': 'active', 'date_from': '2026-10-01'})
        cls.pd_official = env['workset.agent.deal.people'].create({'agent_id': cls.agent.id, 'kind': 'v17', 'state': 'active', 'date_from': '2026-10-01'})
        Acc = env['workset.agent.accrual']
        cls.a_pers = Acc.create({'section': 'people', 'agent_id': cls.agent.id, 'people_deal_id': cls.pd_personal.id, 'kind': 'oneoff', 'amount': 60,
                                 'state': 'due', 'pay_date': D, 'earned_date': '2026-11-02', 'key': 'oneoff:tdec1', 'note': 'тест: за кандидата'})
        cls.a_off = Acc.create({'section': 'people', 'agent_id': cls.agent.id, 'people_deal_id': cls.pd_official.id, 'kind': 'm30', 'amount': 50,
                                'state': 'due', 'pay_date': D, 'earned_date': '2026-11-02', 'key': 'm30:tdec2', 'note': 'тест: рубіж'})
        cls.Run = env['workset.agent.run']
        cls.Dec = env['workset.agent.decision']

    def _night(self, today):
        # ніч без помилок кроків: збій кроку відкочується мовчки (лише рядок ERROR у журналі), і тест пройшов би без нього
        res = self.Run.run_now(dry=False, today=today)
        self.assertFalse(res.get('error') or res.get('errors'), 'ніч %s: %s' % (today, res.get('error') or res.get('errors')))
        return res

    def _decision(self):
        return self.Dec.search([('pay_day', '=', D), ('section', '=', 'people')])

    def test_draft_then_prepare(self):
        self._night('2026-11-07')
        dec = self._decision()
        self.assertEqual((len(dec), dec.state), (1, 'draft'), 'ніч D−3 — чернетка')
        lines = dec.pay_line_ids.sorted('key')
        self.assertEqual(sorted(lines.mapped('pay_path')), ['official_1', 'personal_36'])
        self.assertEqual(sorted(lines.mapped('available')), [50.0, 60.0])
        self.assertTrue(all(l.pay_now == 0 for l in lines), '«Сплатити зараз» вводить власник')
        self.assertEqual(self.a_pers.pay_line_id.decision_id, dec)
        # тривога «ask» власнику: автозатвердження вимкнено (auto_approve не задано)
        ask = self.env['mail.activity'].search([('res_model', '=', 'workset.agent.decision'), ('res_id', '=', dec.id)])
        self.assertEqual((len(ask), ask.user_id), (1, self.owner))
        # наступна ніч — друга така сама тривога не ставиться (полів штабу на тестовій базі немає — за рішенням і заголовком)
        self._night('2026-11-08')
        self.assertEqual(self.env['mail.activity'].search_count([('res_model', '=', 'workset.agent.decision'), ('res_id', '=', dec.id)]), 1)
        # «Затвердити» — лише власник
        with self.assertRaises(UserError):
            dec.with_user(self.hr).action_approve()
        for l in lines:
            l.with_user(self.owner).pay_now = l.available
        dec.with_user(self.owner).action_approve()
        self.assertEqual(dec.state, 'done', 'затверджено й підготовлено тією ж ніччю')
        pays = dec.payout_ids
        pers = pays.filtered(lambda p: p.pay_path == 'personal_36')
        off = pays.filtered(lambda p: p.pay_path == 'official_1')
        self.assertEqual((pers.gross, pers.wh10, pers.net, pers.state, pers.bank_ref), (60.0, 6.0, 54.0, 'prep', self.bank.id))
        self.assertEqual((off.gross, off.tax20, off.net, off.state), (50.0, 10.0, 40.0, 'bill'), 'профілю податків немає — 20 %')
        bill = off.move_id
        self.assertEqual((bill.move_type, bill.state, bill.partner_id, bill.payment_reference), ('in_invoice', 'draft', self.agent.partner_id, 'AGENTS-P%d' % off.id))
        self.assertAlmostEqual(bill.amount_total, 40.0)
        self.assertEqual((self.a_pers.state, self.a_off.state), ('decided', 'decided'))
        self.assertEqual(self.a_pers.payout_id, pers)
        self.assertEqual(set(lines.mapped('state')), {'done'})
        cron = self.env.ref('workset_agents.cron_night')
        bot = self.env.ref('base.user_root')
        office = self.env['mail.activity'].search([('res_model', '=', 'ir.cron'), ('res_id', '=', cron.id), ('user_id', '=', bot.id)])
        self.assertEqual(len(office), 1, 'список офісу — активністю на OdooBot (доставку робить рутина office-desk; полів штабу немає — без них)')
        self.assertIn('виплати', office.summary)

    def test_owner_buttons_not_locked_on_prod(self):
        # П-1: на проді без night_prod_ok планова ніч не пише, а «Затвердити» власника одразу готує виплати (як в еталоні)
        self._night('2026-11-07')
        dec = self._decision()
        for l in dec.pay_line_ids:
            l.pay_now = l.available
        self.env['ir.config_parameter'].sudo().set_param(common.P_IS_COPY, '')
        with patch('odoo.addons.workset_agents.models.common.kyiv_today', return_value='2026-11-08'):
            self.Run._cron_night()
            self.assertEqual(self.Run.search([], limit=1).result, 'Не дозволено')
            dec.with_user(self.owner).action_approve()
        self.assertEqual(dec.state, 'done')
        self.assertTrue(dec.payout_ids)
        self.assertEqual(self.Run.search([], limit=1).kind, 'approve')

    def test_buffer_to_next_decision(self):
        self._night('2026-11-07')
        dec = self._decision()
        for l in dec.pay_line_ids:
            l.pay_now = 30 if l.pay_path == 'personal_36' else l.available
        dec.with_user(self.owner).action_approve()
        self.assertEqual(dec.state, 'done')
        pers_line = dec.pay_line_ids.filtered(lambda l: l.pay_path == 'personal_36')
        self.assertEqual(pers_line.buffer_out, 30.0)
        res = self._night('2026-11-22')
        # крок «Виплачено» (2б) рахувався: підготовлена виплата «особисто» є, журнал WS є, переказу в ньому немає — виплата чекає
        self.assertIsNotNone(res['paid'])
        self.assertEqual(dec.payout_ids.filtered(lambda p: p.pay_path == 'personal_36').state, 'prep')
        nxt = self.Dec.search([('pay_day', '=', '2026-11-25'), ('section', '=', 'people')])
        line = nxt.pay_line_ids.filtered(lambda l: l.pay_path == 'personal_36')
        self.assertEqual((line.buffer_in, line.accrued, line.available, line.buffer_from_id), (30.0, 0.0, 30.0, pers_line))

    def test_skipped_moves_accruals(self):
        self._night('2026-11-07')
        dec = self._decision()
        self._night('2026-11-11')
        self.assertEqual(dec.state, 'skipped')
        self.assertEqual(set(dec.pay_line_ids.mapped('state')), {'skipped'})
        self.assertEqual((self.a_pers.pay_line_id.decision_id != dec, str(self.a_pers.pay_date)), (True, '2026-11-25'), 'нарахування — на наступний день виплати')
        self.assertFalse(dec.payout_ids)

    def test_stop_auto_decision(self):
        dec = self.Dec.create({'pay_day': '2026-11-25', 'section': 'people', 'state': 'done', 'auto': True, 'approved_by': self.owner.id})
        line = self.env['workset.agent.decision.line'].create({'decision_id': dec.id, 'agent_id': self.agent.id, 'state': 'done', 'available': 60, 'pay_now': 60,
                                                               'key': '%d:people:personal_36:%d' % (self.agent.id, self.co.id)})
        po = self.env['workset.agent.payout'].create({'agent_id': self.agent.id, 'section': 'people', 'pay_path': 'personal_36', 'state': 'prep', 'net': 54,
                                                      'gross': 60, 'decision_id': dec.id, 'pay_line_id': line.id, 'pay_day': '2026-11-25'})
        self.a_pers.write({'state': 'decided', 'pay_line_id': line.id, 'payout_id': po.id, 'pay_date': '2026-11-25'})
        self.env['ir.config_parameter'].sudo().set_param(common.P_PROBE_TODAY, '2026-11-20')
        with self.assertRaises(UserError):
            dec.with_user(self.hr).action_stop()
        dec.with_user(self.owner).action_stop()
        self.assertEqual((dec.state, line.state, po.state, self.a_pers.state), ('skipped', 'skipped', 'cancelled', 'due'))
        self.assertIn('зупинено власником', line.guard_note)
        # після D−1 — відмова
        dec2 = self.Dec.create({'pay_day': '2026-11-20', 'section': 'client', 'state': 'done', 'auto': True})
        with self.assertRaises(UserError):
            dec2.with_user(self.owner).action_stop()

    def test_card_year_param(self):
        Zv = self.env['workset.agent.zvirka']
        Zv.create({'agent_id': self.agent.id, 'month': '2025-10-01', 'calc': 7, 'envelope': 7})
        Zv.create({'agent_id': self.agent.id, 'month': '2026-10-01', 'calc': 11, 'envelope': 10})
        self.assertEqual((self.agent.card_year, self.agent.calc_year), (2026, 11.0))
        self.env['ir.config_parameter'].sudo().set_param(common.P_CARD_YEAR, '2025')
        self.agent.invalidate_recordset(['card_year', 'calc_year'])
        self.assertEqual((self.agent.card_year, self.agent.calc_year), (2025, 7.0))
