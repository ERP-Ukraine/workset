# -*- coding: utf-8 -*-
"""Тести «Ставок»: запускаються Odoo.sh на dev-збірці (і локально: odoo-bin -i workset_rates --test-enable --test-tags workset_rates).
Усі дані — вигадані в самому тесті; «сьогодні» підміняється контекстом rates_today (лише адміністратору)."""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..models import agent_deals, calc

DAY = '2026-03-02'   # понеділок
NEXT = '2026-03-03'
DAY3 = '2026-03-04'


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
        cls.line = env['workset.line'].create({'name': 'Тест лінія', 'code': 'TL', 'share_line_found': 6, 'share_we_found': 7, 'customer_ids': [(6, 0, [cls.cust.id])]})
        Emp = env['hr.employee']
        cls.emp_a = Emp.with_company(cls.co_a).create({'name': 'Тест працівник', 'company_id': cls.co_a.id, 'hourly_cost': 10})
        cls.person = cls.emp_a.work_contact_id
        cls.emp_b = Emp.with_company(cls.co_b).create({'name': 'Тест працівник', 'company_id': cls.co_b.id, 'hourly_cost': 10, 'work_contact_id': cls.person.id})
        cls.emp_uah = Emp.with_company(cls.co_uah).create({'name': 'Тест працівник', 'company_id': cls.co_uah.id, 'hourly_cost': 300, 'work_contact_id': cls.person.id})
        cls.emp_out = Emp.with_company(cls.co_out).create({'name': 'Тест працівник', 'company_id': cls.co_out.id, 'hourly_cost': 10, 'work_contact_id': cls.person.id})
        Project = env['project.project']
        cls.p_a = Project.with_company(cls.co_a).create({'name': 'Тест об’єкт А', 'partner_id': cls.cust.id, 'company_id': cls.co_a.id})
        cls.p_b = Project.with_company(cls.co_b).create({'name': 'Тест об’єкт Б', 'partner_id': cls.cust.id, 'company_id': cls.co_b.id})
        cls.p_uah = Project.with_company(cls.co_uah).create({'name': 'Тест об’єкт гривня', 'partner_id': cls.cust.id, 'company_id': cls.co_uah.id})
        cls.p_out = Project.with_company(cls.co_out).create({'name': 'Тест об’єкт поза', 'partner_id': cls.cust.id, 'company_id': cls.co_out.id})
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
            (self.emp_a | self.emp_b | self.emp_uah | self.emp_out).sudo().current_version_id.write({'hourly_wage': wage})

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
        self.assertAlmostEqual(r.price, 21, msg='ціна = на руки + правило лінії «знайшли ми»')
        self.assertAlmostEqual(r.our_share, 7)
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
        p2 = self.env['project.project'].with_company(self.co_a).create({'name': 'Тест об’єкт А2', 'partner_id': self.cust.id, 'company_id': self.co_a.id})
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
        r = self._rate(price=30)  # частка 16 ≠ правило 7 → «не за правилом»
        self.assertFalse(r.rule_ok)
        self.assertTrue(r.contract_ok, 'договір 14 = ставка або модуля зарплат немає — «Договір ≠ ставка» не горить')
        todo = self.env.ref('mail.mail_activity_data_todo')
        Act = self.env['mail.activity']
        res1 = self.R.rates_check()
        self.assertTrue(res1['created'])
        self.assertEqual(res1['sections']['rule'], 1)
        self.assertEqual(res1['sections']['contract'], 0)
        self.assertEqual(res1['sections']['agent'], 0, 'у лінії немає агента — розділу посередників немає')
        res1b = self.R.rates_check()
        self.assertEqual(res1b['reused'], res1['created'], 'повтор того ж дня — той самий запис')
        opened = Act.search([('res_model', '=', 'workset.rate.check'), ('activity_type_id', '=', todo.id)])
        self.assertEqual(len(opened), 1, 'відкрита завжди одна задача')
        res2 = self.R.with_context(rates_today=NEXT).rates_check()
        self.assertTrue(res2['created'] and res2['created'] != res1['created'])
        self.assertEqual(res2['closed'], 1, 'минула задача закрита «замінено новою звіркою»')
        opened = Act.search([('res_model', '=', 'workset.rate.check'), ('activity_type_id', '=', todo.id)])
        self.assertEqual(opened.res_id, res2['created'])
        r.price = 21
        res3 = self.R.with_context(rates_today=DAY3).rates_check()
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

    def test_agent_share_chain_without_deal_model(self):
        if agent_deals.DEAL in self.env:
            self.skipTest('модуль посередників встановлено — ланцюжок перевіряють чисті функції calc')
        r = self._rate()
        self.assertEqual(r.agent_share, 0.0, 'домовленостей немає, агента в лінії немає — 0')
        self.assertEqual(r.agent_source, 'none')
        self.assertIn('посередників немає', r.agent_note)
        agent = self.env['res.partner'].create({'name': 'Тест посередник'})
        self.line.write({'agent_id': agent.id, 'agent_share': 3})
        r2 = self._rate(date_from=NEXT, wage=14)
        self.assertAlmostEqual(r2.agent_share, 3, msg='запасне правило: частка агента з лінії')
        self.assertEqual(r2.agent_source, 'line')
        self.assertAlmostEqual(r2.price, 24, msg='ціна = на руки + правило 7 + посередник 3')
        self.assertAlmostEqual(r2.our_share, 7)

    def test_agent_manual_survives_night_and_resets_on_date(self):
        agent = self.env['res.partner'].create({'name': 'Тест посередник'})
        self.line.write({'agent_id': agent.id, 'agent_share': 3})
        r = self._rate()
        self.assertEqual(r.agent_source, 'line')
        r.agent_share = 2
        self.assertEqual(r.agent_source, 'manual', 'правка руками — джерело «вручну»')
        self.assertIn('вручну', r.agent_note)
        self.R.rates_daily()
        self.assertAlmostEqual(r.agent_share, 2, msg='ніч ручну правку не перетирає (лінія 3)')
        r.date_from = calc.add_days(DAY, -1)
        self.assertEqual(r.agent_source, 'line', 'зміна дати скидає «вручну» — знову ланцюжок')
        self.assertAlmostEqual(r.agent_share, 3)

    def test_agent_manual_on_create_survives_night(self):
        agent = self.env['res.partner'].create({'name': 'Тест посередник'})
        self.line.write({'agent_id': agent.id, 'agent_share': 3})
        r = self._rate(agent_share=2)
        self.assertEqual(r.agent_source, 'manual', 'при створенні число ≠ ланцюжку (лінія 3) — «вручну»')
        self.assertAlmostEqual(r.agent_share, 2)
        self.R.rates_daily()
        self.assertAlmostEqual(r.agent_share, 2, msg='ніч ручну частку зі створення не перетирає')
        same = self._rate(date_from=NEXT, agent_share=3)
        self.assertEqual(same.agent_source, 'line', 'число = ланцюжку — джерело ланцюжка')

    def test_agent_source_gone_falls_to_next_level(self):
        agent = self.env['res.partner'].create({'name': 'Тест посередник'})
        self.line.write({'agent_id': agent.id, 'agent_share': 3})
        r = self._rate()
        self.assertAlmostEqual(r.agent_share, 3)
        self.line.write({'agent_id': False})
        self.R.rates_daily()
        self.assertAlmostEqual(r.agent_share, 0, msg='джерело (лінія) перестало діяти — 0, а не збережене число')
        self.assertEqual(r.agent_source, 'none')

    def test_customer_change_resets_manual(self):
        agent = self.env['res.partner'].create({'name': 'Тест посередник'})
        self.line.write({'agent_id': agent.id, 'agent_share': 3, 'customer_ids': [(4, self.cust2.id)]})
        r = self._rate()
        r.agent_share = 1
        self.assertEqual(r.agent_source, 'manual')
        r.customer_id = self.cust2
        self.assertEqual(r.agent_source, 'line', 'зміна замовника скидає «вручну»')
        self.assertAlmostEqual(r.agent_share, 3)

    def test_unlink_dry_and_no_sync_only_admin(self):
        r = self._rate()
        r.with_context(rates_dry=True).unlink()
        self.assertTrue(r.exists(), 'сухий режим нічого не видаляє')
        self.assertAlmostEqual(self.m_a.cost, 14)
        editor = new_test_user(self.env, login='ws_rates_editor2', groups='base.group_user,workset_rates.group_editor')
        as_editor = r.with_user(editor).with_context(allowed_company_ids=[editor.company_id.id], workset_rates_no_sync=True)
        as_editor.write({'wage': 15})
        self.assertAlmostEqual(self.m_a.cost, 15, msg='не адміністратору «без автоматики» не діє — А1 спрацювала')
