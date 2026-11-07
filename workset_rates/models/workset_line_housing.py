# -*- coding: utf-8 -*-
"""«Житло лінії» (розділ 3.13 спеціфікації М2) — як лінія рахує житло людей: вирахування «години житла × €», буфер житла з оплати
замовника, буфер з пари рахунків, вираховує сам замовник або житло не наше. Правило лінії (замовник порожній) або об'єкта / замовника,
з датами; одне діюче правило на пару «лінія × замовник» на дату, правило замовника перекриває правило лінії (calc.housing_pick).

М2 лише зберігає й показує; рахують «Прибутковість» (М3, факт місяця) і автораскладка оплати (М4) — через _housing_at. Самих рахунків за
житло, буферів і залишків тут немає (це конверти М3 / М4). Суми — дані, пише інструмент штабу або власник; у коді чисел немає.
Права — як у правил ліній (розділ 7): править група «Ставки: правила ліній», редактори, кадри, бухгалтерія й проєкти читають."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc
from .workset_rate import SOURCES, cust_of

HOUSING = 'workset.line.housing'
KINDS = [('hour_deduction', 'Вирахування: години житла × €'), ('payment_buffer', 'Буфер житла з оплати замовника'),
         ('invoice_buffer', 'Буфер житла з пари рахунків'), ('customer_deducts', 'Вираховує замовник'), ('none', 'Житло не наше')]
UNITS = [('hour', '€ за годину житла'), ('month', "€ за місяць (об'єкт)"), ('month_person', '€ за місяць за особу'), ('day_person', '€ за добу за особу'), ('pct', '% з оплати')]
# worker_fund — житло з «фонду працівників» (SoMa: таблиця власника 06.11 12:05 — житло, паливо, інструмент, спецодяг із фонду; читає М3
# workset_profit через _housing_at; у частці WorkSET не бере участі — фонд уже відраховано в ланцюжку ставки)
PAYERS = [('we_buffer', 'Платимо ми з буфера лінії'), ('partner', 'Платить партнер'), ('customer', 'Замовник'),
          ('worker_fund', 'З фонду працівників')]


class WorksetLineHousing(models.Model):
    _name = HOUSING
    _description = 'Житло лінії'
    _inherit = ['mail.thread']
    _order = 'line_id, customer_id, date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    line_id = fields.Many2one('workset.line', string='Лінія', required=True, index=True, tracking=1, ondelete='restrict')
    customer_id = fields.Many2one('res.partner', string="Замовник / об'єкт", index=True, tracking=2, ondelete='restrict',
                                  domain=[('is_company', '=', True)],
                                  help="Порожньо — правило всієї лінії; замовник (об'єкт) — його правило, точніше за правило лінії")
    kind = fields.Selection(KINDS, string='Як рахуємо', required=True, tracking=3)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    # без підсумку в рядку групи: суми в різних одиницях (€ за годину, € за місяць, %) не складаються й не усереднюються (розбір вигляду 06.11)
    amount = fields.Monetary(string='Сума', currency_field='currency_id', tracking=4, aggregator=False, help='У одиниці з поля «Одиниця»; порожньо — суми ще немає')
    amount_unit = fields.Selection(UNITS, string='Одиниця', tracking=5)
    hours_per_person = fields.Float(string='Годин житла на особу', tracking=6, aggregator=False,
                                    help='Лише для «Вирахування: години житла × €»: скільки годин житла на особу за період. Порожньо — не задано')
    payer = fields.Selection(PAYERS, string='Хто платить', tracking=7)
    date_from = fields.Date(string='З', required=True, index=True, tracking=8)
    date_to = fields.Date(string='До', index=True, tracking=9, help='Порожньо — діє досі')
    source = fields.Selection(SOURCES, string='Джерело', required=True, tracking=10)
    source_note = fields.Char(string='Звідки саме', required=True)
    note = fields.Text(string='Нотатка')
    active = fields.Boolean(string='Активна', default=True, tracking=11)

    @api.depends('line_id.name', 'customer_id.name', 'kind', 'amount', 'amount_unit', 'date_from')
    def _compute_name(self):
        # назва зберігається — українською (як інші назви записів модуля)
        for h in self:
            h.name = calc.housing_name(h.line_id.name, h.customer_id.name, h.kind, h.amount, h.amount_unit, h.date_from)

    @api.model
    def _rules(self):
        """Активні правила для calc.housing_pick / housing_clash / housing_missing (замовник — комерційний контрагент, 0 — уся лінія)."""
        return [{'id': h.id, 'line_id': h.line_id.id, 'customer_id': cust_of(h.customer_id), 'date_from': calc.iso(h.date_from),
                 'date_to': calc.iso(h.date_to)} for h in self.sudo().search([])]

    @api.constrains('line_id', 'customer_id', 'date_from', 'date_to', 'active', 'amount', 'hours_per_person', 'kind')
    def _check_housing(self):
        lang = calc.lang_of(self.env.lang)
        rules = None
        for h in self:
            if (h.amount or 0) < 0 or (h.hours_per_person or 0) < 0:
                raise ValidationError(calc.t('negative', lang) % ', '.join([h._fields['amount'].string, h._fields['hours_per_person'].string]))
            if h.hours_per_person and h.kind != 'hour_deduction':
                # «Годин житла на особу» — лише для «Вирахування: години житла × €» (ревю М2c-1, № 8)
                raise ValidationError(calc.t('h_hours_kind', lang))
            if h.date_to and h.date_to < h.date_from:
                raise ValidationError(calc.t('rs_dates', lang))
            if not h.active:
                continue
            rules = rules if rules is not None else self._rules()
            mine = {'id': h.id, 'line_id': h.line_id.id, 'customer_id': cust_of(h.customer_id), 'date_from': calc.iso(h.date_from),
                    'date_to': calc.iso(h.date_to)}
            clash = calc.housing_clash(rules, mine)
            if clash:
                o = self.sudo().browse(clash)
                raise ValidationError(calc.t('h_clash', lang) % (h.line_id.name or '—', o.id, calc.fmt_date(o.date_from),
                                                                 calc.fmt_date(o.date_to) or calc.t('h_open', lang)))

    @api.model
    def _housing_at(self, line, customer, day):
        """Правило житла пари «лінія × замовник» на day для М3 / М4: правило замовника, а немає — лінії. → запис або порожньо."""
        if not line:
            return self.browse()
        rid = calc.housing_pick(self._rules(), line.id, cust_of(customer), calc.iso(day))
        return self.sudo().browse(rid) if rid else self.browse()
