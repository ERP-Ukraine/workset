# -*- coding: utf-8 -*-
"""«Тимчасова зміна» — надбавка «+€ за годину» або разова сума з–до поверх «на руки» пари «працівник × замовник».

База ставки (Р), частка WorkSET і чиста частка від неї НЕ змінюються, у карту проєктів вона не пишеться: на ставці видно лише
«На руки зараз» = Р + Н. Доплата рахується по місяцях (зарплата — місяцями): рядок місяця стає «До доплати» на 1-ше число наступного
місяця або наступного дня після «До»; на всі такі рядки — ОДНА задача To Do виконавцю звірки (параметр workset_rates.check_assignee_uid),
минула відкрита задача доплат закривається «замінено новою»; набір рядків змінився того ж дня, коли задачу поставлено («Виплачено»,
нова зміна), коли задачу створено (день створення за Києвом), — переписується та сама задача (не більше однієї нової задачі на день),
а про нові рядки в ній виконавець отримує повідомлення в історії зміни. «Виплачено» ставить людина кнопкою в рядку місяця.
Рядки місяців, години й статуси пише лише код (sudo); редактор створює й править саму зміну."""
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from . import calc
from .workset_rate import RATE, SOURCES, calc_uniq, cust_of

_logger = logging.getLogger(__name__)

RAISE = 'workset.rate.raise'
MONTH = 'workset.rate.raise.month'
KINDS = [('hour', '+€ за годину'), ('lump', 'Разова сума')]
REASONS = [('few_hours', 'Мало годин'), ('idle', 'Простій'), ('clothes', 'Спецодяг'), ('other', 'Інше')]
RAISE_PAYERS = [('line', 'Лінія'), ('workset', 'WorkSET'), ('client', 'Клієнт')]
# «Нічого доплачувати» — «До» минуло, годин ще немає: щоночі перераховується до строку workset_rates.raise_grace_days; потім «Без годин»
RAISE_STATES = [('future', 'Майбутня'), ('current', 'Діє'), ('due', 'До доплати'), ('task', 'Задачу поставлено'), ('nothing', 'Нічого доплачувати'),
                ('nohours', 'Без годин'), ('paid', 'Виплачено'), ('cancel', 'Скасовано')]
ROW_STATES = [('open', 'Іде'), ('due', 'До доплати'), ('task', 'Задачу поставлено'), ('paid', 'Виплачено'), ('skip', 'Нічого доплачувати')]


class WorksetRateRaise(models.Model):
    _name = RAISE
    _description = 'Тимчасова зміна'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    person_id = fields.Many2one('res.partner', string='Працівник', required=True, index=True, tracking=1, ondelete='restrict',
                                help='Робочий контакт картки працівника — один на всі фірми')
    customer_id = fields.Many2one('res.partner', string='Замовник', required=True, index=True, tracking=2, ondelete='restrict')
    rate_id = fields.Many2one(RATE, string='Ставка', readonly=True, ondelete='set null', help='Ставка пари, чинна на «З» (ставить код)')
    kind = fields.Selection(KINDS, string='Вид', required=True, default='hour', tracking=3)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    amount = fields.Monetary(string='Сума', currency_field='currency_id', required=True, tracking=4, help='€ за годину або разова сума, €')
    date_from = fields.Date(string='З', required=True, index=True, tracking=5)
    date_to = fields.Date(string='До', required=True, index=True, tracking=6, help='Без «До» тимчасову зміну не зберегти: постійне підвищення — нова ставка')
    reason = fields.Selection(REASONS, string='Причина', tracking=7)
    reason_note = fields.Char(string='Причина словами')
    payer = fields.Selection(RAISE_PAYERS, string='Хто платить', required=True, tracking=8)
    source = fields.Selection(SOURCES, string='Джерело', required=True)
    source_note = fields.Char(string='Звідки саме', required=True)
    version_id = fields.Many2one('hr.version', string='Версія договору', ondelete='set null',
                                 help='Якщо підвищення внесли й версією договору — її автоматика «договір → база» не бере')
    month_ids = fields.One2many(MONTH, 'raise_id', string='По місяцях', readonly=True)
    hours = fields.Float(string='Годин', readonly=True, help='Години пари в межах зміни (табелі фірм периметра)')
    total = fields.Monetary(string='До доплати всього', currency_field='currency_id', readonly=True, help='Сума всіх місяців зміни')
    state = fields.Selection(RAISE_STATES, string='Статус', readonly=True, default='future', index=True, tracking=9)
    note = fields.Text(string='Нотатка')
    soon = fields.Boolean(string='Закінчується скоро', compute='_compute_soon',
                          help='Діє й до «До» не більше 3 днів — рядок списку жовтий (як розділ звірки «Тимчасові зміни»)')

    @api.depends('person_id.name', 'customer_id.name', 'date_from', 'date_to')
    def _compute_name(self):
        for r in self:
            r.name = '%s · %s · %s – %s' % (r.person_id.name or '—', r.customer_id.name or '—', calc.fmt_date(r.date_from) or '—', calc.fmt_date(r.date_to) or '—')

    @api.depends('state', 'date_to')
    @api.depends_context('rates_today')
    def _compute_soon(self):
        today = calc.iso(self.env[RATE]._today())
        for r in self:
            r.soon = calc.raise_alert(r.state, calc.iso(r.date_to), today) == 'ending'

    @api.constrains('date_from', 'date_to', 'amount', 'kind', 'person_id', 'customer_id', 'state')
    def _check_raise(self):
        lang = calc.lang_of(self.env.lang)
        for r in self:
            if r.date_to < r.date_from:
                raise ValidationError(calc.t('rs_dates', lang))
            if calc.same_money(r.amount, 0):
                raise ValidationError(calc.t('rs_zero', lang))
            if r.kind != 'hour' or r.state == 'cancel':
                continue
            for o in self.sudo().search([('id', '!=', r.id), ('kind', '=', 'hour'), ('state', '!=', 'cancel'), ('person_id', '=', r.person_id.id),
                                         ('customer_id.commercial_partner_id', '=', cust_of(r.customer_id))]):
                if calc.overlaps(calc.iso(r.date_from), calc.iso(r.date_to), calc.iso(o.date_from), calc.iso(o.date_to)):
                    raise ValidationError(calc.t('rs_overlap', lang) % (o.id, calc.fmt_date(o.date_from), calc.fmt_date(o.date_to)))

    def _warn_long(self):
        # > 90 днів — попередження в історії, не заборона
        lang = calc.lang_of(self.env.lang)
        for r in self:
            if calc.raise_long(calc.iso(r.date_from), calc.iso(r.date_to)):
                r.message_post(body=calc.t('rs_long', lang) % (calc.LONG_RAISE_DAYS, calc.fmt_date(r.date_from), calc.fmt_date(r.date_to)),
                               message_type='comment', subtype_xmlid='mail.mt_note')

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._warn_long()
        recs._refresh()
        self.env[RAISE]._raise_task()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if {'date_from', 'date_to'} & set(vals):
            self._warn_long()
        if {'person_id', 'customer_id', 'kind', 'amount', 'date_from', 'date_to', 'payer'} & set(vals):
            self._refresh()
            self.env[RAISE]._raise_task()
        return res

    # ——— рядки місяців, статус, ставка пари ———
    def _refresh(self, today=None):
        """Ставка пари на «З», рядки місяців (години × сума, «до доплати»), підсумки й статус. Пише лише змінене (sudo). → число змінених змін."""
        Rate = self.env[RATE]
        today = calc.iso(today or Rate._today())
        raw = Rate._param(calc.P_RAISE_GRACE)
        grace = int(calc.parse_float(raw, calc.RAISE_GRACE_DAYS)) if raw else calc.RAISE_GRACE_DAYS
        Month = self.env[MONTH].sudo()
        changed = 0
        for r in self.sudo():
            vals = {}
            pair = Rate.sudo().search([('person_id', '=', r.person_id.id), ('customer_id.commercial_partner_id', '=', cust_of(r.customer_id)),
                                       ('date_from', '<=', r.date_from)], order='date_from desc, id desc', limit=1)
            if r.rate_id != pair:
                vals['rate_id'] = pair.id or False
            if r.state != 'cancel':
                rows = []
                if r.kind == 'hour':
                    rows = Rate._pair_hour_rows(r.person_id.id, cust_of(r.customer_id), calc.iso(r.date_from), min(calc.iso(r.date_to), today))
                plan = calc.raise_months(r.kind, calc.iso(r.date_from), calc.iso(r.date_to), r.amount, rows, today)
                have = {calc.iso(m.month): m for m in r.month_ids}
                for p in plan:
                    m = have.pop(p['month'], None)
                    if m and m.state == 'paid':
                        continue
                    same = bool(m) and calc.same_money(m.total, p['total']) and calc.same_money(m.hours, p['hours'])
                    state = calc.raise_row_state(p['due'], p['total'], m.state if m else '', same)
                    want = {'date_from': p['date_from'], 'date_to': p['date_to'], 'hours': p['hours'], 'amount': r.amount, 'total': p['total'], 'state': state}
                    if m:
                        diff = {k: v for k, v in want.items() if (calc.iso(m[k]) if k in ('date_from', 'date_to') else m[k]) != v}
                        if diff:
                            m.write(diff)
                    else:
                        Month.create(dict(want, raise_id=r.id, month=p['month']))
                # місяць випав (дати скоротили) — рядок прибирається, якщо ще не виплачено
                (Month.browse([m.id for m in have.values() if m.state != 'paid'])).unlink()
                hours = round(sum(r.month_ids.mapped('hours')), 2)
                total = round(sum(r.month_ids.mapped('total')), 2)
                if not calc.same_money(r.hours, hours):
                    vals['hours'] = hours
                if not calc.same_money(r.total, total):
                    vals['total'] = total
                state = calc.raise_state(False, calc.iso(r.date_from), calc.iso(r.date_to), today, r.month_ids.mapped('state'), grace)
                if r.state != state:
                    vals['state'] = state
            if vals:
                changed += 1
                r.write(vals)
        return changed

    @api.model
    def _open_tasks(self):
        """Відкриті задачі доплат (ті, на які посилаються рядки місяців; виконану Odoo видаляє або архівує)."""
        acts = self.env[MONTH].sudo().search([('activity_id', '!=', False)]).activity_id
        return acts.filtered(lambda a: a.exists() and ('active' not in a._fields or a.active))

    @api.model
    def _raise_task(self, today=None):
        """ОДНА відкрита задача To Do на всі рядки «До доплати» / «Задачу поставлено». Набір рядків не змінився — задача та сама;
        змінився — минула закривається «замінено новою», ставиться нова; рядків немає — закривається «доплат більше немає».
        → {'created', 'closed', 'kept', 'rows'}"""
        Rate = self.env[RATE]
        assignee = Rate._assignee()
        lang = calc.lang_of(assignee.lang)
        today = calc.iso(today or Rate._today())
        Month = self.env[MONTH].sudo()
        rows = Month.search([('state', 'in', ['due', 'task']), ('raise_id.state', '!=', 'cancel')], order='month, raise_id, id')
        opened = self._open_tasks()
        same = bool(rows) and len(opened) == 1 and all(m.state == 'task' and m.activity_id == opened for m in rows) \
            and not Month.search_count([('activity_id', '=', opened.id), ('id', 'not in', rows.ids)])
        # набір рядків змінився в той самий день, коли задачу створено (день створення за Києвом, не строк), — переписати ту саму, а не
        # нову щоразу; нові рядки в ній — повідомлення виконавцю (переписана задача сама не сповіщає)
        created_day = Rate._local_day(opened.create_date) if len(opened) == 1 else ''
        mode = calc.raise_task_mode(bool(rows), same, len(opened), created_day, today)
        if mode == 'keep':
            return {'created': 0, 'closed': 0, 'kept': opened.id, 'rows': len(rows), 'updated': 0}
        reuse = opened if mode == 'update' else opened.browse()
        added = rows.filtered(lambda m: m.activity_id != reuse) if reuse else rows.browse()
        closed = 0
        for a in opened - reuse:
            a.action_feedback(feedback=calc.t('rs_replaced' if rows else 'rs_none', lang))
            closed += 1
        Month.search([('activity_id', '!=', False), ('id', 'not in', rows.ids)]).write({'activity_id': False})
        if not rows:
            return {'created': 0, 'closed': closed, 'kept': 0, 'rows': 0, 'updated': 0}
        me = self.with_context(lang=assignee.lang)
        payer = dict(me._fields['payer']._description_selection(me.env))
        src = dict(me._fields['source']._description_selection(me.env))
        table = []
        for m in rows:
            r = m.raise_id
            how = calc.t('lump', lang) if r.kind == 'lump' else '%s × %s' % (calc.num(m.hours), calc.money(m.amount))
            table.append([{'text': r.person_id.name or '', 'href': calc.link(RAISE, r.id)}, r.customer_id.name or '',
                          '%s – %s' % (calc.fmt_date(m.date_from), calc.fmt_date(m.date_to)), how, calc.money(m.total) + ' €',
                          payer.get(r.payer, ''), ('%s: %s' % (src.get(r.source, ''), r.source_note or '')).strip(': ')])
        cols = [calc.t(k, lang) for k in ('c_person', 'c_customer', 'c_period', 'c_hours', 'c_total', 'c_payer', 'c_src')]
        total = round(sum(rows.mapped('total')), 2)
        w = calc.t('rs_row', lang)
        todo = self.env.ref('mail.mail_activity_data_todo')
        vals = {'summary': calc.t('rs_task_summary', lang) % (len(rows), calc.plural(len(rows), w[0], w[1], w[2]), calc.money(total)),
                'note': calc.t('rs_task_note', lang) + calc.table_html(cols, table), 'user_id': assignee.id,
                'date_deadline': calc.workdays_after(today, calc.DUE_WORKDAYS)}
        Act = self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True)
        if reuse:
            act = reuse.sudo().with_context(mail_activity_quick_update=True)
            act.write(vals)
            if added:
                # 4.5: про нову строку виконавець має дізнатися — запис у історії зміни, до якої прив'язано задачу, з адресатом
                who = ', '.join(calc_uniq([m.raise_id.person_id.name for m in added]))
                self.env[RAISE].sudo().browse(act.res_id).with_context(lang=assignee.lang).message_post(
                    body=calc.t('rs_added', lang) % (vals['summary'], len(added), who), message_type='comment',
                    subtype_xmlid='mail.mt_note', partner_ids=assignee.partner_id.ids)
        else:
            act = Act.create(dict(vals, res_model_id=self.env['ir.model']._get_id(RAISE), res_id=rows[0].raise_id.id, activity_type_id=todo.id))
        rows.write({'state': 'task', 'activity_id': act.id})
        rows.raise_id._refresh(today)
        _logger.info('Ставки: задача доплат — рядків %d, %s €, закрито задач %d%s', len(rows), calc.money(total), closed, ' (та сама за день)' if reuse else '')
        return {'created': 0 if reuse else act.id, 'closed': closed, 'kept': 0, 'rows': len(rows), 'updated': act.id if reuse else 0}

    @api.model
    def _raises_daily(self, today=None):
        """А3+ п. 3: зміни, що не скасовані, не виплачені й не «Без годин» (calc.raise_live), — рядки місяців і статуси (і «Нічого
        доплачувати»: табелі, внесені після «До», дадуть доплату); далі одна задача доплат. → {'changed', 'task'}"""
        today = calc.iso(today or self.env[RATE]._today())
        live = self.sudo().search([]).filtered(lambda x: calc.raise_live(x.state))
        changed = live._refresh(today)
        return {'changed': changed, 'task': self._raise_task(today)}

    # ——— кнопки ———
    def _check_editor(self):
        if not (self.env.su or self.env.user.has_group('workset_rates.group_editor') or self.env.user.has_group('base.group_system')):
            raise AccessError(calc.t('only_editor', calc.lang_of(self.env.lang)))

    def action_cancel(self):
        """«Скасувати»: невиплачені рядки — «Нічого доплачувати», задача доплат — заново без них."""
        self._check_editor()
        for r in self.sudo():
            r.month_ids.filtered(lambda m: m.state != 'paid').write({'state': 'skip'})
            r.write({'state': 'cancel'})
        self.env[RAISE]._raise_task()
        return True


class WorksetRateRaiseMonth(models.Model):
    """Рядок «По місяцях» тимчасової зміни: години пари в місяці × сума. Пише код; «Виплачено» — кнопкою."""
    _name = MONTH
    _description = 'Тимчасова зміна: місяць'
    _order = 'month desc, id'

    raise_id = fields.Many2one(RAISE, string='Тимчасова зміна', required=True, index=True, ondelete='cascade', readonly=True)
    person_id = fields.Many2one(related='raise_id.person_id', string='Працівник')
    customer_id = fields.Many2one(related='raise_id.customer_id', string='Замовник')
    payer = fields.Selection(related='raise_id.payer', string='Хто платить')
    month = fields.Date(string='Місяць', required=True, readonly=True, help='Перше число місяця')
    date_from = fields.Date(string='З', readonly=True)
    date_to = fields.Date(string='До', readonly=True)
    currency_id = fields.Many2one(related='raise_id.currency_id', string='Валюта')
    hours = fields.Float(string='Годин', readonly=True)
    amount = fields.Monetary(string='Сума', currency_field='currency_id', readonly=True)
    total = fields.Monetary(string='До доплати', currency_field='currency_id', readonly=True)
    state = fields.Selection(ROW_STATES, string='Статус', readonly=True, default='open', index=True)
    activity_id = fields.Many2one('mail.activity', string='Задача', readonly=True, ondelete='set null')

    def action_paid(self):
        """«Виплачено»: рядок іде із задачі доплат (задача — заново без нього)."""
        self.env[RAISE]._check_editor()
        # посилання на задачу лишається до _raise_task: за ним видно, що набір рядків задачі змінився (задача — заново)
        self.sudo().write({'state': 'paid'})
        self.raise_id._refresh()
        self.env[RAISE]._raise_task()
        return True
