# -*- coding: utf-8 -*-
"""Майстер «Заповнити» блоку «Договір і оплата» екрана «Умови клієнта» (М3, К1): свої поля замовника й стандартний строк оплати
(property_payment_term_id контакту — ним користуються рахунки) в одному вікні. Відкривається з картки лише редактору; «Зберегти»
перевіряє групу ще раз і пише в контакт через sudo лише змінені поля (історія змін — від імені користувача; у редактора може не бути
права правити контакти). Скани, завантажені тут, переходять до контакту замовника."""
from odoo import api, fields, models
from odoo.fields import Command

PARTNER = 'res.partner'
# поле майстра → своє поле замовника (res_partner.py)
FIELD_MAP = (('contract_no', 'workset_client_contract_no'), ('contract_date', 'workset_client_contract_date'),
             ('scan_ids', 'workset_client_contract_scan_ids'), ('sign_id', 'workset_client_sign_id'),
             ('site_contact_id', 'workset_client_site_contact_id'), ('site_address', 'workset_client_site_address'),
             ('withdraw_pct', 'workset_client_withdraw_pct'), ('note', 'workset_client_terms_note'))
CONTACTS = "[('commercial_partner_id', '=', partner_id), ('id', '!=', partner_id)]"  # контакти цього замовника


class WorksetClientTerms(models.TransientModel):
    _name = 'workset.client.terms'
    _description = 'Умови клієнта: заповнити'

    partner_id = fields.Many2one(PARTNER, string='Замовник', required=True, readonly=True, ondelete='cascade')
    contract_no = fields.Char(string='Номер договору')
    contract_date = fields.Date(string='Дата договору')
    scan_ids = fields.Many2many('ir.attachment', 'workset_client_terms_scan_rel', 'wizard_id', 'attachment_id', string='Скан договору')
    payment_term_id = fields.Many2one('account.payment.term', string='Термін оплати',
                                      help='Стандартне поле контакту «Умови оплати»: ним користуються рахунки')
    sign_id = fields.Many2one(PARTNER, string='Хто погоджує табель', domain=CONTACTS)
    site_contact_id = fields.Many2one(PARTNER, string="Контакт на об'єкті", domain=CONTACTS)
    site_address = fields.Char(string="Адреса об'єкта")
    withdraw_pct = fields.Float(string='Відсоток за виведення коштів', digits=(16, 2), help='%; довідково, у розрахунок прибутку не входить')
    note = fields.Text(string='Особливі домовленості')

    @api.model
    def default_get(self, fields_list):
        """Поточні значення замовника (комерційного контрагента з контексту) — щоб у вікні правити, а не вводити заново."""
        res = super().default_get(fields_list)
        pid = res.get('partner_id') or self.env.context.get('default_partner_id')
        partner = self.env[PARTNER].sudo().browse(pid).exists().commercial_partner_id if pid else None
        if not partner:
            return res
        res['partner_id'] = partner.id
        for mine, theirs in FIELD_MAP:
            if mine not in fields_list:
                continue
            value = partner[theirs]
            if self._fields[mine].type == 'many2many':
                value = [Command.set(value.ids)]
            elif self._fields[mine].type == 'many2one':
                value = value.id or False
            res[mine] = value
        if 'payment_term_id' in fields_list:
            res['payment_term_id'] = partner.property_payment_term_id.id or False
        return res

    def action_save(self):
        """Зберегти в контакт замовника лише змінене; група — ще раз (вікно могли відкрити не з картки)."""
        self.ensure_one()
        self.env[PARTNER]._workset_client_check_edit()
        partner = self.partner_id.sudo().commercial_partner_id
        vals = {}
        for mine, theirs in FIELD_MAP:
            new, old = self[mine], partner[theirs]
            kind = self._fields[mine].type
            if kind == 'many2many':
                if set(new.ids) != set(old.ids):
                    vals[theirs] = [Command.set(new.ids)]
            elif kind == 'many2one':
                if new.id != old.id:
                    vals[theirs] = new.id or False
            elif kind == 'float':
                if round(new or 0.0, 2) != round(old or 0.0, 2):
                    vals[theirs] = new or 0.0
            elif (new or False) != (old or False):
                vals[theirs] = new or False
        if self.payment_term_id != partner.property_payment_term_id:
            vals['property_payment_term_id'] = self.payment_term_id.id or False
        # скани, завантажені у вікні, — до контакту замовника (інакше вони лишились би на тимчасовому записі майстра)
        moved = self.scan_ids.sudo().filtered(lambda a: a.res_model != PARTNER or a.res_id != partner.id)
        if moved:
            moved.write({'res_model': PARTNER, 'res_id': partner.id})
        if vals:
            partner.write(vals)
        return {'type': 'ir.actions.act_window_close'}
