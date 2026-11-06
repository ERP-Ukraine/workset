# -*- coding: utf-8 -*-
"""«Поправки» (Фінанси, круг 1): рука штабу в драбині — ступінь 1г (дохід без аналітики) і ступінь 3 (частки партнерів, по лініях) до
автомата «за місяцем роботи». Поправка без підстави не буває: підстава, дата джерела, автор (create_uid), листування (chatter), архів замість
видалення. Перерахунок місяця бере суму активних поправок ступеня; ступінь у драбині отримує пометку «поправка». Правку веде редактор."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc

ADJUST = 'workset.finance.adjust'


class WorksetFinanceAdjust(models.Model):
    _name = ADJUST
    _description = 'Фінанси: поправка'
    _inherit = ['mail.thread']
    _order = 'month desc, step, id'

    month = fields.Date(string='Місяць', required=True, index=True, tracking=True, help='Перше число місяця, не раніше січня 2026')
    step = fields.Selection([('s1g', '1г Дохід без аналітики'), ('s3', '3 Частки партнерів')], string='Ступінь', required=True, tracking=True)
    line_id = fields.Many2one('workset.line', string='Лінія', tracking=True, help='Для ступеня 3: чия частка (можна без лінії — одним числом)')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    amount = fields.Monetary(string='Сума', currency_field='currency_id', required=True, tracking=True,
                             help='Ступінь 3: частки, що віднімаються від «до часток» (додатне). Ступінь 1г: дохід, що додається (додатне)')
    basis = fields.Text(string='Підстава', required=True, tracking=True, help='Звідки число: документ, розрахунок, рішення')
    source_date = fields.Date(string='Дата джерела', tracking=True)
    active = fields.Boolean(string='Діє', default=True, tracking=True, help='Знята з архіву поправка знову входить у драбину при наступному перерахунку')

    @api.constrains('month')
    def _check_month(self):
        for r in self:
            if not calc.month_ok(r.month):
                raise ValidationError(calc.t('month_range', calc.lang_of(self.env.lang)) % calc.iso(r.month))

    @api.constrains('basis')
    def _check_basis(self):
        for r in self:
            if not (r.basis or '').strip():
                raise ValidationError(calc.t('basis', calc.lang_of(self.env.lang)))
