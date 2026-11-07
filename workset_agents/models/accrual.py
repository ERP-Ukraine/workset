# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import calc, common, payplan

AC_NAMES = {'hour': '€ за годину', 'oneoff': 'Разова', 'storno': 'Сторно'}
STORNO_CONFIRM = ('Сторнувати нарахування? Ще не виплачене — обидва стануть «Скасовано»; уже у виплаті — сторно піде мінусом у найближчу виплату.')


class WorksetAgentAccrual(models.Model):
    """Нарахування агенту (обидва розділи). Пише ніч (sudo) і офіс (разові); виправлення — лише сторно: суму записаного не міняємо."""
    _name = 'workset.agent.accrual'
    _description = 'Нарахування агенту'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'earned_date desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    section = fields.Selection(common.SECTIONS, string='Розділ', required=True, index=True)
    people_deal_id = fields.Many2one('workset.agent.deal.people', string='Домовленість агента-кандидата', ondelete='restrict', index=True)
    client_deal_id = fields.Many2one('workset.agent.deal.client', string='Домовленість агента-замовника', ondelete='restrict', index=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True)
    kind = fields.Selection(common.ACC_KINDS, string='Вид', required=True)
    person_id = fields.Many2one('res.partner', string='Людина', ondelete='set null', index=True)
    customer_id = fields.Many2one('res.partner', string='Замовник', ondelete='set null', index=True)
    move_id = fields.Many2one('account.move', string='Рахунок замовника', ondelete='set null', index=True)
    hours = fields.Float(string='Години')
    rate = fields.Float(string='Ставка, €/год')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    amount = fields.Monetary(string='Сума', currency_field='currency_id', tracking=2, aggregator='sum')
    earned_date = fields.Date(string='Зароблено', index=True)
    pay_date = fields.Date(string='Дата виплати', tracking=3)
    work_from = fields.Date(string='Робота з', help='Перший день роботи годин цього нарахування (табелі; без табеля — дата рахунку)')
    work_to = fields.Date(string='Робота по', help='Останній день роботи годин цього нарахування — за ним місяць роботи у виплаті')
    fund = fields.Boolean(string='Фонд', index=True, help='Нарахування фонду працівників: не виплачується нікому, лише звірка з конвертом; ставить нічний розрахунок')
    company_id = fields.Many2one('res.company', string='Фірма виплати', index=True)
    line_id = fields.Many2one('workset.line', string='Чия лінія платить', ondelete='set null', index=True)
    state = fields.Selection(common.ACC_STATES, string='Статус', default='expected', tracking=1, index=True)
    guard = fields.Char(string='Код сторожа')
    guard_note = fields.Char(string='Примітка сторожа')
    key = fields.Char(string='Ключ неповторності', index=True, copy=False,
                      help='Один запис на ключ: нічний розрахунок шукає ключ до створення; база не пускає другий запис з тим самим ключем')
    storno_id = fields.Many2one('workset.agent.accrual', string='Сторно-посилання', ondelete='set null', index=True)
    note = fields.Text(string='Нотатки')
    payout_id = fields.Many2one('workset.agent.payout', string='Виплата', ondelete='set null', index=True)
    pay_line_id = fields.Many2one('workset.agent.decision.line', string='Рядок рішення', ondelete='set null', index=True)
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    # ключ неповторності — у базі (у ручній моделі через API його не поставити — там лише пошук перед створенням)
    _key_uniq = models.Constraint('UNIQUE(key)', 'Ключ нарахування повторюється — дубль означав би подвійну виплату.')

    @api.depends('agent_id.name', 'kind', 'amount')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s · %s €' % (rec.agent_id.name or '—', AC_NAMES.get(rec.kind) or rec.kind or '—', rec.amount)

    def action_storno(self):
        """Кнопка «Сторно»: нове нарахування «Сторно» з мінусом і посиланням на вихідне (адміністратори кадрів і власник). Ще не в оплаті —
        обидва «Скасовано»; уже «У рахунку» / «Виплачено» — сторно «Очікується», день і статус поставить ніч. Причина — у «Нотатки» сторно."""
        if not (self.env.su or self.env.user.has_group(common.G_HR_MANAGER) or common.is_owner(self.env)):
            raise UserError(self.env._('Сторнувати нарахування можуть адміністратори кадрів і власник.'))
        today = common.kyiv_today()
        Acc = self.sudo()
        made = Acc.browse()
        for rec in self.sudo():
            linked = Acc.search_read([('storno_id', '=', rec.id), ('state', '!=', 'cancelled')], ['key', 'amount', 'hours'])
            base = 'storno:' + (rec.key or ('a%d' % rec.id))
            taken = [r['key'] for r in Acc.with_context(active_test=False).search_read([('key', '=like', base + '%')], ['key'])]
            try:
                vals, pre = payplan.storno_button_vals({'id': rec.id, 'kind': rec.kind, 'state': rec.state, 'key': rec.key, 'amount': rec.amount,
                                                        'hours': rec.hours}, linked, today, self.env.user.name, taken)
            except calc.CalcError as e:
                raise UserError(str(e))
            vals.update({'section': rec.section, 'people_deal_id': rec.people_deal_id.id, 'client_deal_id': rec.client_deal_id.id,
                         'agent_id': rec.agent_id.id, 'person_id': rec.person_id.id, 'customer_id': rec.customer_id.id, 'move_id': rec.move_id.id,
                         'rate': rec.rate, 'currency_id': rec.currency_id.id, 'company_id': rec.company_id.id, 'line_id': rec.line_id.id})
            s = Acc.create(vals)
            if pre:
                rec.write({'state': 'cancelled'})
            rec.message_post(body=self.env._('Сторно — %s, нарахування #%d. %s') % (self.env.user.name, s.id, 'Ще не виплачене: обидва «Скасовано».' if pre
                                                                           else 'Уже у виплаті: сторно піде мінусом у найближчу виплату (дату поставить нічний розрахунок).'),
                             message_type='comment', subtype_xmlid='mail.mt_note')
            made |= s
        if len(made) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': made.id, 'view_mode': 'form', 'views': [[False, 'form']],
                    'target': 'current'}
        return True
