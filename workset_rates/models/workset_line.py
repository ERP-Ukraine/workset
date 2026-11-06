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
    # частки й мінімум — з діючої «Загальної ставки» рівня лінії (історія — у правилах); тут лише показ
    rule_ids = fields.One2many('workset.rate.rule', 'line_id', string='Загальні ставки')
    # житло лінії (розділ 3.13): вкладка «Житло» форми лінії; правити — група «Ставки: правила ліній» (права моделі)
    housing_ids = fields.One2many('workset.line.housing', 'line_id', string='Житло')
    rule_id = fields.Many2one('workset.rate.rule', string='Правило лінії', compute='_compute_rule',
                              help='Діюча сьогодні «Загальна ставка» лінії (без замовника)')
    anchor = fields.Selection(related='rule_id.anchor', string='Якір')
    share_line_found = fields.Float(string='Знайшла лінія', compute='_compute_rule',
                                    help='Частка, €/год, якщо людину знайшла лінія — з діючої загальної ставки лінії. 0 — правила немає')
    share_we_found = fields.Float(string='Знайшли ми', compute='_compute_rule',
                                  help='Частка, €/год, якщо людину знайшли ми — з діючої загальної ставки лінії. 0 — правила немає')
    agent_share = fields.Float(string='Частка агента', help='Довідково, €/год. Частку посередника в ставці рахують домовленості з посередниками.')
    agent_expected = fields.Boolean(string='Очікується посередник',
                                    help='Лінія працює з посередником: ставка без діючої домовленості з ним потрапляє у «Звірку»')
    customer_ids = fields.Many2many('res.partner', 'workset_line_partner_rel', 'line_id', 'partner_id', string='Замовники',
                                    help='За ними ставка сама знаходить лінію')
    rule_note = fields.Text(string='Правило')
    active = fields.Boolean(string='Активна', default=True)
    sequence = fields.Integer(string='Порядок', default=10)
    min_share = fields.Float(string='Мінімум частки', compute='_compute_rule',
                             help='Нижче цієї частки WorkSET (€/год) ставка підсвічується червоним — з діючої загальної ставки лінії. 0 — мінімум за замовчуванням.')
    good_share = fields.Float(string='Добра частка', help='Від цієї частки WorkSET (€/год) частка підсвічується зеленим. Порожньо — значення за замовчуванням.')
    rate_count = fields.Integer(string='Діючих ставок', compute='_compute_work')
    work_count = fields.Integer(string='Працюють', compute='_compute_work', help='Скільки людей лінії працює зараз (за табелями)')
    avg_wage = fields.Float(string='Сер. зарплата працівника', compute='_compute_work',
                            help='Середня «Зарплата працівника», €/год, по тих, хто працює зараз')
    avg_price = fields.Float(string='Сер. платять нам', compute='_compute_work', help='Середнє «Платять нам», €/год, по тих, хто працює зараз')
    avg_share = fields.Float(string='Сер. частка', compute='_compute_work', help='Середня «Частка WorkSET», €/год, по тих, хто працює зараз; частка ≤ 0 не входить')

    def _compute_rule(self):
        # правило лінії (замовник порожній), що діє сьогодні: найпізніша «Ставка з» ≤ сьогодні серед активних
        ids = [i for i in self.ids if isinstance(i, int)]
        today = calc.iso(self.env['workset.rate']._today())
        Rule = self.env['workset.rate.rule'].sudo()
        rules = []
        if ids:
            rules = [{'id': r.id, 'line_id': r.line_id.id, 'customer_id': 0, 'date_from': calc.iso(r.date_from)}
                     for r in Rule.search([('line_id', 'in', ids), ('customer_id', '=', False)])]
        for line in self:
            rid = calc.rule_pick(rules, line.id, 0, today) if isinstance(line.id, int) else 0
            rule = self.env['workset.rate.rule'].browse(rid)
            line.rule_id = rule
            line.share_line_found = rule.sudo().share_line_found if rid else 0.0
            line.share_we_found = rule.sudo().share_we_found if rid else 0.0
            line.min_share = rule.sudo().min_share if rid else 0.0

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
