# -*- coding: utf-8 -*-
"""«Хто платить» (М3): хто несе витрату лінії виду «авто», «паливо», «інструмент», «перевезення» з дати — ми (у «чисто»), партнер
(«З грошей лінії»), фонд працівників («З фонду працівників») або замовник. Правило лінії (замовник порожній) або замовника лінії; одне на
«лінія × замовник × вид» на дату, правило замовника точніше (calc.cost_rule_pick). Правила немає — платимо ми. Житло — правило житла
лінії «Ставок» (workset.line.housing, _housing_at). Дані пише інструмент штабу або редактор; сум у коді немає."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc

COST_RULE = 'workset.line.cost.rule'
COST_KINDS = [('auto', 'Авто'), ('fuel', 'Паливо'), ('tool', 'Інструмент і спецодяг'), ('transport', 'Перевезення людей')]
PAYERS = [('we', 'Ми (у «чисто»)'), ('partner', 'Партнер (гроші лінії)'), ('worker_fund', 'Фонд працівників'), ('customer', 'Замовник')]


class WorksetLineCostRule(models.Model):
    _name = COST_RULE
    _description = 'Хто платить'
    _inherit = ['mail.thread']
    _order = 'line_id, customer_id, kind, date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    line_id = fields.Many2one('workset.line', string='Лінія', required=True, index=True, tracking=1, ondelete='restrict')
    customer_id = fields.Many2one('res.partner', string='Замовник', index=True, tracking=2, ondelete='restrict', domain=[('is_company', '=', True)],
                                  help='Порожньо — уся лінія; замовник — його правило, точніше за правило лінії')
    kind = fields.Selection(COST_KINDS, string='Вид витрати', required=True, tracking=3)
    payer = fields.Selection(PAYERS, string='Хто платить', required=True, default='we', tracking=4)
    date_from = fields.Date(string='З', required=True, index=True, tracking=5)
    date_to = fields.Date(string='До', index=True, tracking=6, help='Порожньо — діє досі')
    note = fields.Char(string='Звідки правило', help='Слово власника, дата; розбір рахунків')
    active = fields.Boolean(string='Активне', default=True, tracking=7)

    @api.depends('line_id.name', 'customer_id.name', 'kind', 'payer', 'date_from')
    def _compute_name(self):
        kinds, payers = dict(COST_KINDS), dict(PAYERS)
        for r in self:
            s = r.sudo()
            who = ' · '.join(x for x in (s.line_id.name, s.customer_id.name) if x) or '—'
            r.name = '%s · %s: %s з %s' % (who, kinds.get(r.kind, ''), payers.get(r.payer, ''), calc.iso(r.date_from) or '—')

    @api.model
    def _rules(self):
        """Активні правила для calc.cost_rule_pick (замовник — комерційний, 0 — уся лінія)."""
        return [{'id': r.id, 'line_id': r.line_id.id, 'customer_id': calc.cust_of(r.customer_id), 'kind': r.kind, 'payer': r.payer,
                 'date_from': calc.iso(r.date_from), 'date_to': calc.iso(r.date_to)} for r in self.sudo().search([])]

    @api.constrains('line_id', 'customer_id', 'kind', 'date_from', 'date_to', 'active')
    def _check_rule(self):
        lang = calc.lang_of(self.env.lang)
        rules = None
        for r in self:
            if r.date_to and r.date_to < r.date_from:
                raise ValidationError(calc.t('dates', lang))
            if not r.active:
                continue
            rules = rules if rules is not None else self._rules()
            mine = {'id': r.id, 'line_id': r.line_id.id, 'customer_id': calc.cust_of(r.customer_id), 'kind': r.kind,
                    'date_from': calc.iso(r.date_from), 'date_to': calc.iso(r.date_to)}
            clash = calc.cost_clash(rules, mine)
            if clash:
                raise ValidationError(calc.t('rule_clash', lang) % clash)
