# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import common


class WorksetAgentPayout(models.Model):
    """Виплата агенту. Кадровики, адм. кадрів і бухгалтерія лише читають (інакше «Виплачено» в історії автозатвердження підробив би будь-хто);
    пише ніч (sudo), вікно «Позначити виплаченим вручну» (sudo після перевірки груп) і група «Власник»."""
    _name = 'workset.agent.payout'
    _description = 'Виплата агенту'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'pay_day desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True, tracking=1)
    company_id = fields.Many2one('res.company', string='Фірма виплати', index=True)
    section = fields.Selection(common.SECTIONS, string='Розділ', required=True, index=True)
    pay_path = fields.Selection(common.PAY_PATHS, string='Шлях виплати', tracking=6, help=common.PAY_PATH_HELP)
    pay_day = fields.Date(string='День виплати', index=True, tracking=3)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    gross = fields.Monetary(string='Брутто', currency_field='currency_id', aggregator='sum')
    tax20 = fields.Monetary(string='Утримання 20 %', currency_field='currency_id', aggregator='sum')
    wh10 = fields.Monetary(string='Утримання 10 %', currency_field='currency_id', aggregator='sum')
    net = fields.Monetary(string='До переказу', currency_field='currency_id', tracking=2, aggregator='sum')
    move_id = fields.Many2one('account.move', string='Рахунок постачальника', ondelete='set null', index=True)
    state = fields.Selection(common.PAYOUT_STATES, string='Статус', default='prep', tracking=4, index=True)
    paid_date = fields.Date(string='Дата оплати', tracking=5)
    paid_manual = fields.Boolean(string='Позначено вручну')
    paid_note = fields.Char(string='Коментар до виплати')
    note = fields.Text(string='Нотатки')
    move_line_id = fields.Many2one('account.move.line', string='Рядок оплати', ondelete='set null', index=True,
                                   help='Рядок виписки, платежу або журналу WS, яким закрита виплата; один рядок — одна виплата')
    paid_via = fields.Selection(common.PAID_VIA, string='Звідки оплата', tracking=7)
    paid_amount = fields.Monetary(string='Переказано', currency_field='currency_id', help='Скільки фактично переказано агенту')
    work_month = fields.Date(string='Місяць роботи', help='Останній день місяця роботи, за яку виплата: у P&L витрата лягає в цей місяць, не в місяць виплати')
    paid_by = fields.Many2one('res.users', string='Позначив', ondelete='set null')
    guard = fields.Char(string='Код сторожа')
    guard_note = fields.Char(string='Примітка сторожа')
    bank_ref = fields.Integer(string='Рахунок агента', help='Номер запису банківського рахунку агента (id), на який готували виплату — сам IBAN тут не зберігаємо')
    iban = fields.Char(string='Реквізити', compute='_compute_iban',
                       help='IBAN рахунку агента, на який готували виплату — бачать лише бухгалтерія та адміністратори кадрів')
    purpose = fields.Char(string='Призначення', compute='_compute_purpose',
                          help='Призначення платежу для переказу: номер виплати AGENTS-P… (за ним виписка знайде виплату) і день виплати')
    accrual_ids = fields.One2many('workset.agent.accrual', 'payout_id', string='Нарахування')
    decision_id = fields.Many2one('workset.agent.decision', string='Рішення', ondelete='set null', index=True)
    pay_line_id = fields.Many2one('workset.agent.decision.line', string='Рядок рішення', ondelete='set null')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('agent_id.name', 'pay_day')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s' % (rec.agent_id.name or '—', rec.pay_day.strftime('%d.%m.%Y') if rec.pay_day else '—')

    def _compute_iban(self):
        # без зберігання, правами того, хто дивиться, і лише бухгалтерії / адм. кадрів (без sudo: ПДн)
        ok = self.env.user.has_group(common.G_ACCOUNT) or self.env.user.has_group(common.G_HR_MANAGER)
        Bank = self.env['res.partner.bank']
        for rec in self:
            acc = Bank.browse(rec.bank_ref).exists() if ok and rec.bank_ref else Bank
            rec.iban = acc.acc_number if acc else False

    @api.depends('pay_day')
    def _compute_purpose(self):
        for rec in self:
            day = rec.pay_day.strftime('%d.%m.%Y') if rec.pay_day else ''
            rec.purpose = ('Винагорода агента · AGENTS-P%s · виплата %s' % (rec.id, day)) if isinstance(rec.id, int) else False

    def action_paid_manual(self):
        """Вікно «Позначити виплаченим вручну» для вибраних виплат (бухгалтерія і власник; перевірка — і у вікні)."""
        return {'type': 'ir.actions.act_window', 'name': 'Позначити виплаченим вручну', 'res_model': 'workset.agent.paid.wizard', 'view_mode': 'form',
                'target': 'new', 'context': {'default_payout_ids': [(6, 0, self.ids)], 'default_date': fields.Date.context_today(self)}}
