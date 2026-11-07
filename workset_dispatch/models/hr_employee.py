# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    workset_pd_deal_id = fields.Integer(string='Угода Pipedrive', index=True, copy=False)
    workset_pd_person_id = fields.Integer(string='Контакт Pipedrive', index=True, copy=False)
    workset_dispatch_count = fields.Integer(string='Виїзди', compute='_compute_workset_dispatch_count')
    workset_pd_url = fields.Char(string='Угода в Pipedrive', compute='_compute_workset_pd_url')

    def _compute_workset_dispatch_count(self):
        # sudo — лише на лічильник: назовні йде число
        App = self.env['hr.applicant'].sudo().with_context(active_test=False)
        for rec in self:
            rec.workset_dispatch_count = App.search_count([('employee_id', '=', rec.id), ('workset_state', '!=', False)]) if isinstance(rec.id, int) else 0

    @api.depends('workset_pd_deal_id')
    def _compute_workset_pd_url(self):
        domain = self.env['ir.config_parameter'].sudo().get_param('workset_dispatch.pd_domain') or ''
        for rec in self:
            rec.workset_pd_url = 'https://%s/deal/%s' % (domain, rec.workset_pd_deal_id) if domain and rec.workset_pd_deal_id else False

    def workset_dispatch_open_list(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('workset_dispatch.action_workset_dispatch')
        action.update(domain=[('employee_id', '=', self.id), ('workset_state', '!=', False)], context={'active_test': False})
        return action

    def workset_dispatch_open_pd(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.workset_pd_url, 'target': 'new'}
