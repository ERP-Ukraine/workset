# -*- coding: utf-8 -*-
"""«Виїзд» — стандартна заявка підбору hr.applicant, що в день «Дата відправки» заводить сервер штабу (метод workset_dispatch_import).
Модуль не ходить у мережу: Pipedrive читає інструмент штабу і передає прості словники. Тут — повтори, заявка, вибір фірми, картка, задачі.
Заявки й найм не пишуть у hr.job, крім лічильника no_of_recruitment, який стандартний перехід у «найнятий» етап зменшує сам (сайт цього поля не слухає)."""
import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import calc, names

_logger = logging.getLogger(__name__)

P = 'workset_dispatch.'
# Поля вакансії, створені через API (не модулем): читаються м'яко, імена — з параметрів із цими значеннями за замовчуванням
DEFAULT_JOB_CODE = 'x_code'
DEFAULT_JOB_PARTNER = 'x_partner_id'

STATES = [
    ('wait_firm', 'Чекає фірму'),
    ('check_dup', 'Перевірити дубль'),
    ('done', 'Картка є'),
    ('cancel', 'Не поїхав'),
]
OPEN_STATES = ('wait_firm', 'check_dup')


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    workset_pd_deal_id = fields.Integer(string='Угода Pipedrive', index=True, copy=False)
    workset_pd_person_id = fields.Integer(string='Контакт Pipedrive', index=True, copy=False)
    workset_departure_date = fields.Date(string='Дата виїзду', copy=False, tracking=True)
    workset_birthday = fields.Date(string='Дата народження', copy=False)
    workset_country_id = fields.Many2one('res.country', string='Громадянство', copy=False)
    workset_profession = fields.Char(string='Професія', copy=False)
    workset_customer_id = fields.Many2one('res.partner', string='Замовник', copy=False)
    workset_customer_name = fields.Char(string='Замовник (назва з Pipedrive)', copy=False)
    workset_firm_id = fields.Many2one('res.company', string='Фірма для ZUS', copy=False, tracking=True,
                                      domain="[('workset_dispatch_firm', '=', True)]")
    workset_state = fields.Selection(STATES, string='Статус виїзду', index=True, copy=False, tracking=True)
    workset_dup_employee_ids = fields.Many2many('hr.employee', 'workset_dispatch_dup_rel', 'applicant_id', 'employee_id',
                                                string='Можливі дублі', copy=False, context={'active_test': False})
    workset_same_employee_ids = fields.Many2many('hr.employee', 'workset_dispatch_same_rel', 'applicant_id', 'employee_id',
                                                 string='Ця ж людина (за контактом чи телефоном)', copy=False, context={'active_test': False})
    workset_pick_no = fields.Integer(string='Номер картки зі списку', copy=False,
                                     help='Яка картка зі списку схожих — та сама людина (якщо схожа одна, номер не потрібен)')
    workset_dup_text = fields.Text(string='Схожі картки', compute='_compute_workset_texts')
    workset_same_text = fields.Text(string='Ця ж людина вже є', compute='_compute_workset_texts')
    workset_cancel_source = fields.Selection([('pipedrive', 'Угода закрита в Pipedrive'), ('human', 'Позначила людина')],
                                             string='Хто скасував', copy=False)
    workset_name_check = fields.Boolean(string="Звірити ім'я з паспортом", copy=False)
    workset_pd_url = fields.Char(string='Угода в Pipedrive', compute='_compute_workset_pd_url')
    workset_firm_activity_id = fields.Many2one('mail.activity', copy=False, ondelete='set null')
    workset_dup_activity_id = fields.Many2one('mail.activity', copy=False, ondelete='set null')

    _workset_pd_deal_uniq = models.UniqueIndex(
        '(workset_pd_deal_id) WHERE workset_pd_deal_id > 0',
        'Виїзд для цієї угоди Pipedrive вже є.',
    )

    @api.depends('workset_pd_deal_id')
    def _compute_workset_pd_url(self):
        domain = self.env['ir.config_parameter'].sudo().get_param(P + 'pd_domain') or ''
        for rec in self:
            rec.workset_pd_url = 'https://%s/deal/%s' % (domain, rec.workset_pd_deal_id) if domain and rec.workset_pd_deal_id else False

    @api.depends('workset_dup_employee_ids', 'workset_same_employee_ids')
    def _compute_workset_texts(self):
        # через sudo: у Odoo 19 пов'язані записи чужих фірм у many2many відсіюються за вибраними фірмами, а людині треба бачити всі схожі картки
        for rec in self:
            rec.workset_dup_text = rec._workset_employees_text(rec.sudo().workset_dup_employee_ids)
            rec.workset_same_text = rec._workset_employees_text(rec.sudo().workset_same_employee_ids)

    @api.model
    def _workset_employees_text(self, employees):
        lines = []
        for i, e in enumerate(employees.sudo().with_context(active_test=False).sorted('id'), 1):
            lines.append('%d) %s — %s%s' % (i, e.name, e.company_id.name or '', _(' (в архіві)') if not e.active else ''))
        return '\n'.join(lines) or False

    # ——— налаштування й оточення ———
    @api.model
    def _workset_cfg(self):
        icp = self.env['ir.config_parameter'].sudo()

        def user(key):
            v = int(icp.get_param(P + key) or 0)
            return self.env['res.users'].sudo().browse(v).exists() if v else self.env['res.users']

        company_id = int(icp.get_param(P + 'recruit_company_id') or 0)
        return {
            'job_code_field': icp.get_param(P + 'job_code_field') or DEFAULT_JOB_CODE,
            'job_partner_field': icp.get_param(P + 'job_partner_field') or DEFAULT_JOB_PARTNER,
            'firm_user': user('firm_user_id'),
            'hr_user': user('hr_user_id'),
            'recruit_company': self.env['res.company'].sudo().browse(company_id).exists() if company_id else self.env['res.company'],
        }

    def _workset_env(self):
        """Повні права й усі фірми: заявки й картки лежать у різних фірмах, а працює Юля чи сервер штабу."""
        companies = self.env['res.company'].sudo().search([])
        return self.sudo().with_context(allowed_company_ids=companies.ids)

    def _workset_today(self):
        # context workset_today — лише для тестів (адміністратору)
        forced = self.env.context.get('workset_today')
        if forced and self.env.is_admin():
            return fields.Date.to_date(forced)
        return fields.Date.context_today(self)

    @api.model
    def _workset_check_caller(self):
        user = self.env.user
        if not (user.has_group('hr.group_hr_manager') or user.has_group('base.group_system')):
            raise AccessError(_('Виїзди з Pipedrive заводять лише керівники кадрів та адміністратори.'))

    # ——— повтори ———
    def _workset_employee_index(self):
        """Усі картки (з архівом, усі фірми) → [{'id', 'name', 'person_id', 'phones': {останні 9 цифр}, 'firm'}]."""
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False)
        phone_fields = [f for f in ('private_phone', 'mobile_phone') if f in Emp._fields]
        rows = Emp.search_read([], ['name', 'company_id', 'workset_pd_person_id'] + phone_fields)
        index = []
        for r in rows:
            phones = {p for p in (names.phone9(r.get(f)) for f in phone_fields) if p}
            index.append({
                'id': r['id'], 'name': r['name'] or '', 'person_id': r.get('workset_pd_person_id') or 0, 'phones': phones,
                'firm': r['company_id'][0] if r['company_id'] else 0,
            })
        return index

    def _workset_firm_label(self, company_ids):
        out = []
        for c in self.env['res.company'].sudo().browse(sorted(set(company_ids))):
            out.append(c.workset_name_suffix or c.name)
        return out

    # ——— вхід від інструмента штабу ———
    @api.model
    def workset_dispatch_import(self, items, dry_run=False):
        """items — [{deal_id, person_id, full_name, last_name, first_name, phone, phones, birthday, citizenship, profession, vacancy_label, org_id,
        org_name, departure_date}] → по кожній угоді {deal_id, decision, firms, created, applicant_id, no_job}.
        decision: created (нових повторів немає) | strong (той самий контакт чи телефон у картці; firms — фірми) | weak (збіг лише за ім'ям)
        | no_job (вакансії за кодом немає) | exists (Виїзд цієї угоди вже є) | skipped (замало даних) | error.
        Ідемпотентно за deal_id; dry_run=True нічого не пише."""
        self._workset_check_caller()
        App = self._workset_env()
        cfg = App._workset_cfg()
        index = App._workset_employee_index()
        shared = calc.shared_phones([(p, e['name']) for e in index for p in e['phones']])
        results = []
        for item in items or []:
            deal_id = int((item or {}).get('deal_id') or 0)
            try:
                with self.env.cr.savepoint():
                    res = App._workset_import_one(item or {}, cfg, index, shared, bool(dry_run))
            except Exception as e:  # noqa: BLE001 — одна угода не зупиняє решту; секретів у модулі немає, тип і початок повідомлення — у журнал
                _logger.warning('Виїзд, угода %s: збій (%s: %s)', deal_id, type(e).__name__, ' '.join(str(e).split())[:200])
                res = {'deal_id': deal_id, 'decision': 'error', 'firms': [], 'created': False, 'applicant_id': 0, 'no_job': False}
            results.append(res)
        return results

    def _workset_import_one(self, item, cfg, index, shared, dry_run):
        deal_id = int(item.get('deal_id') or 0)
        res = {'deal_id': deal_id, 'decision': 'skipped', 'firms': [], 'created': False, 'applicant_id': 0, 'no_job': False}
        dep = calc.first_date(item.get('departure_date'))
        if not deal_id or not dep or not (item.get('full_name') or item.get('last_name') or item.get('first_name')):
            return res
        existing = self.with_context(active_test=False).search([('workset_pd_deal_id', '=', deal_id)], limit=1)
        reopen = bool(existing) and existing.workset_state == 'cancel' and existing.workset_cancel_source == 'pipedrive'
        if existing and not reopen:
            res.update(decision='exists', applicant_id=existing.id)
            # перенесена «Дата відправки» у відкритого Виїзду — оновити дату
            if existing.workset_state in OPEN_STATES and existing.workset_departure_date != fields.Date.to_date(dep):
                res['decision'] = 'updated'
                if not dry_run:
                    existing.message_post(body=_('Дату виїзду змінено: %s → %s') % (existing.workset_departure_date, dep))
                    existing.write({'workset_departure_date': dep})
            return res
        person_id = int(item.get('person_id') or 0)
        country_code = calc.country_code(item.get('citizenship'))
        country = self.env['res.country'].search([('code', '=', country_code)], limit=1) if country_code else self.env['res.country']
        # порядок слів у Pipedrive різний — full_name (як набрано) упорядковується за довідником імен; first/last — лише коли full_name порожнє
        full_name, was_translit = names.order_name(item.get('full_name'), item.get('first_name'), item.get('last_name'), country_code)
        phones = calc.phone_list(item.get('phone'), item.get('phones'))
        phones9 = [p for p in (names.phone9(p) for p in phones) if p]
        primary9 = names.phone9(phones[0]) if phones else ''
        extra9 = [p for p in (names.phone9(x) for x in phones[1:]) if p]
        strong, weak = calc.classify(person_id, primary9, extra9, full_name, index, shared)
        job = self._workset_find_job(cfg, calc.vacancy_code(item.get('vacancy_label')))
        res['no_job'] = not job
        by_id = {e['id']: e for e in index}
        res['firms'] = self._workset_firm_label([by_id[i]['firm'] for i in strong])
        res['decision'] = 'strong' if strong else 'weak' if weak else 'no_job' if not job else 'created'
        if reopen:
            res.update(decision='reopened', applicant_id=existing.id)
            if not dry_run:
                existing._workset_reopen(dep, strong, weak)
            return res
        if dry_run:
            return res
        customer, customer_name = self._workset_find_customer(cfg, job, item.get('org_name'))
        company = job.company_id or cfg['recruit_company'] or self.env.company
        vals = {
            'partner_name': full_name,
            'partner_phone': (phones[0] if phones else False) and phones[0][:32],
            'job_id': job.id or False,
            'company_id': company.id,
            'stage_id': self.env.ref('workset_dispatch.stage_dispatch').id,
            'user_id': cfg['firm_user'].id or False,
            'workset_pd_deal_id': deal_id,
            'workset_pd_person_id': person_id,
            'workset_departure_date': dep,
            'workset_birthday': calc.first_date(item.get('birthday')) or False,
            'workset_country_id': country.id or False,
            'workset_profession': (item.get('profession') or '').strip() or False,
            'workset_customer_id': customer.id or False,
            'workset_customer_name': customer_name or False,
            'workset_name_check': was_translit,
            'workset_state': 'check_dup' if (weak and not strong) else 'wait_firm',
            'workset_same_employee_ids': [(6, 0, strong)],
            'workset_dup_employee_ids': [(6, 0, weak if not strong else [])],
        }
        app = self.with_context(mail_create_nolog=True).create(vals)
        app.message_post(body=Markup(_('Створено з Pipedrive, угода %s')) % deal_id)
        app._workset_schedule_tasks(strong, weak)
        res.update(created=True, applicant_id=app.id)
        return res

    def _workset_find_job(self, cfg, code):
        Job = self.env['hr.job'].sudo().with_context(active_test=False)
        field = cfg['job_code_field']
        if not code or field not in Job._fields:
            return Job
        return Job.search([(field, '=ilike', code)], order='active desc, id', limit=1)

    def _workset_find_customer(self, cfg, job, org_name):
        Partner = self.env['res.partner'].sudo()
        field = cfg['job_partner_field']
        if job and field in job._fields and job[field] and job[field]._name == 'res.partner':
            return job[field], False
        org = (org_name or '').strip()
        if org:
            found = Partner.search([('is_company', '=', True), ('name', '=ilike', org)], limit=1)
            if found:
                return found, False
        return Partner, org

    # ——— задачі ———
    def _workset_note(self, *lines):
        return Markup('<br/>').join(escape(l) for l in lines if l)

    def _workset_schedule_tasks(self, strong, weak):
        cfg = self._workset_cfg()
        for app in self:
            customer = app.workset_customer_id.name or app.workset_customer_name or ''
            line_who = '%s, %s' % (app.partner_name, _('виїзд %s') % app.workset_departure_date)
            if cfg['firm_user']:
                app.workset_firm_activity_id = app.activity_schedule(
                    'mail.mail_activity_data_todo', date_deadline=self._workset_today(), summary=_('Фірма для ZUS'),
                    note=self._workset_note(line_who, customer and _('Замовник: %s') % customer, app.workset_pd_url),
                    user_id=cfg['firm_user'].id)
            if weak and not strong and cfg['hr_user']:
                names_ = ', '.join(app.workset_dup_employee_ids.mapped('name'))
                app.workset_dup_activity_id = app.activity_schedule(
                    'mail.mail_activity_data_todo', date_deadline=self._workset_today(), summary=_('Перевірити дубль'),
                    note=self._workset_note(line_who, _('Схожі картки: %s') % names_), user_id=cfg['hr_user'].id)

    def _workset_close_tasks(self, done=True):
        for app in self:
            for act in (app.workset_firm_activity_id | app.workset_dup_activity_id).exists():
                if done:
                    act.sudo().action_feedback()
                else:
                    act.sudo().unlink()

    # ——— відміна ———
    @api.model
    def workset_dispatch_cancel(self, deal_ids):
        """Угоди, що стали lost чи видалені: відкриті Виїзди (чекає фірму / перевірити дубль) → «Не поїхав» і архів; картки немає.
        → {deal_id: cancelled | skipped (картка вже є чи Виїзду немає)}."""
        self._workset_check_caller()
        App = self._workset_env().with_context(active_test=False)
        out = {}
        for deal_id in deal_ids or []:
            app = App.search([('workset_pd_deal_id', '=', int(deal_id))], limit=1)
            if app and app.workset_state in OPEN_STATES:
                app._workset_cancel(_('Угода в Pipedrive закрита чи видалена'), 'pipedrive')
                out[int(deal_id)] = 'cancelled'
            else:
                out[int(deal_id)] = 'skipped'
        return out

    def _workset_cancel(self, why, source='human'):
        for app in self:
            app._workset_close_tasks(done=False)
            app.message_post(body=why)
            app.write({'workset_state': 'cancel', 'active': False, 'workset_cancel_source': source})

    def _workset_reopen(self, dep, strong, weak):
        """Скасований через Pipedrive Виїзд, чия угода знову на етапі «ОТПРАВКА» й не lost: повертаємо, нова задача Юлі (скасований людиною не повертається)."""
        self.ensure_one()
        self.write({
            'active': True, 'workset_state': 'check_dup' if (weak and not strong) else 'wait_firm', 'workset_departure_date': dep,
            'workset_cancel_source': False, 'workset_firm_id': False, 'workset_pick_no': 0,
            'workset_same_employee_ids': [(6, 0, strong)], 'workset_dup_employee_ids': [(6, 0, weak if not strong else [])],
        })
        self.message_post(body=_('Угода знову на етапі відправки — Виїзд повернуто'))
        self._workset_schedule_tasks(strong, weak)

    def workset_dispatch_not_ours(self):
        """Кнопка «Не наш / не поїхав»."""
        for app in self.filtered(lambda a: a.workset_state in OPEN_STATES):
            app._workset_cancel(_('Не наш або не поїхав — позначила людина'))
        return True

    # ——— вибір фірми й картка ———
    def write(self, vals):
        res = super().write(vals)
        if vals.get('workset_firm_id') and not self.env.context.get('workset_skip_make'):
            self.filtered(lambda a: a.workset_state in OPEN_STATES)._workset_make_employee()
        return res

    def workset_dispatch_make(self):
        """Кнопка «Створити картку» (фірма вже вибрана)."""
        for app in self:
            if not app.workset_firm_id:
                raise UserError(_('Спочатку виберіть фірму.'))
        self._workset_make_employee()
        return True

    def workset_dispatch_same_person(self):
        for app in self:
            if app.workset_state != 'check_dup':
                continue
            # схожі картки — через sudo й за порядком id (як у тексті «Схожі картки»): чужі фірми поза вибраними користувачеві не видно
            candidates = app.sudo().with_context(active_test=False).workset_dup_employee_ids.sorted('id')
            if len(candidates) == 1:
                picked = candidates
            elif 1 <= app.workset_pick_no <= len(candidates):
                picked = candidates[app.workset_pick_no - 1]
            else:
                raise UserError(_('Схожих карток кілька — вкажіть номер картки зі списку.'))
            app.write({'workset_same_employee_ids': [(4, picked.id)], 'workset_state': 'wait_firm', 'workset_pick_no': 0})
            app.message_post(body=_('Підтверджено: це та сама людина (%s)') % picked.name)
            app._workset_close_tasks_dup()
        self.filtered(lambda a: a.workset_firm_id and a.workset_state == 'wait_firm')._workset_make_employee()
        return True

    def workset_dispatch_new_person(self):
        for app in self:
            if app.workset_state != 'check_dup':
                continue
            app.write({'workset_state': 'wait_firm'})
            app.message_post(body=_('Підтверджено: це нова людина'))
            app._workset_close_tasks_dup()
        self.filtered(lambda a: a.workset_firm_id and a.workset_state == 'wait_firm')._workset_make_employee()
        return True

    def _workset_close_tasks_dup(self):
        for app in self:
            app.workset_dup_activity_id.exists().sudo().action_feedback()

    def _workset_make_employee(self):
        for app in self:
            if not app.workset_firm_id or app.workset_state not in OPEN_STATES:
                continue
            if app.workset_state == 'check_dup':
                app.message_post(body=_('Фірму вибрано: %s. Картку створимо, коли Оля перевірить дубль.') % app.workset_firm_id.name)
                continue
            app._workset_make_employee_one()

    def _workset_make_employee_one(self):
        self.ensure_one()
        if not self.env.user.has_group('hr.group_hr_user'):
            raise AccessError(_('Картку працівника створює лише той, хто має права кадрів (Співробітники: Офіцер).'))
        cfg = self._workset_cfg()
        firm = self.workset_firm_id.sudo()
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False, allowed_company_ids=self.env['res.company'].sudo().search([]).ids)
        same = self.sudo().workset_same_employee_ids.with_context(active_test=False)
        if self.workset_pd_person_id:
            same |= Emp.search([('workset_pd_person_id', '=', self.workset_pd_person_id)])
        in_firm = same.filtered(lambda e: e.company_id == firm)
        profession = self.workset_profession or False
        extra_task = ''
        if in_firm:
            emp = in_firm.sorted(lambda e: (not e.active, e.id))[0]
            if not emp.active:
                emp.action_unarchive()
            fill = {}
            if not emp.private_phone and self.partner_phone:
                fill['private_phone'] = self.partner_phone
            if not emp.birthday and self.workset_birthday:
                fill['birthday'] = self.workset_birthday
            if not emp.private_country_id and self.workset_country_id:
                fill['private_country_id'] = self.workset_country_id.id
            if not emp.job_title and profession:
                fill['job_title'] = profession
            if not emp.workset_pd_deal_id:
                fill['workset_pd_deal_id'] = self.workset_pd_deal_id
            if not emp.workset_pd_person_id:
                fill['workset_pd_person_id'] = self.workset_pd_person_id
            if fill:
                emp.write(fill)
            self.message_post(body=_('Картка вже є у фірмі %s — повернута й доповнена: %s') % (firm.name, emp.name))
        else:
            vals = {
                'name': names.with_suffix(self.partner_name, firm.workset_name_suffix),
                'company_id': firm.id,
                'employee_type': 'employee',
                'private_phone': self.partner_phone or False,
                'private_country_id': self.workset_country_id.id or False,
                'birthday': self.workset_birthday or False,
                'workset_pd_deal_id': self.workset_pd_deal_id,
                'workset_pd_person_id': self.workset_pd_person_id,
            }
            if profession:
                vals['job_title'] = profession
            if cfg['hr_user']:
                vals['hr_responsible_id'] = cfg['hr_user'].id
            # та сама людина з карткою в іншій фірмі — спільний work_contact (заборони в hr/models/hr_employee.py немає; Odoo сам
            # враховує спільний контакт: рядки 835 і 845); інакше — наявний контакт людини; інакше стандарт Odoo
            same_contacts = same.mapped('work_contact_id')
            if same_contacts:
                partner, doubt = same_contacts[0], False
            else:
                partner, doubt = self._workset_find_partner(firm, names.clean_name(self.partner_name), [names.phone9(self.partner_phone)], same)
            if partner:
                vals['work_contact_id'] = partner.id
            extra_task = _('перевір контакт: знайшлося кілька схожих контактів людини') if doubt else ''
            emp = Emp.with_company(firm).create(vals)
            self.message_post(body=_('Створено картку: %s') % emp.name)
        # sudo: переход в «найнятий» етап стандартом зменшує лічильник вакансії — права на вакансію Юлі й Олі для цього не потрібні
        self.sudo().with_context(workset_skip_make=True).write({
            'employee_id': emp.id,
            'stage_id': self.env.ref('workset_dispatch.stage_hired').id,
            'workset_state': 'done',
        })
        self._workset_close_tasks()
        if cfg['hr_user']:
            customer = self.workset_customer_id.name or self.workset_customer_name or ''
            emp.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=self._workset_today() + timedelta(days=1), summary=_('Договір і ставка'),
                note=self._workset_note(
                    customer and _('Замовник: %s') % customer, self.job_id.name and _('Вакансія: %s') % self.job_id.name,
                    self.workset_departure_date and _('Виїзд: %s') % self.workset_departure_date, self.workset_pd_url, extra_task),
                user_id=cfg['hr_user'].id)
        return emp

    def _workset_find_partner(self, firm, name_clean, phones9, same=None):
        """Існуючий контакт людини для work_contact_id нової картки → (контакт | порожньо, сумнів).
        Кандидат: не компанія, не робочий контакт картки ІНШОЇ людини (картки same — цієї ж) і не користувач; той самий телефон (останні 9 цифр) або те саме ім'я
        (порядок слів не важливий). Рівно один — беремо; кілька — стандарт Odoo (новий контакт) і сумнів."""
        Partner = self.env['res.partner'].sudo()
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False)
        taken = Emp.search([('id', 'not in', (same or Emp).ids)]).mapped('work_contact_id').ids
        pf = [f for f in ('phone', 'mobile') if f in Partner._fields]
        domain = [('is_company', '=', False), ('id', 'not in', taken), ('user_ids', '=', False), '|', ('company_id', '=', False), ('company_id', '=', firm.id)]
        rows = Partner.search_read(domain, ['name'] + pf)
        key = ' '.join(sorted(names.name_words(name_clean)))
        wanted = {p for p in phones9 if p}
        found = []
        for r in rows:
            by_phone = bool(wanted & {names.phone9(r.get(f)) for f in pf})
            by_name = bool(key) and ' '.join(sorted(names.name_words(r['name'] or ''))) == key
            if by_phone or by_name:
                found.append(r['id'])
        if len(found) == 1:
            return Partner.browse(found), False
        return Partner, len(found) > 1
