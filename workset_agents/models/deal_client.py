# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import calc, common
from .deal_people import ACC_LIVE_DOMAIN, DEAL_ZV_HELP

CD_NAMES = {'hour': '€ за годину', 'fixed': 'Фікс-аванс', calc.FUND_KIND: 'Фонд працівників'}


class WorksetAgentDealClient(models.Model):
    """Домовленість з посередником замовників. Умова агента по замовниках живе ЛИШЕ тут: «Ставки» читають цю ставку на дату (адаптер
    workset_rates/models/agent_deals.py — поля rate, state, kind, customer_ids, line_id, date_from, date_to; не перейменовувати)."""
    _name = 'workset.agent.deal.client'
    _description = 'Домовленість — агенти-замовники'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'workset.agent.deal.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True, tracking=1)
    customer_ids = fields.Many2many('res.partner', 'workset_agent_deal_client_partner_rel', 'deal_id', 'partner_id', string='Замовники')
    line_id = fields.Many2one('workset.line', string='Лінія', ondelete='set null', index=True)
    kind = fields.Selection(common.CD_KINDS, string='Вид домовленості', required=True, tracking=2)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    rate = fields.Monetary(string='Ставка, €/год', currency_field='currency_id', tracking=7,
                           help='€ за годину людей агента в цих замовників (або на цій лінії), за період «Діє з / по»')
    rate_full = fields.Monetary(string='Ставка на повну', currency_field='currency_id', tracking=10, help=common.FUND_FULL_HELP)
    withhold_pct = fields.Float(string='Утримання, %', help='Скільки відсотків утримуємо з виплати агенту; утримане — на аналітику утримання')
    withhold_analytic_id = fields.Many2one('account.analytic.account', string='Аналітика утримання', ondelete='set null')
    date_from = fields.Date(string='Діє з', tracking=4)
    date_to = fields.Date(string='Діє по', tracking=5)
    company_id = fields.Many2one('res.company', string='Фірма виплати', index=True)
    # посередники — старі домовленості: за замовчуванням personal_36
    pay_path = fields.Selection(common.PAY_PATHS, string='Шлях виплати', default='personal_36', tracking=8, help=common.PAY_PATH_HELP)
    fixed_advance = fields.Monetary(string='Фікс-аванс, €/міс', currency_field='currency_id', tracking=9,
                                    help='Фіксований аванс посередникові щомісяця (вид «Фікс-аванс із сальдо»); розрахунок аванса із сальдо — етап 3')
    addendum = fields.Char(string='Допугода', help='Посилання на допугоду до договору: файл або документ')
    terms_source = fields.Char(string='Джерело умов')
    owner_ok = fields.Boolean(string='Підтверджено власником', tracking=6)
    state = fields.Selection(common.DEAL_STATES, string='Статус', default='draft', tracking=3, index=True)
    mode = fields.Selection(common.DEAL_MODES, string='Режим', default='pay', tracking=11, help=common.MODE_HELP)
    note = fields.Text(string='Нотатки')
    cust_n = fields.Integer(string='Замовників', compute='_compute_cust_n')
    accrual_ids = fields.One2many('workset.agent.accrual', 'client_deal_id', string='Нарахування')
    accrual_live_ids = fields.One2many('workset.agent.accrual', 'client_deal_id', string='Нарахування без скасованих', domain=ACC_LIVE_DOMAIN)
    acc_sum = fields.Monetary(string='Нараховано', currency_field='currency_id', compute='_compute_acc_sum')
    env_sum = fields.Monetary(string='Конверт агента за рік', currency_field='currency_id', compute='_compute_zv_sums', help=DEAL_ZV_HELP)
    zv_diff_sum = fields.Monetary(string='Різниця агента за рік', currency_field='currency_id', compute='_compute_zv_sums', help=DEAL_ZV_HELP)
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('agent_id.name', 'kind')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s' % (rec.agent_id.name or '—', CD_NAMES.get(rec.kind) or '—')

    @api.depends('customer_ids')
    def _compute_cust_n(self):
        for rec in self:
            rec.cust_n = len(rec.customer_ids)

    def action_open_accruals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Нарахування', 'res_model': 'workset.agent.accrual', 'view_mode': 'list,form',
                'domain': [('client_deal_id', '=', self.id)], 'target': 'current'}
