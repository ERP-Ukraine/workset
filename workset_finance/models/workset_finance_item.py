# -*- coding: utf-8 -*-
"""«Рядки» (Фінанси, круг 1): деталі кліків екрана «Власник» — з чого склався кожен ступінь місяця: статті власників, перекази, «II етап»,
неоплачені рахунки, кошики якості даних, Україна, кандидати 1г, поправки. Рядок знає модель і домен списка, який відкриває клік.
Пише лише код (перерахунок місяця замінює рядки місяця); бачить «Власник»."""
import json

from odoo import _, fields, models

KIND_LABELS = [('step', 'Ступінь'), ('owner', 'Власники: витрати компанії'), ('owner_share', 'Власники: частка'), ('transfer', 'Переказ на особистий рахунок'),
               ('stage2', 'II етап'), ('cash', 'За грошима'), ('cash_other', 'II етап: не в «за грошима»'), ('unpaid', 'Не оплачено'), ('quality', 'Якість даних'), ('ukraine', 'Україна'),
               ('candidate', '1г: кандидат'), ('adjust', 'Поправка')]


class WorksetFinanceItem(models.Model):
    _name = 'workset.finance.item'
    _description = 'Фінанси: рядок деталізації'
    _order = 'month desc, kind, sort, id'

    month = fields.Date(string='Місяць', required=True, index=True, readonly=True)
    kind = fields.Selection(KIND_LABELS, string='Вид', required=True, index=True, readonly=True)
    name = fields.Char(string='Рядок', readonly=True)
    sort = fields.Integer(string='Порядок', readonly=True, aggregator=False)
    analytic_id = fields.Many2one('account.analytic.account', string='Стаття аналітики', readonly=True, ondelete='set null')
    partner_id = fields.Many2one('res.partner', string='Контрагент', readonly=True, ondelete='set null')
    line_id = fields.Many2one('workset.line', string='Лінія', readonly=True, ondelete='set null')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    amount = fields.Monetary(string='Сума', currency_field='currency_id', readonly=True)
    count = fields.Integer(string='Записів', readonly=True)
    note = fields.Char(string='Примітка', readonly=True, help='Службове: ключ ступеня, розібрано / без документа, кошик, не розпізнано')
    res_model = fields.Char(string='Модель списку', readonly=True)
    domain = fields.Text(string='Домен списку', readonly=True, help='JSON: домен, який відкриває клік по рядку')

    def action_open(self):
        """Клік: список записів, з яких складено рядок (правами користувача)."""
        self.ensure_one()
        if not self.res_model:
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Фінанси'), 'message': _('Для цього рядка немає списку.')}}
        try:
            domain = json.loads(self.domain or '[]')
        except ValueError:
            domain = []
        return {'type': 'ir.actions.act_window', 'name': self.name or '', 'res_model': self.res_model, 'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
                'domain': domain, 'target': 'current'}
