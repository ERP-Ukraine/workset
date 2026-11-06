# -*- coding: utf-8 -*-
"""Правка ставки прямо зі списку «Працюють зараз» (слово власника 06.11 «редагування: як пропонує штаб»): олівець біля «Зарплата
працівника», «Платять нам» і «Посередник» відкриває майстер workset.rate.edit, кнопка «Переговорити» — майстер задачі workset.rate.talk.

- «Нова ставка з дати» — нова ставка пари з цієї дати (копія поточної — workset.rate._successor_vals, нове число — у ній); попередня
  закінчується напередодні за правилами модуля (А1), частка — одразу ланцюжком.
- «Виправити помилку» — число змінюється в цій самій ставці; причина обов'язкова й іде в історію ставки (message_post).
- «Посередник»: «лише цій людині» — своє значення на ставці з причиною (agent_own, ланцюжок бере його замість домовленостей); «для всієї
  домовленості» — картка домовленості модуля посередників (кнопка лише коли модель є й користувач має право її правити).
- «Переговорити» — задача mail.activity на ставці для обраного користувача (за замовчуванням — сам) з цифрами людини в назві; відкриті
  такі задачі — рядком «Переговорити» в «Тривогах» «Огляду».
Право — лише в того, хто пише ставки (check_access('write') ставки; майстри — редактору й адміністратору)."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import agent_deals, calc
from .workset_rate import RATE, SOURCES, cust_of

WHAT = [('wage', 'Зарплата працівника'), ('price', 'Платять нам'), ('agent', 'Посередник')]
SCOPES = [('person', 'Лише цій людині'), ('deal', 'Для всієї домовленості')]


class WorksetRateActions(models.Model):
    _inherit = RATE

    def _edit_action(self, what):
        self.ensure_one()
        self.check_access('write')
        lang = self._lang()
        value = {'wage': self.wage, 'price': self.price, 'agent': self.agent_client_share}[what]
        return {'type': 'ir.actions.act_window', 'name': calc.t('e_title', lang) % calc.t('e_' + what, lang), 'res_model': 'workset.rate.edit',
                'view_mode': 'form', 'views': [[False, 'form']], 'target': 'new',
                'context': {'default_rate_id': self.id, 'default_what': what, 'default_value': value, 'default_date': calc.iso(self._today())}}

    def action_edit_wage(self):
        return self._edit_action('wage')

    def action_edit_price(self):
        return self._edit_action('price')

    def action_edit_agent(self):
        return self._edit_action('agent')

    def action_agent_by_deal(self):
        """«За домовленістю»: «Посередник — своє» знімається, частка — знову з домовленостей замовника або лінії; що було — в історію."""
        self.check_access('write')
        lang = self._lang()
        for r in self.filtered('agent_own'):
            was = '%s · %s' % (calc.money(r.agent_own_value), r.agent_own_reason or '—')
            r.write({'agent_own': False, 'agent_own_value': 0.0, 'agent_own_reason': False})
            r.message_post(body=calc.t('e_agent_back', lang) % was, message_type='comment', subtype_xmlid='mail.mt_note')
        return True

    def action_talk(self):
        self.ensure_one()
        self.check_access('write')
        return {'type': 'ir.actions.act_window', 'name': calc.t('talk_summary', self._lang()) % (self.person_id.name or ''), 'res_model': 'workset.rate.talk',
                'view_mode': 'form', 'views': [[False, 'form']], 'target': 'new', 'context': {'default_rate_id': self.id}}

    def _talk_numbers(self):
        agents = (self.agent_client_share or 0.0) + ((self.agent_people_share or 0.0) if self.people_agent_payer == 'workset' else 0.0)
        return {'wage': self.wage, 'price': self.price, 'agent': agents, 'fund': self.worker_fund_share, 'car': self.car_rent_share,
                'share': self.our_share}


class WorksetRateEdit(models.TransientModel):
    _name = 'workset.rate.edit'
    _description = 'Правка ставки'

    rate_id = fields.Many2one(RATE, string='Ставка', required=True, ondelete='cascade')
    what = fields.Selection(WHAT, string='Що змінюємо', required=True, readonly=True)
    currency_id = fields.Many2one(related='rate_id.currency_id', string='Валюта')
    person_id = fields.Many2one(related='rate_id.person_id', string='Працівник')
    customer_id = fields.Many2one(related='rate_id.customer_id', string='Замовник')
    old_value = fields.Monetary(string='Зараз', currency_field='currency_id', compute='_compute_old')
    rate_from = fields.Date(related='rate_id.date_from', string='Ставка з')
    value = fields.Monetary(string='Нове значення', currency_field='currency_id', required=True)
    date = fields.Date(string='З якої дати', required=True, help='Для «Нова ставка з дати»: з цього дня діє нова ставка, попередня закінчується напередодні')
    reason = fields.Char(string='Причина', help='Звідки нове число; для «Виправити помилку» — обов\'язково (іде в історію ставки)')
    source = fields.Selection(SOURCES, string='Джерело', default='call', help='Джерело нової ставки (для «Нова ставка з дати»)')
    agent_scope = fields.Selection(SCOPES, string='Кому', default='person',
                                   help='Лише цій людині — своє значення на ставці з причиною; для всієї домовленості — правка домовленості посередника')
    deal_ok = fields.Boolean(string='Домовленість можна правити', compute='_compute_deal_ok')

    @api.depends('rate_id', 'what')
    def _compute_old(self):
        for w in self:
            r = w.rate_id
            w.old_value = {'wage': r.wage, 'price': r.price, 'agent': r.agent_client_share}.get(w.what or 'wage', 0.0) if r else 0.0

    @api.depends('rate_id')
    def _compute_deal_ok(self):
        # кнопка «Відкрити домовленість»: модель посередників є, користувач може її правити, і домовленість цієї ставки знайшлася
        for w in self:
            r = w.rate_id
            w.deal_ok = bool(r) and agent_deals.deal_writable(self.env) and bool(agent_deals.deal_ids(self.env, cust_of(r.customer_id), r.line_id.id))

    def _check_value(self):
        lang = calc.lang_of(self.env.lang)
        if self.what == 'wage' and (self.value or 0.0) <= 0:
            raise UserError(calc.t('e_zero', lang))
        if (self.value or 0.0) < 0:
            raise UserError(calc.t('e_negative', lang))

    def _label(self, lang):
        return calc.t('e_' + self.what, lang)

    def _change(self, rate, reason):
        """Значення запису для зміни self.what у ставці rate (нова ставка або та сама)."""
        if self.what == 'wage':
            return {'wage': self.value}
        if self.what == 'price':
            # «Своя» вже з причиною — причину не підміняємо; інакше ціна ≠ правилу зробить ставку «Своєю» з цією причиною
            return {'price': self.value} if rate.is_own else {'price': self.value, 'own_reason': reason or False}
        return {'agent_own': True, 'agent_own_value': self.value, 'agent_own_reason': reason or False}

    def _done(self, text):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.rate_id.display_name, 'message': text, 'type': 'success', 'sticky': False,
                           'next': {'type': 'ir.actions.act_window_close'}}}

    def action_new_from_date(self):
        """«Нова ставка з дати»: нова ставка пари з self.date (копія поточної) з новим числом; попередня закінчується напередодні (А1)."""
        self.ensure_one()
        rate = self.rate_id
        rate.check_access('write')
        self.env[RATE].check_access('create')
        lang = calc.lang_of(self.env.lang)
        self._check_value()
        day = calc.iso(self.date)
        if day == calc.iso(rate.date_from):
            raise UserError(calc.t('e_same_day', lang) % calc.fmt_date(day))
        reason = (self.reason or '').strip()
        vals = rate._successor_vals(day, self.value if self.what == 'wage' else rate.wage)
        vals.update(self._change(rate, reason))
        vals.update({'source': self.source or 'other', 'source_note': reason or calc.t('e_title', lang) % self._label(lang),
                     'wage_source': 'manual' if self.what == 'wage' else (rate.wage_source or 'manual')})
        if self.what != 'wage' and rate.wage_source == 'contract':
            vals.update({'wage_source': 'contract', 'contract_version_id': rate.contract_version_id.id})
        old = {'value': self.old_value, 'share': rate.our_share}
        new = self.env[RATE].create(vals)
        new.message_post(body=calc.t('e_new_note', lang) % (calc.fmt_date(day), self._label(lang), calc.money(old['value']), calc.money(self.value),
                                                            reason), message_type='comment', subtype_xmlid='mail.mt_note')
        return self._done(calc.t('e_done_new', lang) % (calc.fmt_date(day), calc.money(old['value']), calc.money(self.value), calc.money(old['share']),
                                                        calc.money(new.our_share)))

    def action_fix(self):
        """«Виправити помилку»: число змінюється в цій самій ставці; без причини — ні (причина — в історію ставки)."""
        self.ensure_one()
        rate = self.rate_id
        rate.check_access('write')
        lang = calc.lang_of(self.env.lang)
        reason = (self.reason or '').strip()
        if not reason:
            raise UserError(calc.t('e_reason', lang))
        self._check_value()
        old = {'value': self.old_value, 'share': rate.our_share}
        rate.write(self._change(rate, reason))
        rate.message_post(body=calc.t('e_fix_note', lang) % (self._label(lang), calc.money(old['value']), calc.money(self.value), reason),
                          message_type='comment', subtype_xmlid='mail.mt_note')
        return self._done(calc.t('e_done_fix', lang) % (calc.money(old['value']), calc.money(self.value), calc.money(old['share']),
                                                        calc.money(rate.our_share)))

    def action_open_deal(self):
        """«Для всієї домовленості»: картка (або список) домовленостей посередника цієї ставки — правка там, у модулі посередників."""
        self.ensure_one()
        return self.rate_id.action_open_agent_deal()


class WorksetRateTalk(models.TransientModel):
    _name = 'workset.rate.talk'
    _description = 'Переговорити'

    rate_id = fields.Many2one(RATE, string='Ставка', required=True, ondelete='cascade')
    person_id = fields.Many2one(related='rate_id.person_id', string='Працівник')
    customer_id = fields.Many2one(related='rate_id.customer_id', string='Замовник')
    user_id = fields.Many2one('res.users', string='Кому', required=True, default=lambda self: self.env.user, domain=[('share', '=', False)])
    summary = fields.Char(string='Задача', compute='_compute_summary', store=True, readonly=False, required=True)
    date_deadline = fields.Date(string='Строк', required=True, default=lambda self: self.env[RATE]._today())
    note = fields.Text(string='Нотатка')

    @api.depends('rate_id')
    def _compute_summary(self):
        lang = calc.lang_of(self.env.lang)
        for w in self:
            w.summary = calc.t('talk_summary', lang) % calc.talk_text(w.rate_id._talk_numbers(), lang) if w.rate_id else ''

    def action_create(self):
        """Задача To Do на ставці для обраного користувача; назва — цифри людини (як у списку на момент задачі)."""
        self.ensure_one()
        rate = self.rate_id
        rate.check_access('write')
        act = rate.activity_schedule('mail.mail_activity_data_todo', date_deadline=self.date_deadline, summary=self.summary,
                                     note=self.note or False, user_id=self.user_id.id)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': rate.display_name, 'message': act.summary or '', 'type': 'success', 'sticky': False,
                           'next': {'type': 'ir.actions.act_window_close'}}}
