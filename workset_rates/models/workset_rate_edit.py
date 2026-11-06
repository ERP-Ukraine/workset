# -*- coding: utf-8 -*-
"""Правка ставки прямо зі списку «Працюють зараз» (слово власника 06.11 «редагування: як пропонує штаб»): олівець біля «Зарплата
працівника», «Платять нам» і «Посередник» відкриває майстер workset.rate.edit, кнопка «Переговорити» — майстер задачі workset.rate.talk.

- «Нова ставка з дати» — нова ставка пари з цієї дати (копія поточної — workset.rate._successor_vals, нове число — у ній); попередня
  закінчується напередодні за правилами модуля (А1), частка — одразу ланцюжком. Дата — лише пізніше за «Ставка з» цієї ставки.
- «Виправити помилку» — число змінюється в цій самій ставці; причина обов'язкова й іде в історію ставки (message_post).
- «Посередник»: рядок «Посередник: ім'я · звідки сума» (з домовленості, що стосується ставки, без неї — агент лінії; «за домовленістю» /
  «своє — домовленості немає» — calc.agent_who); «лише цій людині» — своє значення на ставці з причиною (agent_own, ланцюжок бере його
  замість домовленостей); «для всієї домовленості» — картка домовленості модуля посередників: вибір видно лише коли домовленість ставки є
  й користувач може її правити, інакше — підказка, чому значення діє лише для цієї людини.
- «Переговорити» — задача mail.activity типу «Переговорити» (TALK_TYPE) на ставці для обраного користувача («Кому доручити»: за
  замовчуванням для замовника й посередника — користувач параметра workset_rates.talk_customer_uid, інакше сам; у виборі — лише ті, хто
  читає ставки) з цифрами людини в назві; «З ким говорити» — працівник, посередник, замовник, партнер лінії (лише ті, хто в ставки є; за
  замовчуванням — посередник, інакше працівник): ім'я, телефон (у працівника — і особистий) і Telegram — першим абзацом нотатки задачі. Відкриті задачі цього типу — рядком «Переговорити» в «Тривогах» «Огляду».
Право — лише в того, хто пише ставки (check_access('write') ставки; майстри — редактору й адміністратору); минулу ставку майстер не правит."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import agent_deals, calc
from .workset_rate import RATE, SOURCES, TALK_TYPE, cust_of

WHAT = [('wage', 'Зарплата працівника'), ('price', 'Платять нам'), ('agent', 'Посередник')]
SCOPES = [('person', 'Лише цій людині'), ('deal', 'Для всієї домовленості')]
WHO = [('worker', 'Працівник'), ('agent', 'Посередник'), ('customer', 'Замовник'), ('partner', 'Партнер лінії')]  # порядок — calc.TALK_WHO
# телефони картки працівника (hr.employee), які читає «Переговорити», — у порядку вибору: особистий, мобільний, робочий
EMP_PHONES = ('private_phone', 'mobile_phone', 'work_phone')
# «Кому доручити» за замовчуванням, коли говорити із замовником чи посередником: користувач із цього параметра (менеджер із замовниками;
# сіє інструмент штабу за ім'ям, у модулі імен немає). Не задано, немає такого або він не читає ставок — той, хто ставить задачу
P_TALK_CUSTOMER = 'workset_rates.talk_customer_uid'
TALK_TO_MANAGER = ('customer', 'agent')


def check_live(rate, lang):
    """Минула (і архівна) ставка — історія пари: майстер правки її не змінює. Олівці в списку сховані, а тут — відмова й для виклику
    повз список (RPC, стара вкладка)."""
    if not rate.active or rate.state == 'past':
        raise UserError(calc.t('e_past', lang))


class WorksetRateActions(models.Model):
    _inherit = RATE

    def _edit_action(self, what):
        self.ensure_one()
        self.check_access('write')
        lang = self._lang()
        check_live(self, lang)
        value = {'wage': self.wage, 'price': self.price, 'agent': self.agent_client_share}[what]
        # дата «Нова ставка з дати» за замовчуванням — сьогодні, але не раніше дня після «Ставка з» (раніша — відмова майстра)
        day = max(calc.iso(self._today()), calc.add_days(calc.iso(self.date_from), 1))
        return {'type': 'ir.actions.act_window', 'name': calc.t('e_title', lang) % calc.t('e_' + what, lang), 'res_model': 'workset.rate.edit',
                'view_mode': 'form', 'views': [[False, 'form']], 'target': 'new',
                'context': {'default_rate_id': self.id, 'default_what': what, 'default_value': value, 'default_date': day}}

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

    # ——— чий посередник і з ким говорити (вікно правки «Посередник», «Переговорити»; слово власника 06.11) ———
    def _agent_who(self):
        """Чий посередник у ставки (calc.agent_who): ім'я й контакт — з домовленостей, що стосуються ставки (те саме правило, що сума), без
        них — агент лінії; source — звідки сума. Агент лінії — під sudo (контакт може належати іншій фірмі; читається лише ім'я)."""
        self.ensure_one()
        agent = self.line_id.sudo().agent_id
        return calc.agent_who(self._deals(), cust_of(self.customer_id), self.line_id.id, self._chain_day(self._today()), self.agent_own,
                              [agent.id, agent.name or ''] if agent else None)

    def _talk_people(self):
        """«З ким говорити» → контакт (res.partner під sudo; з нього беремо лише ім'я, телефон і Telegram): працівник — робочий контакт картки,
        посередник — _agent_who, замовник — комерційний контрагент, партнер лінії — поле лінії. Людини немає — порожній запис."""
        self.ensure_one()
        agent_id = self._agent_who()['agent_id']
        return {'worker': self.person_id.sudo(), 'agent': self.env['res.partner'].sudo().browse(agent_id).exists() if agent_id else self.env['res.partner'],
                'customer': self.customer_id.sudo().commercial_partner_id, 'partner': self.line_id.sudo().partner_id}

    def _talk_options(self, people=None):
        """→ (ролі «З ким говорити», для яких людина є; роль за замовчуванням) — calc.talk_options."""
        people = self._talk_people() if people is None else people
        return calc.talk_options({k: bool(p) for k, p in people.items()})

    def _talk_phone(self, role, partner):
        """Телефон для дзвінка. Працівник — картки з цим робочим контактом (sudo; читаються лише поля телефонів): особистий, мобільний,
        робочий, наостанок телефон контакту. Особистий — усім, хто пише ставки (рішення власника 06.11 «телефон: всем редакторам»; майстер
        «Переговорити» відкривають лише редактори й адміністратор), без перевірки групи кадрів. Решта — телефон контакту (у Odoo 19 в
        res.partner лише phone)."""
        phones = []
        if role == 'worker':
            Emp = self.env['hr.employee'].sudo()
            names = [f for f in EMP_PHONES if f in Emp._fields]
            cards = Emp.search_read([('work_contact_id', '=', partner.id)], names, order='id') if names else []
            for f in names:
                phones += [c.get(f) for c in cards]
        phones.append(partner.phone)
        return calc.first_filled(phones)

    def _talk_reach(self, role, partner, lang):
        """«Ім'я · телефон · Telegram» контакту ролі; чого немає, того й у рядку немає — без «телефон: —» (розбір вигляду 06.11). Telegram —
        поле модуля посередників, якщо воно є (agent_deals)."""
        phone = self._talk_phone(role, partner)
        tg = str(agent_deals.partner_tg(partner) or '').strip()
        parts = [str(partner.name or '').strip() or '—', phone, calc.t('talk_tg', lang) % tg if tg else '']
        return ' · '.join(p for p in parts if p)

    def _talk_user(self, who):
        """«Кому доручити» за замовчуванням: замовник і посередник — користувач параметра P_TALK_CUSTOMER (якщо він є, діє й читає ставки),
        решта — той, хто ставить задачу."""
        me = self.env.user
        if who not in TALK_TO_MANAGER:
            return me
        ids = calc.parse_ids(self._param(P_TALK_CUSTOMER))
        user = self.env['res.users'].sudo().browse(ids[0]).exists() if ids else self.env['res.users']
        if user and user.active and not user.share and self.env[RATE].with_user(user).has_access('read'):
            return user
        return me


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
    # без підказки «?»: варіанти вибору самі кажуть, що роблять (розбір вигляду 06.11 — «?» лише в неочевидних полів)
    agent_scope = fields.Selection(SCOPES, string='Кому', default='person')
    deal_ok = fields.Boolean(string='Домовленість можна правити', compute='_compute_deal_ok')
    deal_found = fields.Boolean(string='Домовленість є', compute='_compute_deal_ok')
    # «Посередник» (слово власника 06.11: «какой посредник… за кем прикреплено, непонятно»): чий він і звідки сума. Ім'я — посиланням на
    # домовленість, коли сума з неї й домовленість видно (agent_deal_name), інакше текстом (agent_name); «своє» — помітним попередженням із
    # причиною (agent_warning, agent_reason), «за домовленістю» / «домовленості немає» — сірим (agent_source)
    agent_name = fields.Char(string='Чий посередник', compute='_compute_agent_who')
    agent_deal_name = fields.Char(string='Посередник за домовленістю', compute='_compute_agent_who')
    agent_source = fields.Char(string='Звідки сума посередника', compute='_compute_agent_who')
    agent_warning = fields.Char(string='Посередник — своє значення', compute='_compute_agent_who')
    agent_reason = fields.Char(related='rate_id.agent_own_reason', string='Чому посередник свій')

    @api.depends('rate_id', 'what')
    def _compute_old(self):
        for w in self:
            r = w.rate_id
            w.old_value = {'wage': r.wage, 'price': r.price, 'agent': r.agent_client_share}.get(w.what or 'wage', 0.0) if r else 0.0

    @api.depends('rate_id')
    def _compute_deal_ok(self):
        # «Для всієї домовленості» (кнопка «Відкрити домовленість»): модель посередників є, домовленість цієї ставки знайшлася (deal_found —
        # користувач її бачить) і користувач може її правити (deal_ok). Немає — вибору «Кому» не видно, підказка — чому лише цій людині
        for w in self:
            r = w.rate_id
            found = bool(r) and bool(agent_deals.deal_ids(self.env, cust_of(r.customer_id), r.line_id.id))
            w.deal_found = found
            w.deal_ok = found and agent_deals.deal_writable(self.env)

    @api.depends('rate_id', 'what')
    def _compute_agent_who(self):
        lang = calc.lang_of(self.env.lang)
        for w in self:
            r = w.rate_id
            who = r._agent_who() if (r and w.what == 'agent') else None
            name = (', '.join(who['names']) or calc.t('e_agent_noname', lang)) if who else ''
            # посилання — лише коли сума справді з домовленості, дія модуля посередників є й користувач цю домовленість бачить (модуля
            # посередників немає — текст)
            link = bool(who) and who['source'] in ('deal', 'own_deal') and bool(agent_deals.deal_link(self.env, who['ids'], name))
            text = calc.t('e_agent_src_' + who['source'], lang) if who else ''
            own = bool(who) and who['source'] in ('own', 'own_deal')
            w.agent_deal_name = name if link else ''
            w.agent_name = '' if link else name
            w.agent_warning = text if own else ''
            w.agent_source = '' if own else text

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
        """«Нова ставка з дати»: нова ставка пари з self.date (копія поточної) з новим числом; попередня закінчується напередодні (А1).
        Дата — лише пізніше за «Ставка з» цієї ставки: та сама — це вона («Виправити помилку»); раніша встала б перед нею й укоротила б
        попередню ставку пари — змінились би минулі місяці."""
        self.ensure_one()
        rate = self.rate_id
        rate.check_access('write')
        self.env[RATE].check_access('create')
        lang = calc.lang_of(self.env.lang)
        check_live(rate, lang)
        self._check_value()
        day = calc.iso(self.date)
        if day == calc.iso(rate.date_from):
            raise UserError(calc.t('e_same_day', lang) % calc.fmt_date(day))
        if day < calc.iso(rate.date_from):
            raise UserError(calc.t('e_before', lang) % (calc.fmt_date(rate.date_from), calc.fmt_date(day)))
        reason = (self.reason or '').strip()
        if self.what == 'agent' and not reason:
            # посередник лише цій людині — «своє» значення: без причини не видно, чому не за домовленістю
            raise UserError(calc.t('e_agent_reason', lang))
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
        check_live(rate, lang)
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

    def action_open_agent_link(self):
        """Ім'я посередника — посилання: домовленість (або кілька), з якої сума ставки (calc.agent_who → 'ids'), дією модуля посередників
        (agent_deals.deal_link). Посилання вже немає (домовленість закрили, права забрали) — повідомлення, без помилки."""
        self.ensure_one()
        who = self.rate_id._agent_who()
        act = agent_deals.deal_link(self.env, who['ids'], ', '.join(who['names']) or calc.t('agent_deal', calc.lang_of(self.env.lang)))
        return act or self.rate_id.action_open_agent_deal()


class WorksetRateTalk(models.TransientModel):
    _name = 'workset.rate.talk'
    _description = 'Переговорити'

    rate_id = fields.Many2one(RATE, string='Ставка', required=True, ondelete='cascade')
    person_id = fields.Many2one(related='rate_id.person_id', string='Працівник')
    customer_id = fields.Many2one(related='rate_id.customer_id', string='Замовник')
    # «З ким говорити» (слово власника 06.11 «переговорити: значок и с кем»): лише ролі, для яких у ставки є людина (who_options — віджет
    # workset_rates_talk_who показує лише їх; сервер перевіряє вибір ще раз), за замовчуванням — посередник, якщо є, інакше працівник.
    # precompute — як «Задача»: обов'язкове поле рахується й при створенні без форми
    who = fields.Selection(WHO, string='З ким говорити', compute='_compute_who', store=True, readonly=False, required=True, precompute=True,
                           help='Посередник — з домовленості, що стосується ставки, або агент лінії; партнер лінії — з картки лінії. Кого в ставки '
                                'немає, того й у виборі немає. Ім\'я, телефон і Telegram підуть першим рядком нотатки задачі.')
    who_options = fields.Char(string='Можна говорити з', compute='_compute_who_options')
    contact_text = fields.Char(string='Контакт', compute='_compute_contact_text')
    # за замовчуванням (правило власника: замовники — менеджер із замовниками): говорити із замовником чи посередником — менеджер із замовниками
    # (параметр P_TALK_CUSTOMER), з працівником чи партнером лінії — той, хто ставить; змінили «З ким говорити» — підставляється знову
    user_id = fields.Many2one('res.users', string='Кому доручити', required=True, compute='_compute_user_id', store=True, readonly=False, precompute=True,
                              domain=[('share', '=', False)])
    # «Кому доручити» — лише ті, хто читає ставки: задачу на ставці людині без доступу Odoo не створить (помилка замість підказки)
    reader_ids = fields.Many2many('res.users', 'workset_rate_talk_reader_rel', 'talk_id', 'user_id', string='Читають ставки', compute='_compute_reader_ids')
    # precompute: назва рахується й при створенні без форми (RPC, тест) — інакше обов'язкове поле йде в базу порожнім (NOT NULL, збірка Odoo.sh 06.11)
    summary = fields.Char(string='Задача', compute='_compute_summary', store=True, readonly=False, required=True, precompute=True)
    date_deadline = fields.Date(string='Строк', required=True, default=lambda self: self.env[RATE]._today())
    note = fields.Text(string='Нотатка')

    @api.depends('rate_id')
    def _compute_summary(self):
        lang = calc.lang_of(self.env.lang)
        for w in self:
            w.summary = calc.t('talk_summary', lang) % calc.talk_text(w.rate_id._talk_numbers(), lang) if w.rate_id else ''

    @api.depends('rate_id')
    def _compute_who(self):
        for w in self:
            w.who = w.rate_id._talk_options()[1] if w.rate_id else False

    @api.depends('rate_id', 'who')
    def _compute_user_id(self):
        for w in self:
            w.user_id = w.rate_id._talk_user(w.who) if w.rate_id else self.env.user

    @api.depends('rate_id')
    def _compute_who_options(self):
        for w in self:
            w.who_options = ','.join(w.rate_id._talk_options()[0]) if w.rate_id else ''

    @api.depends('rate_id', 'who')
    def _compute_contact_text(self):
        # під вибором «З ким говорити»: ім'я, телефон, Telegram — те, що піде в нотатку задачі
        lang = calc.lang_of(self.env.lang)
        for w in self:
            partner = w.rate_id._talk_people().get(w.who) if (w.rate_id and w.who) else None
            w.contact_text = w.rate_id._talk_reach(w.who, partner, lang) if partner else ''

    @api.model
    def _readers(self):
        """Внутрішні користувачі, які читають ставки (права моделі: редактори, кадри, бухгалтерія, проєкти, адміністратор)."""
        users = self.env['res.users'].sudo().search([('share', '=', False)])
        return users.filtered(lambda u: self.env[RATE].with_user(u).has_access('read'))

    def _compute_reader_ids(self):
        readers = self._readers() if self else self.env['res.users']
        for w in self:
            w.reader_ids = readers

    def action_create(self):
        """Задача «Переговорити» (тип модуля — її рахує тривога «Огляду») на ставці для обраного користувача; назва — цифри людини (як у
        списку на момент задачі); нотатка — першим абзацом з ким говорити (роль, ім'я, телефон, Telegram), далі — нотатка з майстра. Обраний
        не читає ставок або в ставки немає людини обраної ролі — пояснення, а не помилка доступу Odoo."""
        self.ensure_one()
        rate = self.rate_id
        rate.check_access('write')
        lang = calc.lang_of(self.env.lang)
        if not self.env[RATE].with_user(self.user_id).has_access('read'):
            raise UserError(calc.t('talk_user', lang) % (self.user_id.name or ''))
        people = rate._talk_people()
        if self.who not in rate._talk_options(people)[0]:
            raise UserError(calc.t('talk_who_none', lang))
        note = calc.talk_note(self.who, rate._talk_reach(self.who, people[self.who], lang), self.note, lang)
        act = rate.activity_schedule(TALK_TYPE, date_deadline=self.date_deadline, summary=self.summary, note=note or False,
                                     user_id=self.user_id.id)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': rate.display_name, 'message': act.summary or '', 'type': 'success', 'sticky': False,
                           'next': {'type': 'ir.actions.act_window_close'}}}
