# -*- coding: utf-8 -*-
"""«Бонус офісу» — правило з датами: хто з офісу отримує, скільки (€ за годину / € за працівника на місяць / € за своїх людей на місяць),
у яких замовників і ліній. У ставці — лише підсумок «Бонус офісу, €/год» (calc.office_bonus; пишуть «щодня» і збереження ставки).

Записи правил бачать лише група «Ставки: правила ліній» (правка) і «Ставки: бонуси офісу — перегляд» (читання) — це по суті частина
зарплати людей офісу; редактори й читачі ставок бачать на ставці лише підсумок (права — security/ir.model.access.csv)."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc
from .workset_rate import SOURCES

BONUS = 'workset.office.bonus'
KINDS = [('hour', '€ за годину'), ('worker_month', '€ за працівника на місяць'), ('own_worker_month', '€ за своїх людей на місяць')]


class WorksetOfficeBonus(models.Model):
    _name = BONUS
    _description = 'Бонус офісу'
    _inherit = ['mail.thread']
    _order = 'employee_id, date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    employee_id = fields.Many2one('hr.employee', string='Хто отримує', required=True, index=True, tracking=1, ondelete='restrict')
    kind = fields.Selection(KINDS, string='Вид', required=True, default='hour', tracking=2,
                            help='€ за годину — за кожну годину людей; € за працівника на місяць — ділиться на години людини в цього замовника за минулий '
                                 'місяць; € за своїх людей на місяць — так само, але лише за людей зі списку «Люди»')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    amount = fields.Monetary(string='Сума', currency_field='currency_id', required=True, tracking=3)
    customer_ids = fields.Many2many('res.partner', 'workset_office_bonus_partner_rel', 'bonus_id', 'partner_id', string='Замовники',
                                    help='Порожньо — усі замовники')
    line_ids = fields.Many2many('workset.line', 'workset_office_bonus_line_rel', 'bonus_id', 'line_id', string='Лінії', help='Порожньо — усі лінії')
    person_ids = fields.Many2many('res.partner', 'workset_office_bonus_person_rel', 'bonus_id', 'partner_id', string='Люди',
                                  help='Для «€ за своїх людей на місяць»: робочі контакти людей, яких дав цей працівник офісу')
    paid_only = fields.Boolean(string='Лише оплачені', help='Бонус лише за оплачені рахунки замовника. У плані ставки бонус не зменшує — це умова виплати.')
    date_from = fields.Date(string='З', required=True, tracking=4)
    date_to = fields.Date(string='До', tracking=5, help='Порожньо — діє досі')
    source = fields.Selection(SOURCES, string='Джерело', required=True)
    note = fields.Text(string='Нотатка')
    active = fields.Boolean(string='Активне', default=True, tracking=6)

    @api.depends('employee_id.name', 'kind', 'amount')
    def _compute_name(self):
        labels = dict(KINDS)
        for b in self:
            b.name = '%s · %s · %s' % (b.employee_id.name or '—', labels.get(b.kind, ''), calc.money(b.amount))

    @api.constrains('amount', 'date_from', 'date_to')
    def _check_bonus(self):
        lang = calc.lang_of(self.env.lang)
        for b in self:
            if (b.amount or 0) < 0:
                raise ValidationError(calc.t('negative', lang) % b._fields['amount'].string)
            if b.date_to and b.date_from and b.date_to < b.date_from:
                raise ValidationError(calc.t('rs_dates', lang))

    @api.model
    def _rules(self):
        """Усі активні правила для calc.office_bonus (замовники — комерційні контрагенти)."""
        return [{'id': b.id, 'kind': b.kind, 'amount': b.amount, 'customer_ids': sorted(set(b.customer_ids.commercial_partner_id.ids)),
                 'line_ids': b.line_ids.ids, 'person_ids': b.person_ids.ids, 'date_from': calc.iso(b.date_from), 'date_to': calc.iso(b.date_to)}
                for b in self.sudo().search([])]
