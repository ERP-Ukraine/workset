# -*- coding: utf-8 -*-
import datetime

from odoo import api, fields, models

from . import calc, common


class WorksetAgent(models.Model):
    """Агент — підбір людей або посередник замовників. Контакт людини (res.partner) — через _inherits: ім'я, телефон, почта, банки — поля
    контакту; умови й гроші — у «Домовленостях» агента. Один агент на контакт."""
    _name = 'workset.agent'
    _description = 'Агент'
    _inherits = {'res.partner': 'partner_id'}
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name, id'

    partner_id = fields.Many2one('res.partner', string='Контакт', required=True, ondelete='restrict', index=True,
                                 help='Загальний контакт людини (фірма порожня), не нова картка')
    code = fields.Char(string='Код агента', index=True, tracking=1, help='Код із бота агентів; у двох агентів не повторюється')
    tg_id = fields.Char(string='Telegram id', index=True, help='Числовий id у Telegram; у двох агентів не повторюється')
    form_state = fields.Selection(common.FORM_STATES, string='Стан анкети', help='Чи заповнив агент анкету в боті (IBAN, статус, країна, довідка)')
    envelope_id = fields.Many2one('account.analytic.account', string='Конверт', ondelete='set null', index=True, tracking=2,
                                  help='Аналітичний рахунок «ДОЛЯ | …» (фірма особистих переказів), куди офіс вносить частку агента: з ним звіряє '
                                       '«Звірка з конвертами» і сторож подвійного обліку')
    pd_ids = fields.One2many('workset.agent.deal.people', 'agent_id', string='Домовленості · кандидати', help='Домовленості агента з підбору кандидатів')
    cd_ids = fields.One2many('workset.agent.deal.client', 'agent_id', string='Домовленості · замовники', help='Домовленості агента-посередника замовників')
    member_ids = fields.One2many('workset.agent.member', 'agent_id', string='Люди агента', help='Люди з домовленостей агента з підбору кандидатів')
    accrual_ids = fields.One2many('workset.agent.accrual', 'agent_id', string='Нарахування')
    payout_ids = fields.One2many('workset.agent.payout', 'agent_id', string='Виплати')
    zvirka_ids = fields.One2many('workset.agent.zvirka', 'agent_id', string='Звірка з конвертом')
    tax_ids = fields.One2many('workset.agent.tax', 'agent_id', string='Податки', groups='account.group_account_user,hr.group_hr_manager')
    kind = fields.Selection(common.AGENT_KINDS, string='Вид агента', index=True, compute='_compute_kind', store=True,
                            help='За домовленостями агента: кандидати, замовники чи обидва')
    state = fields.Selection(common.AGENT_MODES, string='Стан агента', index=True, compute='_compute_state', store=True, tracking=3,
                             help='Найгірший із живих домовленостей: лише звірка, платимо; живих немає — чернетка, усі закриті — закрито, '
                                  'інакше немає домовленостей')
    pd_n = fields.Integer(string='Домовленостей · кандидати', compute='_compute_counts', help='Скільки домовленостей з підбору кандидатів')
    cd_n = fields.Integer(string='Домовленостей · замовники', compute='_compute_counts', help='Скільки домовленостей по замовниках')
    deal_count = fields.Integer(string='Домовленостей', compute='_compute_counts')
    people_n = fields.Integer(string='Людей агента', compute='_compute_counts', help='Різні люди в «Люди агента» його домовленостей')
    cust_n = fields.Integer(string='Замовників агента', compute='_compute_counts', help='Різні замовники в домовленостях агента-посередника')
    bank_n = fields.Integer(string='Рахунків', compute='_compute_counts', help='Банківські рахунки агента (бачать бухгалтерія та адміністратори кадрів)')
    accrual_n = fields.Integer(string='Нарахувань', compute='_compute_counts')
    payout_n = fields.Integer(string='Виплат', compute='_compute_counts')
    # рік сум — поточний за Києвом (параметр card_year перевизначає); рік у підписі не пишемо — підпис поля статичний
    card_year = fields.Integer(string='Рік картки', compute='_compute_year', help='Поточний рік за Києвом або системний параметр card_year')
    calc_year = fields.Float(string='Нараховано за рік', digits=(16, 2), compute='_compute_year',
                             help='Розрахунок Odoo за рік картки — зі «Звірки з конвертами»')
    env_year = fields.Float(string='Конверт за рік', digits=(16, 2), compute='_compute_year',
                            help='Конверт агента за рік картки — зі «Звірки з конвертами»')
    diff_year = fields.Float(string='Різниця за рік', digits=(16, 2), compute='_compute_year',
                             help='Розрахунок Odoo мінус конверт за рік картки')
    diff_pct = fields.Float(string='Різниця, %', digits=(16, 1), compute='_compute_year', help='Різниця від конверта у відсотках')
    paid_year = fields.Float(string='Виплачено за рік', digits=(16, 2), compute='_compute_year',
                             help='«Переказано» виплат «Виплачено» та «Виплачено частково» з датою оплати в році картки')
    next_pay = fields.Char(string='Наступна виплата', compute='_compute_next',
                           help='Найближче рішення про виплату з рядком агента: день і сума; лише рядки «Звірка» — «лише звірка»')
    next_pay_day = fields.Date(string='День наступної виплати', compute='_compute_next')
    due_next = fields.Float(string='До виплати', digits=(16, 2), compute='_compute_next',
                            help='Сума найближчого рішення: «Сплатити зараз», інакше «Пропозиція штабу»')
    is_test = fields.Boolean(string='Тест', compute='_compute_is_test', store=True, readonly=False, index=True,
                             help='Записи «ТЕСТ-ШТАБ» для показу — прибрати перед показом власнику (інструмент штабу --demo-remove)')
    legacy_ref = fields.Char(string='Звідки перенесено', index=True, copy=False, readonly=True)

    _partner_uniq = models.Constraint('UNIQUE(partner_id)', 'Цей контакт уже агент: один агент на контакт.')

    @api.depends('pd_ids', 'cd_ids')
    def _compute_kind(self):
        for rec in self:
            rec.kind = calc.agent_kind_of(len(rec.pd_ids), len(rec.cd_ids))

    @api.depends('pd_ids.state', 'pd_ids.mode', 'pd_ids.date_to', 'cd_ids.state', 'cd_ids.mode', 'cd_ids.date_to')
    def _compute_state(self):
        # «сьогодні» — дата сервера (UTC), як у ручній збірці; збережене значення оновлюється при правці домовленості
        today = fields.Date.to_string(fields.Date.today())
        for rec in self:
            ds = [{'state': d.state, 'mode': d.mode, 'date_to': fields.Date.to_string(d.date_to) if d.date_to else ''} for d in list(rec.pd_ids) + list(rec.cd_ids)]
            rec.state = calc.agent_mode_of(ds, today)

    @api.depends('code', 'name')
    def _compute_is_test(self):
        for rec in self:
            # поставлене вручну (інструмент --demo) не знімаємо
            rec.is_test = rec.is_test or bool((rec.code or '').startswith('TEST-SHTAB') or 'ТЕСТ-ШТАБ' in (rec.name or ''))

    def _compute_counts(self):
        for rec in self:
            rec.pd_n = len(rec.pd_ids)
            rec.cd_n = len(rec.cd_ids)
            rec.deal_count = rec.pd_n + rec.cd_n
            rec.people_n = len(set(m.partner_id.id for d in rec.pd_ids for m in d.member_ids if m.partner_id))
            rec.cust_n = len(set(c.id for d in rec.cd_ids for c in d.customer_ids))
            rec.bank_n = len(rec.bank_ids)
            rec.accrual_n = len(rec.accrual_ids)
            rec.payout_n = len(rec.payout_ids)

    def _compute_year(self):
        # суми — зі «Звірки з конвертами» (розрахунок Odoo, конверт, різниця — одним джерелом); sudo — лише суми, без ПДн
        fr, to = common.year_bounds(self.env)
        year = int(fr[0:4])
        Zv = self.env['workset.agent.zvirka'].sudo()
        Po = self.env['workset.agent.payout'].sudo()
        for rec in self:
            rows = Zv.search([('agent_id', '=', rec.id), ('month', '>=', fr), ('month', '<', to)]) if rec.id else Zv
            rec.card_year = year
            rec.calc_year = sum(rows.mapped('calc'))
            rec.env_year = sum(rows.mapped('envelope'))
            rec.diff_year = sum(rows.mapped('diff'))
            rec.diff_pct = calc.zv_badge_pct(rec.calc_year, rec.env_year)
            pays = Po.search([('agent_id', '=', rec.id), ('state', 'in', ['paid', 'paid_part']), ('paid_date', '>=', fr), ('paid_date', '<', to)]) if rec.id else Po
            rec.paid_year = sum(pays.mapped('paid_amount'))

    def _compute_next(self):
        Line = self.env['workset.agent.decision.line'].sudo()
        for rec in self:
            ls = Line.search([('agent_id', '=', rec.id), ('decision_id.state', 'in', ['draft', 'review', 'approved'])]) if rec.id else Line
            pay = [l for l in ls if l.state not in ('zvirka', 'skipped') and l.decision_id.pay_day]
            if pay:
                day = min(l.decision_id.pay_day for l in pay)
                amt = sum(((l.pay_now if (l.pay_now or 0.0) > 0.005 else l.proposal) or 0.0) for l in pay if l.decision_id.pay_day == day)
                rec.next_pay = '%s · %s €' % (day.strftime('%d.%m'), ('%.2f' % amt).replace('.', ','))
                rec.next_pay_day = day
                rec.due_next = amt
            else:
                rec.next_pay = 'лише звірка' if ls else '—'
                rec.next_pay_day = False
                rec.due_next = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        # мітка «Агент» на контакті (сумісність фільтрів контактів); параметра немає — без мітки
        tag = common.param(self.env, common.P_AGENT_TAG, required=False)
        if tag.isdigit():
            cat = self.env['res.partner.category'].sudo().browse(int(tag)).exists()
            if cat:
                recs.mapped('partner_id').sudo().write({'category_id': [(4, cat.id)]})
        return recs

    def _open(self, model, name, domain, ctx=None):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': 'list,form', 'domain': domain,
                'context': dict(ctx or {}), 'target': 'current'}

    def action_open_deals_people(self):
        return self._open('workset.agent.deal.people', 'Домовленості · кандидати', [('agent_id', '=', self.id)], {'default_agent_id': self.id})

    def action_open_deals_client(self):
        return self._open('workset.agent.deal.client', 'Домовленості · замовники', [('agent_id', '=', self.id)], {'default_agent_id': self.id})

    def action_open_members(self):
        return self._open('workset.agent.member', 'Люди агента', [('agent_id', '=', self.id)])

    def action_open_accruals(self):
        return self._open('workset.agent.accrual', 'Нарахування агента', [('agent_id', '=', self.id)])

    def action_open_payouts(self):
        return self._open('workset.agent.payout', 'Виплати', [('agent_id', '=', self.id)])

    def action_open_zvirka(self):
        return self._open('workset.agent.zvirka', 'Звірка агента', [('agent_id', '=', self.id)])


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # не agent_ids: так зветься поле стандартного модуля Odoo 19 «ai» (res.partner.agent_ids → ai.agent) — воно перекрило б наше, is_agent
    # рахувався б за чужими агентами (dev-збірка 07.11). Підпис — не «Агент»: інакше дві однакові підписи на контакті (і в workset.agent,
    # res.users через _inherits)
    workset_agent_ids = fields.One2many('workset.agent', 'partner_id', string='Агенти WorkSET')
    is_agent = fields.Boolean(string='Агент', compute='_compute_is_agent', store=True, index=True,
                              help='Позначка: цей контакт — агент (підбір людей або посередник замовників). Умови — у «Домовленостях»')

    @api.depends('workset_agent_ids')
    def _compute_is_agent(self):
        for rec in self:
            rec.is_agent = bool(rec.workset_agent_ids)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    agent_id = fields.Many2one('workset.agent', string='Привів агент', compute='_compute_agent',
                               help='Агент, у чиїй домовленості «Люди агента» є робочий контакт працівника (спершу жива домовленість). Вносять у меню «Агенти»')
    agent_deal_id = fields.Many2one('workset.agent.deal.people', string='Домовленість агента', compute='_compute_agent')

    def _compute_agent(self):
        today = datetime.date.today().isoformat()
        Mem = self.env['workset.agent.member'].sudo()
        for rec in self:
            p = rec.work_contact_id
            ms = Mem.search([('partner_id', '=', p.id)]) if p else Mem
            rows = [{'id': m.id, 'date_from': fields.Date.to_string(m.date_from) if m.date_from else '',
                     'date_to': fields.Date.to_string(m.date_to) if m.date_to else '', 'deal_state': m.deal_id.state} for m in ms]
            best = Mem.browse(calc.best_member(rows, today)) if rows else Mem
            rec.agent_deal_id = best.deal_id if best else False
            rec.agent_id = best.deal_id.agent_id if best else False
