# -*- coding: utf-8 -*-
"""Картки DKV, їхні періоди й транзакції (М3).

Картка — номер, порядковий № у кабінеті DKV, машина (fleet.vehicle, необов'язково). Період картки з датами: лінія (і замовник), «Офіс»
або «Перевезення людей»; одна картка — один період на дату (calc.period_clash). Транзакція — заправка або інша операція з рахунку DKV:
дата, картка, країна, місто, група товару, кількість, нетто й брутто EUR, номер рахунку DKV, рахунок постачальника в Odoo (необов'язково),
джерело (PDF рахунку або API). Транзакції пише інструмент штабу (із PDF, згодом з API) за «Ключ рядка» — повтор не множить.
У «Прибутковості» витрата — брутто EUR; рахунок постачальника з транзакціями (полем або за номером рахунку = «Референс» рахунку) своєю
аналітикою вдруге не рахується, різниця «рахунок − транзакції» — «Не розподілено: DKV різниця»."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import calc

CARD = 'workset.dkv.card'
PERIOD = 'workset.dkv.card.period'
TXN = 'workset.dkv.txn'
TARGETS = [('line', 'Лінія'), ('office', 'Офіс'), ('transport', 'Перевезення людей')]
TXN_SOURCES = [('pdf', 'PDF рахунку'), ('api', 'API DKV')]


class WorksetDkvCard(models.Model):
    _name = CARD
    _description = 'Картка DKV'
    _order = 'seq, number, id'

    name = fields.Char(string='Картка', compute='_compute_name', store=True)
    number = fields.Char(string='Номер картки', required=True, index=True)
    seq = fields.Integer(string='Порядковий №', help='Номер картки в кабінеті DKV')
    vehicle_id = fields.Many2one('fleet.vehicle', string='Машина', ondelete='set null')
    period_ids = fields.One2many(PERIOD, 'card_id', string='Періоди')
    now_text = fields.Char(string='Зараз', compute='_compute_now', help='Період картки на сьогодні')
    txn_count = fields.Integer(string='Транзакцій', compute='_compute_txn_count')
    note = fields.Char(string='Нотатка')
    active = fields.Boolean(string='Активна', default=True)

    @api.depends('number', 'seq')
    def _compute_name(self):
        for c in self:
            c.name = ('№ %d · %s' % (c.seq, c.number or '—')) if c.seq else (c.number or '—')

    @api.depends('period_ids.target', 'period_ids.line_id', 'period_ids.date_from', 'period_ids.date_to')
    def _compute_now(self):
        today = calc.iso(self.env['workset.profit.month']._today())
        targets = dict(TARGETS)
        for c in self:
            periods = [{'id': p.id, 'card_id': c.id, 'target': p.target, 'line_id': p.line_id.id, 'date_from': calc.iso(p.date_from),
                        'date_to': calc.iso(p.date_to)} for p in c.period_ids if isinstance(p.id, int)]
            p = calc.card_period(periods, c.id, today)
            if not p:
                c.now_text = '—'
                continue
            rec = c.period_ids.filtered(lambda x: x.id == p['id'])
            c.now_text = rec.line_id.name if (p['target'] == 'line' and rec.line_id) else targets.get(p['target'], '')

    def _compute_txn_count(self):
        groups = dict(self.env[TXN]._read_group([('card_id', 'in', self.ids)], ['card_id'], ['__count'])) if self.ids else {}
        for c in self:
            c.txn_count = groups.get(c, 0) if isinstance(c.id, int) else 0

    @api.constrains('number')
    def _check_number(self):
        lang = calc.lang_of(self.env.lang)
        for c in self:
            same = self.sudo().with_context(active_test=False).search([('id', '!=', c.id), ('number', '=', c.number)], limit=1)
            if same:
                raise ValidationError(calc.t('card_dup', lang) % (c.number, same.name))

    def action_open_txns(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': calc.t('a_txns', calc.lang_of(self.env.lang)), 'res_model': TXN, 'view_mode': 'list,form',
                'views': [[False, 'list'], [False, 'form']], 'domain': [('card_id', '=', self.id)], 'context': {'default_card_id': self.id}}


class WorksetDkvCardPeriod(models.Model):
    _name = PERIOD
    _description = 'Період картки DKV'
    _order = 'card_id, date_from desc, id desc'

    card_id = fields.Many2one(CARD, string='Картка', required=True, index=True, ondelete='cascade')
    target = fields.Selection(TARGETS, string='Куди', required=True, default='line')
    line_id = fields.Many2one('workset.line', string='Лінія', index=True, ondelete='restrict')
    customer_id = fields.Many2one('res.partner', string='Замовник', ondelete='restrict', domain=[('is_company', '=', True)],
                                  help="Необов'язково: для правила «Хто платить» замовника")
    date_from = fields.Date(string='З', required=True, index=True)
    date_to = fields.Date(string='До', index=True, help='Порожньо — діє досі')
    note = fields.Char(string='Звідки', help='Чиє слово, дата')

    @api.model
    def _periods(self):
        """Періоди активних карток для calc.card_period (замовник — комерційний)."""
        return [{'id': p.id, 'card_id': p.card_id.id, 'target': p.target, 'line_id': p.line_id.id, 'customer_id': calc.cust_of(p.customer_id),
                 'date_from': calc.iso(p.date_from), 'date_to': calc.iso(p.date_to)}
                for p in self.sudo().search([('card_id.active', '=', True)])]

    @api.constrains('card_id', 'target', 'line_id', 'date_from', 'date_to')
    def _check_period(self):
        lang = calc.lang_of(self.env.lang)
        periods = None
        for p in self:
            if p.target == 'line' and not p.line_id:
                raise ValidationError(calc.t('period_line', lang))
            if p.date_to and p.date_to < p.date_from:
                raise ValidationError(calc.t('dates', lang))
            periods = periods if periods is not None else [
                {'id': x.id, 'card_id': x.card_id.id, 'date_from': calc.iso(x.date_from), 'date_to': calc.iso(x.date_to)}
                for x in self.sudo().search([('card_id', 'in', self.card_id.ids)])]
            clash = calc.period_clash(periods, {'id': p.id, 'card_id': p.card_id.id, 'date_from': calc.iso(p.date_from),
                                                'date_to': calc.iso(p.date_to)})
            if clash:
                raise ValidationError(calc.t('period_clash', lang) % clash)


class WorksetDkvTxn(models.Model):
    _name = TXN
    _description = 'Транзакція DKV'
    _order = 'date desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    date = fields.Date(string='Дата', required=True, index=True)
    card_id = fields.Many2one(CARD, string='Картка', required=True, index=True, ondelete='restrict')
    country = fields.Char(string='Країна')
    city = fields.Char(string='Місто')
    product_group = fields.Char(string='Група товару', help='Паливо, принадлежності, дорожній збір — як у рахунку DKV')
    quantity = fields.Float(string='Кількість', digits=(16, 2))
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    amount_net = fields.Monetary(string='Нетто, EUR', currency_field='currency_id')
    amount_gross = fields.Monetary(string='Брутто, EUR', currency_field='currency_id', required=True,
                                   help='Так проведено в книгах: у «Прибутковість» іде брутто')
    invoice_no = fields.Char(string='Рахунок DKV', index=True,
                             help='Номер рахунку DKV. Поле «Рахунок постачальника» порожнє — транзакція привʼязується до рахунку Odoo, '
                                  'у якого «Референс» = цей номер')
    move_id = fields.Many2one('account.move', string='Рахунок постачальника', index=True, ondelete='set null',
                              help='Рахунок DKV в Odoo: його аналітика вдруге не рахується — рахуються транзакції; різниця «рахунок − '
                                   'транзакції» (частково завантажені) — рядком «Не розподілено: DKV різниця». Порожньо й номер рахунку не '
                                   'знайдено — транзакція рахується по лініях, у звірці — «без рахунку в Odoo»')
    source = fields.Selection(TXN_SOURCES, string='Джерело', required=True, default='pdf')
    ref = fields.Char(string='Ключ рядка', index=True, help='Унікальний ключ рядка з PDF або API: повторний запис не множить транзакцію')
    note = fields.Char(string='Нотатка')

    @api.depends('date', 'card_id.name', 'amount_gross', 'city')
    def _compute_name(self):
        for x in self:
            x.name = ' · '.join(v for v in (calc.iso(x.date), x.card_id.name, x.city, calc.money_text(x.amount_gross)) if v)

    @api.constrains('ref')
    def _check_ref(self):
        lang = calc.lang_of(self.env.lang)
        for x in self:
            if x.ref and self.sudo().search_count([('id', '!=', x.id), ('ref', '=', x.ref)]):
                raise ValidationError(calc.t('txn_dup', lang) % x.ref)
