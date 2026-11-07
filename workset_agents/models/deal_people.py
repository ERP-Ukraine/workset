# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import common

ACC_LIVE_DOMAIN = [('kind', '!=', 'storno'), ('state', '!=', 'cancelled')]
DEAL_ZV_HELP = 'По агенту за рік картки (поточний за Києвом або параметр card_year), не по домовленості: конверт «ДОЛЯ | агент» один на агента'
PD_NAMES = {'v17': 'v1.7', 'hour': '€ за годину', 'oneoff': 'Разова'}


class DealMixin(models.AbstractModel):
    """Спільне для домовленостей обох розділів: суми договору й звірки агента за рік, режим."""
    _name = 'workset.agent.deal.mixin'
    _description = 'Домовленість агента (спільне)'

    def _compute_acc_sum(self):
        for rec in self:
            rec.acc_sum = sum(a.amount or 0.0 for a in rec.accrual_ids if a.state != 'cancelled')

    def _compute_zv_sums(self):
        fr, to = common.year_bounds(self.env)
        Zv = self.env['workset.agent.zvirka'].sudo()
        for rec in self:
            rows = Zv.search([('agent_id', '=', rec.agent_id.id), ('month', '>=', fr), ('month', '<', to)]) if rec.agent_id else Zv
            rec.env_sum = sum(rows.mapped('envelope'))
            rec.zv_diff_sum = sum(rows.mapped('diff'))


class WorksetAgentDealPeople(models.Model):
    _name = 'workset.agent.deal.people'
    _description = 'Домовленість — агенти-кандидати'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'workset.agent.deal.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True, tracking=1)
    kind = fields.Selection(common.PD_KINDS, string='Вид домовленості', required=True, tracking=2)
    date_from = fields.Date(string='Діє з', tracking=4)
    date_to = fields.Date(string='Діє по', tracking=5)
    company_id = fields.Many2one('res.company', string='Фірма виплати', index=True)
    pay_path = fields.Selection(common.PAY_PATHS, string='Шлях виплати', compute='_compute_pay_path', store=True, readonly=False, tracking=8,
                                help=common.PAY_PATH_HELP)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    rate = fields.Monetary(string='Ставка, €/год', currency_field='currency_id', help='€ за годину кожної людини агента (в оплачених рахунках замовників)')
    withhold_pct = fields.Float(string='Утримання, %', help='Скільки відсотків утримуємо з виплати агенту, напр. 10 %: до переказу 90 %, утримане — на аналітику утримання')
    withhold_analytic_id = fields.Many2one('account.analytic.account', string='Аналітика утримання', ondelete='set null')
    terms_version = fields.Char(string='Версія умов')
    terms_source = fields.Char(string='Джерело умов')
    owner_ok = fields.Boolean(string='Підтверджено власником', tracking=6)
    state = fields.Selection(common.DEAL_STATES, string='Статус', default='draft', tracking=3, index=True)
    mode = fields.Selection(common.DEAL_MODES, string='Режим', default='pay', tracking=9, help=common.MODE_HELP)
    oneoff_amount = fields.Monetary(string='Умова за людину', currency_field='currency_id', tracking=10,
                                    help='Разова: скільки євро агент отримує за кожну людину. Довідково — суму разової нарахування вносить офіс у самому нарахуванні')
    note = fields.Text(string='Нотатки')
    member_ids = fields.One2many('workset.agent.member', 'deal_id', string='Люди агента')
    people_n = fields.Integer(string='Людей', compute='_compute_people_n')
    accrual_ids = fields.One2many('workset.agent.accrual', 'people_deal_id', string='Нарахування')
    accrual_live_ids = fields.One2many('workset.agent.accrual', 'people_deal_id', string='Нарахування без скасованих', domain=ACC_LIVE_DOMAIN)
    acc_sum = fields.Monetary(string='Нараховано', currency_field='currency_id', compute='_compute_acc_sum')
    env_sum = fields.Monetary(string='Конверт агента за рік', currency_field='currency_id', compute='_compute_zv_sums', help=DEAL_ZV_HELP)
    zv_diff_sum = fields.Monetary(string='Різниця агента за рік', currency_field='currency_id', compute='_compute_zv_sums', help=DEAL_ZV_HELP)
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('agent_id.name', 'kind', 'oneoff_amount')
    def _compute_name(self):
        for rec in self:
            tail = PD_NAMES.get(rec.kind) or '—'
            if rec.kind == 'oneoff' and rec.oneoff_amount:
                tail += ' · Умова: %s € за людину' % ('%g' % rec.oneoff_amount)
            rec.name = '%s · %s' % (rec.agent_id.name or '—', tail)

    @api.depends('kind')
    def _compute_pay_path(self):
        # договір v1.7 → official_1, решта («по-старому») → personal_36; «За запитом» вид не перебиває
        for rec in self:
            if rec.pay_path != 'custom':
                rec.pay_path = 'official_1' if rec.kind == 'v17' else 'personal_36'

    @api.depends('member_ids.partner_id')
    def _compute_people_n(self):
        for rec in self:
            rec.people_n = len(set(m.partner_id.id for m in rec.member_ids if m.partner_id))

    def action_open_accruals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Нарахування', 'res_model': 'workset.agent.accrual', 'view_mode': 'list,form',
                'domain': [('people_deal_id', '=', self.id)], 'target': 'current'}


class WorksetAgentMember(models.Model):
    _name = 'workset.agent.member'
    _description = 'Люди агента'
    _order = 'date_from, id'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    deal_id = fields.Many2one('workset.agent.deal.people', string='Домовленість', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Людина', required=True, ondelete='restrict', index=True, help='Робочий контакт людини — один на всі фірми')
    date_from = fields.Date(string='Працює з')
    date_to = fields.Date(string='Працює по')
    agent_id = fields.Many2one('workset.agent', string='Агент', related='deal_id.agent_id', store=True, index=True)
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('partner_id.name', 'deal_id.agent_id.name')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s' % (rec.deal_id.agent_id.name or '—', rec.partner_id.name or '—')
