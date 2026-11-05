# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import calc


class WorksetLine(models.Model):
    """Лінія — маленький довідник (без фірми): правило частки WorkSET і замовники, за якими ставка сама знаходить лінію."""
    _name = 'workset.line'
    _description = 'Лінія'
    _order = 'sequence, name, id'

    name = fields.Char(string='Лінія', required=True)
    code = fields.Char(string='Код')
    plan_ids = fields.Many2many('account.analytic.plan', 'workset_line_plan_rel', 'line_id', 'plan_id', string='Вузли аналітики',
                                help='Вузли дерева аналітики, під якими лежать замовники лінії')
    agent_id = fields.Many2one('res.partner', string='Агент лінії',
                               help='Довідково. Частку посередника в ставці рахують домовленості з посередниками (модуль посередників).')
    share_line_found = fields.Float(string='Знайшла лінія', help='Частка WorkSET, €/год, якщо людину знайшла лінія. 0 — правила немає: ціну вводять вручну')
    share_we_found = fields.Float(string='Знайшли ми', help='Частка WorkSET, €/год, якщо людину знайшли ми. 0 — правила немає: ціну вводять вручну')
    agent_share = fields.Float(string='Частка агента', help='Довідково, €/год. Частку посередника в ставці рахують домовленості з посередниками.')
    customer_ids = fields.Many2many('res.partner', 'workset_line_partner_rel', 'line_id', 'partner_id', string='Замовники',
                                    help='За ними ставка сама знаходить лінію')
    rule_note = fields.Text(string='Правило')
    active = fields.Boolean(string='Активна', default=True)
    sequence = fields.Integer(string='Порядок', default=10)
    min_share = fields.Float(string='Мінімум частки',
                             help='Нижче цієї частки WorkSET (€/год) ставка підсвічується червоним. Порожньо — правило лінії, а де його немає — мінімум за замовчуванням.')
    good_share = fields.Float(string='Добра частка', help='Від цієї частки WorkSET (€/год) частка підсвічується зеленим. Порожньо — значення за замовчуванням.')
    rate_count = fields.Integer(string='Діючих ставок', compute='_compute_work')
    work_count = fields.Integer(string='Працюють', compute='_compute_work', help='Скільки людей лінії працює зараз (за табелями)')
    avg_wage = fields.Float(string='Сер. на руки', compute='_compute_work', help='Середнє «На руки», €/год, по тих, хто працює зараз')
    avg_price = fields.Float(string='Сер. ціна', compute='_compute_work', help='Середня «Ціна», €/год, по тих, хто працює зараз')
    avg_share = fields.Float(string='Сер. частка', compute='_compute_work', help='Середня «Частка WorkSET», €/год, по тих, хто працює зараз; частка ≤ 0 не входить')

    def _compute_work(self):
        # один запит на всі лінії: діючі ставки → підсумок по кожній лінії (та сама функція, що й полоса підсумків)
        ids = [i for i in self.ids if isinstance(i, int)]
        by_line = {}
        if ids:
            rates = self.env['workset.rate'].search_read([('line_id', 'in', ids), ('state', '=', 'current')], ['line_id', 'work', 'wage', 'price', 'our_share'])
            for r in rates:
                by_line.setdefault(r['line_id'][0], []).append({'work': r['work'], 'wage': r['wage'], 'price': r['price'], 'share': r['our_share']})
        for line in self:
            s = calc.summary(by_line.get(line.id, []) if isinstance(line.id, int) else [])
            line.rate_count = s['total']
            line.work_count = s['working']
            line.avg_wage = s['avg_wage']
            line.avg_price = s['avg_price']
            line.avg_share = s['avg_share']

    def action_open_rates(self):
        self.ensure_one()
        return self.env['workset.rate']._rates_action(calc.t('line_rates', self.env['workset.rate']._lang()), [('line_id', '=', self.id)],
                                                      {'search_default_current': 1, 'default_line_id': self.id})

    @api.model
    def _panel_order(self):
        """Лінії за числом тих, хто працює зараз: {id лінії: число}."""
        groups = self.env['workset.rate'].sudo()._read_group([('state', '=', 'current'), ('work_group', '=', 'g1_work')], ['line_id'], ['__count'])
        return {line.id: n for line, n in groups if line}
