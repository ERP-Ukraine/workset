# -*- coding: utf-8 -*-
"""«Бонус офісу» — правило з датами: хто з офісу отримує, скільки (€ за годину / € за працівника на місяць / € за своїх людей на місяць),
у яких замовників і ліній. У ставці — лише підсумок «Бонус офісу, €/год» (calc.office_bonus; пишуть «щодня» і збереження ставки).
«Хто отримує» — картка працівника або, якщо картки немає, ім'я (employee_label): правило діє однаково.

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
    _order = 'employee_label, date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    # картки працівника в людини офісу може не бути (на проді — у частини офісу, а картки без користувача не додаємо — рішення власника
    # 05.11): тоді правило — за іменем (employee_label); бонус у ставці від картки не залежить (calc.office_bonus її не читає)
    employee_id = fields.Many2one('hr.employee', string='Картка працівника', index=True, tracking=1, ondelete='restrict',
                                  help='Картка того, хто отримує. Немає картки — порожньо, ім\'я — у «Хто отримує»')
    # «Хто отримує»: ім'я картки (sudo — і для «Бонуси офісу — перегляд» без прав кадрів: картка в чужій фірмі дала б AccessError на
    # display_name; ревю М2c-1, № 4) або, без картки, ім'я, вписане руками. Хранимо: список, пошук і групування — без картки теж
    employee_label = fields.Char(string='Хто отримує', compute='_compute_employee_label', store=True, readonly=False, tracking=7,
                                 help="Ім'я того, хто отримує бонус: з картки працівника, а без картки — вписане вручну")
    has_card = fields.Boolean(string='Є картка працівника', compute='_compute_has_card')
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

    @api.depends('employee_label', 'kind', 'amount')
    def _compute_name(self):
        labels = dict(KINDS)
        for b in self:
            b.name = '%s · %s · %s' % (b.employee_label or '—', labels.get(b.kind, ''), calc.money(b.amount))

    @api.depends('employee_id.name')
    def _compute_employee_label(self):
        # є картка — її ім'я; немає — що вписали (картку зняли — ім'я лишається)
        for b in self:
            b.employee_label = b.sudo().employee_id.name or b.employee_label or ''

    @api.depends('employee_id')
    def _compute_has_card(self):
        # «Хто отримує» вписують лише без картки; ознака — без поля hr.employee у виді (його немає в «Бонуси офісу — перегляд»)
        for b in self:
            b.has_card = bool(b.sudo().employee_id)

    @api.constrains('amount', 'date_from', 'date_to', 'employee_id', 'employee_label')
    def _check_bonus(self):
        lang = calc.lang_of(self.env.lang)
        for b in self:
            if (b.amount or 0) < 0:
                raise ValidationError(calc.t('negative', lang) % b._fields['amount'].string)
            if b.date_to and b.date_from and b.date_to < b.date_from:
                raise ValidationError(calc.t('rs_dates', lang))
            if not b.employee_id and not (b.employee_label or '').strip():
                raise ValidationError(calc.t('b_who', lang))

    @api.model
    def _rules(self):
        """Усі активні правила для calc.office_bonus (замовники — комерційні контрагенти)."""
        return [{'id': b.id, 'kind': b.kind, 'amount': b.amount, 'customer_ids': sorted(set(b.customer_ids.commercial_partner_id.ids)),
                 'line_ids': b.line_ids.ids, 'person_ids': b.person_ids.ids, 'date_from': calc.iso(b.date_from), 'date_to': calc.iso(b.date_to)}
                for b in self.sudo().search([])]
