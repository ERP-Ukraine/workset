# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import calc, common, payplan


class WorksetAgentPaidWizard(models.TransientModel):
    """Вікно «Позначити виплаченим вручну»: виплаті «Готуємо … Сплачено, чекаємо виписку» (і «На перевірці») — «Виплачено» або «Виплачено
    частково» (сума менша за «До переказу»: недоплата брутто — «Недоплачено» рядка рішення, вона в буфері наступного рішення); нарахування —
    «Виплачено», якщо виплата покрила рядок рішення, інакше примітка «частково, залишок у буфері». Лише бухгалтерія і «Власник»; запис — sudo
    після перевірки груп; в історію виплати й рішення — хто, коли, скільки, звідки, коментар."""
    _name = 'workset.agent.paid.wizard'
    _description = 'Агенти: позначити виплаченим вручну'

    name = fields.Char(string='Назва')
    payout_ids = fields.Many2many('workset.agent.payout', 'workset_agent_paid_wizard_payout_rel', 'wizard_id', 'payout_id', string='Виплати')
    date = fields.Date(string='Дата виплати', required=True, default=fields.Date.context_today)
    note = fields.Char(string='Коментар', required=True, help='Як і де виплачено: з якого рахунку, де квитанція')
    amount = fields.Float(string='Сума, €', digits=(16, 2), compute='_compute_amount', store=True, readonly=False,
                          help='Скільки переказано (як «До переказу»). Менше — «Виплачено частково», решта — у буфер наступного рішення')
    source = fields.Selection(common.PAID_VIA, string='Що саме', required=True, help='Де видно переказ: виписка банку, журнал WS чи інше (квитанція)')

    @api.depends('payout_ids')
    def _compute_amount(self):
        for rec in self:
            rec.amount = sum(p.net or 0.0 for p in rec.payout_ids)

    def action_confirm(self):
        if not (self.env.su or self.env.user.has_group(common.G_ACCOUNT) or common.is_owner(self.env)):
            raise UserError(self.env._('Позначати виплати вручну можуть лише власник і бухгалтерія.'))
        labels = dict(common.PAYOUT_STATES)
        via_labels = dict((k, v.lower()) for k, v in common.PAID_VIA)
        for rec in self:
            note = (rec.note or '').strip()
            if not rec.date or not note or not rec.source:
                raise UserError(self.env._('Вкажіть дату, що саме (виписка / журнал WS / інше) і коментар (як і де виплачено).'))
            pays = rec.payout_ids.sudo().filtered(lambda p: p.state not in ('paid', 'paid_part', 'cancelled'))
            if not pays:
                raise UserError(self.env._('Серед вибраних немає виплат, які можна позначити (уже «Виплачено», «Виплачено частково» або «Скасовано»). Нічого не позначено.'))
            amount = round(rec.amount or 0.0, 2)
            total = round(sum(p.net or 0.0 for p in pays), 2)
            if len(pays) > 1 and abs(amount - total) > 0.005:
                raise UserError(self.env._('Часткову суму вказують для однієї виплати. Виберіть одну або залиште суму «До переказу» разом (%.2f €).') % total)
            for payout in pays:
                net = round(payout.net or 0.0, 2)
                try:
                    amt, part, short = payplan.manual_paid(net, payout.gross or 0.0, amount, total, len(pays))
                except calc.CalcError as e:
                    raise UserError(str(e))
                was = labels.get(payout.state, payout.state)
                line = payout.pay_line_id
                rest = (line.buffer_out or 0.0) if line else 0.0
                payout.write({'state': 'paid_part' if part else 'paid', 'paid_date': rec.date, 'paid_manual': True, 'paid_note': note, 'paid_via': rec.source,
                              'paid_amount': amt, 'paid_by': self.env.user.id, 'guard': False, 'guard_note': False})
                if short > 0.005 and line:
                    line.write({'unpaid': (line.unpaid or 0.0) + short})
                    rest = rest + short
                for accrual in payout.accrual_ids:
                    if accrual.state not in ('due', 'in_bill', 'decided'):
                        continue
                    if rest > 0.005:
                        tail = 'частково, залишок у буфері %.2f €' % rest
                        if tail not in (accrual.note or ''):
                            accrual.write({'note': ((accrual.note or '') + '\n' + tail).strip()})
                    else:
                        accrual.write({'state': 'paid'})
                body = 'Позначено вручну: %s. %s %.2f € з «До переказу» %.2f € (%s), дата %s, статус був «%s». Коментар: %s' % (
                    self.env.user.name, 'Частково' if part else 'Виплачено', amt, net, via_labels.get(rec.source, rec.source), rec.date.strftime('%d.%m.%Y'), was, note)
                if short > 0.005:
                    body = body.rstrip('.') + '. Недоплачено %.2f € брутто — у буфер наступного рішення.' % short
                payout.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
                if payout.decision_id:
                    payout.decision_id.message_post(body='Виплата #%d (%s): %s' % (payout.id, payout.agent_id.name or '—', body),
                                                    message_type='comment', subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.act_window_close'}
