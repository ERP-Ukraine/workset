# -*- coding: utf-8 -*-
"""Смарт-кнопки «Ставки» на контакті, картці працівника й проєкті: лише нехранимий лічильник і дія; колонок у стандартних моделях немає."""
from odoo import api, fields, models

from . import calc
from .workset_rate import RATE, cust_of


class ResPartner(models.Model):
    _inherit = 'res.partner'

    workset_rate_count = fields.Integer(string='Діючих ставок', compute='_compute_workset_rate_count')
    # «Працівник» у ставці й тимчасовій зміні: контакт, у якого є картка працівника. Домен по employee_ids (поле лише кадрам) дав би
    # редактору без прав кадрів AccessError — тому своє нехраниме поле з пошуком через sudo (назовні — лише id контактів; ревю М2c-1, № 3)
    workset_is_worker = fields.Boolean(string='Є картка працівника', compute='_compute_workset_is_worker', search='_search_workset_is_worker')

    def _worker_partner_ids(self):
        return set(self.env['hr.employee'].sudo().search([('work_contact_id', '!=', False)]).work_contact_id.ids)

    def _compute_workset_is_worker(self):
        ids = self._worker_partner_ids() if self.ids else set()
        for p in self:
            p.workset_is_worker = p.id in ids

    def _search_workset_is_worker(self, operator, value):
        # Odoo 19 може передати і «=» / «!=» з True / False, і «in» / «not in» зі списком — як _search_has_raise ставки
        values = set(value) if isinstance(value, (list, tuple, set, frozenset)) else {value}
        if operator in ('!=', 'not in'):
            values = {True, False} - {bool(v) for v in values}
        elif operator in ('=', 'in'):
            values = {bool(v) for v in values}
        else:
            raise ValueError('workset_is_worker: %s' % operator)
        if values == {True, False}:
            return []
        if not values:
            return [('id', '=', 0)]
        return [('id', 'in' if True in values else 'not in', list(self._worker_partner_ids()))]

    def _compute_workset_rate_count(self):
        # sudo — лише на лічильник (назовні йде число); замовник — ставки в нього, працівник — його ставки
        R = self.env[RATE].sudo()
        for p in self:
            if not isinstance(p.id, int):
                p.workset_rate_count = 0
            elif p.is_company or p.customer_rank:
                p.workset_rate_count = R.search_count([('customer_id.commercial_partner_id', '=', p.commercial_partner_id.id), ('state', '=', 'current')])
            else:
                p.workset_rate_count = R.search_count([('person_id', '=', p.id), ('state', '=', 'current')])

    def action_workset_rates(self):
        self.ensure_one()
        Rate = self.env[RATE]
        lang = Rate._lang()
        if self.is_company or self.customer_rank:
            c = self.commercial_partner_id.id
            return Rate._rates_action(calc.t('cust_rates', lang), [('customer_id.commercial_partner_id', '=', c)], {'search_default_current': 1, 'default_customer_id': c})
        return Rate._rates_action(calc.t('person_rates', lang), [('person_id', '=', self.id)], {'default_person_id': self.id})


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # groups: поле hr.employee, якого немає на hr.employee.public, — лише кадрам (тест hr test_employee_fields_groups; сборка Odoo.sh
    # 41352965, 06.11.2026); форму працівника і так бачать лише кадри, решта — публічний профіль без кнопки
    workset_rate_count = fields.Integer(string='Діючих ставок', compute='_compute_workset_rate_count', groups='hr.group_hr_user')

    def _compute_workset_rate_count(self):
        R = self.env[RATE].sudo()
        for e in self:
            pid = e.sudo().work_contact_id.id if isinstance(e.id, int) else 0
            e.workset_rate_count = R.search_count([('person_id', '=', pid), ('state', '=', 'current')]) if pid else 0

    def action_workset_rates(self):
        # по робочому контакту (одна ставка на всі фірми); кнопка видна й при 0 — першу ставку заводять звідси, працівник підставлений
        self.ensure_one()
        Rate = self.env[RATE]
        pid = self.sudo().work_contact_id.id
        return Rate._rates_action(calc.t('person_rates', Rate._lang()), [('person_id', '=', pid)], {'default_person_id': pid or False})


class ProjectProject(models.Model):
    _inherit = 'project.project'

    workset_rate_count = fields.Integer(string='Ставок замовника', compute='_compute_workset_rate_count')

    @api.depends('partner_id')
    def _compute_workset_rate_count(self):
        R = self.env[RATE].sudo()
        for p in self:
            cid = cust_of(p.sudo().partner_id) if isinstance(p.id, int) else 0
            p.workset_rate_count = R.search_count([('customer_id.commercial_partner_id', '=', cid), ('state', '=', 'current')]) if cid else 0

    def action_workset_rates(self):
        self.ensure_one()
        Rate = self.env[RATE]
        c = cust_of(self.sudo().partner_id)
        return Rate._rates_action(calc.t('cust_rates', Rate._lang()), [('customer_id.commercial_partner_id', '=', c)],
                                 {'search_default_current': 1, 'default_customer_id': c or False})
