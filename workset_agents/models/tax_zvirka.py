# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import calc, common


class WorksetAgentTax(models.Model):
    """Профіль податків агента (ПДн): окремо від контакту — права лише бухгалтерії та адміністраторів кадрів. На агенті з нього немає ні related,
    ні compute (вычисляемое поле читалось би в обхід прав моделі). Файл довідки — вкладення цього запису (чат)."""
    _name = 'workset.agent.tax'
    _description = 'Податки агента'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name, id'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True, tracking=1,
                               help='Агент; на одного агента — один профіль')
    tax_status = fields.Selection(common.TAX_STATUSES, string='Податковий статус', tracking=2,
                                  help='ФОП, фірма чи фізособа: від цього залежить документ для виплати (рахунок або rachunek)')
    tax_country = fields.Many2one('res.country', string='Країна податків', ondelete='set null', tracking=3, help='Країна податкового резидентства агента')
    cert_until = fields.Date(string='Довідка до', tracking=4,
                             help='Довідка про резидентство діє до цієї дати (оновлюється що 12 місяців, п. 7.1); без чинної довідки нерезиденту утримується 20 %')
    tax_number = fields.Char(string='Податковий номер', tracking=5, help='ІПН, NIP або інший номер платника податків агента')
    note = fields.Text(string='Нотатки')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    _agent_uniq = models.Constraint('UNIQUE(agent_id)', 'У агента вже є профіль податків: один профіль на агента.')

    @api.depends('agent_id.name')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.agent_id.name or '—'


class WorksetAgentZvirka(models.Model):
    """«Звірка з конвертом» — агент × місяць: розрахунок Odoo проти рядків конверта «ДОЛЯ | …». Пише ніч / інструмент штабу (sudo); люди читають."""
    _name = 'workset.agent.zvirka'
    _description = 'Звірка з конвертом'
    _order = 'month desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True)
    section = fields.Selection(common.SECTIONS, string='Розділ', index=True, help='Агенти-кандидати чи агенти-замовники — за домовленостями агента')
    envelope_id = fields.Many2one('account.analytic.account', string='Конверт', ondelete='set null')
    month = fields.Date(string='Місяць', required=True, index=True, help='Перший день місяця')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    calc = fields.Monetary(string='Розрахунок, €', currency_field='currency_id', aggregator='sum',
                           help='Нарахування агента за місяць «Зароблено» (для «Чернетки» — пробний розрахунок ночі)')
    envelope = fields.Monetary(string='Конверт, €', currency_field='currency_id', aggregator='sum',
                               help='Рядки конверта за місяць із сумою більше нуля (частка; виплати й витрати — окремо)')
    diff = fields.Monetary(string='Різниця, €', currency_field='currency_id', aggregator='sum', help='Розрахунок мінус конверт')
    diff_pct = fields.Float(string='%', digits=(16, 1), compute='_compute_diff_pct', store=True, aggregator='avg', help='Різниця від конверта у відсотках')
    lines = fields.Integer(string='Рядків конверта')
    matched = fields.Integer(string='Збіглося по рахунку',
                             help='Рядки конверта, сума яких дорівнює розрахунку за ті самі рахунки замовників (ключ — номер рахунку)')
    source = fields.Selection(common.ZV_SOURCES, string='Вид розрахунку')
    calc_note = fields.Text(string='Звідки розрахунок', help='Скільки рахунків і годин людей агента, за якою ставкою — простими словами')
    note = fields.Text(string='Розбіжності')
    state_badge = fields.Selection(common.ZV_BADGES, string='Стан', index=True,
                                   help='Збігається — різниця до одного євро; до 5 % і понад 5 % — від конверта; немає конверта — за місяць рядків '
                                        'конверта немає; архів — більшість конверта — рахунки архівних фірм')
    month_label = fields.Char(string='Місяць словами', compute='_compute_month_label', store=True)
    key = fields.Char(string='Ключ', index=True)
    checked_at = fields.Datetime(string='Звірено')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('agent_id.name', 'month')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s' % (rec.agent_id.name or '—', rec.month.strftime('%m.%Y') if rec.month else '—')

    @api.depends('month')
    def _compute_month_label(self):
        for rec in self:
            rec.month_label = '%s %d' % (common.MONTHS[rec.month.month - 1], rec.month.year) if rec.month else False

    @api.depends('calc', 'envelope')
    def _compute_diff_pct(self):
        for rec in self:
            rec.diff_pct = calc.zv_badge_pct(rec.calc, rec.envelope)
