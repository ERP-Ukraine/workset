# -*- coding: utf-8 -*-
"""Ставка «працівник × замовник × Ставка з» — одна на всі фірми, з історією.

Автоматика — методи моделі (без автоматизацій, записаних у базі):
- А1 «ставка змінилась» — create / write полів-тригерів TRIGGERS / unlink: пара заново («Ставка до», статус), чинна ставка — у рядки карти
  проєктів замовника в усіх фірмах периметра (cost + прапорець ручної правки, лише рядки в EUR); пари без чинної — рядки назад на hourly_cost;
- А2 — models/project_map.py (рядок карти створено або змінено працівника чи проєкт);
- А3 «щодня» і А4 «звірка» — плановані дії data/ir_cron.xml (_cron_daily, _cron_check);
- А5 «робота» — rates_recalc_work (разово, інструмент штабу).
Службові поля (статус, «Ставка до», контроль, робота) пише код; серед TRIGGERS їх немає — запис у ту саму ставку по тих самих полях не зациклюється.
Контекст rates_dry (лише розрахунок), rates_today, rates_work_ref, rates_work_days — лише системному адміністратору.
"""
import datetime
import logging
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Command

from . import agent_deals, calc

_logger = logging.getLogger(__name__)

RATE = 'workset.rate'
CHECK = 'workset.rate.check'
MAP = 'project.sale.line.employee.map'
TRIGGERS = frozenset(['person_id', 'customer_id', 'date_from', 'wage', 'price', 'agent_share', 'active'])

FOUND_BY = [('line', 'Лінія'), ('us', 'Ми')]
STATES = [('future', 'Майбутня'), ('current', 'Діє'), ('past', 'Минула')]
WORKS = [('work', 'Працює'), ('work_new', 'Новий'), ('z_idle', 'Не працює'), ('z_none', 'Годин не було')]
WORK_GROUPS = [('g1_work', 'Працюють'), ('g2_idle', 'Не працюють'), ('g3_none', 'Годин не було')]
AGENT_SOURCES = [('deal', 'Домовленість'), ('line', 'Лінія'), ('manual', 'Вручну'), ('none', 'Немає')]
SHARE_LEVELS = [('low', 'Низька'), ('mid', 'Звичайна'), ('good', 'Добра')]
SOURCES = [('whatsapp', 'WhatsApp лінії'), ('email', 'Лист'), ('contract', 'Договір'), ('call', 'Розмова'), ('migration', 'Перенесено з проєкту'), ('other', 'Інше')]


def cust_of(partner):
    """Замовник порівнюється за комерційним контрагентом (проєкт може стояти на контакті всередині фірми)."""
    return partner.commercial_partner_id.id if partner else 0


class WorksetRate(models.Model):
    _name = RATE
    _description = 'Ставка'
    _inherit = ['mail.thread']
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Назва', compute='_compute_name', store=True)
    person_id = fields.Many2one('res.partner', string='Працівник', required=True, index=True, tracking=1, ondelete='restrict',
                                help='Робочий контакт картки працівника — один на всі фірми')
    customer_id = fields.Many2one('res.partner', string='Замовник', required=True, index=True, tracking=2, ondelete='restrict')
    line_id = fields.Many2one('workset.line', string='Лінія', index=True, tracking=3, compute='_compute_line_id', store=True, readonly=False,
                              help='Підставляється сама за замовником (список «Замовники» лінії); можна змінити')
    found_by = fields.Selection(FOUND_BY, string='Хто знайшов', tracking=4,
                                help='Лінія — людину знайшла лінія; Ми — знайшли ми. Від цього залежить частка WorkSET за правилом лінії.')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False))
    # гроші — хранимі з усередненням: у рядку кожної групи списку — середнє, у підвалі — середнє по списку
    wage = fields.Monetary(string='На руки', currency_field='currency_id', required=True, tracking=5, aggregator='avg')
    agent_share = fields.Monetary(string='Частка посередника', currency_field='currency_id', tracking=6, aggregator='avg',
                                  compute='_compute_agent_share', store=True, readonly=False,
                                  help='Підставляється сама: сума «€/год» діючих погодинних домовленостей з посередниками цього замовника або лінії; '
                                       'домовленості немає — частка агента лінії; можна змінити.')
    # звідки частка посередника: правка руками ставить «вручну» — ніч таку ставку не чіпає; зміна замовника, лінії чи дати — знову ланцюжок
    agent_source = fields.Selection(AGENT_SOURCES, string='Частка посередника з', compute='_compute_agent_share', store=True, readonly=False)
    agent_note = fields.Char(string='Посередники', compute='_compute_agent_note')
    price = fields.Monetary(string='Ціна', currency_field='currency_id', tracking=7, aggregator='avg', compute='_compute_price', store=True, readonly=False,
                            help='Що платить замовник або лінія за годину. Підставляється сама: на руки + частка WorkSET за правилом лінії + частка посередника; можна змінити.')
    our_share = fields.Monetary(string='Частка WorkSET', currency_field='currency_id', aggregator='avg', compute='_compute_our_share', store=True,
                                help='Ціна − на руки − частка посередника')
    rule_share = fields.Monetary(string='За правилом лінії', currency_field='currency_id', compute='_compute_rule')
    rule_ok = fields.Boolean(string='За правилом', compute='_compute_rule_ok', store=True)
    rule_text = fields.Char(string='Правило лінії', compute='_compute_rule')
    date_from = fields.Date(string='Ставка з', required=True, index=True, tracking=8, default=lambda self: self._today(),
                            help='Перший день, з якого діє ця ставка (на руки, ціна) — в усіх проєктах замовника в усіх фірмах. Попередня ставка цієї пари '
                                 'закінчується напередодні. «Вартість години» в проєктах змінюється при збереженні (майбутня ставка — уночі в день «Ставка з»); '
                                 'уже внесені табелі не перераховуються.')
    date_to = fields.Date(string='Ставка до', readonly=True,
                          help='Останній день ставки: рахується сама як день перед «Ставка з» наступної ставки цієї пари. Порожньо — наступної ставки ще немає, ця діє досі.')
    state = fields.Selection(STATES, string='Статус ставки', readonly=True, index=True,
                             help='Рахується за датами «Ставка з» і «Ставка до»: Діє — ставка в силі сьогодні; Майбутня — ще не почалась; Минула — її замінила новіша '
                                  '(історія ставок пари). Це не про те, чи людина зараз працює, — див. «Робота».')
    last_hours = fields.Date(string='Останні години', readonly=True,
                             help='Дата останніх годин людини в табелях у цього замовника (усі картки людини, усі проєкти замовника в усіх фірмах). Порожньо — годин не було.')
    work = fields.Selection(WORKS, string='Робота', readonly=True, index=True,
                            help='Працює — останні години в табелях у цього замовника не старші за вікно в днях; Не працює — старші; Годин не було — у цього замовника '
                                 'годин ще не було; Новий — ставка діє недавно, годин ще немає. Рахується для ставки зі статусом «Діє» щоночі й при зміні ставки.')
    work_group = fields.Selection(WORK_GROUPS, string='Робота зараз', readonly=True, index=True,
                                  help='Працюють — «Працює» і «Новий» разом; Не працюють; Годин не було. Для розділу «Робота» в панелі.')
    company_ids = fields.Many2many('res.company', 'workset_rate_company_rel', 'rate_id', 'company_id', string='Фірми', readonly=True,
                                   help='Фірми, у проєктах яких цей працівник стоїть у цього замовника (за картою проєктів).')
    project_ids = fields.Many2many('project.project', 'workset_rate_project_rel', 'rate_id', 'project_id', string='Проєкти', readonly=True,
                                   help="Проєкти (об'єкти) цього замовника, де працівник стоїть у карті проєктів.")
    firms_text = fields.Char(string='У фірмах', readonly=True)
    projects_text = fields.Char(string="На об'єктах", readonly=True)
    source = fields.Selection(SOURCES, string='Джерело', required=True, tracking=9)
    source_note = fields.Char(string='Звідки саме')
    note = fields.Text(string='Нотатка')
    contract_wage = fields.Float(string='У договорі', readonly=True)
    contract_company_id = fields.Many2one('res.company', string='Фірма договору', readonly=True)
    contract_ok = fields.Boolean(string='Договір збігається', readonly=True, default=True)
    map_count = fields.Integer(string='Рядків у проєктах', readonly=True)
    map_mismatch = fields.Integer(string='Розбіжностей у проєктах', readonly=True)
    order_price_ok = fields.Boolean(string='Ціна в замовленні', readonly=True, default=True)
    to_check = fields.Boolean(string='Перевірити')
    check_reason = fields.Char(string='Що перевірити')
    control_html = fields.Html(string='Контроль', readonly=True, sanitize=True)
    control_sig = fields.Char(string='Відбиток контролю', readonly=True)
    active = fields.Boolean(string='Активна', default=True, tracking=10)
    # нехранимі зв'язки — з явними іменами таблиць (таблиць немає; без імен Odoo не вивів би назву для зв'язку моделі з собою)
    employee_ids = fields.Many2many('hr.employee', 'workset_rate_employee_rel', 'rate_id', 'employee_id', string='Картки у фірмах', compute='_compute_employees')
    employee_summary = fields.Char(string='Картки у фірмах', compute='_compute_employees')
    pair_rate_ids = fields.Many2many(RATE, 'workset_rate_pair_rel', 'rate_id', 'other_id', string='Інші ставки пари', compute='_compute_pair')
    others_count = fields.Integer(string='Інших ставок', readonly=True)
    others_note = fields.Char(string='Інші ставки', compute='_compute_others_note',
                              help='Скільки ще діючих ставок цей працівник має в інших замовників. Список — у вкладці «Інші ставки» картки.')
    person_rate_ids = fields.Many2many(RATE, 'workset_rate_person_rel', 'rate_id', 'other_id', string='Ставки працівника', compute='_compute_pair')
    share_level = fields.Selection(SHARE_LEVELS, string='Рівень частки', compute='_compute_share_level', store=True, index=True,
                                   help='Низька — частка WorkSET ≤ 0 або нижче «Мінімуму частки» лінії; Добра — від «Доброї частки» лінії. Пороги — у картці лінії.')
    issues = fields.Char(string='Перевірити', compute='_compute_issues')
    line_agent_id = fields.Many2one('res.partner', string='Агент лінії', related='line_id.agent_id')
    subtitle = fields.Char(string='Замовник · лінія', compute='_compute_texts')
    formula = fields.Char(string='На руки + частка = ціна', compute='_compute_texts')

    # ——— службове: мова, «сьогодні», параметри ———
    def _lang(self):
        return calc.lang_of(self.env.lang)

    def _is_admin(self):
        return self.env.is_superuser() or self.env.user.has_group('base.group_system')

    def _dry(self):
        return bool(self.env.context.get('rates_dry')) and self._is_admin()

    @api.model
    def _today(self):
        # підміна «сьогодні» — лише адміністратору (проба на копії й тести): звичайна правка ставки завжди йде за справжньою датою
        forced = self.env.context.get('rates_today')
        if forced and self._is_admin():
            return calc.to_date(str(forced)[0:10])
        return datetime.datetime.now(ZoneInfo(calc.TZ)).date()

    def _param(self, key):
        return self.env['ir.config_parameter'].sudo().get_param(key) or ''

    def _work_ref(self, today):
        # опорна дата «Працює»: на нейтралізованій копії табелі обриваються на даті вивантаження — параметр workset_rates.work_ref;
        # на проді (не нейтралізована база) параметр ігнорується. Контекст rates_work_ref — разова підміна, лише адміністратору
        forced = self.env.context.get('rates_work_ref')
        if forced and self._is_admin():
            return str(forced)[0:10]
        neutral = self._param('database.is_neutralized').lower() in ('1', 'true')
        ref = self._param(calc.P_WORK_REF)
        if neutral and ref:
            return ref[0:10]
        return calc.iso(today)

    def _work_days(self):
        forced = self.env.context.get('rates_work_days')
        if forced and self._is_admin():
            return int(forced)
        return calc.WORK_DAYS

    def _skip_companies(self):
        return calc.parse_ids(self._param(calc.P_OUT))

    def _assignee(self):
        ids = calc.parse_ids(self._param(calc.P_ASSIGNEE))
        user = self.env['res.users'].sudo().browse(ids[0]).exists() if ids else self.env['res.users']
        return user or self.env.ref('base.user_admin')

    # ——— обчислювані поля ———
    @api.depends('person_id', 'customer_id', 'date_from', 'person_id.name', 'customer_id.name')
    def _compute_name(self):
        for r in self:
            r.name = '%s · %s · %s' % (r.person_id.name or '—', r.customer_id.name or '—', calc.fmt_date(r.date_from) or '—')

    @api.depends('customer_id')
    def _compute_line_id(self):
        # лінія — сама за замовником; замовника немає в жодній лінії — порожньо (не лінія попереднього замовника). Рахується лише при
        # створенні й зміні замовника, тому ручний вибір лінії після цього зберігається
        Line = self.env['workset.line']
        for r in self:
            cust = r.customer_id.commercial_partner_id
            r.line_id = Line.search([('customer_ids', 'in', cust.ids)], limit=1) if cust else Line

    def _deals(self):
        """Домовленості з посередниками (адаптер agent_deals.py); None — моделі посередників немає."""
        return agent_deals.read_deals(self.env)

    def _agent_choice(self, deals, today=None, keep_manual=True):
        """Частка посередника однієї ставки з джерелом (calc.agent_choice): дата пошуку — calc.agent_day за статусом.
        keep_manual False — ланцюжок заново (зміна замовника, лінії, дати скидає «вручну»)."""
        today = calc.iso(today or self._today())
        day = calc.agent_day(self.state, calc.iso(self.date_from), calc.iso(self.date_to), today)
        line = self.line_id
        return calc.agent_choice(deals, cust_of(self.customer_id), line.id, day, bool(line.agent_id), line.agent_share,
                                 self.agent_source if keep_manual else '', self.agent_share)

    @api.depends('customer_id', 'line_id', 'date_from')
    def _compute_agent_share(self):
        # спрацьовує при створенні й зміні замовника, лінії, дати: ланцюжок домовленість → лінія → 0 заново, «вручну» скидається
        deals = self._deals()
        today = self._today()
        for r in self:
            choice = r._agent_choice(deals, today, keep_manual=False)
            r.agent_share = choice['value']
            r.agent_source = choice['source']

    @api.depends('agent_source', 'agent_share', 'customer_id', 'line_id', 'date_from', 'state')
    @api.depends_context('lang')
    def _compute_agent_note(self):
        deals = self._deals()
        today = self._today()
        lang = self._lang()
        for r in self:
            ids = r._agent_choice(deals, today)['ids'] if r.agent_source == 'deal' else []
            r.agent_note = calc.agent_note(r.agent_source or 'none', ids, deals is not None, lang)

    @api.depends('wage', 'found_by', 'line_id', 'agent_share')
    def _compute_price(self):
        # ціна за замовчуванням = на руки + частка WorkSET за правилом лінії + частка посередника; правила немає — лишається введена.
        # Залежить лише від полів самої ставки: зміна правила лінії не переписує ціни минулих ставок
        for r in self:
            rule = calc.rule_share(r.found_by, r.line_id.share_line_found, r.line_id.share_we_found) if r.line_id else 0.0
            if rule > 0 and r.wage:
                r.price = round(r.wage + rule + (r.agent_share or 0.0), 2)
            else:
                r.price = r.price

    @api.depends('price', 'wage', 'agent_share')
    def _compute_our_share(self):
        for r in self:
            r.our_share = round((r.price or 0.0) - (r.wage or 0.0) - (r.agent_share or 0.0), 2)

    @api.depends('line_id', 'found_by', 'line_id.share_line_found', 'line_id.share_we_found')
    @api.depends_context('lang')
    def _compute_rule(self):
        lang = self._lang()
        for r in self:
            rule = calc.rule_share(r.found_by, r.line_id.share_line_found, r.line_id.share_we_found) if r.line_id else 0.0
            r.rule_share = rule
            r.rule_text = calc.rule_text(bool(r.line_id), r.found_by, rule, lang)

    @api.depends('line_id', 'found_by', 'line_id.share_line_found', 'line_id.share_we_found', 'our_share')
    def _compute_rule_ok(self):
        for r in self:
            rule = calc.rule_share(r.found_by, r.line_id.share_line_found, r.line_id.share_we_found) if r.line_id else 0.0
            r.rule_ok = (not rule) or abs((r.our_share or 0.0) - rule) < 0.005

    @api.depends('line_id', 'found_by', 'our_share', 'line_id.share_line_found', 'line_id.share_we_found', 'line_id.min_share', 'line_id.good_share')
    def _compute_share_level(self):
        min_default = calc.parse_float(self._param(calc.P_MIN_SHARE))
        good_default = calc.parse_float(self._param(calc.P_GOOD_SHARE))
        for r in self:
            rule = calc.rule_share(r.found_by, r.line_id.share_line_found, r.line_id.share_we_found) if r.line_id else 0.0
            r.share_level = calc.share_level(r.our_share, rule, r.line_id.min_share, r.line_id.good_share, min_default, good_default)

    @api.depends('active', 'state', 'rule_ok', 'rule_share', 'our_share', 'contract_ok', 'map_mismatch', 'order_price_ok', 'to_check', 'check_reason')
    @api.depends_context('lang')
    def _compute_issues(self):
        lang = self._lang()
        for r in self:
            r.issues = calc.issues({'active': r.active, 'state': r.state, 'rule_ok': r.rule_ok, 'rule_share': r.rule_share, 'our_share': r.our_share,
                                    'contract_ok': r.contract_ok, 'map_mismatch': r.map_mismatch, 'order_price_ok': r.order_price_ok,
                                    'to_check': r.to_check, 'check_reason': r.check_reason}, lang)

    @api.depends('customer_id', 'line_id', 'wage', 'our_share', 'agent_share', 'price')
    @api.depends_context('lang')
    def _compute_texts(self):
        lang = self._lang()
        for r in self:
            r.subtitle = ' · '.join([p for p in [r.customer_id.name or '', r.line_id.name or ''] if p])
            r.formula = calc.formula(r.wage, r.our_share, r.agent_share, r.price, lang)

    @api.depends('person_id')
    @api.depends_context('lang', 'allowed_company_ids')
    def _compute_employees(self):
        # картки — ті, що бачить користувач (його фірми; з архівними): чужа фірма в тегах дала б помилку доступу на всю форму.
        # Повний список усіх фірм — у вкладці «Контроль»
        lang = self._lang()
        Emp = self.env['hr.employee'].with_context(active_test=False)
        for r in self:
            cards = Emp.browse()
            try:
                if r.person_id:
                    cards = Emp.search([('work_contact_id', '=', r.person_id.id)], order='company_id, id')
            except AccessError:
                cards = Emp.browse()
            r.employee_ids = cards
            r.employee_summary = ', '.join(['%s · %s' % (c.company_id.name or '—', calc.t('card_active' if c.active else 'card_archived', lang)) for c in cards])

    @api.depends('person_id', 'customer_id', 'state', 'active')
    def _compute_pair(self):
        R = self.env[RATE].with_context(active_test=False)
        for r in self:
            rid = r.id if isinstance(r.id, int) else 0
            cid = cust_of(r.customer_id)
            if r.person_id and cid:
                r.pair_rate_ids = R.search([('person_id', '=', r.person_id.id), ('customer_id.commercial_partner_id', '=', cid), ('id', '!=', rid)],
                                           order='date_from desc, id desc')
            else:
                r.pair_rate_ids = R.browse()
            if rid and r.person_id and r.active and r.state == 'current':
                r.person_rate_ids = self.env[RATE].search([('person_id', '=', r.person_id.id), ('state', '=', 'current'),
                                                           ('customer_id.commercial_partner_id', '!=', cid)], order='last_hours desc, id')
            else:
                r.person_rate_ids = R.browse()

    @api.depends('others_count')
    @api.depends_context('lang')
    def _compute_others_note(self):
        lang = self._lang()
        for r in self:
            r.others_note = calc.others_note(r.others_count, lang)

    # ——— перевірки: заборона — лише дубль «працівник × замовник × Ставка з» і «на руки» ≤ 0 ———
    @api.constrains('wage', 'person_id', 'customer_id', 'date_from', 'active')
    def _check_wage_dup(self):
        lang = self._lang()
        for r in self:
            if not r.active:
                continue
            if (r.wage or 0) <= 0:
                raise ValidationError(calc.t('wage0', lang))
            same = self.sudo().search([('id', '!=', r.id), ('person_id', '=', r.person_id.id),
                                       ('customer_id.commercial_partner_id', '=', cust_of(r.customer_id)), ('date_from', '=', r.date_from)], limit=1)
            if same:
                raise ValidationError(calc.t('dup', lang) % (calc.fmt_date(r.date_from), same.id))

    # ——— А1: створення, правка полів-тригерів, видалення ———
    def _no_sync(self):
        # «без автоматики» (тести, службові правки) — лише суперкористувачу або адміністратору; іншим контекст ігнорується
        return bool(self.env.context.get('workset_rates_no_sync')) and self._is_admin()

    @staticmethod
    def _mark_manual(vals):
        # частку посередника задали руками (без зміни замовника, лінії, дати й без явного джерела) — джерело «вручну»
        if 'agent_share' in vals and 'agent_source' not in vals and not ({'customer_id', 'line_id', 'date_from'} & set(vals)):
            vals = dict(vals, agent_source='manual')
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        # частку посередника передали явно й вона ≠ ланцюжку для цієї пари й дати — «вручну» (переживе ніч); збігається — джерело ланцюжка
        given = [(rec, vals) for rec, vals in zip(recs, vals_list) if vals.get('agent_share') is not None and 'agent_source' not in vals]
        deals = self._deals() if given else None
        for rec, vals in given:
            src = calc.create_source(vals['agent_share'], rec._agent_choice(deals, keep_manual=False))
            if src != rec.agent_source:
                rec.write({'agent_source': src})
        if not self._no_sync():
            recs._after_change({}, set())
        return recs

    def write(self, vals):
        vals = self._mark_manual(vals)
        if self._no_sync() or not (TRIGGERS & set(vals)):
            return super().write(vals)
        old = {}
        for r in self:
            old[r.id] = {'person': r.person_id.id, 'cust': cust_of(r.customer_id), 'state': r.state, 'wage': r.wage}
        res = super().write(vals)
        self._after_change(old, set(vals))
        return res

    def _after_change(self, old, fnames):
        # пара заново; змінили працівника чи замовника — і колишня пара: була ця ставка там чинною — її рядки назад на картку
        today = self._today()
        jobs = []
        for rate in self:
            o = old.get(rate.id)
            was_wage = o['wage'] if o else rate.wage
            key = (rate.person_id.id, cust_of(rate.customer_id))
            if key[0] and key[1]:
                jobs.append((key, [(rate, was_wage)] if (o and 'wage' in fnames) else []))
            if o and ('person_id' in fnames or 'customer_id' in fnames):
                key2 = (o['person'], o['cust'])
                if key2[0] and key2[1] and key2 != key:
                    jobs.append((key2, [(rate, was_wage)] if o['state'] == 'current' else []))
        done = []
        for key, prev in jobs:
            if key in done:
                continue
            done.append(key)
            self._sync_pair(key[0], key[1], today, 'auto', True, prev)

    def unlink(self):
        # видалення (лише системний адміністратор — права моделі): пара перераховується без видалюваних ставок усім набором одразу;
        # інша стала чинною — її «на руки» в рядки карти; чинної не лишилось — рядки видаленої чинної назад на картку.
        # Права — до будь-якого запису в рядки карти; сухий режим — нічого не видаляє й не пише
        self.check_access('unlink')
        if self._dry():
            _logger.info('Ставки: rates_dry — видалення %d ставок пропущено', len(self))
            return True
        if self._no_sync():
            return super().unlink()
        today = self._today()
        gone = list(self.ids)
        doomed = self.sudo().with_context(active_test=False).browse(gone)
        done = []
        for rate in doomed:
            key = (rate.person_id.id, cust_of(rate.customer_id))
            if not key[0] or not key[1] or key in done:
                continue
            done.append(key)
            prev = [(False, r.wage) for r in doomed if (r.person_id.id, cust_of(r.customer_id)) == key and r.state == 'current']
            res = self._sync_pair(key[0], key[1], today, 'unlink', True, prev, gone)
            if not self._dry() and (res['updated'] or res['reverted']):
                _logger.info('Ставки: видалено ставку пари (контакт %d, замовник %d) — чинна тепер %s, оновлено рядків %d, повернуто на картку %d',
                             key[0], key[1], res['current'] or '—', res['updated'], res['reverted'])
        return super().unlink()

    # ——— рушій пари (sudo: ставка спільна для всіх фірм, а рядки карти, картки й табелі лежать у фірмах поза перемикачем користувача) ———
    def _pair_domain(self, person_id, cust_id, exclude):
        dom = [('person_id', '=', person_id), ('customer_id.commercial_partner_id', '=', cust_id)]
        if exclude:
            dom.append(('id', 'not in', exclude))
        return dom

    def _pair_rates(self, person_id, cust_id, exclude=None):
        return self.env[RATE].sudo().search(self._pair_domain(person_id, cust_id, exclude), order='date_from asc, id asc')

    def _stored_current(self, person_id, cust_id, exclude=None):
        # чинна ставка пари за записаним статусом — до перерахунку (разом із щойно архівованою): її «на руки» стоїть у рядках карти
        return self.env[RATE].sudo().with_context(active_test=False).search(self._pair_domain(person_id, cust_id, exclude) + [('state', '=', 'current')])

    def _recompute_pair(self, person_id, cust_id, today, exclude=None):
        # «Ставка до» і статус ставок пари → (чинна, id змінених, ставки пари, архівні ставки пари). Архівна — завжди «Минула»
        dry = self._dry()
        rates = self._pair_rates(person_id, cust_id, exclude)
        plan = calc.pair_plan([{'id': r.id, 'date_from': calc.iso(r.date_from)} for r in rates], calc.iso(today))
        changed = []
        for r in rates:
            want = plan['states'][r.id]
            vals = {}
            if calc.iso(r.date_to) != want['date_to']:
                vals['date_to'] = want['date_to'] or False
            if r.state != want['state']:
                vals['state'] = want['state']
            if vals:
                changed.append(r.id)
                if not dry:
                    r.write(vals)
        archived = self.env[RATE].sudo().with_context(active_test=False).search(self._pair_domain(person_id, cust_id, exclude) + [('active', '=', False)])
        for r in archived:
            if r.state != 'past':
                changed.append(r.id)
                if not dry:
                    r.write({'state': 'past'})
        cur = rates.browse(plan['current']) if plan['current'] else rates.browse()
        return cur, changed, rates, archived

    def _perimeter_domain(self):
        # проєкт діючий, фірма діюча й не поза периметром рекрутингу (список — системний параметр, пише інструмент штабу)
        return [('project_id.active', '=', True), ('project_id.company_id.active', '=', True), ('project_id.company_id', 'not in', self._skip_companies())]

    def _split_currency(self, maps):
        # рядки в євро веде автоматика; картка в іншій валюті (cost_currency_id) — лише показ і «Звірка»
        eur = self.env.ref('base.EUR').id
        ok = maps.filtered(lambda m: not m.cost_currency_id or m.cost_currency_id.id == eur)
        return ok, maps - ok

    def _pair_maps(self, person_id, cust_id):
        rows = self.env[MAP].sudo().search([('employee_id.work_contact_id', '=', person_id), ('project_id.partner_id.commercial_partner_id', '=', cust_id)]
                                           + self._perimeter_domain(), order='id asc')
        return self._split_currency(rows)

    def _map_info(self, m):
        return {'id': m.id, 'cost': m.cost, 'changed': m.is_cost_changed, 'order_price': m.sale_line_id.price_unit if m.sale_line_id else None,
                'project_id': m.project_id.id, 'project': m.project_id.display_name or '', 'company': m.project_id.company_id.name or '',
                'employee': m.employee_id.name or '', 'currency': m.cost_currency_id.name or ''}

    def _apply_rate(self, rate, maps):
        # «Вартість години» = «на руки» разом із прапорцем ручної правки: без прапорця _compute_cost повернув би hourly_cost картки
        todo = maps.browse(calc.lines_to_write([self._map_info(m) for m in maps], rate.wage))
        if todo and not self._dry():
            todo.write({'cost': rate.wage, 'is_cost_changed': True})
        return todo

    def _revert_lines(self, maps, wages):
        # пара втратила чинну ставку: рядки з її «на руки» — назад на картку (cost = hourly_cost, прапорець знято)
        todo = maps.browse(calc.lines_to_revert([self._map_info(m) for m in maps], wages))
        if not self._dry():
            for m in todo:
                m.write({'cost': m.employee_id.hourly_cost or 0.0, 'is_cost_changed': False})
        return todo

    def _has_contract(self):
        """Погодинна ставка в договорі (hr.version.hourly_wage) є лише з модулем зарплат (hr_payroll); без нього договір не звіряється."""
        return 'hr.version' in self.env and 'hourly_wage' in self.env['hr.version']._fields

    def _card_infos(self, person_id, cust_id, maps):
        cards = self.env['hr.employee'].sudo().with_context(active_test=False).search([('work_contact_id', '=', person_id)], order='id asc')
        has_wage = self._has_contract()
        at = maps.employee_id.ids
        Lines = self.env['account.analytic.line'].sudo()
        out = []
        for c in cards:
            ts = Lines.search([('employee_id', '=', c.id), ('project_id.partner_id.commercial_partner_id', '=', cust_id)], order='date desc', limit=1)
            wage = c.current_version_id.hourly_wage if (has_wage and c.current_version_id) else 0.0
            out.append({'id': c.id, 'name': c.name or '', 'company': c.company_id.name or '', 'company_id': c.company_id.id, 'active': c.active,
                        'at_customer': c.id in at, 'last_ts': calc.iso(ts.date) if ts else '', 'wage': (wage or 0.0) if has_wage else None, 'chosen': False})
        return out

    def _control(self, rate, cur, maps, other):
        # поля контролю й вкладка «Контроль» (пише код; серед тригерів їх немає). Вкладка порівнюється відбитком — html Odoo чистить
        lang = self._lang()
        if cur and rate.id == cur.id:
            infos = [self._map_info(m) for m in maps]
            cards = self._card_infos(rate.person_id.id, cust_of(rate.customer_id), maps)
            card = calc.pick_card(cards)
            if card:
                card['chosen'] = True
            vals = calc.control_vals(rate.wage, rate.price, card, infos, self._has_contract())
            vals['contract_company_id'] = card['company_id'] if card else False
            html = calc.control_html(cards, infos, [self._map_info(m) for m in other], rate.wage, rate.price, lang)
        else:
            vals = calc.neutral_control()
            vals['contract_company_id'] = False
            html = ''
        vals['control_sig'] = calc.sig(html) if html else ''
        diff = {}
        for k, v in vals.items():
            have = rate[k].id if k == 'contract_company_id' else rate[k]
            if isinstance(v, float) or isinstance(have, float):
                if not calc.same_money(have, v):
                    diff[k] = v
            elif (have or False) != (v or False):
                diff[k] = v
        if 'control_sig' in diff:
            diff['control_html'] = html or False
        if diff and not self._dry():
            rate.write(diff)
        return diff

    def _projects_text(self, lines):
        return ', '.join(calc_uniq(['«%s» (%s)' % (m.project_id.display_name or '', m.project_id.company_id.name or '') for m in lines]))

    def _post_applied(self, rate, done, why):
        lang = self._lang()
        firms = calc_uniq([m.project_id.company_id.name for m in done])
        body = calc.t('applied', lang) % (calc.t(why, lang), calc.money(rate.wage), len(done), self._projects_text(done), ', '.join(firms))
        if not self._dry():
            rate.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return body

    def _post_reverted(self, rates, back, why):
        lang = self._lang()
        body = calc.t('reverted', lang) % (calc.t(why, lang), len(back), self._projects_text(back))
        if not self._dry():
            for r in rates:
                r.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return body

    def _last_hours(self, person_id, cust_id):
        # дата останніх годин людини в замовника: усі картки (і архівні), усі проєкти замовника в усіх фірмах
        ts = self.env['account.analytic.line'].sudo().with_context(active_test=False).search(
            [('employee_id.work_contact_id', '=', person_id), ('project_id.partner_id.commercial_partner_id', '=', cust_id)], order='date desc', limit=1)
        return calc.iso(ts.date) if ts else ''

    def _refresh_work(self, person_id, cust_id, today, maps, other, exclude=None):
        # «Робота» всіх ставок пари: останні години, стан роботи (лише в чинної), фірми й проєкти пари. Пише лише змінене → число змінених ставок
        last = self._last_hours(person_id, cust_id)
        both = maps | other
        pids = sorted(set(both.project_id.ids))
        cids = sorted(set(both.project_id.company_id.ids))
        firms = ', '.join(calc_uniq([m.project_id.company_id.name for m in both]))
        projects = ', '.join(calc_uniq([m.project_id.display_name for m in both]))
        cur = self._stored_current(person_id, cust_id, exclude)
        state = calc.work_state(last, self._work_ref(today), self._work_days(), calc.iso(cur[:1].date_from) if cur else '')
        n = 0
        for r in self.env[RATE].sudo().with_context(active_test=False).search(self._pair_domain(person_id, cust_id, exclude)):
            mine = state if (cur and r.id in cur.ids) else ''
            have = {'work_group': r.work_group or '', 'last_hours': calc.iso(r.last_hours), 'work': r.work or '', 'firms_text': r.firms_text or '',
                    'projects_text': r.projects_text or ''}
            want = {'work_group': calc.work_group(mine), 'last_hours': last, 'work': mine, 'firms_text': firms, 'projects_text': projects}
            diff = {k: (want[k] or False) for k in want if have[k] != want[k]}
            if sorted(r.company_ids.ids) != cids:
                diff['company_ids'] = [Command.set(cids)]
            if sorted(r.project_ids.ids) != pids:
                diff['project_ids'] = [Command.set(pids)]
            if diff:
                n += 1
                if not self._dry():
                    r.write(diff)
        return n

    def _refresh_others(self, person_id, exclude=None):
        # «ще N ставок»: у кожної чинної ставки працівника — його чинні ставки в ІНШИХ замовників
        dom = [('person_id', '=', person_id)]
        if exclude:
            dom.append(('id', 'not in', exclude))
        rates = self.env[RATE].sudo().with_context(active_test=False).search(dom)
        live = [cust_of(r.customer_id) for r in rates if r.active and r.state == 'current']
        n = 0
        for r in rates:
            cnt = 0
            if r.active and r.state == 'current':
                mine = cust_of(r.customer_id)
                cnt = len([c for c in live if c != mine])
            if (r.others_count or 0) != cnt:
                n += 1
                if not self._dry():
                    r.write({'others_count': cnt})
        return n

    def _sync_pair(self, person_id, cust_id, today, why, force, prev=None, exclude=None):
        """Пара заново: статуси → чинна ставка в рядки карти (force — завжди; інакше лише якщо статуси змінились); чинної вже немає,
        а була — її рядки назад на картку → контроль → робота → «ще N ставок». prev — [(ставка або False, «на руки»)] колишньої чинної,
        якої в парі вже немає; exclude — id видалюваних ставок."""
        before = self._stored_current(person_id, cust_id, exclude)
        wages = [r.wage for r in before] + [p[1] for p in (prev or [])]
        notify = [r for r in before] + [p[0] for p in (prev or []) if p[0]]
        cur, changed, rates, archived = self._recompute_pair(person_id, cust_id, today, exclude)
        maps, other = self._pair_maps(person_id, cust_id)
        done = maps.browse()
        back = maps.browse()
        if cur and (force or changed):
            done = self._apply_rate(cur, maps)
        elif not cur and wages:
            back = self._revert_lines(maps, wages)
        for r in rates:
            self._control(r, cur, maps, other)
        for r in archived:
            self._control(r, False, maps, other)
        worked = self._refresh_work(person_id, cust_id, today, maps, other, exclude)
        self._refresh_others(person_id, exclude)
        if cur and done:
            self._post_applied(cur, done, why)
        if back and notify:
            self._post_reverted(notify, back, why)
        return {'current': cur.id if cur else 0, 'updated': len(done), 'reverted': len(back), 'changed': changed, 'maps': len(maps),
                'other': len(other), 'worked': worked}

    def _all_pairs(self):
        # усі пари, що мають ставки (і архівні: пара, де лишилась тільки архівна «чинна», має повернути рядки на картку)
        pairs = []
        for r in self.env[RATE].sudo().with_context(active_test=False).search([]):
            key = (r.person_id.id, cust_of(r.customer_id))
            if key[0] and key[1] and key not in pairs:
                pairs.append(key)
        return pairs

    def _current_keys(self):
        return {(r.person_id.id, cust_of(r.customer_id)): True for r in self.env[RATE].sudo().search([('state', '=', 'current')])}

    def _recent_ids(self, maps, today):
        # рядки, де за RECENT_DAYS днів є табелі цієї картки в будь-якому проєкті цього замовника
        if not maps:
            return []
        cutoff = calc.add_days(calc.iso(today), -calc.RECENT_DAYS)
        rows = self.env['account.analytic.line'].sudo().search_read([('date', '>=', cutoff), ('employee_id', 'in', maps.employee_id.ids), ('project_id', '!=', False)],
                                                                   ['employee_id', 'project_id'])
        projects = self.env['project.project'].sudo().with_context(active_test=False).browse(list({r['project_id'][0] for r in rows}))
        cust = {p.id: cust_of(p.partner_id) for p in projects}
        keys = {(r['employee_id'][0], cust.get(r['project_id'][0], 0)) for r in rows}
        return [m.id for m in maps if (m.employee_id.id, cust_of(m.project_id.partner_id)) in keys]

    def _nomap_lines(self, have):
        # рядки карти периметра без чинної ставки пари → (із замовником, без замовника в проєкті)
        with_cust = []
        no_cust = []
        Map = self.env[MAP].sudo()
        for m in Map.search(self._perimeter_domain(), order='project_id, id'):
            if not m.project_id.partner_id:
                no_cust.append(m.id)
                continue
            person = m.employee_id.work_contact_id.id
            if not person or (person, cust_of(m.project_id.partner_id)) not in have:
                with_cust.append(m.id)
        return Map.browse(with_cust), Map.browse(no_cust)

    @api.model
    def _rates_action(self, name, domain, ctx):
        context = dict(ctx)
        context.setdefault('default_date_from', calc.iso(self._today()))
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': RATE, 'view_mode': 'list,form', 'views': [[False, 'list'], [False, 'form']],
                'domain': domain, 'context': context}

    def _maps_action(self, name, ids, recent, extra):
        ctx = {'create': False, 'edit': False, 'delete': False, 'rates_recent_ids': recent}
        ctx.update(extra)
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': MAP, 'view_mode': 'list,form', 'domain': [('id', 'in', ids)],
                'views': [[self.env.ref('workset_rates.view_map_list').id, 'list'], [self.env.ref('workset_rates.view_map_form').id, 'form']],
                'search_view_id': [self.env.ref('workset_rates.view_map_search').id, 'search'], 'context': ctx}

    # ——— кнопки ———
    def action_new_from_date(self):
        # «Нова ставка з дати»: нова форма з числами поточної й «Ставка з» = сьогодні; «Джерело» порожнє. Нічого не зберігає
        self.ensure_one()
        self.check_access('write')
        r = self
        return {'type': 'ir.actions.act_window', 'name': calc.t('new_rate', self._lang()), 'res_model': RATE, 'view_mode': 'form', 'views': [[False, 'form']],
                'target': 'current', 'context': {'default_person_id': r.person_id.id, 'default_customer_id': r.customer_id.id, 'default_line_id': r.line_id.id or False,
                                                 'default_found_by': r.found_by or False, 'default_wage': r.wage, 'default_price': r.price,
                                                 'default_date_from': calc.iso(self._today())}}

    def action_apply_now(self):
        # «Застосувати зараз»: те саме, що А1, для пари цієї ставки — і повідомлення завжди
        self.ensure_one()
        self.check_access('write')
        lang = self._lang()
        res = self._sync_pair(self.person_id.id, cust_of(self.customer_id), self._today(), 'button', True)
        text = calc.t('btn_done', lang) % (res['updated'], res['maps'])
        if not res['current']:
            text = calc.t('btn_none', lang)
        elif not res['updated']:
            text = calc.t('btn_same', lang) % res['maps']
            self.sudo().message_post(body=calc.t('btn_note', lang) % res['maps'], message_type='comment', subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Ставки'), 'message': text, 'type': 'success', 'sticky': False, 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_open_maps(self):
        self.ensure_one()
        maps, other = self._pair_maps(self.person_id.id, cust_of(self.customer_id))
        both = maps | other
        return self._maps_action(calc.t('maps_title', self._lang()), both.ids, self._recent_ids(both, self._today()), {})

    @api.model
    def rates_nomap_action(self):
        # меню «Звірка → Проєкти без ставки»: рядки карти периметра без чинної ставки пари й проєкти без замовника; збираються при відкритті
        nomap, nocust = self._nomap_lines(self._current_keys())
        both = nomap | nocust
        return self._maps_action(calc.t('nomap_title', self._lang()), both.ids, self._recent_ids(both, self._today()),
                                 {'search_default_recent': 1, 'search_default_groupby_customer': 1})

    # ——— полоса підсумків над списком і канбаном: один виклик по домену поточного пошуку ———
    @api.model
    def rates_summary(self, domain=None):
        rows = self.search_read(list(domain or []), ['work', 'wage', 'price', 'our_share'])
        s = calc.summary([{'work': r['work'], 'wage': r['wage'], 'price': r['price'], 'share': r['our_share']} for r in rows])
        s['days'] = self._work_days()
        s['ref'] = self._work_ref(self._today())
        return s

    # ——— ліва панель без порожніх значень; лінії й фірми — за числом тих, хто працює ———
    @api.model
    def search_panel_select_range(self, field_name, **kwargs):
        res = super().search_panel_select_range(field_name, **kwargs)
        if field_name in ('line_id', 'customer_id') and kwargs.get('enable_counters') and 'values' in res:
            order = self.env['workset.line']._panel_order() if field_name == 'line_id' else None
            res['values'] = calc.panel_values(res['values'], order)
        return res

    @api.model
    def search_panel_select_multi_range(self, field_name, **kwargs):
        res = super().search_panel_select_multi_range(field_name, **kwargs)
        if field_name == 'company_ids' and kwargs.get('enable_counters') and 'values' in res:
            groups = self.sudo()._read_group([('state', '=', 'current'), ('work_group', '=', 'g1_work')], ['company_ids'], ['__count'])
            order = {c.id: n for c, n in groups if c}
            res['values'] = calc.panel_values(res['values'], order)
        return res

    # ——— А3 «щодня», А4 «звірка», А5 «робота» ———
    def _check_admin(self):
        if not self._is_admin():
            raise AccessError(calc.t('only_admin', self._lang()))

    @api.model
    def _cron_daily(self):
        self.rates_daily()

    @api.model
    def rates_daily(self):
        """А3: посередники чинних і майбутніх ставок на «Ставка з» → майбутні, в яких настала «Ставка з», → діють; рядки карти — лише
        в пар, де змінився статус (ручну правку ніч не чіпає); пара без чинної — рядки назад на картку; контроль усіх ставок."""
        self._check_admin()
        assignee = self._assignee()
        lang = calc.lang_of(assignee.lang)
        me = self.with_context(lang=assignee.lang)
        today = self._today()
        dry = self._dry()
        # частки посередників змінюються в домовленостях, а не в ставці: звіряємо щоночі; запис — через write, щоб спрацювала А1 пари
        # ланцюжок домовленість → лінія → вручну на сьогодні (майбутня — на її «Ставка з»); запис лише якщо число змінилось —
        # тоді А1 пари й рядки карти, без зайвих записів
        agents = 0
        deals = self._deals()
        for r in self.env[RATE].sudo().search([('state', 'in', ['current', 'future']), ('agent_source', '!=', 'manual')]):
            choice = r._agent_choice(deals, today)
            if not calc.same_money(choice['value'], r.agent_share) or choice['source'] != (r.agent_source or 'none'):
                agents += 1
                if not dry:
                    r.write({'agent_share': choice['value'], 'agent_source': choice['source']})
        moved = updated = reverted = 0
        pairs = me._all_pairs()
        for key in pairs:
            res = me._sync_pair(key[0], key[1], today, 'daily', False)
            moved += 1 if res['changed'] else 0
            updated += res['updated']
            reverted += res['reverted']
        summary = calc.t('daily_sum', lang) % (calc.fmt_date(today), len(pairs), moved, updated, reverted, calc.t('dry', lang) if dry else '')
        if not dry:
            _logger.info(summary)
        return {'pairs': len(pairs), 'moved': moved, 'updated': updated, 'reverted': reverted, 'agents': agents, 'dry': dry, 'message': summary}

    @api.model
    def rates_recalc_work(self):
        """А5: «Робота» в усіх пар (останні години, стан, фірми й проєкти) без перерахунку статусів і рядків карти."""
        self._check_admin()
        assignee = self._assignee()
        lang = calc.lang_of(assignee.lang)
        today = self._today()
        ref = self._work_ref(today)
        days = self._work_days()
        changed = 0
        people = []
        pairs = self._all_pairs()
        for key in pairs:
            maps, other = self._pair_maps(key[0], key[1])
            changed += self._refresh_work(key[0], key[1], today, maps, other)
            if key[0] not in people:
                people.append(key[0])
                changed += self._refresh_others(key[0])
        summary = calc.t('work_sum', lang) % (len(pairs), changed, days, calc.fmt_date(ref), calc.t('dry', lang) if self._dry() else '')
        if not self._dry():
            _logger.info(summary)
        return {'pairs': len(pairs), 'changed': changed, 'dry': self._dry(), 'ref': ref, 'days': days, 'message': summary}

    @api.model
    def _cron_check(self):
        self.rates_check()

    @api.model
    def rates_check(self):
        """А4: контроль усіх пар заново → запис «Звірка ставок <дата>» з розділами й на ньому ОДНА задача To Do (без листа, строк +2 робочі дні).
        Повтор того ж дня — той самий запис і задача; минулі відкриті задачі закриваються «замінено новою звіркою». Розбіжностей немає —
        нового запису немає, відкрита задача закривається. Ставок ще немає — звірка не складається."""
        self._check_admin()
        assignee = self._assignee()
        lang = calc.lang_of(assignee.lang)
        me = self.with_context(lang=assignee.lang)
        today = self._today()
        dry = self._dry()
        R = self.env[RATE].sudo()
        total = R.with_context(active_test=False).search_count([])
        for key in me._all_pairs():
            me._sync_pair(key[0], key[1], today, 'daily', False)
        cur = R.search([('state', '=', 'current')], order='customer_id, person_id, id')

        def rate_cell(r):
            return {'text': r.person_id.name or '', 'href': calc.link(RATE, r.id)}

        def map_row(m, note):
            return [{'text': m.project_id.display_name or '', 'href': calc.link('project.project', m.project_id.id)}, m.project_id.company_id.name or '',
                    m.project_id.partner_id.commercial_partner_id.name or '—', {'text': m.employee_id.name or '', 'href': calc.link('hr.employee', m.employee_id.id)},
                    calc.money(m.cost) + ' €', note]

        contract_rows, mism_rows, order_rows, rule_rows, cur_rows = [], [], [], [], []
        for r in cur:
            cust = r.customer_id.name or ''
            maps, other = me._pair_maps(r.person_id.id, cust_of(r.customer_id))
            if not r.contract_ok:
                contract_rows.append([rate_cell(r), cust, r.contract_company_id.name or '—',
                                      (calc.money(r.contract_wage) + ' €') if r.contract_wage else calc.t('no_wage', lang), calc.money(r.wage) + ' €'])
            if r.map_mismatch > 0:
                mism_rows.append([rate_cell(r), cust, str(r.map_mismatch), calc.money(r.wage) + ' €'])
            if not r.order_price_ok:
                prices = [calc.money(m.sale_line_id.price_unit) + ' €' for m in maps if m.sale_line_id and not calc.same_money(m.sale_line_id.price_unit, r.price)]
                order_rows.append([rate_cell(r), cust, calc.money(r.price) + ' €', ', '.join(calc_uniq(prices))])
            if not r.rule_ok or r.our_share <= 0:
                rule_rows.append([rate_cell(r), cust, r.line_id.name or '—', calc.money(r.our_share) + ' €', r.with_context(lang=assignee.lang).rule_text or ''])
            for m in other:
                cur_rows.append([rate_cell(r), cust, m.project_id.display_name or '', m.cost_currency_id.name or ''])
        nomap, nocust = me._nomap_lines(me._current_keys())
        recent = set(me._recent_ids(nomap | nocust, today))
        nomap_rows, nocust_rows = [], []
        stale = 0
        for m in nomap:
            if m.id not in recent:
                stale += 1
                continue
            note = calc.t('no_contact', lang) if not m.employee_id.work_contact_id else calc.t('manual' if m.is_cost_changed else 'from_card', lang)
            nomap_rows.append(map_row(m, note))
        for m in nocust:
            if m.id in recent:
                nocust_rows.append(map_row(m, ''))
        check_rows = [[rate_cell(r), r.customer_id.name or '', r.check_reason or ''] for r in R.search([('to_check', '=', True)], order='customer_id, person_id, id')]
        # лінія з агентом, а частка посередника не з домовленості — лише коли модель домовленостей у базі є
        agent_rows = []
        deals = me._deals()
        if deals is not None:
            for r in cur.filtered(lambda x: x.line_id.agent_id and x.agent_source != 'deal'):
                agent_rows.append([rate_cell(r), r.customer_id.name or '', r.line_id.agent_id.name or '', calc.money(r.agent_share) + ' €',
                                   calc.agent_note(r.agent_source or 'none', [], True, lang)])
        days = calc.RECENT_DAYS
        t = lambda k: calc.t(k, lang)  # noqa: E731
        cols_map = [t('c_project'), t('c_firm'), t('c_customer'), t('c_card'), t('c_cost'), t('c_note')]
        sections = [
            {'key': 'contract', 'title': t('s_contract'), 'hint': t('h_contract'), 'cols': [t('c_person'), t('c_customer'), t('c_cfirm'), t('c_contract'), t('c_wage')], 'rows': contract_rows},
            {'key': 'mismatch', 'title': t('s_mism'), 'hint': t('h_mism'), 'cols': [t('c_person'), t('c_customer'), t('c_rows'), t('c_wage')], 'rows': mism_rows},
            {'key': 'nomap', 'title': t('s_nomap') % days, 'hint': t('h_nomap') % (stale, days, days), 'cols': cols_map, 'rows': nomap_rows, 'more': t('more_nomap')},
            {'key': 'nocust', 'title': t('s_nocust') % days, 'hint': t('h_nocust'), 'cols': cols_map, 'rows': nocust_rows},
            {'key': 'currency', 'title': t('s_cur'), 'hint': t('h_cur'), 'cols': [t('c_person'), t('c_customer'), t('c_project'), t('c_cur')], 'rows': cur_rows},
            {'key': 'order', 'title': t('s_order'), 'hint': t('h_order'), 'cols': [t('c_person'), t('c_customer'), t('c_rprice'), t('c_orders')], 'rows': order_rows},
            {'key': 'rule', 'title': t('s_rule'), 'hint': t('h_rule'), 'cols': [t('c_person'), t('c_customer'), t('c_line'), t('c_share'), t('c_rule')], 'rows': rule_rows},
            {'key': 'check', 'title': t('s_check'), 'hint': t('h_check'), 'cols': [t('c_person'), t('c_customer'), t('c_what')], 'rows': check_rows},
            {'key': 'agent', 'title': t('s_agent'), 'hint': t('h_agent'), 'cols': [t('c_person'), t('c_customer'), t('c_agent'), t('c_ashare'), t('c_asrc')], 'rows': agent_rows},
        ]
        n = calc.issue_count(sections) if total else 0
        counts = {s['key']: len(s['rows']) for s in sections}
        counts['nomap_stale'] = stale
        created = reused = closed = 0
        if total and not dry:
            todo = self.env.ref('mail.mail_activity_data_todo')
            Act = self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True)
            Check = self.env[CHECK].sudo()
            opened = Act.search([('res_model', '=', CHECK), ('activity_type_id', '=', todo.id)])
            day = Check.search([('date', '=', today)], limit=1)
            title = t('check_title') % calc.fmt_date(today)
            if n:
                body = calc.sections_html(sections, calc.HTML_LIMIT, lang)
                if day:
                    day.write({'name': title, 'issue_count': n, 'body': body, 'done': False})
                    reused = day.id
                else:
                    day = Check.create({'name': title, 'date': today, 'issue_count': n, 'body': body})
                    created = day.id
                summary = t('act_summary') % (title, n, calc.issues_word(n, lang))
                deadline = calc.workdays_after(calc.iso(today), calc.DUE_WORKDAYS)
                mine = opened.filtered(lambda a: a.res_id == day.id)[:1]
                if mine:
                    mine.write({'summary': summary, 'date_deadline': deadline, 'user_id': assignee.id})
                else:
                    Act.create({'res_model_id': self.env['ir.model']._get_id(CHECK), 'res_id': day.id, 'activity_type_id': todo.id,
                                'summary': summary, 'note': t('act_note'), 'user_id': assignee.id, 'date_deadline': deadline})
                for a in opened - mine:
                    a.action_feedback(feedback=t('replaced'))
                    closed += 1
            else:
                if day:
                    day.write({'issue_count': 0, 'body': t('clean_body'), 'done': True})
                    reused = day.id
                for a in opened:
                    a.action_feedback(feedback=t('clean'))
                    closed += 1
            _logger.info('Ставки: звірка %s — розбіжностей %d, запис № %d, закрито задач %d', calc.fmt_date(today), n, created or reused, closed)
        message = (t('check_msg') % (calc.fmt_date(today), n, t('dry') if dry else '')) if total else t('no_rates')
        return {'issues': n, 'sections': counts, 'created': created, 'reused': reused, 'closed': closed, 'rates': total, 'dry': dry, 'message': message}


def calc_uniq(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out
