# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import calc, common, office_bridge, payplan

PAYABLE_DOMAIN = [('state', '!=', 'zvirka')]
ZVIRKA_DOMAIN = [('state', '=', 'zvirka')]
DL_NAMES = {'personal_36': 'особисто · конверт', 'official_1': 'офіційно', 'custom': 'за запитом'}
APPROVE_CONFIRM = 'Затвердити рішення? Далі штаб підготує виплати: «Сплатити зараз» — до виплати, решта — у буфер наступного рішення.'
STOP_CONFIRM = ('Зупинити автоматичне рішення? Виплати буде скасовано, чернетки рахунків і список офісу прибрано, нарахування перейдуть у рішення '
                'наступного дня виплати.')


def _payable(lines):
    return [l for l in lines if l.state not in ('zvirka', 'skipped')]


class WorksetAgentDecision(models.Model):
    """Рішення про виплату до дня виплати D: «Чернетка» (ніч D−3 зібрала, рядки перезбираються до «Затвердити») → «Затверджено» (власник) →
    «Переказано» (виплати, рахунки, список офісу); «На перевірці» — після «Затвердити» знайшлися дублі / перетини / інші суми; «Пропущено» — день
    виплати минув без «Затвердити». Кадровики читають; «Затвердити», «Сплатити зараз», створення й видалення — група «Власник»."""
    _name = 'workset.agent.decision'
    _description = 'Рішення про виплату'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'pay_day desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    pay_day = fields.Date(string='День виплати', required=True, index=True, tracking=1)
    section = fields.Selection(common.SECTIONS, string='Розділ', index=True,
                               help='Агенти-кандидати чи агенти-замовники: у рішенні — лише рядки цього розділу')
    state = fields.Selection(common.DEC_STATES, string='Статус', default='draft', tracking=2, index=True)
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    approved_by = fields.Many2one('res.users', string='Затвердив', ondelete='set null', tracking=3)
    approved_at = fields.Datetime(string='Коли затверджено', tracking=4)
    prepared_at = fields.Datetime(string='Підготовлено')
    guard_note = fields.Char(string='Чому на перевірці')
    auto = fields.Boolean(string='Автоматично', tracking=5,
                          help='Затвердила ніч за правилом власника 06.11.2026; не згодні — «Зупинити» до дня перед виплатою')
    auto_note = fields.Char(string='Автоматика: чому')
    note = fields.Text(string='Нотатки')
    pay_line_ids = fields.One2many('workset.agent.decision.line', 'decision_id', string='Агенти')
    payable_line_ids = fields.One2many('workset.agent.decision.line', 'decision_id', string='Рядки до оплати', domain=PAYABLE_DOMAIN,
                                       help='Рядки рішення, крім «Звірки»: до них — «Сплатити зараз» і підсумки вгорі')
    zvirka_line_ids = fields.One2many('workset.agent.decision.line', 'decision_id', string='Рядки звірки', domain=ZVIRKA_DOMAIN,
                                      help='Рядки «Звірка»: Odoo рахує за період рішення і звіряє з конвертом, не платить')
    payout_ids = fields.One2many('workset.agent.payout', 'decision_id', string='Виплати')
    available_total = fields.Monetary(string='Разом до рішення', currency_field='currency_id', compute='_compute_totals', store=True)
    pay_total = fields.Monetary(string='Сплатити зараз', currency_field='currency_id', compute='_compute_totals', store=True)
    net_total = fields.Monetary(string='До переказу', currency_field='currency_id', compute='_compute_totals', store=True)
    buffer_total = fields.Monetary(string='У буфер', currency_field='currency_id', compute='_compute_totals', store=True)
    cut_total = fields.Monetary(string='Податок і утримання', currency_field='currency_id', compute='_compute_totals', store=True,
                                help='Утримання і податок від «Сплатити зараз»: різниця між «Сплатити зараз» і «До переказу»')
    line_count = fields.Integer(string='Агентів', compute='_compute_totals', store=True)
    zvirka_total = fields.Monetary(string='Звірка, €', currency_field='currency_id', compute='_compute_zv_totals', store=True)
    zv_diff_total = fields.Monetary(string='Різниця за період', currency_field='currency_id', compute='_compute_zv_totals', store=True,
                                    help='Розрахунок Odoo мінус конверт за рахунки періоду рішення (рядки «Звірка»)')
    zv_more_total = fields.Monetary(string='Конверт більше', currency_field='currency_id', compute='_compute_zv_totals', store=True,
                                    help='Сума рядків «Звірки», де конверт більший за розрахунок Odoo')
    zv_less_total = fields.Monetary(string='Конверт менше', currency_field='currency_id', compute='_compute_zv_totals', store=True,
                                    help='Сума рядків «Звірки», де конверт менший за розрахунок Odoo')
    zv_missing_total = fields.Integer(string='Рахунків без конверта', compute='_compute_zv_totals', store=True,
                                      help='Рахунки Odoo за період, яких немає в конвертах агентів')
    overdue_total = fields.Monetary(string='Прострочено замовниками', currency_field='currency_id', compute='_compute_zv_totals', store=True,
                                    help='Неоплачені прострочені рахунки замовників агентів рішення (кожен агент — один раз)')
    hq_advice = fields.Text(string='Що пропонує штаб',
                            help='Довідково, пише нічний розрахунок: по агенту — сплатити, притримати чи перевірити, чому і скільки. Рішення — ваше')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('pay_day', 'section')
    def _compute_name(self):
        # формат «Рішення · Агенти-кандидати · 10.11.2026» читає дашборд «Агенти»; розділу немає — «Рішення · 10.11.2026»
        secs = dict(common.SECTIONS)
        for rec in self:
            day = rec.pay_day.strftime('%d.%m.%Y') if rec.pay_day else '—'
            sec = secs.get(rec.section)
            rec.name = 'Рішення · %s · %s' % (sec, day) if sec else 'Рішення · %s' % day

    @api.depends('pay_line_ids.available', 'pay_line_ids.pay_now', 'pay_line_ids.pay_net', 'pay_line_ids.buffer_out', 'pay_line_ids.pay_wh',
                 'pay_line_ids.pay_tax', 'pay_line_ids.state', 'payable_line_ids.available', 'payable_line_ids.pay_now', 'payable_line_ids.pay_net',
                 'payable_line_ids.buffer_out', 'payable_line_ids.pay_wh', 'payable_line_ids.pay_tax', 'payable_line_ids.state')
    def _compute_totals(self):
        # суми — за рядками до оплати (крім «Звірки» й «Пропущено»): правка «Сплатити зараз» у формі перераховує шапку одразу
        for rec in self:
            ls = _payable(rec.payable_line_ids)
            rec.available_total = sum(l.available or 0.0 for l in ls)
            rec.pay_total = sum(l.pay_now or 0.0 for l in ls)
            rec.net_total = sum(l.pay_net or 0.0 for l in ls)
            rec.buffer_total = sum(l.buffer_out or 0.0 for l in ls)
            rec.cut_total = sum((l.pay_wh or 0.0) + (l.pay_tax or 0.0) for l in ls)
            rec.line_count = len(_payable(rec.pay_line_ids))

    @api.depends('pay_line_ids.accrued', 'pay_line_ids.zv_diff', 'pay_line_ids.zv_missing', 'pay_line_ids.cust_overdue', 'pay_line_ids.agent_id',
                 'pay_line_ids.state')
    def _compute_zv_totals(self):
        for rec in self:
            zv = [l for l in rec.pay_line_ids if l.state == 'zvirka']
            rec.zvirka_total = sum(l.accrued or 0.0 for l in zv)
            rec.zv_diff_total = sum(l.zv_diff or 0.0 for l in zv)
            # різниці зі знаком гасять одна одну — у шапці два напрями: мінус — «конверт більше», плюс — «конверт менше»
            rec.zv_more_total = sum(abs(l.zv_diff or 0.0) for l in zv if (l.zv_diff or 0.0) < -0.005)
            rec.zv_less_total = sum(abs(l.zv_diff or 0.0) for l in zv if (l.zv_diff or 0.0) > 0.005)
            rec.zv_missing_total = sum(l.zv_missing or 0 for l in zv)
            seen = {}
            for l in rec.pay_line_ids:
                if l.state != 'skipped':
                    seen[l.agent_id.id] = max(seen.get(l.agent_id.id, 0.0), l.cust_overdue or 0.0)
            rec.overdue_total = sum(seen.values())

    # ── кнопки (група «Власник»; запис — sudo після перевірки) ──
    def action_approve(self):
        """«Затвердити»: «Сплатити зараз» від 0 до max(0, «Разом до рішення»); рядки «На перевірці» — «Пропущено» (нарахування ніч перенесе на
        наступний день виплати); день виплати минув — відмова. Потім одразу ніч (kind approve): крок виплат готує виплати або «На перевірці»."""
        common.check_owner(self.env, self.env._('Затвердити рішення про виплату'))
        Run = self.env['workset.agent.run']
        today = Run._today(dry=False)
        now = fields.Datetime.now()
        Line = self.env['workset.agent.decision.line'].sudo()
        for rec in self.sudo():
            if rec.state not in ('draft', 'review'):
                raise UserError(self.env._('Рішення %s уже не чернетка — затверджувати нічого.') % (rec.name or rec.id))
            day = fields.Date.to_string(rec.pay_day) if rec.pay_day else ''
            if day and day < today:
                raise UserError(self.env._('День виплати %s уже минув — затвердити заднім числом не можна. Уночі рішення стане «Пропущено», нарахування '
                                  'перейдуть у рішення наступного дня виплати.') % calc.fmt_date(day))
            lines = Line.search([('decision_id', '=', rec.id), ('state', 'in', ['draft', 'review'])])
            bad, pay, keep, left = payplan.approve_check([{'state': l.state, 'pay_now': l.pay_now, 'available': l.available, 'agent': l.agent_id.name}
                                                          for l in lines])
            if bad:
                raise UserError(self.env._('Не затверджено — виправте «Сплатити зараз»:\n') + '\n'.join(bad))
            for line in lines:
                if line.state == 'review':
                    line.write({'state': 'skipped', 'guard_note': ('не в рішенні (на перевірці при затвердженні): %s' % (line.guard_note or line.guard or '—'))[0:250]})
                else:
                    line.write({'state': 'approved'})
            rec.write({'state': 'approved', 'approved_by': self.env.user.id, 'approved_at': now, 'guard_note': False, 'auto': False})
            rec.message_post(body=self.env._('Затверджено: %s. Сплатити зараз %.2f € (рядків %d)%s.') % (
                self.env.user.name, pay, keep, ', на перевірці — не в рішенні: %d (перенесено на наступний день виплати)' % left if left else ''),
                message_type='comment', subtype_xmlid='mail.mt_note')
        Run._run(kind='approve', dry=False)
        return True

    def action_apply_advice(self):
        """«Як пропонує штаб»: «Сплатити зараз» = «Пропозиція штабу» в рядках-чернетках; далі власник править сам і тисне «Затвердити»."""
        common.check_owner(self.env, self.env._('Заповнювати «Сплатити зараз»'))
        for rec in self.sudo():
            if rec.state not in ('draft', 'review'):
                raise UserError(self.env._('Рішення %s уже не чернетка — змінювати нічого.') % (rec.name or rec.id))
            for line in rec.pay_line_ids.filtered(lambda l: l.state == 'draft'):
                line.write({'pay_now': line.proposal or 0.0})
        return True

    def _office_acts(self, key):
        # активності штабу з цим ключем (поля штабу на mail.activity знає лише адаптер models/office_bridge.py; полів немає — порожньо)
        return office_bridge.find(self.env, key)

    def action_stop(self):
        """«Зупинити» автоматично затверджене рішення — до D − 1 включно: рішення й рядки — «Пропущено» («зупинено власником»), виплати —
        «Скасовано», чернетки рахунків — видалити, список офісу й підсумок власнику — прибрати, нарахування «Закрито рішенням» — знову «До виплати»;
        уже в оплаті — відмова. Потім ніч (kind stop) переносить нарахування на наступний день виплати."""
        common.check_owner(self.env, self.env._('Зупинити рішення'))
        Run = self.env['workset.agent.run']
        today = Run._today(dry=False)
        Po = self.env['workset.agent.payout'].sudo()
        Acc = self.env['workset.agent.accrual'].sudo()
        Line = self.env['workset.agent.decision.line'].sudo()
        Move = self.env['account.move'].sudo()
        for rec in self.sudo():
            if rec.state != 'done' or not rec.auto:
                raise UserError(self.env._('Зупинити можна лише рішення, яке затвердила й підготувала автоматика (статус «Переказано», «Автоматично»).'))
            day = fields.Date.to_string(rec.pay_day) if rec.pay_day else ''
            last = payplan.stop_last_day(day)
            if not day:
                raise UserError(self.env._('У рішення немає дня виплати.'))
            if today > last:
                raise UserError(self.env._('Зупинити можна до %s включно — далі список виплат уже в офісі.') % calc.fmt_date(last))
            pays = Po.search([('decision_id', '=', rec.id)])
            busy = pays.filtered(lambda p: p.state not in ('prep', 'docs', 'bill', 'cancelled'))
            if busy:
                raise UserError(self.env._('Не зупинено: виплати вже в оплаті (%s) — далі лише вручну.') % ', '.join('#%d' % p.id for p in busy))
            bills = pays.mapped('move_id')
            posted = bills.filtered(lambda b: b.state != 'draft')
            if posted:
                raise UserError(self.env._('Не зупинено: рахунок постачальника вже проведено (%s) — далі лише вручну.') % ', '.join('#%d' % b.id for b in posted))
            # список офісу (ключ agents-pay-<D>-d<id>): уже надіслано — відмова; підсумок власнику «затверджено автоматично» (…-auto) — теж прочь
            acts = self._office_acts('%s%s-d%d' % (payplan.KEY_PREFIX, day, rec.id))
            if office_bridge.sent(acts):
                raise UserError(self.env._('Не зупинено: список виплат офісу вже надіслано — далі лише вручну.'))
            own = self._office_acts('%s%s-d%d-auto' % (payplan.OWNER_KEY_PREFIX, day, rec.id))
            had_acts = bool(acts)
            (acts | own).unlink()
            n_bills = len(bills)
            for p in pays:
                if p.state != 'cancelled':
                    p.write({'state': 'cancelled', 'move_id': False,
                             'note': ((p.note or '') + '\nЗупинено власником %s (%s): автоматичне рішення скасовано.' % (today, self.env.user.name)).strip()})
            if bills:
                Move.browse(bills.ids).unlink()
            lines = Line.search([('decision_id', '=', rec.id)])
            for a in Acc.search([('pay_line_id', 'in', lines.ids), ('state', '=', 'decided')]):
                a.write({'state': 'due', 'payout_id': False})
            shown = calc.fmt_date(today)
            for l in lines:
                if l.state != 'zvirka':
                    l.write({'state': 'skipped', 'guard_note': 'зупинено власником %s — наступне рішення лише за власником' % shown})
            rec.write({'state': 'skipped', 'guard_note': ('зупинено власником %s: нарахування — на наступний день виплати' % today)[0:250]})
            rec.message_post(body=self.env._('Зупинено власником, %s (%s). Виплат скасовано %d, чернеток рахунків видалено %d, список офісу прибрано: %s. '
                                    'Нарахування перейдуть у рішення наступного дня виплати — його затверджуєте ви.') % (
                shown, self.env.user.name, len(pays.filtered(lambda p: p.state == 'cancelled')), n_bills, 'так' if had_acts else 'його не було'),
                message_type='comment', subtype_xmlid='mail.mt_note')
        Run._run(kind='stop', dry=False, today=today)
        return True

    # ── кроки ночі (3) і (4) ──
    @api.model
    def _night_pay_step(self, today, now, dry):
        """Крок виплат ночі (3): пропущені рішення, перенос «До виплати», підготовка затверджених (виплата, чернетка рахунку official, запис
        «з особистого рахунку», список офісу), перезбір чернеток, нове рішення D−3 — payplan.pay_plan за базою після запису нарахувань."""
        return payplan.pay_plan(self.env['workset.agent.run']._load_pay(today), today, now)

    @api.model
    def _night_auto_step(self, today, now, dry):
        """Крок (4): автозатвердження чернеток — затверджує лише при workset.agent.run._auto_allowed() (копія й auto_approve = 1); інакше — тривога
        «ask» власнику на кожну чернетку з сумою до виплати (Р-2). За базою ПІСЛЯ кроку (3) цієї ночі."""
        return payplan.auto_plan(self.env['workset.agent.run']._load_pay(today), today, now)


class WorksetAgentDecisionLine(models.Model):
    """Рядок рішення: агент × розділ × «Шлях виплати» × фірма; «Звірка» — окремим рядком (рахуємо, не платимо)."""
    _name = 'workset.agent.decision.line'
    _description = 'Рядок рішення'
    _order = 'name, id'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    decision_id = fields.Many2one('workset.agent.decision', string='Рішення', required=True, ondelete='cascade', index=True)
    agent_id = fields.Many2one('workset.agent', string='Агент', required=True, ondelete='restrict', index=True)
    section = fields.Selection(common.SECTIONS, string='Розділ')
    pay_path = fields.Selection(common.PAY_PATHS, string='Шлях виплати', help=common.PAY_PATH_HELP)
    company_id = fields.Many2one('res.company', string='Фірма виплати')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=common.default_eur)
    state = fields.Selection(common.LINE_STATES, string='Статус', default='draft', index=True)
    key = fields.Char(string='Ключ рядка', index=True, help='Агент:розділ:шлях:фірма — за ним ніч знаходить рядок і буфер наступного рішення')
    accrued = fields.Monetary(string='Нараховано', currency_field='currency_id',
                              help='Нарахування «До виплати» з днем виплати не пізніше дня рішення; для «Звірки» — за період від попереднього дня виплати')
    review_amount = fields.Monetary(string='На перевірці, €', currency_field='currency_id',
                                    help='Нарахування агента «На перевірці» — у суму не входять, поки сторож не знятий')
    buffer_in = fields.Monetary(string='Буфер з минулого', currency_field='currency_id',
                                help='Скільки лишилось несплаченим із попереднього рішення (окремим записом: суми нарахувань не змінюються)')
    buffer_from_id = fields.Many2one('workset.agent.decision.line', string='Звідки буфер', ondelete='set null')
    available = fields.Monetary(string='Разом до рішення', currency_field='currency_id', help='Буфер з минулого + нараховано')
    wh_pct = fields.Float(string='Утримання, %', help='Особисто · конверт — 10 %, якщо в домовленості не задано інше; інші шляхи — як у домовленості')
    tax_pct = fields.Float(string='Податок, %', help='Лише «Офіційно»: 20 % без чинної довідки на день виплати (п. 7.2), інакше 0 — за профілем податків')
    wh_note = fields.Char(string='Утримання: чому')
    proposal = fields.Monetary(string='Пропозиція штабу', currency_field='currency_id',
                               help='Довідково, без автоматики: усе «Разом до рішення»; борги замовників і правила буфера вирішує власник')
    proposal_note = fields.Char(string='Пропозиція: чому')
    pay_now = fields.Monetary(string='Сплатити зараз, €', currency_field='currency_id',
                              help='Вводить власник: брутто — скільки нарахувань закриваємо зараз (від 0 до «Разом до рішення»); утримання і «До переказу» — '
                                   'поруч; решта — у буфер наступного рішення')
    pay_wh = fields.Monetary(string='Утримати зараз', currency_field='currency_id', compute='_compute_pay', store=True)
    pay_tax = fields.Monetary(string='Податок зараз', currency_field='currency_id', compute='_compute_pay', store=True)
    pay_net = fields.Monetary(string='До переказу', currency_field='currency_id', compute='_compute_pay', store=True)
    unpaid = fields.Monetary(string='Недоплачено', currency_field='currency_id',
                             help='Частина «Сплатити зараз», яку не переказали (позначено вручну частково) — іде в буфер')
    seen_at = fields.Datetime(string='Запис ночі', help='Коли нічний розрахунок востаннє записав рядок: правка пізніше — рішення лише для власника')
    owner_edit = fields.Boolean(string='Правив власник', help='Рядок правили після нічного розрахунку — автоматично не затверджується')
    night_pay = fields.Monetary(string='Сплатити — ніч', currency_field='currency_id', help='«Сплатити зараз», яке ніч бачила або ставила востаннє')
    buffer_out = fields.Monetary(string='У буфер', currency_field='currency_id', compute='_compute_buffer_out', store=True)
    cust_paid = fields.Monetary(string='Оплачено', currency_field='currency_id', help='Рахунки замовників, за якими нараховано в цьому рядку')
    cust_unpaid = fields.Monetary(string='Не оплачено', currency_field='currency_id',
                                  help='Відкриті рахунки замовників агента (не оплачено і частково) — довідково')
    cust_overdue = fields.Monetary(string='Прострочено', currency_field='currency_id', help='З них прострочено (строк оплати минув)')
    cust_text = fields.Text(string='Замовники агента')
    overdue_move_ids = fields.Many2many('account.move', 'workset_agent_decision_line_overdue_rel', 'line_id', 'move_id', string='Прострочені рахунки',
                                        help='Неоплачені рахунки замовників агента, строк оплати яких минув (на ніч розрахунку)')
    zv_period = fields.Char(string='Період', help='Дні «Зароблено» нарахувань цього рішення: від попереднього дня виплати до цього (з відсічкою 3 дні)')
    zv_env = fields.Monetary(string='Конверт, €', currency_field='currency_id',
                             help='Рядки конверта агента «ДОЛЯ | …» за ті самі рахунки замовників, що й розрахунок Odoo за період')
    zv_diff = fields.Monetary(string='Різниця, €', currency_field='currency_id', help='Розрахунок Odoo за період мінус конверт за ті самі рахунки')
    zv_note = fields.Text(string='Що перевірити')
    zv_missing = fields.Integer(string='Рахунків без конверта', help='Рахунки Odoo за період, яких немає в конверті агента (конверта немає — усі)')
    advice = fields.Selection(common.ADVICE_KINDS, string='Порада штабу',
                              help='Що пропонує штаб цьому рядку: сплатити, перевірити чи притримати (пише нічний розрахунок)')
    advice_text = fields.Char(string='Чому так', help='Причина поради; порожньо — «сплатити все до рішення»')
    guard = fields.Char(string='Код сторожа')
    guard_note = fields.Char(string='Примітка сторожа')
    payout_id = fields.Many2one('workset.agent.payout', string='Виплата', ondelete='set null')
    accrual_ids = fields.One2many('workset.agent.accrual', 'pay_line_id', string='Нарахування')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    @api.depends('agent_id.name', 'pay_path')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s · %s' % (rec.agent_id.name or '—', DL_NAMES.get(rec.pay_path) or '—')

    @api.depends('pay_now', 'wh_pct', 'tax_pct')
    def _compute_pay(self):
        # утримання, податок і «До переказу» — від «Сплатити зараз» (брутто), як payplan.pay_amounts
        for rec in self:
            wh, tax, net = payplan.pay_amounts(rec.pay_now or 0.0, rec.wh_pct, rec.tax_pct)
            rec.pay_wh = wh
            rec.pay_tax = tax
            rec.pay_net = net

    @api.depends('available', 'pay_now', 'state', 'unpaid')
    def _compute_buffer_out(self):
        # «У буфер» = «Разом до рішення» − «Сплатити зараз» + «Недоплачено»; «Звірка» і «Пропущено» — 0
        for rec in self:
            rec.buffer_out = 0.0 if rec.state in ('zvirka', 'skipped') else (rec.available or 0.0) - (rec.pay_now or 0.0) + (rec.unpaid or 0.0)

    def action_open_overdue(self):
        """«Прострочено» → список рахунків замовників агента (лише читання, права — як у людини)."""
        ids = self.mapped('overdue_move_ids').ids
        lv = self.env.ref('account.view_out_invoice_tree', raise_if_not_found=False)
        fv = self.env.ref('account.view_move_form', raise_if_not_found=False)
        return {'type': 'ir.actions.act_window', 'name': 'Прострочені рахунки замовників агента', 'res_model': 'account.move', 'view_mode': 'list,form',
                'views': [[lv.id if lv else False, 'list'], [fv.id if fv else False, 'form']], 'domain': [('id', 'in', ids)],
                'context': {'create': False}, 'target': 'current'}

    def action_open_line(self):
        """«Деталі рядка» — вікно рядка поверх (у редагованому списку клік править рядок); заголовок — «агент · день виплати»."""
        self.ensure_one()
        day = self.decision_id.pay_day.strftime('%d.%m.%Y') if self.decision_id.pay_day else ''
        title = ' · '.join(s for s in [self.agent_id.name or '', day] if s) or 'Рядок рішення'
        return {'type': 'ir.actions.act_window', 'name': title, 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'views': [[False, 'form']], 'target': 'new'}
