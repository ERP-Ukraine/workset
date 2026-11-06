# -*- coding: utf-8 -*-
"""«Розмітка аналітики» (М3): куди «Прибутковість» відносить суму аналітики плану рекрутингу — вузол плану, стаття аналітики, рахунок
або контрагент → вид (житло, паливо, …, власник, офіс, ігнор) і лінія (та замовник, якщо треба). Ключі правила складаються: сума береться
правилом, де збігаються всі заповнені ключі; найточніше — calc.map_pick. Лінії в правилі немає — лінія за вузлом статті або замовником.
Підказка виду за назвою статті (calc.kind_hint) — лише в формі й у рядках «Не розподілено»: рахує розмітка.
Розмітку пише інструмент штабу або редактор; сум і назв статей у коді немає."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc

MAP = 'workset.profit.analytic.map'
MAP_KINDS = [('housing', 'Житло'), ('housing_buffer', 'Буфер житла (довідково)'), ('fuel', 'Паливо'), ('tool', 'Інструмент і спецодяг'),
             ('auto', 'Авто'), ('transport', 'Перевезення людей'), ('dkv_bill', 'Рахунок DKV'), ('partner', 'Гроші лінії (партнер)'),
             ('owner', 'Власник'), ('office', 'Офіс'), ('ignore', 'Не враховувати')]
NO_LINE = ('dkv_bill', 'owner', 'office', 'ignore')  # види без лінії


class WorksetProfitAnalyticMap(models.Model):
    _name = MAP
    _description = 'Розмітка аналітики'
    _order = 'kind, line_id, id'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    plan_id = fields.Many2one('account.analytic.plan', string='Вузол плану', index=True, ondelete='cascade',
                              help='Усі статті під цим вузлом (і глибше), якщо точнішого правила немає')
    analytic_id = fields.Many2one('account.analytic.account', string='Стаття аналітики', index=True, ondelete='cascade')
    account_id = fields.Many2one('account.account', string='Рахунок', index=True, ondelete='cascade',
                                 help='Рахунок рядка проводки (напр. паливо в зарплаті): разом зі статтею або вузлом — точніше')
    partner_id = fields.Many2one('res.partner', string='Контрагент', index=True, ondelete='cascade',
                                 help='Постачальник рядка (напр. рахунки DKV) — найточніший ключ')
    kind = fields.Selection(MAP_KINDS, string='Вид', required=True)
    line_id = fields.Many2one('workset.line', string='Лінія', index=True, ondelete='restrict',
                              help='Порожньо — лінія за вузлом статті (вузли аналітики лінії) або за замовником статті')
    customer_id = fields.Many2one('res.partner', string='Замовник', ondelete='restrict', domain=[('is_company', '=', True)],
                                  help='Для правила «Хто платить» і житла лінії; порожньо — контрагент статті аналітики')
    date_from = fields.Date(string='З', help='Порожньо — від початку')
    date_to = fields.Date(string='До', help='Порожньо — без кінця')
    note = fields.Char(string='Нотатка')
    active = fields.Boolean(string='Активне', default=True)

    @api.depends('plan_id.name', 'analytic_id.name', 'account_id.name', 'partner_id.name', 'kind', 'line_id.name')
    def _compute_name(self):
        # назва зберігається — українською (як назви записів «Ставок»); назви ключів — sudo: рахунки бачить не кожен
        labels = dict(MAP_KINDS)
        for r in self:
            s = r.sudo()
            keys = ' · '.join(x for x in (s.plan_id.name, s.analytic_id.name, s.account_id.name, s.partner_id.name) if x) or '—'
            r.name = '%s → %s%s' % (keys, labels.get(r.kind, ''), (' · ' + s.line_id.name) if s.line_id else '')

    @api.onchange('analytic_id', 'account_id')
    def _onchange_hint(self):
        # підказка виду за назвою (рахунок, потім стаття) — лише коли вид ще не обрано
        if not self.kind:
            hint = calc.kind_hint(self.account_id.name, self.analytic_id.name)
            if hint:
                self.kind = hint

    @api.model
    def _rules(self):
        """Активні правила для calc.map_pick / classify: контрагент і замовник — комерційні."""
        return [{'id': r.id, 'plan_id': r.plan_id.id, 'analytic_id': r.analytic_id.id, 'account_id': r.account_id.id,
                 'partner_id': calc.cust_of(r.partner_id), 'kind': r.kind, 'line_id': 0 if r.kind in NO_LINE else r.line_id.id,
                 'customer_id': calc.cust_of(r.customer_id), 'date_from': calc.iso(r.date_from), 'date_to': calc.iso(r.date_to)}
                for r in self.sudo().search([])]

    @api.constrains('plan_id', 'analytic_id', 'account_id', 'partner_id', 'date_from', 'date_to', 'active')
    def _check_map(self):
        lang = calc.lang_of(self.env.lang)
        rules = None
        for r in self:
            if not (r.plan_id or r.analytic_id or r.account_id or r.partner_id):
                raise ValidationError(calc.t('map_empty', lang))
            if r.date_from and r.date_to and r.date_to < r.date_from:
                raise ValidationError(calc.t('dates', lang))
            if not r.active:
                continue
            rules = rules if rules is not None else self._rules()
            mine = {'id': r.id, 'plan_id': r.plan_id.id, 'analytic_id': r.analytic_id.id, 'account_id': r.account_id.id,
                    'partner_id': calc.cust_of(r.partner_id), 'date_from': calc.iso(r.date_from), 'date_to': calc.iso(r.date_to)}
            clash = calc.map_clash(rules, mine)
            if clash:
                raise ValidationError(calc.t('map_clash', lang) % clash)
