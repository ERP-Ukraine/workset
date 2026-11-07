# -*- coding: utf-8 -*-
"""Тести «Виїздів»: запускаються Odoo.sh на dev-збірці (і локально: odoo-bin -i workset_dispatch --test-enable --test-tags workset_dispatch).
Усі дані — вигадані в самому тесті; «сьогодні» підміняється контекстом workset_today (лише адміністратору). Чисті функції імен і підписів
(models/names.py, models/calc.py) окремо перевіряє стенд штабу без Odoo — tools/tests/odoo_dispatch_module.test.js.
Юля й Оля в тестах діють з однією вибраною фірмою (фірма заявок) — як у житті, коли в перемикачі фірм не всі."""
import ast
import os
import socket
from datetime import date
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, new_test_user, tagged

TODAY = '2026-11-09'
CODE_FIELD = 'x_code'
PARTNER_FIELD = 'x_partner_id'
PHONE = '+380 67 000 11 22'
GROUPS = 'base.group_user,hr.group_hr_manager,hr_recruitment.group_hr_recruitment_manager'


@tagged('post_install', '-at_install')
class TestWorksetDispatch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Company = env['res.company']
        cls.co_rec = Company.create({'name': 'Тест фірма заявок'})
        cls.co_a = Company.create({'name': 'Тест фірма А', 'workset_dispatch_firm': True, 'workset_name_suffix': 'AA'})
        cls.co_b = Company.create({'name': 'Тест фірма Б', 'workset_dispatch_firm': True, 'workset_name_suffix': 'BB'})
        cls.companies = Company.search([])
        access = {'company_id': cls.co_rec.id, 'company_ids': [(6, 0, cls.companies.ids)]}
        cls.u_firm = new_test_user(env, login='wd_firm_user', groups=GROUPS, **access)
        cls.u_hr = new_test_user(env, login='wd_hr_user', groups=GROUPS, **access)
        cls.u_plain = new_test_user(env, login='wd_plain_user', groups='base.group_user', **access)
        cls.u_recruit = new_test_user(env, login='wd_recruit_user', groups='base.group_user,hr_recruitment.group_hr_recruitment_user', **access)
        icp = env['ir.config_parameter'].sudo()
        icp.set_param('workset_dispatch.firm_user_id', str(cls.u_firm.id))
        icp.set_param('workset_dispatch.hr_user_id', str(cls.u_hr.id))
        icp.set_param('workset_dispatch.recruit_company_id', str(cls.co_rec.id))
        icp.set_param('workset_dispatch.pd_domain', 'test.example.com')
        # поля вакансії, які на проді створені через API (на чистій базі їх немає)
        model = env['ir.model']._get('hr.job')
        if CODE_FIELD not in env['hr.job']._fields:
            env['ir.model.fields'].create({'name': CODE_FIELD, 'model_id': model.id, 'field_description': 'Код', 'ttype': 'char', 'state': 'manual'})
        if PARTNER_FIELD not in env['hr.job']._fields:
            env['ir.model.fields'].create({'name': PARTNER_FIELD, 'model_id': model.id, 'field_description': 'Замовник', 'ttype': 'many2one',
                                           'relation': 'res.partner', 'state': 'manual'})
        cls.customer = env['res.partner'].create({'name': 'Тест замовник', 'is_company': True})
        # вакансії лежать у фірмі заявок (так і на проді: 13 з 15 — у фірмі вакансій)
        cls.job = env['hr.job'].create({'name': 'Тест вакансія', 'company_id': cls.co_rec.id, CODE_FIELD: 'WS-TT-0101-01', PARTNER_FIELD: cls.customer.id})
        cls.env = env(user=env.ref('base.user_admin'), context=dict(env.context, workset_today=TODAY, allowed_company_ids=cls.companies.ids))
        cls.App = cls.env['hr.applicant']
        cls.Emp = cls.env['hr.employee'].with_context(active_test=False)

    # ——— помічники ———
    def _item(self, **kw):
        item = {
            'deal_id': 9001, 'person_id': 501, 'last_name': 'TESTOV', 'first_name': 'PETRO', 'phone': PHONE, 'birthday': '1990-05-17',
            'citizenship': 'Украина', 'profession': 'Бетонщик, Арматурщик', 'vacancy_label': 'WS-TT-0101-01 Тест', 'org_id': 77,
            'org_name': 'Тест замовник', 'departure_date': TODAY,
        }
        item.update(kw)
        return item

    def _import(self, **kw):
        res = self.App.workset_dispatch_import([self._item(**kw)])
        return res[0], self.App.with_context(active_test=False).browse(res[0]['applicant_id'])

    def _card(self, company, name, **kw):
        vals = {'name': name, 'company_id': company.id}
        vals.update(kw)
        return self.Emp.with_company(company).create(vals)

    def _tasks(self, rec):
        return {a.summary: a for a in rec.activity_ids}

    def _as(self, user, app):
        """Заявка очима Юлі чи Олі: вибрана одна фірма — фірма заявок."""
        return app.with_user(user).with_context(allowed_company_ids=[self.co_rec.id], active_test=False)

    # ——— (1) заявка з усіма полями, повторний прогін без дубля ———
    def test_01_created_with_all_fields_and_idempotent(self):
        res, app = self._import()
        self.assertEqual(res['decision'], 'created')
        self.assertTrue(res['created'])
        self.assertEqual(app.partner_name, 'TESTOV PETRO')
        self.assertEqual(app.partner_phone, PHONE)
        self.assertEqual(app.job_id, self.job)
        self.assertEqual(app.company_id, self.co_rec)
        self.assertEqual(app.stage_id, self.env.ref('workset_dispatch.stage_dispatch'))
        self.assertEqual(app.user_id, self.u_firm)
        self.assertEqual(app.workset_pd_deal_id, 9001)
        self.assertEqual(app.workset_pd_person_id, 501)
        self.assertEqual(app.workset_departure_date, date(2026, 11, 9))
        self.assertEqual(app.workset_birthday, date(1990, 5, 17))
        self.assertEqual(app.workset_country_id.code, 'UA')
        self.assertEqual(app.workset_profession, 'Бетонщик, Арматурщик')
        self.assertEqual(app.workset_customer_id, self.customer)
        self.assertEqual(app.workset_state, 'wait_firm')
        self.assertFalse(app.workset_name_check)
        self.assertEqual(app.workset_pd_url, 'https://test.example.com/deal/9001')
        task = self._tasks(app)['Фірма для ZUS']
        self.assertEqual(task.user_id, self.u_firm)
        self.assertEqual(task.date_deadline, date(2026, 11, 9), 'строк — сьогодні')
        self.assertIn('виїзд 2026-11-09', task.note)
        again, same = self._import()
        self.assertEqual(again['decision'], 'exists')
        self.assertEqual(same, app)
        self.assertEqual(self.App.with_context(active_test=False).search_count([('workset_pd_deal_id', '=', 9001)]), 1)

    # ——— (2) вхолостую нічого не пише; замало даних — skipped ———
    def test_02_dry_run_and_skipped(self):
        before = self.App.with_context(active_test=False).search_count([])
        res = self.App.workset_dispatch_import([self._item()], dry_run=True)[0]
        self.assertEqual(res['decision'], 'created')
        self.assertFalse(res['created'])
        self.assertEqual(self.App.with_context(active_test=False).search_count([]), before)
        for bad in ({'departure_date': ''}, {'deal_id': 0}, {'last_name': '', 'first_name': ''}):
            self.assertEqual(self.App.workset_dispatch_import([self._item(**bad)])[0]['decision'], 'skipped')
        res = self.App.workset_dispatch_import([self._item(vacancy_label='📦 У базу (немає вакансії)')])[0]
        self.assertEqual(res['decision'], 'no_job')
        self.assertTrue(res['no_job'])
        self.assertFalse(self.App.browse(res['applicant_id']).job_id)
        self.assertEqual(self.App.browse(res['applicant_id']).company_id, self.co_rec)

    # ——— (3) угода стала lost → не поїхав, архів ———
    def test_03_cancel_archives(self):
        _res, app = self._import()
        out = self.App.workset_dispatch_cancel([9001, 9999])
        self.assertEqual(out, {9001: 'cancelled', 9999: 'skipped'})
        self.assertEqual((app.workset_state, app.active, app.workset_cancel_source), ('cancel', False, 'pipedrive'))
        app.invalidate_recordset()
        self.assertFalse(app.activity_ids)
        # кнопка «Не наш / не поїхав» — те саме, але «позначила людина»
        _res, app2 = self._import(deal_id=9002, person_id=502, last_name='NEXT', first_name='ONE', phone='+380 50 111 22 33')
        app2.workset_dispatch_not_ours()
        self.assertEqual((app2.workset_state, app2.active, app2.employee_id.id, app2.workset_cancel_source), ('cancel', False, False, 'human'))

    # ——— (4) вибір фірми → картка ———
    def test_04_firm_creates_card(self):
        _res, app = self._import()
        app.write({'workset_firm_id': self.co_a.id})
        emp = app.employee_id
        self.assertTrue(emp)
        self.assertEqual(emp.company_id, self.co_a)
        self.assertEqual(emp.name, 'TESTOV PETRO | AA')
        self.assertEqual(emp.private_phone, PHONE)
        self.assertEqual(emp.private_country_id.code, 'UA')
        self.assertEqual(emp.birthday, date(1990, 5, 17))
        self.assertEqual(emp.job_title, 'Бетонщик, Арматурщик')
        self.assertEqual(emp.employee_type, 'employee')
        self.assertEqual((emp.workset_pd_deal_id, emp.workset_pd_person_id), (9001, 501))
        self.assertEqual(emp.hr_responsible_id, self.u_hr)
        self.assertFalse(emp.version_id.contract_date_start, 'договору немає')
        self.assertEqual(app.stage_id, self.env.ref('workset_dispatch.stage_hired'))
        self.assertTrue(app.stage_id.hired_stage)
        self.assertEqual(app.workset_state, 'done')
        self.assertTrue(app.date_closed)
        task = self._tasks(emp)['Договір і ставка']
        self.assertEqual(task.user_id, self.u_hr)
        self.assertEqual(task.date_deadline, date(2026, 11, 10))
        self.assertIn('Тест замовник', task.note)
        app.invalidate_recordset()
        self.assertNotIn('Фірма для ZUS', self._tasks(app), 'задача Юлі закрита')
        self.assertEqual(emp.workset_dispatch_count, 1)

    # ——— (5) архівна картка тієї ж фірми за контактом → повернена, нової немає ———
    def test_05_archived_card_same_firm_returned(self):
        old = self._card(self.co_a, 'TESTOV PETRO | AA', workset_pd_person_id=501, private_phone='KEEP-ME')
        old.with_context(no_wizard=True).action_archive()
        self.assertFalse(old.active)
        res, app = self._import()
        self.assertEqual(res['decision'], 'strong')
        self.assertEqual(res['firms'], ['AA'])
        app.write({'workset_firm_id': self.co_a.id})
        self.assertEqual(app.employee_id, old)
        self.assertTrue(old.active)
        self.assertEqual(old.private_phone, 'KEEP-ME', 'наявне не перезаписується')
        self.assertEqual(old.birthday, date(1990, 5, 17), 'порожнє дописано')
        self.assertEqual(self.Emp.search_count([('workset_pd_person_id', '=', 501)]), 1)

    # ——— (6) картка в іншій фірмі → нова в обраній, той самий work_contact ———
    def test_06_other_firm_new_card_shares_work_contact(self):
        other = self._card(self.co_b, 'TESTOV PETRO | BB', workset_pd_person_id=501)
        res, app = self._import()
        self.assertEqual(res['decision'], 'strong')
        self.assertEqual(res['firms'], ['BB'])
        app.write({'workset_firm_id': self.co_a.id})
        emp = app.employee_id
        self.assertNotEqual(emp, other)
        self.assertEqual(emp.company_id, self.co_a)
        self.assertEqual(emp.work_contact_id, other.work_contact_id)
        self.assertTrue(emp.work_contact_id)
        self.assertEqual(other.company_id, self.co_b, 'стара картка не чіпана')

    # ——— (7) збіг лише за ім'ям → перевірити дубль, рішення Олі (одна вибрана фірма — фірма заявок) ———
    def test_07_name_only_check_dup_as_hr(self):
        near = self._card(self.co_b, 'TESTOV  PETRO | BB')   # інший контакт і телефон, два пробіли
        res, app = self._import(person_id=777, phone='+380 99 555 44 33')
        self.assertEqual(res['decision'], 'weak')
        self.assertEqual(app.workset_state, 'check_dup')
        self.assertEqual(app.sudo().workset_dup_employee_ids, near)
        self.assertIn('TESTOV  PETRO | BB', self._as(self.u_hr, app).workset_dup_text, 'дублі видно текстом, хоч фірма Б не вибрана')
        self.assertIn('Тест фірма Б', app.workset_dup_text)
        self.assertEqual(self._tasks(app)['Перевірити дубль'].user_id, self.u_hr)
        # Юля вибирає фірму — картки ще немає
        self._as(self.u_firm, app).write({'workset_firm_id': self.co_a.id})
        self.assertFalse(app.employee_id)
        self.assertEqual(app.workset_state, 'check_dup')
        # Оля: «Нова людина» → картка в обраній фірмі
        self._as(self.u_hr, app).workset_dispatch_new_person()
        self.assertEqual(app.workset_state, 'done')
        self.assertEqual(app.employee_id.company_id, self.co_a)
        self.assertNotEqual(app.employee_id, near)

    def test_07b_same_person_single_candidate_and_numbered_choice(self):
        # одна схожа картка: «Це та сама людина» бере її без вибору
        same = self._card(self.co_a, 'ROSSUM KAREL | AA')
        res, app = self._import(deal_id=9003, person_id=778, last_name='ROSSUM', first_name='KAREL', phone='+380 99 000 00 01')
        self.assertEqual(res['decision'], 'weak')
        self._as(self.u_firm, app).write({'workset_firm_id': self.co_a.id})
        self._as(self.u_hr, app).workset_dispatch_same_person()
        self.assertEqual(app.employee_id, same)
        self.assertEqual(app.workset_state, 'done')
        # кілька схожих: без номера — UserError, з номером — вибрана картка
        c1 = self._card(self.co_a, 'LANGE ULRIKE | AA')
        c2 = self._card(self.co_b, 'LANGE ULRIKE | BB')
        _res, app2 = self._import(deal_id=9004, person_id=779, last_name='LANGE', first_name='ULRIKE', phone='+380 99 000 00 02')
        self.assertEqual(app2.sudo().workset_dup_employee_ids, c1 | c2)
        self.assertEqual(app2.workset_dup_text.count('\n'), 1)
        with self.assertRaises(UserError):
            self._as(self.u_hr, app2).workset_dispatch_same_person()
        app2.workset_pick_no = 2
        self._as(self.u_firm, app2).write({'workset_firm_id': self.co_b.id})
        self._as(self.u_hr, app2).workset_dispatch_same_person()
        self.assertEqual(app2.employee_id, c2, 'друга зі списку (за порядком id), у фірмі Б')

    def test_07c_fuzzy_names_invented_pair(self):
        # вигадані: латинське прізвище з кириличною «А» в кінці, -A/-O, ім'я з вставленою Z
        near = self._card(self.co_a, 'ZELENKOVSKO  TAMDIN | AA')
        far = self._card(self.co_a, 'PETRO BRANTENKO | AA')
        res, app = self._import(deal_id=9010, person_id=910, last_name='ZELENKOVSKА', first_name='TAMDZIN', phone='+380 11 000 00 02')
        self.assertEqual(res['decision'], 'weak')
        self.assertEqual(app.partner_name, 'ZELENKOVSKA TAMDZIN')
        self.assertEqual(app.sudo().workset_dup_employee_ids, near)
        res2, app2 = self._import(deal_id=9011, person_id=911, last_name='TESTENKO', first_name='BRANT', phone='+380 11 000 00 03')
        self.assertEqual(res2['decision'], 'created')
        self.assertFalse(app2.sudo().workset_dup_employee_ids & far)

    def test_07d_equivalent_first_names(self):
        near = self._card(self.co_a, 'KRUMLOVSKY MYKHAILO | AA')
        res, app = self._import(deal_id=9012, person_id=912, last_name='KRUMLOVSKY', first_name='MIKHAIL', phone='+380 11 000 00 04')
        self.assertEqual(res['decision'], 'weak')
        self.assertEqual(app.sudo().workset_dup_employee_ids, near)

    # ——— телефон без імені — не «та сама людина» ———
    def test_07e_phone_rules(self):
        card = self._card(self.co_a, 'ALPHA ONE | AA', private_phone='+380 77 123 45 67')
        # основний телефон + схоже ім'я — сильний
        res, _app = self._import(deal_id=9020, person_id=920, last_name='ALPHA', first_name='ONE', phone='+380 77 123 45 67')
        self.assertEqual((res['decision'], res['firms']), ('strong', ['AA']))
        # основний телефон, ім'я інше — перевірити дубль
        res, app = self._import(deal_id=9021, person_id=921, last_name='GAMMA', first_name='THREE', phone='+380 77 123 45 67')
        self.assertEqual(res['decision'], 'weak')
        self.assertEqual((app.workset_state, app.sudo().workset_dup_employee_ids), ('check_dup', card))
        # додатковий телефон контакту = телефон картки з іншим іменем — перевірити дубль
        res, app = self._import(deal_id=9022, person_id=922, last_name='DELTA', first_name='FOUR', phone='+380 11 222 33 44', phones=['+380 77 123 45 67'])
        self.assertEqual(res['decision'], 'weak')
        self.assertEqual(app.sudo().workset_dup_employee_ids, card)

    def test_07f_shared_phone_is_not_a_match(self):
        self._card(self.co_a, 'ALPHA ONE | AA', private_phone='+380 77 123 45 67')
        self._card(self.co_b, 'BETA TWO | BB', private_phone='+380 77 123 45 67')
        res, _app = self._import(deal_id=9023, person_id=923, last_name='GAMMA', first_name='THREE', phone='+380 77 123 45 67')
        self.assertEqual(res['decision'], 'created', 'телефон двох різних імен — спільний')

    # ——— (8) кирилиця → латиниця і ознака звірки ———
    def test_08_cyrillic_to_latin(self):
        _res, ua = self._import(deal_id=9030, person_id=930, last_name='Іванов', first_name='Сергій', phone='+380 11 000 00 04', citizenship='Украина')
        self.assertEqual(ua.partner_name, 'IVANOV SERHII')
        self.assertTrue(ua.workset_name_check)
        _res, by = self._import(deal_id=9031, person_id=931, last_name='Іванаў', first_name='Сяргей', phone='+375 11 000 00 05', citizenship='Беларусь')
        self.assertEqual(by.partner_name, 'IVANAU SIARHEI')
        self.assertTrue(by.workset_name_check)
        self.assertEqual(by.workset_country_id.code, 'BY')
        _res, other = self._import(deal_id=9032, person_id=932, last_name='Попов', first_name='Иван', phone='+375 11 000 00 06', citizenship='Другие страны')
        self.assertTrue(other.workset_name_check)
        self.assertFalse(other.workset_country_id)

    # ——— порядок слів в імені: Pipedrive набирають по-різному ———
    def test_08b_name_order_from_full_name(self):
        # «ІМ'Я ПРІЗВИЩЕ» з перепутаними first/last Pipedrive → «ПРІЗВИЩЕ ІМ'Я», звірка не потрібна
        _res, a = self._import(deal_id=9090, person_id=990, full_name='ANDRII TESTENKO', last_name='ANDRII', first_name='TESTENKO', phone='+380 11 000 00 20')
        self.assertEqual((a.partner_name, a.workset_name_check), ('TESTENKO ANDRII', False))
        _res, b = self._import(deal_id=9091, person_id=991, full_name='TESTENKO ANDRII', last_name='TESTENKO', first_name='ANDRII', phone='+380 11 000 00 21')
        self.assertEqual((b.partner_name, b.workset_name_check), ('TESTENKO ANDRII', False))
        # обидва слова — імена: як набрано і звірка
        _res, c = self._import(deal_id=9092, person_id=992, full_name='IVAN PAVLO', phone='+380 11 000 00 22')
        self.assertEqual((c.partner_name, c.workset_name_check), ('IVAN PAVLO', True))
        # кирилиця: перестановка + транслітерація зі звіркою
        _res, d = self._import(deal_id=9093, person_id=993, full_name='Андрій Тестенко', phone='+380 11 000 00 23')
        self.assertEqual((d.partner_name, d.workset_name_check), ('TESTENKO ANDRII', True))
        # картка отримує ім'я в паспортному порядку
        a.write({'workset_firm_id': self.co_a.id})
        self.assertEqual(a.employee_id.name, 'TESTENKO ANDRII | AA')

    # ——— (9) модуль не ходить у мережу ———
    def test_09_no_network_code(self):
        root = get_module_path('workset_dispatch')
        banned = ('import requests', 'from requests', 'import urllib', 'from urllib', 'import http', 'from http', 'import socket', 'urlopen')
        for base, _dirs, files in os.walk(root):
            if os.path.basename(base) == 'tests':
                continue
            for name in files:
                if name.endswith('.py'):
                    with open(os.path.join(base, name), encoding='utf-8') as f:
                        text = f.read()
                    for word in banned:
                        self.assertNotIn(word, text, '%s: %s' % (name, word))
        with patch.object(socket, 'create_connection', side_effect=AssertionError('мережа')), patch.object(socket.socket, 'connect', side_effect=AssertionError('мережа')):
            self._import(deal_id=9040, person_id=940, last_name='NETLESS', first_name='ONE', phone='+380 11 000 00 07')

    # ——— (10) заявки й найм не пишуть у hr.job (крім лічильника, що його зменшує стандарт) ———
    def test_10_job_not_written(self):
        Job = type(self.env['hr.job'])
        original = Job.write
        seen = []

        def spy(records, vals):
            seen.append(set(vals))
            return original(records, vals)

        before = self.job.read(['name', 'active', 'company_id'])
        with patch.object(Job, 'write', spy):
            _res, app = self._import()
            app.write({'workset_firm_id': self.co_a.id})
        self.assertEqual(app.workset_state, 'done')
        for keys in seen:
            self.assertLessEqual(keys, {'no_of_recruitment'}, 'сайт слухає name, active, company_id і x_*')
        self.assertEqual(self.job.read(['name', 'active', 'company_id']), before)

    # ——— контакт працівника ———
    def test_12_existing_partner_reused(self):
        partner = self.env['res.partner'].create({'name': 'TESTOV PETRO', 'phone': PHONE})
        _res, app = self._import()
        app.write({'workset_firm_id': self.co_a.id})
        self.assertEqual(app.employee_id.work_contact_id, partner)

    def test_12b_doubt_default_and_task_line(self):
        p1 = self.env['res.partner'].create({'name': 'TESTOV PETRO', 'phone': PHONE})
        p2 = self.env['res.partner'].create({'name': 'PETRO TESTOV'})
        _res, app = self._import()
        app.write({'workset_firm_id': self.co_a.id})
        emp = app.employee_id
        self.assertNotIn(emp.work_contact_id, p1 | p2)
        self.assertIn('перевір контакт', self._tasks(emp)['Договір і ставка'].note)

    def test_12c_other_persons_contact_not_taken(self):
        stranger = self._card(self.co_b, 'STRANGER MAN | BB')
        stranger.work_contact_id.name = 'TESTOV PETRO'
        _res, app = self._import()
        app.write({'workset_firm_id': self.co_a.id})
        self.assertNotEqual(app.employee_id.work_contact_id, stranger.work_contact_id)

    # ——— права ———
    def test_13_access(self):
        App = self.App.with_user(self.u_plain)
        with self.assertRaises(AccessError):
            App.workset_dispatch_import([self._item()])
        with self.assertRaises(AccessError):
            App.workset_dispatch_cancel([9001])

    def test_13b_card_only_for_hr_officer(self):
        if self.u_recruit.has_group('hr.group_hr_user'):
            self.skipTest('група підбору в цій збірці вже включає права кадрів — перевірка неможлива')
        _res, app = self._import()
        with self.assertRaises(AccessError):
            self._as(self.u_recruit, app).write({'workset_firm_id': self.co_a.id})
        self.assertFalse(app.employee_id)

    # ——— Юля й Оля з однією вибраною фірмою ———
    def test_14_firm_and_buttons_as_users_with_one_firm(self):
        _res, app = self._import()
        self._as(self.u_firm, app).write({'workset_firm_id': self.co_a.id})
        self.assertEqual(app.employee_id.company_id, self.co_a)
        self.assertEqual(app.workset_state, 'done')
        _res, app2 = self._import(deal_id=9060, person_id=960, last_name='NOTOURS', first_name='MAN', phone='+380 11 000 00 08')
        self._as(self.u_firm, app2).workset_dispatch_not_ours()
        self.assertEqual((app2.workset_state, app2.active), ('cancel', False))
        _res, app3 = self._import(deal_id=9061, person_id=961, last_name='NOTOURS', first_name='TWO', phone='+380 11 000 00 09')
        self._as(self.u_hr, app3).workset_dispatch_not_ours()
        self.assertEqual(app3.workset_state, 'cancel')

    # ——— архів вакансії архівує заявки — Виїзд не пропадає з екрана ———
    def test_15_archived_job_keeps_dispatch_visible(self):
        job = self.env['hr.job'].create({'name': 'Тест архівна вакансія', 'company_id': self.co_rec.id, CODE_FIELD: 'WS-TT-0202-02'})
        _res, app = self._import(deal_id=9070, person_id=970, last_name='ARCHIVED', first_name='JOB', phone='+380 11 000 00 10', vacancy_label='WS-TT-0202-02')
        self.assertEqual(app.job_id, job)
        job.active = False
        self.assertFalse(app.active, 'стандарт: архів вакансії архівує заявки')
        action = self.env.ref('workset_dispatch.action_workset_dispatch')
        domain = ast.literal_eval(action.domain)
        ctx = ast.literal_eval(action.context)
        self.assertFalse(ctx.get('active_test'), 'дія показує й архівні')
        self.assertIn('search_default_not_cancel', ctx)
        self.assertFalse(self.App.search(domain + [('id', '=', app.id)]), 'без active_test=False Виїзд зник би')
        self.assertTrue(self.App.with_context(active_test=False).search(domain + [('workset_state', '!=', 'cancel'), ('id', '=', app.id)]))
        self._as(self.u_firm, app).write({'workset_firm_id': self.co_a.id})
        self.assertEqual(app.workset_state, 'done')

    # ——— угода знову на «ОТПРАВКА» й перенесена дата ———
    def test_16_reopened_and_updated(self):
        _res, app = self._import(deal_id=9080, person_id=980, last_name='REOPEN', first_name='ME', phone='+380 11 000 00 11')
        # перенесена дата відправки
        res = self.App.workset_dispatch_import([self._item(deal_id=9080, person_id=980, last_name='REOPEN', first_name='ME', departure_date='2026-11-12')])[0]
        self.assertEqual(res['decision'], 'updated')
        self.assertEqual(app.workset_departure_date, date(2026, 11, 12))
        res = self.App.workset_dispatch_import([self._item(deal_id=9080, person_id=980, last_name='REOPEN', first_name='ME', departure_date='2026-11-12')])[0]
        self.assertEqual(res['decision'], 'exists', 'та сама дата — без змін')
        # lost → скасовано → знову на етапі — повернуто, нова задача Юлі
        self.App.workset_dispatch_cancel([9080])
        self.assertFalse(app.active)
        item = self._item(deal_id=9080, person_id=980, last_name='REOPEN', first_name='ME', departure_date='2026-11-12', phone='+380 11 000 00 11')
        dry = self.App.workset_dispatch_import([item], dry_run=True)[0]
        self.assertEqual(dry['decision'], 'reopened')
        self.assertFalse(app.active, 'вхолостую нічого не повертає')
        res = self.App.workset_dispatch_import([item])[0]
        self.assertEqual(res['decision'], 'reopened')
        app.invalidate_recordset()
        self.assertTrue(app.active)
        self.assertEqual((app.workset_state, app.workset_cancel_source), ('wait_firm', False))
        self.assertIn('Фірма для ZUS', self._tasks(app))
        # скасований людиною не повертається, хоч угода й на етапі
        _res, hand = self._import(deal_id=9081, person_id=981, last_name='HANDMADE', first_name='ONE', phone='+380 11 000 00 12')
        hand.workset_dispatch_not_ours()
        res = self.App.workset_dispatch_import([self._item(deal_id=9081, person_id=981, last_name='HANDMADE', first_name='ONE', phone='+380 11 000 00 12')])[0]
        self.assertEqual(res['decision'], 'exists')
        self.assertEqual(hand.workset_state, 'cancel')
