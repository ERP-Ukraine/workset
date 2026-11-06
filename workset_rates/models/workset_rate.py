# -*- coding: utf-8 -*-
"""Ставка «працівник × замовник × Ставка з» — одна на всі фірми, з історією.

Автоматика — методи моделі (без автоматизацій, записаних у базі):
- А1 «ставка змінилась» — create / write полів-тригерів TRIGGERS / unlink: пара заново («Ставка до», статус), чинна ставка — у рядки карти
  проєктів замовника в усіх фірмах периметра (cost + прапорець ручної правки, лише рядки в EUR); пари без чинної — рядки назад на hourly_cost;
- А2 — models/project_map.py (рядок карти створено або змінено працівника чи проєкт);
- А3 «щодня» і А4 «звірка» — плановані дії data/ir_cron.xml (_cron_daily, _cron_check);
- А5 «робота» — rates_recalc_work (разово, інструмент штабу).
- М2 «ланцюжок цін» (А1+): на збереженні (CHAIN_TRIGGERS) — загальна ставка на «Ставка з» (calc.rule_pick), числа правила (Ц, тип, фікс,
  ціна клієнта), «Своя», якщо Ц / тип / фікс правили вручну й вони ≠ правилу; щоразу й щоночі — посередник і агент людей (адаптер
  agent_deals.py), бонус офісу, години минулого місяця, частка WorkSET, чиста частка, рівні. Формули — calc (розділ 3.1 спеціфікації М2).
  У карту проєктів пишеться лише «на руки» (база); тимчасова зміна (workset_rate_raise.py) — лише «На руки зараз».
- А6 «договір → база» (М2b): версія договору створена або змінена (models/hr_version.py) → _contract_to_base: у парах, де картка — картка
  договору, ставка на дату версії отримує «На руки» з договору (нова ставка з дати або та сама дата — правка), ціна — за якорем (calc.contract_*);
  ставка з версії йде за нею (_contract_follow: позначили «Тимчасово», архівували, перенесли дату — в архів); щоночі — догон
  (_contract_catchup) і страж «вгору і назад» (_updown_mark). Вимикач — параметр workset_rates.a6_mode (немає — вимкнено). Договір модуль лише читає.
Службові поля (статус, «Ставка до», контроль, робота, ланцюжок) пише код; серед тригерів їх немає, а ланцюжок пише з контекстом
workset_rates_internal — запис у ту саму ставку не зациклюється.
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
RULE = 'workset.rate.rule'
RAISE = 'workset.rate.raise'
INTERNAL = 'workset_rates_internal'  # запис службових полів ланцюжка — без автоматики
TALK_TYPE = 'workset_rates.mail_activity_talk'  # тип задачі «Переговорити» (data/mail_activity_data.xml): тривога «Огляду» рахує лише його
# А1 М1: пара заново й карта проєктів; CHAIN_TRIGGERS — ще й ланцюжок цін (М2)
TRIGGERS = frozenset(['person_id', 'customer_id', 'date_from', 'wage', 'price', 'active'])
CHAIN_TRIGGERS = TRIGGERS | frozenset(['price_mode', 'fixed_month', 'client_price', 'is_own', 'found_by', 'line_id', 'people_agent_payer',
                                       'agent_own', 'agent_own_value'])

FOUND_BY = [('line', 'Лінія'), ('us', 'Ми')]
STATES = [('future', 'Майбутня'), ('current', 'Діє'), ('past', 'Минула')]
WORKS = [('work', 'Працює'), ('work_new', 'Новий'), ('z_idle', 'Не працює'), ('z_none', 'Годин не було')]
WORK_GROUPS = [('g1_work', 'Працюють'), ('g2_idle', 'Не працюють'), ('g3_none', 'Годин не було')]
AGENT_SOURCES = [('deal', 'Домовленість'), ('line', 'Лінія'), ('manual', 'Вручну'), ('none', 'Немає')]
SHARE_LEVELS = [('low', 'Низька'), ('mid', 'Звичайна'), ('good', 'Добра')]
SOURCES = [('whatsapp', 'WhatsApp лінії'), ('email', 'Лист'), ('contract', 'Договір'), ('call', 'Розмова'), ('migration', 'Перенесено з проєкту'),
           ('owner', 'Слово власника'), ('rule', 'Загальна ставка'), ('chat', 'Переписка'), ('odoo', 'Odoo'), ('other', 'Інше')]
ANCHORS = [('markup', 'Надбавка'), ('tariff', 'Тариф')]
PRICE_MODES = [('hour', 'За годину'), ('fixed_month', 'Фікс за місяць')]
PAYERS = [('line', 'Лінія'), ('client', 'Клієнт зверху'), ('workset', 'WorkSET')]
WAGE_SOURCES = [('contract', 'Договір'), ('manual', 'Вручну'), ('migration', 'Перенос')]


def cust_of(partner):
    """Замовник порівнюється за комерційним контрагентом (проєкт може стояти на контакті всередині фірми)."""
    return partner.commercial_partner_id.id if partner else 0


class WorksetRate(models.Model):
    _name = RATE
    _description = 'Ставка'
    # задачі «Переговорити» (кнопка рядка списку) — на самій ставці
    _inherit = ['mail.thread', 'mail.activity.mixin']
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
    wage = fields.Monetary(string='Зарплата працівника', currency_field='currency_id', required=True, tracking=5, aggregator='avg',
                           help='Скільки працівник отримує на руки за годину.')
    # М1 до посередників з домовленостей: довідкове число й його джерело (перенос М2 переводить у «Своя» з причиною); у частку не входить
    agent_share = fields.Monetary(string='Частка посередника', currency_field='currency_id', aggregator='avg',
                                  help='Довідково: частка посередника, введена до М2. У частку WorkSET не входить — її рахує «Посередник» з домовленостей.')
    agent_source = fields.Selection(AGENT_SOURCES, string='Частка посередника з', readonly=True)
    agent_note = fields.Char(string='Посередники', compute='_compute_agent_note')
    # ——— ланцюжок цін (М2): Ц — з правила або «Своя»; решту пише код ———
    rule_id = fields.Many2one(RULE, string='Загальна ставка', readonly=True, index=True, ondelete='set null',
                              help='Правило, що діє на «Ставка з»: тариф замовника, а немає — правило лінії')
    anchor = fields.Selection(related='rule_id.anchor', store=True, string='Якір',
                              help='Надбавка: росте зарплата працівника — росте «Платять нам». Тариф: «Платять нам» те саме — зменшується наша частка.')
    price_mode = fields.Selection(PRICE_MODES, string='Тип ціни', default='hour', required=True, tracking=11,
                                  help='З загальної ставки; у «Своєї» можна змінити. Фікс за місяць: замовник платить зарплату працівника за години як є '
                                       'і фіксовану суму за оформлення людини на місяць.')
    is_own = fields.Boolean(string='Своя', tracking=12, help='Ставиться сама, коли числа змінено вручну. Загальна ставка таку ставку не змінює.')
    own_reason = fields.Char(string='Чому своя', tracking=13)
    price = fields.Monetary(string='Платять нам', currency_field='currency_id', tracking=7, aggregator='avg',
                            help='Що платить нам замовник або лінія за годину. Сама з загальної ставки: надбавка — зарплата працівника + частка за «Хто '
                                 'знайшов»; тариф — ціна тарифу; фікс за місяць — зарплата працівника. Змінили вручну й число ≠ правилу — ставка стає «Своєю».')
    rule_price = fields.Monetary(string='За правилом', currency_field='currency_id', compute='_compute_rule', help='«Платять нам», яке дала б загальна ставка')
    fixed_month = fields.Monetary(string='Фікс за місяць', currency_field='currency_id', tracking=14, aggregator='avg',
                                  help='Замовник платить зарплату працівника за години як є і фіксовану суму за оформлення людини на місяць.')
    client_price = fields.Monetary(string='Ціна клієнта', currency_field='currency_id', tracking=15, aggregator='avg',
                                   help='Скільки платить кінцевий клієнт посереднику (лінії), якщо відомо. У нашу частку не входить.')
    client_price_source = fields.Char(string='Джерело ціни')
    client_price_date = fields.Date(string='Дата ціни')
    client_price_manual = fields.Boolean(string='Ціна клієнта вручну', readonly=True,
                                         help='Ціну клієнта ввели в цій ставці; інакше — із загальної ставки')
    line_share = fields.Monetary(string='Частка лінії', currency_field='currency_id', readonly=True, aggregator='avg',
                                 help='Ціна клієнта − «Платять нам», €/год. 0 — ціна клієнта невідома.')
    agent_client_share = fields.Monetary(string='Посередник', currency_field='currency_id', readonly=True, aggregator='avg',
                                         help='Сума «€/год» діючих погодинних домовленостей з посередниками (модуль посередників): домовленість із '
                                              'замовниками — коли замовник ставки серед них; без замовників — за лінією ставки. Своє значення '
                                              'для цієї людини («Посередник — своє») — замість домовленостей.')
    agent_client_note = fields.Char(string='Хто посередник', readonly=True)
    # посередник лише цій людині: своє значення з причиною (майстер правки «Посередник» → «лише цій людині»); інакше — з домовленостей
    agent_own = fields.Boolean(string='Посередник — своє', tracking=17,
                               help='Для цієї людини посередник — своє значення, а не сума домовленостей замовника або лінії')
    agent_own_value = fields.Monetary(string='Посередник (своє)', currency_field='currency_id', tracking=18, aggregator='avg')
    agent_own_reason = fields.Char(string='Чому посередник свій', tracking=19)
    worker_fund_share = fields.Monetary(string='Фонд працівників', currency_field='currency_id', readonly=True, aggregator='avg',
                                        help='€/год у фонд працівників (житло, паливо, інструмент, спецодяг) за домовленістю «фонд працівників» '
                                             'замовника або лінії; повна ставка зайнятості в договорі — своя сума домовленості. Гроші працівників: '
                                             'віднімається від частки WorkSET, але це не посередник.')
    car_rent_share = fields.Monetary(string='Оренда авто', currency_field='currency_id', readonly=True, aggregator='avg',
                                     help='€/год під оренду авто — із загальної ставки. Віднімається від частки WorkSET.')
    agent_people_share = fields.Monetary(string='Агент людей', currency_field='currency_id', readonly=True, aggregator='avg',
                                         help='€/год агента, що дав цю людину (модуль посередників). Частку WorkSET зменшує, лише коли платить WorkSET.')
    people_agent_payer = fields.Selection(PAYERS, string='Хто платить агенту', tracking=16, help='З загальної ставки (порожньо там — лінія); можна змінити')
    people_agent_payer_manual = fields.Boolean(string='Хто платить агенту вручну', readonly=True,
                                               help='Змінили в цій ставці й не так, як у загальній ставці; інакше — із загальної ставки')
    our_share = fields.Monetary(string='Частка WorkSET', currency_field='currency_id', aggregator='avg', readonly=True,
                                help='За годину, до ZUS і податків на зарплату: платять нам − зарплата працівника − посередник − фонд працівників − '
                                     'оренда авто (− агент людей, якщо платить WorkSET); у «Фікс за місяць» — фікс ÷ годин місяця.')
    month_hours = fields.Float(string='Годин за місяць', readonly=True, help='Години пари в табелях за минулий повний місяць (усі фірми периметра)')
    office_bonus = fields.Monetary(string='Бонус офісу', currency_field='currency_id', readonly=True, aggregator='avg',
                                   help='€/год за правилами бонусів офісу: € за годину + € за працівника на місяць ÷ годин за місяць')
    bonus_norm = fields.Boolean(string='Бонус за нормою годин', readonly=True, help='Табелів за минулий місяць немає — місячні бонуси поділено на норму годин')
    net_share = fields.Monetary(string='Після бонусів офісу', currency_field='currency_id', readonly=True, aggregator='avg',
                                help='Частка WorkSET − бонус офісу, €/год. Не «після податків»: ZUS і податки на зарплату — у «Прибутковості».')
    net_level = fields.Selection(SHARE_LEVELS, string='Рівень після бонусів', readonly=True, index=True)
    found_missing = fields.Boolean(string='Хто знайшов не вказано', readonly=True,
                                   help='Надбавка, а «Хто знайшов» порожньо: «Платять нам» — за правилом «Знайшла лінія»')
    wage_source = fields.Selection(WAGE_SOURCES, string='Зарплата з', default='manual')
    contract_version_id = fields.Many2one('hr.version', string='Версія договору', readonly=True, ondelete='set null',
                                          help='Версія договору, з якої взято «Зарплата працівника»')
    # ставку внесли не з договору, а А6 переписав її «На руки» на ту саму дату: що було до того — щоб повернути, коли договір перестане бути базою
    wage_before_contract = fields.Monetary(string='Зарплата до договору', currency_field='currency_id', readonly=True, aggregator='avg',
                                           help='«Зарплата працівника», яка стояла в ставці до того, як її переписав договір на ту саму дату')
    wage_source_before = fields.Selection(WAGE_SOURCES, string='Зарплата з (до договору)', readonly=True)
    raise_ids = fields.Many2many(RAISE, 'workset_rate_raise_rel', 'rate_id', 'raise_id', string='Тимчасові зміни', compute='_compute_raises')
    raise_now = fields.Monetary(string='Надбавка зараз', currency_field='currency_id', compute='_compute_raises',
                                help='Сума тимчасових змін «+€ за годину» пари, що діють сьогодні')
    wage_now = fields.Monetary(string='Зарплата зараз', currency_field='currency_id', compute='_compute_raises',
                               help='Зарплата працівника + тимчасові зміни сьогодні. У карту проєктів і частку не входить.')
    # ——— М2c: тексти екранів (нехранимі) ———
    raise_note = fields.Char(string='Зарплата зараз до', compute='_compute_raises',
                             help='«зараз 17 до 30.11» під «Зарплата працівника», коли діє тимчасова зміна')
    raise_badge = fields.Char(string='Надбавка до', compute='_compute_raises', help='Бейдж дошки «+2 до 30.11»')
    has_raise = fields.Boolean(string='Є тимчасова зміна', compute='_compute_raises', search='_search_has_raise',
                               help='У пари сьогодні діє тимчасова зміна (не скасована)')
    fixed_note = fields.Char(string='Фікс за годину', compute='_compute_texts', help='Фікс за місяць ÷ години минулого місяця (немає — норма)')
    missing_text = fields.Char(string='Бракує', compute='_compute_missing')
    wage_note = fields.Char(string='Зарплата звідки', compute='_compute_wage_note')
    card_line = fields.Char(string='Рядок дошки', compute='_compute_texts')
    rule_share = fields.Monetary(string='За правилом лінії', currency_field='currency_id', compute='_compute_rule')
    rule_ok = fields.Boolean(string='Відповідає правилу', readonly=True, default=True)
    rule_text = fields.Char(string='Правило лінії', compute='_compute_rule')
    date_from = fields.Date(string='Ставка з', required=True, index=True, tracking=8, default=lambda self: self._today(),
                            help='Перший день, з якого діє ця ставка (зарплата працівника, платять нам) — в усіх проєктах замовника в усіх фірмах. Попередня ставка цієї пари '
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
    # вкладка «Контроль» — мовою того, хто дивиться: обидві мови хранимі (пише _control), показ — нехранимий за мовою контексту
    control_html_uk = fields.Html(string='Контроль (укр.)', readonly=True, sanitize=True)
    control_html_ru = fields.Html(string='Контроль (рос.)', readonly=True, sanitize=True)
    control_html = fields.Html(string='Контроль', compute='_compute_control_html', sanitize=False)
    control_sig = fields.Char(string='Відбиток контролю', readonly=True)
    active = fields.Boolean(string='Активна', default=True, tracking=10)
    # нехранимі зв'язки — з явними іменами таблиць (таблиць немає; без імен Odoo не вивів би назву для зв'язку моделі з собою)
    employee_ids = fields.Many2many('hr.employee', 'workset_rate_employee_rel', 'rate_id', 'employee_id', string='Картки у фірмах', compute='_compute_employees',
                                    groups='hr.group_hr_user')
    employee_summary = fields.Char(string='Картки у фірмах (стисло)', compute='_compute_employee_summary')
    pair_rate_ids = fields.Many2many(RATE, 'workset_rate_pair_rel', 'rate_id', 'other_id', string='Інші ставки пари', compute='_compute_pair')
    others_count = fields.Integer(string='Інших ставок', readonly=True)
    others_note = fields.Char(string='Інші ставки', compute='_compute_others_note',
                              help='Скільки ще діючих ставок цей працівник має в інших замовників. Список — у вкладці «Інші ставки» картки.')
    person_rate_ids = fields.Many2many(RATE, 'workset_rate_person_rel', 'rate_id', 'other_id', string='Ставки працівника', compute='_compute_pair')
    share_level = fields.Selection(SHARE_LEVELS, string='Рівень частки', readonly=True, index=True,
                                   help='Низька — частка WorkSET ≤ 0 або нижче мінімуму (загальної ставки, лінії або за замовчуванням); Добра — від «Доброї частки».')
    issues = fields.Char(string='Перевірити: список', compute='_compute_issues')
    line_agent_id = fields.Many2one('res.partner', string='Агент лінії', related='line_id.agent_id')
    subtitle = fields.Char(string='Замовник · лінія', compute='_compute_texts')
    formula = fields.Char(string='Зарплата + частка = платять нам', compute='_compute_texts')
    # кнопка «Домовленість» біля «Посередник» у формі: є модель посередників (адаптер agent_deals.py), користувач її читає і є домовленість
    # із замовником ставки (лінія — та сама або без лінії). Нехраниме, без залежності від моделі посередників: на проді її немає
    agent_deal_ok = fields.Boolean(string='Є домовленість посередника', compute='_compute_agent_deal_ok')

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

    @api.depends('agent_client_note')
    def _compute_agent_note(self):
        # «Посередники» форми М1 — те саме, що «Хто посередник» М2 (форму перебудовує круг екранів)
        for r in self:
            r.agent_note = r.agent_client_note or ''

    @api.depends('customer_id', 'line_id')
    def _compute_agent_deal_ok(self):
        # моделі посередників немає (прод) або користувач її не читає — False без помилки (адаптер agent_deals.deal_ids → None)
        for r in self:
            r.agent_deal_ok = bool(agent_deals.deal_ids(self.env, cust_of(r.customer_id), r.line_id.id))

    def action_open_agent_deal(self):
        """«Домовленість» біля «Посередник»: домовленості посередника із замовником ставки (лінія — та сама або без лінії); одна — картка,
        кілька — список. Моделі посередників немає або домовленості не знайшлося — повідомлення, без помилки."""
        self.ensure_one()
        lang = self._lang()
        act = agent_deals.deal_action(self.env, cust_of(self.customer_id), self.line_id.id, calc.t('agent_deal', lang))
        if act:
            return act
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Ставки'), 'message': calc.t('agent_deal_none', lang), 'type': 'warning', 'sticky': False}}

    # ——— ланцюжок цін: дані одним набором на пакет ставок ———
    @api.model
    def _rules_index(self):
        """Активні загальні ставки для calc.rule_pick: [{'id', 'line_id', 'customer_id' (комерційний), 'date_from'}]."""
        return [{'id': r.id, 'line_id': r.line_id.id, 'customer_id': cust_of(r.customer_id), 'date_from': calc.iso(r.date_from)}
                for r in self.env[RULE].sudo().search([])]

    @api.model
    def _rule_dict(self, rule):
        rule = rule.sudo()
        return {'anchor': rule.anchor, 'price_mode': rule.price_mode, 'share_line_found': rule.share_line_found, 'share_we_found': rule.share_we_found,
                'price': rule.price, 'fixed_month': rule.fixed_month} if rule else None

    @api.model
    def _hours_default(self):
        return calc.parse_float(self._param(calc.P_MONTH_HOURS)) or calc.MONTH_HOURS_DEFAULT

    @api.model
    def _chain_data(self):
        return {'rules': self._rules_index(), 'deals': self._deals(), 'people': agent_deals.read_people(self.env),
                'bonuses': self.env['workset.office.bonus']._rules(), 'hours_default': self._hours_default(),
                'min_default': calc.parse_float(self._param(calc.P_MIN_SHARE)), 'good_default': calc.parse_float(self._param(calc.P_GOOD_SHARE))}

    @api.model
    def _pair_hour_rows(self, person_id, cust_id, date_from, date_to):
        """Табелі пари «людина × замовник» (усі картки, фірми периметра) за «з – по» → [[«РРРР-ММ-ДД», години]]."""
        if not person_id or not cust_id or date_to < date_from:
            return []
        rows = self.env['account.analytic.line'].sudo().with_context(active_test=False).search_read(
            [('employee_id.work_contact_id', '=', person_id), ('project_id.partner_id.commercial_partner_id', '=', cust_id),
             ('project_id.company_id', 'not in', self._skip_companies()), ('date', '>=', date_from), ('date', '<=', date_to)], ['date', 'unit_amount'])
        return [[calc.iso(r['date']), r['unit_amount']] for r in rows]

    def _month_hours(self, person_id, cust_id, day):
        first, last = calc.prev_month(day)
        return calc.sum_hours(self._pair_hour_rows(person_id, cust_id, first, last), first, last)

    def _chain_day(self, today):
        """Дата, на яку рахуються посередники, бонус і години: діюча — сьогодні, майбутня — «Ставка з», минула — «Ставка до»
        (calc.agent_day). Статусу ще немає (щойно створена) — за планом пари."""
        state, date_to = self.state, calc.iso(self.date_to)
        if not state and isinstance(self.id, int):
            rates = self._pair_rates(self.person_id.id, cust_of(self.customer_id))
            plan = calc.pair_plan([{'id': r.id, 'date_from': calc.iso(r.date_from)} for r in rates], calc.iso(today))
            want = plan['states'].get(self.id) or {}
            state, date_to = want.get('state', ''), want.get('date_to', '')
        return calc.agent_day(state, calc.iso(self.date_from), date_to, calc.iso(today))

    def _prev_price(self):
        """«Ціна нам» попередньої ставки пари (для тарифу без ціни: росте на руки — ціна та сама)."""
        if not self.person_id or not self.customer_id or not self.date_from:
            return 0.0
        dom = self._pair_domain(self.person_id.id, cust_of(self.customer_id), [self._origin.id] if isinstance(self._origin.id, int) else None)
        prev = self.env[RATE].sudo().search(dom + [('date_from', '<', self.date_from)], order='date_from desc, id desc', limit=1)
        return prev.price if prev else 0.0

    def _chain_light(self, day, data, nums):
        """Те, що рахується щоразу й щоночі, на day: посередник, агент людей, години минулого місяця, бонус офісу, W, Ч, П, рівні.
        nums — числа ставки після правила: {'price', 'price_mode', 'fixed_month', 'client_price', 'people_agent_payer', 'rule'}."""
        lang = self._lang()
        person = self.person_id.id
        cust = cust_of(self.customer_id)
        line = self.line_id
        deals = data['deals']
        if self.agent_own:
            # посередник лише цій людині — своє значення з причиною замість домовленостей
            a_c, a_note = round(float(self.agent_own_value or 0.0), 2), calc.t('agent_own_note', lang) % (self.agent_own_reason or '—')
        else:
            a_c, used = calc.agent_share_for(deals or [], cust, line.id, day)
            a_note = calc.agent_client_note(deals, used, lang)
        # фонд працівників: сума «на повну ставку» — лише якщо така є в домовленості (тоді читаємо версію договору людини на day).
        # «Фікс за місяць»: фонд і оренда авто з частки не віднімаються (calc.shares) — тож і в ставці 0: стовпець, «Переговорити» й
        # «Прибутковість» (_plan_at) не показують суму, якої в частці немає
        fixed = nums['price_mode'] == 'fixed_month'
        full = self._full_rate_on(person, cust, day) if (not fixed and calc.fund_needs_full(deals or [], cust, line.id, day)) else False
        fund = 0.0 if fixed else calc.worker_fund_for(deals or [], cust, line.id, day, full)[0]
        rule = nums['rule']
        car = round(float(rule.sudo().car_rent or 0.0), 2) if (rule and not fixed) else 0.0
        a_p, _ppl = calc.people_share_for(data['people'], person, day)
        hours = self._month_hours(person, cust, day)
        bonus = calc.office_bonus(data['bonuses'], cust, line.id, person, day, hours, data['hours_default'])
        payer = nums['people_agent_payer'] or 'line'
        sh = calc.shares(nums['price_mode'], self.wage, nums['price'], nums['fixed_month'], hours, data['hours_default'], a_c, a_p, payer, bonus['value'],
                         fund, car)
        low = calc.min_share_of(rule.sudo().min_share if rule else 0.0, line.min_share, data['min_default'])
        good = line.good_share or data['good_default']
        return {'agent_client_share': a_c, 'agent_client_note': a_note, 'agent_people_share': a_p,
                'worker_fund_share': fund, 'car_rent_share': car,
                'month_hours': hours, 'office_bonus': bonus['value'], 'bonus_norm': bonus['norm'], 'our_share': sh['our_share'], 'net_share': sh['net_share'],
                'line_share': calc.line_share(nums['client_price'], nums['price']),
                'share_level': calc.share_level(sh['our_share'], 0.0, low, good, 0.0, 0.0), 'net_level': calc.share_level(sh['net_share'], 0.0, low, good, 0.0, 0.0)}

    def _chain_vals(self, data, full, edited, today, payer_edited=False):
        """Значення ланцюжка однієї ставки (без запису). full — ще й загальна ставка на «Ставка з», числа правила, «Своя», ціна клієнта
        з правила; edited — Ц / тип / фікс змінено в цьому записі вручну; payer_edited — так само «Хто платить агенту»."""
        vals = {}
        rule = self.rule_id
        nums = {'price': self.price, 'price_mode': self.price_mode or 'hour', 'fixed_month': self.fixed_month, 'client_price': self.client_price,
                'people_agent_payer': self.people_agent_payer}
        if full:
            rid = calc.rule_pick(data['rules'], self.line_id.id, cust_of(self.customer_id), calc.iso(self.date_from))
            rule = self.env[RULE].browse(rid)
            by_rule = calc.rule_numbers(self._rule_dict(rule), self.wage, self.found_by, self._prev_price()) if rid else None
            is_own = calc.own_decision(edited, nums, by_rule, self.is_own)
            vals.update({'rule_id': rid or False, 'is_own': is_own, 'rule_ok': not (by_rule and is_own),
                         'found_missing': bool(rid) and rule.sudo().anchor == 'markup' and (by_rule or {}).get('price_mode') == 'hour' and not self.found_by})
            if by_rule and not is_own:
                vals.update(by_rule)
                nums.update(by_rule)
            r = rule.sudo()
            if not self.client_price_manual:
                vals.update({'client_price': r.client_price if rid else 0.0, 'client_price_source': (r.client_price_source if rid else False) or False,
                             'client_price_date': (r.client_price_date if rid else False) or False})
                nums['client_price'] = vals['client_price']
            # хто платить агенту людей — як «Ціна клієнта»: поки не змінили руками, іде за правилом; змінили на інше, ніж у правилі, —
            # «вручну» (правило й майстер його не чіпають); повернули таке саме, як у правилі, або очистили — знову за правилом
            by_rule_payer = (r.people_agent_payer if rid else False) or 'line'
            manual = self.people_agent_payer_manual
            if payer_edited:
                manual = bool(self.people_agent_payer) and self.people_agent_payer != by_rule_payer
            vals['people_agent_payer_manual'] = manual
            if not manual:
                vals['people_agent_payer'] = by_rule_payer
                nums['people_agent_payer'] = by_rule_payer
        nums['rule'] = rule
        vals.update(self._chain_light(self._chain_day(today), data, nums))
        return vals

    def _chain(self, data=None, full=True, edited=False, today=None, payer_edited=False):
        """Ланцюжок цін для набору ставок: пише лише змінене (контекст INTERNAL — без автоматики). → число змінених ставок."""
        data = data or self._chain_data()
        today = today or self._today()
        n = 0
        for r in self:
            want = r._chain_vals(data, full, edited, today, payer_edited)
            diff = {}
            for k, v in want.items():
                have = r[k]
                if isinstance(have, models.BaseModel):
                    have = have.id
                if k in ('client_price_date',):
                    have, v = calc.iso(have), calc.iso(v)
                if isinstance(v, float) or isinstance(have, float):
                    if not calc.same_money(have, v):
                        diff[k] = v
                elif (have or False) != (v or False):
                    diff[k] = v
            if diff:
                n += 1
                if not self._dry():
                    # sudo: INTERNAL діє лише в суперкористувача (_strip_internal); права на саму правку перевірено до ланцюжка
                    r.sudo().with_context(**{INTERNAL: True}).write(diff)
        return n

    def _rechain(self):
        """Ланцюжок заново й пара (карта проєктів, контроль) — для «Застосувати з дати» на ту саму дату."""
        self._chain(full=True)
        self._after_change({}, set())

    @api.onchange('wage', 'found_by', 'line_id', 'customer_id', 'date_from')
    def _onchange_chain(self):
        # форма: ціна, тип і фікс — одразу за загальною ставкою (у «Своєї» — як є); збереження порівняє їх із правилом ще раз
        if self.is_own or not self.date_from:
            return
        rid = calc.rule_pick(self._rules_index(), self.line_id.id, cust_of(self.customer_id), calc.iso(self.date_from))
        if rid:
            by_rule = calc.rule_numbers(self._rule_dict(self.env[RULE].browse(rid)), self.wage, self.found_by, self._prev_price())
            self.price = by_rule['price']
            self.price_mode = by_rule['price_mode']
            self.fixed_month = by_rule['fixed_month']

    @api.model
    def _plan_at(self, pairs, day):
        """План для «Прибутковості» (М3): пара (контакт, комерційний замовник) × дата → числа ставки, що діє на day (Р, Ц, К, П, тип ціни, фікс)
        і пораховане на day (посередник, агент людей і хто платить, бонус офісу, W, Ч). Ставки на day немає — None."""
        day = calc.iso(day)
        data = self._chain_data()
        out = {}
        for person, cust in pairs:
            rate = self.env[RATE].sudo().search(self._pair_domain(person, cust, None) + [('date_from', '<=', day)], order='date_from desc, id desc', limit=1)
            if not rate:
                out[(person, cust)] = None
                continue
            nums = {'price': rate.price, 'price_mode': rate.price_mode or 'hour', 'fixed_month': rate.fixed_month, 'client_price': rate.client_price,
                    'people_agent_payer': rate.people_agent_payer, 'rule': rate.rule_id}
            v = rate._chain_light(day, data, nums)
            # фонд працівників і оренда авто — поруч із частками (частка й чиста — уже без них): «Прибутковість» показує їх окремими рядками
            out[(person, cust)] = {'rate_id': rate.id, 'wage': rate.wage, 'price': rate.price, 'client_price': rate.client_price, 'line_share': v['line_share'],
                                   'agent_client': v['agent_client_share'], 'agent_people': v['agent_people_share'],
                                   'people_agent_payer': rate.people_agent_payer or 'line', 'office_bonus': v['office_bonus'],
                                   'worker_fund_share': v['worker_fund_share'], 'car_rent_share': v['car_rent_share'], 'our_share': v['our_share'],
                                   'net_share': v['net_share'], 'price_mode': rate.price_mode or 'hour', 'fixed_month': rate.fixed_month,
                                   'month_hours': v['month_hours']}
        return out

    @api.depends('rule_id', 'found_by', 'wage', 'date_from', 'price_mode')
    @api.depends_context('lang')
    def _compute_rule(self):
        lang = self._lang()
        for r in self:
            rule = r.rule_id.sudo()
            share = calc.found_share(r.found_by, rule.share_line_found, rule.share_we_found) if rule else 0.0
            by_rule = calc.rule_numbers(r._rule_dict(rule), r.wage, r.found_by, r._prev_price()) if rule else None
            r.rule_share = share
            r.rule_price = by_rule['price'] if by_rule else 0.0
            r.rule_text = calc.rule_text(bool(r.line_id), r.found_by or 'line', share, lang)

    @api.depends('person_id', 'customer_id', 'wage')
    @api.depends_context('rates_today', 'lang')
    def _compute_raises(self):
        today = calc.iso(self._today())
        lang = self._lang()
        Raise = self.env[RAISE].sudo()
        for r in self:
            cid = cust_of(r.customer_id)
            raises = Raise.search([('person_id', '=', r.person_id.id), ('customer_id.commercial_partner_id', '=', cid), ('state', '!=', 'cancel')]) \
                if (r.person_id and cid) else Raise.browse()
            txt = calc.raise_texts(r.wage, [{'kind': x.kind, 'amount': x.amount, 'date_from': calc.iso(x.date_from), 'date_to': calc.iso(x.date_to)}
                                            for x in raises], today, lang)
            r.raise_ids = raises
            r.raise_now = round(txt['now'] - (r.wage or 0.0), 2)
            r.wage_now = txt['now']
            r.raise_note = txt['note']
            r.raise_badge = txt['badge']
            r.has_raise = any(calc.iso(x.date_from) <= today <= calc.iso(x.date_to) for x in raises)

    def _search_has_raise(self, operator, value):
        # фільтр «Є тимчасова зміна»: пари, у яких сьогодні діє не скасована зміна (як «Тимчасових» полоси підсумків).
        # Оператор і значення в будь-якому вигляді Odoo 19 (in / not in з OrderedSet) — calc.bool_search
        today = calc.iso(self._today())
        ids = []
        for x in self.env[RAISE].sudo().search([('state', '!=', 'cancel'), ('date_from', '<=', today), ('date_to', '>=', today)]):
            ids += self.env[RATE].sudo().search([('person_id', '=', x.person_id.id),
                                                 ('customer_id.commercial_partner_id', '=', cust_of(x.customer_id))]).ids
        return calc.bool_search(operator, value, ids)

    @api.depends('active', 'state', 'rule_ok', 'rule_share', 'our_share', 'contract_ok', 'map_mismatch', 'order_price_ok', 'to_check', 'check_reason',
                 'is_own', 'own_reason', 'found_missing')
    @api.depends_context('lang')
    def _compute_issues(self):
        lang = self._lang()
        for r in self:
            r.issues = calc.issues({'active': r.active, 'state': r.state, 'rule_ok': r.rule_ok, 'rule_share': r.rule_share, 'our_share': r.our_share,
                                    'contract_ok': r.contract_ok, 'map_mismatch': r.map_mismatch, 'order_price_ok': r.order_price_ok,
                                    'to_check': r.to_check, 'check_reason': r.check_reason, 'is_own': r.is_own, 'own_reason': r.own_reason,
                                    'found_missing': r.found_missing}, lang)

    @api.depends('customer_id', 'line_id', 'wage', 'our_share', 'agent_client_share', 'agent_people_share', 'people_agent_payer', 'price', 'price_mode',
                 'fixed_month', 'month_hours', 'net_share', 'worker_fund_share', 'car_rent_share')
    @api.depends_context('lang')
    def _compute_texts(self):
        lang = self._lang()
        hours_default = self._hours_default()
        for r in self:
            r.subtitle = ' · '.join([p for p in [r.customer_id.name or '', r.line_id.name or ''] if p])
            if r.price_mode == 'fixed_month':
                hours, norm = calc.hours_or_norm(r.month_hours, hours_default)
                r.formula = calc.formula_fixed(r.wage, r.fixed_month, hours, norm, lang)
                r.fixed_note = calc.fixed_note(r.fixed_month, r.month_hours, hours_default, lang)
            else:
                agents = (r.agent_client_share or 0.0) + ((r.agent_people_share or 0.0) if r.people_agent_payer == 'workset' else 0.0)
                r.formula = calc.formula(r.wage, r.our_share, [agents, r.worker_fund_share, r.car_rent_share], r.price, lang)
                r.fixed_note = ''
            r.card_line = calc.card_line(r.formula, r.net_share, lang)

    @api.depends('found_missing', 'is_own', 'own_reason', 'share_level', 'net_level', 'active', 'state')
    @api.depends_context('lang')
    def _compute_missing(self):
        # «Бракує» форми (стандарт форми, правило 4): одна темна строка — лише в живої ставки
        lang = self._lang()
        for r in self:
            live = r.active and r.state != 'past'
            r.missing_text = calc.missing(r.found_missing, r.is_own, r.own_reason, 'low' in (r.share_level, r.net_level), lang) if live else ''

    @api.depends('contract_version_id', 'wage_source')
    @api.depends_context('lang')
    def _compute_wage_note(self):
        # «з договору від 01.09.2026 · фірма» — версію читаємо sudo: редактор ставок без прав кадрів теж бачить, звідки «На руки»
        lang = self._lang()
        for r in self:
            v = r.contract_version_id.sudo()
            r.wage_note = calc.wage_note(calc.iso(v.date_version), v.employee_id.company_id.name, lang) \
                if (v and r.wage_source == 'contract') else ''

    @api.depends('person_id')
    @api.depends_context('allowed_company_ids')
    def _compute_employees(self):
        # картки — ті, що бачить користувач (його фірми; з архівними): чужа фірма в тегах дала б помилку доступу на всю форму.
        # Поле — лише кадрам (groups): Odoo 19 перевіряє право читання hr.employee навіть при записі порожнього значення в обчислюване
        # поле x2many, і форма ставки в редактора без прав кадрів падала (AccessError, перша збірка Odoo.sh 06.11)
        Emp = self.env['hr.employee'].with_context(active_test=False)
        for r in self:
            cards = Emp.browse()
            try:
                if r.person_id:
                    cards = Emp.search([('work_contact_id', '=', r.person_id.id)], order='company_id, id')
            except AccessError:
                cards = Emp.browse()
            r.employee_ids = cards

    @api.depends('person_id')
    @api.depends_context('lang', 'allowed_company_ids')
    def _compute_employee_summary(self):
        # «Фірма · діє / архів» карток людини у фірмах користувача (фірми контексту — як правило доступу карток), без поля hr.employee:
        # рядок бачить і редактор ставок без прав кадрів. Повний список усіх фірм — у вкладці «Контроль»
        lang = self._lang()
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False)
        firms = self.env.companies.ids
        for r in self:
            cards = Emp.search([('work_contact_id', '=', r.person_id.id), ('company_id', 'in', firms)], order='company_id, id') \
                if r.person_id else Emp.browse()
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

    @api.depends('control_html_uk', 'control_html_ru')
    @api.depends_context('lang')
    def _compute_control_html(self):
        ru = self._lang() == 'ru'
        for r in self:
            r.control_html = (r.control_html_ru if ru else r.control_html_uk) or False

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

    @api.constrains('is_own', 'own_reason')
    def _check_own(self):
        lang = self._lang()
        for r in self:
            if r.is_own and not (r.own_reason or '').strip():
                raise ValidationError(calc.t('own_reason', lang) % calc.money(r.rule_price))

    @api.constrains('agent_own', 'agent_own_reason')
    def _check_agent_own(self):
        lang = self._lang()
        for r in self:
            if r.agent_own and not (r.agent_own_reason or '').strip():
                raise ValidationError(calc.t('agent_own_reason', lang))

    @api.constrains('client_price', 'client_price_source', 'client_price_date', 'price', 'fixed_month', 'agent_own_value')
    def _check_chain_money(self):
        lang = self._lang()
        for r in self:
            neg = [r._fields[f].string for f in ('price', 'fixed_month', 'client_price', 'agent_own_value') if (r[f] or 0) < 0]
            if neg:
                raise ValidationError(calc.t('negative', lang) % ', '.join(neg))
            if r.client_price and not ((r.client_price_source or '').strip() and r.client_price_date):
                raise ValidationError(calc.t('client_src', lang))

    # ——— А1: створення, правка полів-тригерів, видалення ———
    def _no_sync(self):
        # «без автоматики» (тести, службові правки) — лише суперкористувачу або адміністратору; іншим контекст ігнорується
        return bool(self.env.context.get('workset_rates_no_sync')) and self._is_admin()

    @staticmethod
    def _mark_client(vals):
        # ціну клієнта ввели в самій ставці — вона «своя» в ставки (правило її більше не підставляє); очистили — знову з правила
        if 'client_price' in vals and 'client_price_manual' not in vals:
            vals = dict(vals, client_price_manual=bool(vals['client_price']))
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        self = self._strip_internal()
        vals_list = [self._mark_client(v) for v in vals_list]
        recs = super().create(vals_list)
        if not self._no_sync():
            # А1+: ланцюжок (на руки, правило, «Своя» — якщо Ц / тип / фікс передали явно й вони ≠ правилу), далі А1 М1
            data = self._chain_data()
            for rec, vals in zip(recs, vals_list):
                rec._chain(data, full=True, edited=bool(set(calc.OWN_FIELDS) & set(vals)), payer_edited='people_agent_payer' in vals)
            recs._after_change({}, set())
        return recs

    def _strip_internal(self):
        # контекст INTERNAL (службові поля ланцюжка без автоматики) — лише суперкористувачу (код модуля пише ним через sudo()); від
        # користувача (RPC) він знімається, щоб редактор не обійшов А1 і ланцюжок — і щоб не потрапив у вкладені sudo-записи
        if self.env.context.get(INTERNAL) and not self.env.su:
            return self.with_context(**{INTERNAL: False})
        return self

    def write(self, vals):
        self = self._strip_internal()
        if self.env.context.get(INTERNAL):
            return super().write(vals)
        vals = self._mark_client(vals)
        if self._no_sync() or not (CHAIN_TRIGGERS & set(vals)):
            return super().write(vals)
        old = {}
        for r in self:
            old[r.id] = {'person': r.person_id.id, 'cust': cust_of(r.customer_id), 'state': r.state, 'wage': r.wage}
        res = super().write(vals)
        self._chain(full=True, edited=bool(set(calc.OWN_FIELDS) & set(vals)), payer_edited='people_agent_payer' in vals)
        self._after_change(old, set(vals))
        return res

    def action_back_to_rule(self):
        """«Повернути до загальної»: «Своя» знімається, числа — з правила."""
        self.check_access('write')
        self.write({'is_own': False, 'own_reason': False})
        return True

    def action_new_raise(self):
        """«Тимчасова зміна» / «Додати»: нова зміна цієї пари у вікні (нічого не зберігає; «Джерело» порожнє)."""
        self.ensure_one()
        self.env[RAISE].check_access('create')
        return {'type': 'ir.actions.act_window', 'name': calc.t('s_raise', self._lang()), 'res_model': RAISE, 'view_mode': 'form',
                'views': [[False, 'form']], 'target': 'new',
                'context': {'default_person_id': self.person_id.id, 'default_customer_id': self.customer_id.id,
                            'default_date_from': calc.iso(self._today()), 'default_kind': 'hour'}}

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
        return {'id': m.id, 'cost': m.cost, 'changed': m.is_cost_changed, 'card_cost': m.employee_id.hourly_cost or 0.0,
                'order_price': m.sale_line_id.price_unit if m.sale_line_id else None,
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

    @api.model
    def _contract_of(self, version):
        """Ставка версії договору — м'яко (поля модуля зарплат може не бути) → {'wage': €/год, 'hourly': погодинна?}. Вид ставки (wage_type)
        є не скрізь: немає поля — погодинна, якщо ставка за годину > 0. Одне місце читання договору для контролю М1 і А6."""
        if not version or not self._has_contract():
            return {'wage': 0.0, 'hourly': False}
        v = version.sudo()
        wage = float(v.hourly_wage or 0.0)
        hourly = (v.wage_type == 'hourly') if 'wage_type' in v._fields else wage > 0
        return {'wage': wage, 'hourly': bool(hourly)}

    def _card_infos(self, person_id, cust_id, maps):
        cards = self.env['hr.employee'].sudo().with_context(active_test=False).search([('work_contact_id', '=', person_id)], order='id asc')
        has_wage = self._has_contract()
        at = maps.employee_id.ids
        Lines = self.env['account.analytic.line'].sudo()
        out = []
        for c in cards:
            ts = Lines.search([('employee_id', '=', c.id), ('project_id.partner_id.commercial_partner_id', '=', cust_id)], order='date desc', limit=1)
            wage = self._contract_of(c.current_version_id)['wage'] if (has_wage and c.current_version_id) else 0.0
            out.append({'id': c.id, 'name': c.name or '', 'company': c.company_id.name or '', 'company_id': c.company_id.id, 'active': c.active,
                        'at_customer': c.id in at, 'last_ts': calc.iso(ts.date) if ts else '', 'wage': (wage or 0.0) if has_wage else None, 'chosen': False,
                        'temporary': bool(c.current_version_id.workset_temporary)})
        return out

    def _raise_now(self, person_id, cust_id, today):
        """Надбавка пари сьогодні: сума тимчасових змін «+€ за годину», що діють (не скасовані), €/год."""
        day = calc.iso(today)
        xs = self.env[RAISE].sudo().search([('person_id', '=', person_id), ('customer_id.commercial_partner_id', '=', cust_id), ('kind', '=', 'hour'),
                                            ('state', '!=', 'cancel'), ('date_from', '<=', day), ('date_to', '>=', day)])
        return round(sum(xs.mapped('amount')), 2)

    def _control(self, rate, cur, maps, other):
        # поля контролю й вкладка «Контроль» обома мовами (пише код; серед тригерів їх немає). Вкладка порівнюється відбитком
        # обох мов — html Odoo чистить
        if cur and rate.id == cur.id:
            infos = [self._map_info(m) for m in maps]
            cards = self._card_infos(rate.person_id.id, cust_of(rate.customer_id), maps)
            card = calc.pick_card(cards)
            if card:
                card['chosen'] = True
            # «Договір ≠ база» мовчить лише при договорі = Р + Н (тимчасова зміна) або позначці «Тимчасово» без надбавки (розділ 5.5)
            excused = bool(card) and card['wage'] is not None and not calc.same_money(card['wage'], rate.wage) and \
                calc.contract_excused(card['wage'], rate.wage, self._raise_now(rate.person_id.id, cust_of(rate.customer_id), self._today()),
                                      card['temporary'])
            vals = calc.control_vals(rate.wage, rate.price, card, infos, self._has_contract(), excused)
            vals['contract_company_id'] = card['company_id'] if card else False
            others = [self._map_info(m) for m in other]
            html = calc.control_html(cards, infos, others, rate.wage, rate.price, 'uk')
            html_ru = calc.control_html(cards, infos, others, rate.wage, rate.price, 'ru')
        else:
            vals = calc.neutral_control()
            vals['contract_company_id'] = False
            html = html_ru = ''
        # відбиток — від обох мов: правка лише російського словника теж переписує вкладку
        vals['control_sig'] = calc.sig(html + html_ru) if html else ''
        diff = {}
        for k, v in vals.items():
            have = rate[k].id if k == 'contract_company_id' else rate[k]
            if isinstance(v, float) or isinstance(have, float):
                if not calc.same_money(have, v):
                    diff[k] = v
            elif (have or False) != (v or False):
                diff[k] = v
        # відбиток той самий, а мовних полів ще немає (оновлення до 19.0.2: був один control_html) — теж записати
        if 'control_sig' in diff or (html and not (rate.control_html_uk and rate.control_html_ru)):
            diff['control_html_uk'] = html or False
            diff['control_html_ru'] = html_ru or False
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
        # рядки, де за RECENT_DAYS днів є табелі цієї картки в будь-якому проєкті цього замовника. Вікно — від опорної дати «Працює»
        # (на нейтралізованій копії табелі обриваються на даті вивантаження — як і «Працює», інакше копія через 60 днів показала б порожньо)
        if not maps:
            return []
        cutoff = calc.add_days(self._work_ref(today), -calc.RECENT_DAYS)
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

    def _norate_pairs(self, today, have=None):
        """«Працюють без ставки» (звірка «Проєкти без ставки» і тривога «Огляду»): людина з табелями за RECENT_DAYS днів (від опорної дати
        «Працює») у проєктах периметра із замовником, а діючої ставки пари «людина × замовник» немає — незалежно від того, чи є вона в карті
        проєкту. Раніше звірка дивилась лише на рядки карти й людину, яку не внесли в карту проєкту, не бачила.
        → calc.norate_rows: [{'person', 'cust', 'hours', 'last', 'aal', 'cards': [(картка, проєкт)]}]."""
        have = self._current_keys() if have is None else have
        cutoff = calc.add_days(self._work_ref(today), -calc.RECENT_DAYS)
        AAL = self.env['account.analytic.line'].sudo().with_context(active_test=False)
        groups = AAL._read_group([('date', '>=', cutoff), ('employee_id', '!=', False)] + self._perimeter_domain(), ['employee_id', 'project_id'],
                                 ['unit_amount:sum', 'date:max', 'id:max'])
        rows = [{'employee': emp.id, 'project': proj.id, 'hours': hours, 'last': calc.iso(last), 'aal': aal} for emp, proj, hours, last, aal in groups if emp and proj]
        emps = self.env['hr.employee'].sudo().with_context(active_test=False).browse(sorted({r['employee'] for r in rows}))
        projects = self.env['project.project'].sudo().with_context(active_test=False).browse(sorted({r['project'] for r in rows}))
        return calc.norate_rows(rows, set(have), {e.id: e.work_contact_id.id for e in emps}, {p.id: cust_of(p.partner_id) for p in projects})

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
                                                 'default_price_mode': r.price_mode or 'hour', 'default_fixed_month': r.fixed_month,
                                                 'default_is_own': r.is_own, 'default_own_reason': r.own_reason or False,
                                                 'default_people_agent_payer': (r.people_agent_payer if r.people_agent_payer_manual else False) or False,
                                                 'default_agent_own': r.agent_own, 'default_agent_own_value': r.agent_own_value,
                                                 'default_agent_own_reason': r.agent_own_reason or False,
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
        # М2: ще середня чиста частка, «Нижче мінімуму» (рівень частки або чистої — низький), «Тимчасових» (пара має зміну, що діє сьогодні)
        # і «Договір ≠ база» (діюча ставка не збігається з договором — той самий прапорець, що фільтр «≠ договір»)
        rows = self.search_read(list(domain or []), ['work', 'wage', 'price', 'our_share', 'net_share', 'share_level', 'net_level', 'person_id', 'customer_id',
                                                     'state', 'contract_ok'])
        today = calc.iso(self._today())
        partners = self.env['res.partner'].browse([r['customer_id'][0] for r in rows if r['customer_id']])
        commercial = {p.id: p.commercial_partner_id.id for p in partners}
        live = set()
        if rows:
            for x in self.env[RAISE].sudo().search([('state', '!=', 'cancel'), ('date_from', '<=', today), ('date_to', '>=', today),
                                                    ('person_id', 'in', list({r['person_id'][0] for r in rows if r['person_id']}))]):
                live.add((x.person_id.id, cust_of(x.customer_id)))
        s = calc.summary([{'work': r['work'], 'wage': r['wage'], 'price': r['price'], 'share': r['our_share'], 'net': r['net_share'],
                           'low': 'low' in (r['share_level'], r['net_level']),
                           'contract': r['state'] == 'current' and not r['contract_ok'],
                           'raise': bool(r['person_id'] and r['customer_id']) and (r['person_id'][0], commercial.get(r['customer_id'][0], 0)) in live}
                          for r in rows])
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

    # ——— А6 «договір → база» (розділ 5 спеціфікації М2): читає версії договору, пише лише ставки ———
    def _max_wage(self):
        return calc.parse_float(self._param(calc.P_MAX_WAGE)) or calc.MAX_HOURLY_WAGE

    def _temp_window(self):
        return int(calc.parse_float(self._param(calc.P_TEMP_WINDOW))) or calc.TEMP_WINDOW_DAYS

    def _a6_mode(self):
        """Вимикач А6 (параметр workset_rates.a6_mode): 'off' — немає параметра; 'dry' — ніч лише рахує; 'on' — діє."""
        return calc.a6_mode(self._param(calc.P_A6_MODE))

    @api.model
    def _local_day(self, moment):
        """Дата-час Odoo (UTC без поясу) → «РРРР-ММ-ДД» за Києвом (день створення задачі доплат)."""
        if not moment:
            return ''
        return calc.iso(moment.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(calc.TZ)).date())

    def _contract_card(self, person_id, cust_id):
        """Картка договору пари — та сама, що «У договорі» М1 (calc.pick_card: через неї людина працює в цього замовника) → hr.employee."""
        maps, _other = self._pair_maps(person_id, cust_id)
        card = calc.pick_card(self._card_infos(person_id, cust_id, maps))
        return self.env['hr.employee'].sudo().with_context(active_test=False).browse(card['id'] if card else [])

    def _full_rate_on(self, person_id, cust_id, day):
        """«Повна ставка» зайнятості людини на day: версія договору картки договору пари (та сама, що «У договорі»), що діє на day
        («Дата версії» ≤ day, найпізніша), позначена workset_full_rate. Картки або версії немає — неповна (False)."""
        card = self._contract_card(person_id, cust_id)
        if not card:
            return False
        ver = self.env['hr.version'].sudo().search([('employee_id', '=', card.id), ('date_version', '<=', calc.iso(day))],
                                                   order='date_version desc, id desc', limit=1)
        return bool(ver.workset_full_rate)

    def _pairs_of_card(self, emp):
        """Пари «людина × замовник» (є ставки), де ця картка — картка договору → [(контакт, комерційний замовник)]."""
        person = emp.work_contact_id.id
        if not person:
            return []
        custs = calc_uniq([cust_of(r.customer_id) for r in self.env[RATE].sudo().search([('person_id', '=', person)])])
        return [(person, c) for c in custs if self._contract_card(person, c) == emp]

    def _contract_cards(self):
        """Картки договору всіх пар із чинною ставкою → {(контакт, замовник): (картка, чинна ставка)}."""
        out = {}
        for r in self.env[RATE].sudo().search([('state', '=', 'current')], order='id'):
            key = (r.person_id.id, cust_of(r.customer_id))
            if key[0] and key[1] and key not in out:
                out[key] = (self._contract_card(key[0], key[1]), r)
        return out

    def _contract_copy(self, prev, version, day, wage, firm, lang):
        """Значення нової ставки з дати версії: «На руки» з договору, решта — копія попередньої. Ціна: «Своя» — та сама (і «Своя» лишається),
        правила на дату немає — та сама (фікс за місяць — = на руки); інакше ціну поставить ланцюжок за якорем (calc.rule_numbers)."""
        vals = {'person_id': prev.person_id.id, 'customer_id': prev.customer_id.id, 'line_id': prev.line_id.id or False, 'found_by': prev.found_by or False,
                'wage': wage, 'date_from': day, 'source': 'contract', 'source_note': calc.t('a6_src', lang) % (calc.fmt_date(day), firm or '—'),
                'wage_source': 'contract', 'contract_version_id': version.id}
        rid = calc.rule_pick(self._rules_index(), prev.line_id.id, cust_of(prev.customer_id), day)
        if prev.is_own:
            vals.update({'price': prev.price, 'price_mode': prev.price_mode or 'hour', 'fixed_month': prev.fixed_month, 'is_own': True,
                         'own_reason': prev.own_reason})
        elif not rid:
            mode = prev.price_mode or 'hour'
            vals.update({'price': wage if mode == 'fixed_month' else prev.price, 'price_mode': mode, 'fixed_month': prev.fixed_month})
        if prev.client_price_manual:
            vals.update({'client_price': prev.client_price, 'client_price_source': prev.client_price_source, 'client_price_date': prev.client_price_date})
        if prev.people_agent_payer_manual:
            vals['people_agent_payer'] = prev.people_agent_payer
        return vals

    def _successor_vals(self, day, wage):
        """Нова ставка пари з day — копія цієї (як «Нова ставка з дати»), «Зарплата працівника» — wage. Ціна, тип і фікс — лише в «Своєї»
        (і «Своя» лишається з причиною) та коли загальної ставки на day немає (фікс за місяць — ціна = зарплата); інакше ціну поставить
        ланцюжок за правилом (надбавка — зарплата + частка, тариф — та сама). Ціна клієнта, «Хто платить агенту» й посередник — лише ручні.
        Джерела ставки тут немає — дописує той, хто створює. → vals для create."""
        self.ensure_one()
        prev = self
        vals = {'person_id': prev.person_id.id, 'customer_id': prev.customer_id.id, 'line_id': prev.line_id.id or False, 'found_by': prev.found_by or False,
                'wage': wage, 'date_from': day}
        rid = calc.rule_pick(self._rules_index(), prev.line_id.id, cust_of(prev.customer_id), day)
        mode = prev.price_mode or 'hour'
        if prev.is_own:
            vals.update({'price': prev.price, 'price_mode': mode, 'fixed_month': prev.fixed_month, 'is_own': True, 'own_reason': prev.own_reason})
        elif not rid:
            vals.update({'price': wage if mode == 'fixed_month' else prev.price, 'price_mode': mode, 'fixed_month': prev.fixed_month})
        if prev.client_price_manual:
            vals.update({'client_price': prev.client_price, 'client_price_source': prev.client_price_source, 'client_price_date': prev.client_price_date})
        if prev.people_agent_payer_manual:
            vals['people_agent_payer'] = prev.people_agent_payer
        if prev.agent_own:
            vals.update({'agent_own': True, 'agent_own_value': prev.agent_own_value, 'agent_own_reason': prev.agent_own_reason})
        return vals

    def _link_temporary(self, version, pairs, firm, dry):
        """Версію позначено «Тимчасово»: ставка не змінюється; тимчасова зміна пари на дату версії без версії — зв'язати. → число зв'язаних."""
        day = calc.iso(version.date_version)
        lang = self._lang()
        n = 0
        Raise = self.env[RAISE].sudo()
        for person, cust in pairs:
            if Raise.search_count([('version_id', '=', version.id), ('person_id', '=', person)]):
                continue
            x = Raise.search([('person_id', '=', person), ('customer_id.commercial_partner_id', '=', cust), ('state', '!=', 'cancel'),
                              ('date_from', '<=', day), ('date_to', '>=', day), ('version_id', '=', False)], order='date_from desc, id desc', limit=1)
            if x:
                n += 1
                if not dry:
                    x.write({'version_id': version.id})
                    x.message_post(body=calc.t('a6_link', lang) % (calc.fmt_date(day), firm or '—'), message_type='comment', subtype_xmlid='mail.mt_note')
        return n

    def _contract_fallback(self, rate, version, skip, max_wage):
        """Інша діюча версія договору людини на «Ставка з» ставки (не version, не «Тимчасово», картка в периметрі, погодинна й ≤ поріг) —
        до неї повертається ставка, коли version перестала бути базою (calc.contract_fallback; ревю № 3 М2b). → hr.version або порожньо."""
        Version = self.env['hr.version'].sudo()
        if not rate.person_id or not rate.date_from:
            return Version.browse()
        found = Version.search([('employee_id.work_contact_id', '=', rate.person_id.id), ('date_version', '=', rate.date_from),
                                ('active', '=', True), ('id', '!=', version.id)])
        rows = []
        for v in found:
            info = self._contract_of(v)
            rows.append({'id': v.id, 'ok': not v.workset_temporary and v.employee_id.company_id.id not in skip
                         and calc.wage_ok(info['hourly'], info['wage'], max_wage)})
        return Version.browse(calc.contract_fallback(rows, version.id) or [])

    def _contract_follow(self, version, skip, max_wage, dry, lang):
        """Ставка йде за своєю версією договору: ставки з «Версія договору» = version і «На руки з» = «Договір», що вже не база
        (calc.contract_gone: версію архівовано, позначено «Тимчасово», картка поза периметром, ставка версії не годиться, дату версії
        перенесли): на ту саму дату діє інша версія договору людини — ставка повертається до неї (_contract_fallback); інакше створену А6
        (source = contract) — в архів; внесену інакше, де А6 лише переписав «На руки» на ту саму дату, — назад прежнє «На руки»
        (wage_before_contract), а немає його — «Перевірити» з причиною (calc.contract_undo). А1 перераховує пару й карту проєктів; причина —
        в історії ставки. → {'archived', 'restored', 'check'} (повернення — і до іншої версії, і до прежнього «На руки»)."""
        out = {'archived': 0, 'restored': 0, 'check': 0}
        rates = self.env[RATE].sudo().search([('contract_version_id', '=', version.id), ('wage_source', '=', 'contract')])
        if not rates:
            return out
        day = calc.iso(version.date_version)
        emp = version.employee_id
        info = self._contract_of(version)
        for r in rates:
            why = calc.contract_gone(version.active, version.workset_temporary, bool(emp) and emp.company_id.id not in skip,
                                     calc.wage_ok(info['hourly'], info['wage'], max_wage), day, calc.iso(r.date_from))
            if not why:
                continue
            other = self._contract_fallback(r, version, skip, max_wage)
            undo = 'fallback' if other else calc.contract_undo(r.source, r.wage_before_contract)
            out['archived' if undo == 'archive' else ('check' if undo == 'check' else 'restored')] += 1
            if dry:
                continue
            words = calc.t('a6_why_' + why, lang) % calc.fmt_date(day or r.date_from)
            back = {'wage_source': r.wage_source_before or 'manual', 'contract_version_id': False, 'wage_before_contract': 0.0,
                    'wage_source_before': False}
            if undo == 'fallback':
                # на ту саму дату діє інша версія — ставка лишається «з договору», лише за нею (прежнє «На руки» до договору — те саме)
                wage = self._contract_of(other)['wage']
                body = calc.t('a6_fallback', lang) % (words, calc.fmt_date(r.date_from), other.employee_id.company_id.name or '—',
                                                      calc.money(r.wage), calc.money(wage))
                r.write({'wage': wage, 'contract_version_id': other.id})
            elif undo == 'archive':
                if why == 'moved':
                    body = calc.t('a6_gone_moved', lang) % (calc.fmt_date(r.date_from), calc.fmt_date(day))
                else:
                    body = calc.t('a6_gone_' + why, lang) % calc.fmt_date(day or r.date_from)
                r.write({'active': False})
            elif undo == 'restore':
                body = calc.t('a6_restored', lang) % (words, calc.money(r.wage), calc.money(r.wage_before_contract))
                r.write(dict(back, wage=r.wage_before_contract))  # «На руки» — тригер: А1 поверне карту проєктів
            else:
                body = calc.t('a6_check', lang) % words
                r.with_context(**{INTERNAL: True}).write(dict(back, to_check=True, check_reason=body))
            r.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return out

    @api.model
    def _contract_to_base(self, versions):
        """А6: кожна версія договору → ставки пар, де її картка — картка договору (розділ 5.2):
        1) картка в периметрі, версія діюча, є робочий контакт; позначена «Тимчасово» — ставка не змінюється, лише зв'язок із тимчасовою зміною;
        2) погодинна й 0 < ставка ≤ поріг (параметр) — інакше нічого (видно у «Звірці»);
        3) ставка пари на дату версії: те саме «На руки» — лише «Версія договору»; та сама «Ставка з» — нова «На руки» в ній; інакше — нова
           ставка з дати версії (calc.contract_action); ціна — за якорем ланцюжком; запис в історію ставки.
        0) ставки, створені з цієї версії (_contract_follow), що вже не база (версію позначили, архівували, перенесли дату …), — в архів.
        Сухий режим (rates_dry, адміністратор) — лише рахує. → {'versions', 'created', 'updated', 'linked', 'temp', 'archived', 'restored',
        'check', 'rates'} (restored — повернуто «На руки»: прежнє або з іншої діючої версії на ту саму дату; check — «Перевірити»)"""
        res = {'versions': 0, 'created': 0, 'updated': 0, 'linked': 0, 'temp': 0, 'archived': 0, 'restored': 0, 'check': 0, 'rates': []}
        if not self._has_contract():
            return res
        dry = self._dry()
        skip = self._skip_companies()
        max_wage = self._max_wage()
        lang = self._lang()
        R = self.env[RATE].sudo()
        for v in versions.sudo().with_context(active_test=False):
            emp = v.employee_id
            for k, n in self._contract_follow(v, skip, max_wage, dry, lang).items():
                res[k] += n
            if not v.active or not v.date_version or not emp or not emp.work_contact_id or emp.company_id.id in skip:
                continue
            pairs = self._pairs_of_card(emp)
            if not pairs:
                continue
            res['versions'] += 1
            day = calc.iso(v.date_version)
            firm = emp.company_id.name or ''
            if v.workset_temporary:
                res['temp'] += self._link_temporary(v, pairs, firm, dry)
                continue
            info = self._contract_of(v)
            if not calc.wage_ok(info['hourly'], info['wage'], max_wage):
                continue
            for person, cust in pairs:
                rates = self._pair_rates(person, cust)
                act = calc.contract_action([{'id': r.id, 'date_from': calc.iso(r.date_from), 'wage': r.wage} for r in rates], day, info['wage'])
                if act['kind'] == 'skip':
                    continue
                rate = R.browse(act['rate'])
                if act['kind'] == 'link':
                    # ставка «з договору» з іншою датою належить своїй версії — не перев'язувати; ручна й на дату версії — лише на новішу
                    # (дві версії з тим самим «На руки» не переписують одна одну щоночі)
                    was = rate.contract_version_id
                    if calc.contract_link(rate.wage_source, calc.iso(rate.date_from), day, calc.iso(was.date_version) if was else '', was.id, v.id):
                        res['linked'] += 1
                        if not dry:
                            rate.with_context(**{INTERNAL: True}).write({'contract_version_id': v.id})
                    continue
                res['updated' if act['kind'] == 'update' else 'created'] += 1
                if dry:
                    continue
                old = {'wage': rate.wage, 'price': rate.price, 'share': rate.our_share}
                if act['kind'] == 'update':
                    vals = {'wage': info['wage'], 'wage_source': 'contract', 'contract_version_id': v.id}
                    if rate.source != 'contract' and rate.wage_source != 'contract':
                        # ставку внесли не з договору — запам'ятати її «На руки», щоб повернути, коли договір перестане бути базою
                        vals.update({'wage_before_contract': rate.wage, 'wage_source_before': rate.wage_source or 'manual'})
                    rate.write(vals)
                    target = rate
                else:
                    target = R.create(self._contract_copy(rate, v, day, info['wage'], firm, lang))
                new = {'wage': target.wage, 'price': target.price, 'share': target.our_share, 'low': 'low' in (target.share_level, target.net_level)}
                body = calc.contract_note(old, new, day, firm, calc.contract_kind(target.is_own, target.price_mode, target.anchor), lang)
                target.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
                res['rates'].append(target.id)
        return res

    @api.model
    def rates_wage_from_contract(self, ids=None, apply=False):
        """«Зарплата за договором» (слово власника 06.11; інструмент штабу --wage-from-contract — і після переносу на прод: перенос бере
        «Зарплата працівника» з підпису рядка замовлення й повторює помилку там, де договір підвищили): діючі ставки (ids — лише ці), у яких
        погодинна ставка поточної версії договору картки договору пари (та сама, що «У договорі»; не «Тимчасово», погодинна й ≤ поріг) ≠
        «Зарплата працівника», а дата версії пізніше за «Ставка з». План — «людина, замовник, було → стало, з дати версії»; apply — нова ставка
        з дати версії (на ту саму дату ставка вже є — «Зарплата працівника» в ній), «На руки з» — «Договір»; «Платять нам» за якорем
        (calc.contract_raise_price: тариф — та сама, надбавка — + різниця зарплат, і в «Своєї» на надбавці теж). А6 (вимикач) тут не потрібен.
        Лише адміністратор; без модуля зарплат — порожньо. → {'rows': [...], 'created', 'updated', 'dry', 'has_contract'}."""
        self._check_admin()
        out = {'rows': [], 'created': 0, 'updated': 0, 'dry': not apply, 'has_contract': self._has_contract()}
        if not out['has_contract']:
            return out
        lang = self._lang()
        skip = self._skip_companies()
        max_wage = self._max_wage()
        R = self.env[RATE].sudo()
        dom = [('state', '=', 'current')] + ([('id', 'in', [int(i) for i in ids])] if ids else [])
        rules = self._rules_index()
        for r in R.search(dom, order='customer_id, person_id, id'):
            person, cust = r.person_id.id, cust_of(r.customer_id)
            card = self._contract_card(person, cust)
            ver = card.current_version_id if card else self.env['hr.version']
            if not ver or card.company_id.id in skip or ver.workset_temporary:
                continue
            info = self._contract_of(ver)
            day = calc.iso(ver.date_version)
            if not calc.wage_ok(info['hourly'], info['wage'], max_wage) or not day or day <= calc.iso(r.date_from) or calc.same_money(info['wage'], r.wage):
                continue
            act = calc.contract_action([{'id': x.id, 'date_from': calc.iso(x.date_from), 'wage': x.wage} for x in self._pair_rates(person, cust)],
                                       day, info['wage'])
            if act['kind'] not in ('create', 'update'):
                continue
            prev = R.browse(act['rate'])
            rid = calc.rule_pick(rules, prev.line_id.id, cust, day)
            anchor = self.env[RULE].sudo().browse(rid).anchor if rid else ''
            kind = calc.contract_kind(prev.is_own, prev.price_mode, anchor)
            if kind == 'own' and anchor:
                kind = 'own_' + anchor
            price = calc.contract_raise_price(kind, prev.price, prev.wage, info['wage'])
            firm = card.company_id.name or ''
            row = {'rate_id': prev.id, 'person': r.person_id.name or '', 'customer': r.customer_id.name or '', 'firm': firm, 'date': day,
                   'wage_was': prev.wage, 'wage_new': info['wage'], 'price_was': prev.price, 'price_new': price, 'kind': act['kind'],
                   'version_id': ver.id, 'own': bool(prev.is_own)}
            out['rows'].append(row)
            if not apply:
                continue
            old = {'wage': prev.wage, 'price': prev.price, 'share': prev.our_share}
            contract = {'wage_source': 'contract', 'contract_version_id': ver.id}
            if act['kind'] == 'update':
                vals = dict(contract, wage=info['wage'])
                if prev.is_own or not rid:
                    vals['price'] = price
                prev.write(vals)
                target = prev
                out['updated'] += 1
            else:
                vals = prev._successor_vals(day, info['wage'])
                vals.update(contract, source='contract', source_note=calc.t('a6_src', lang) % (calc.fmt_date(day), firm or '—'))
                if 'price' in vals:
                    vals['price'] = price  # «Своя» або без правила — ціна за якорем; інакше ціну ставить ланцюжок за правилом
                target = R.create(vals)
                out['created'] += 1
            new = {'wage': target.wage, 'price': target.price, 'share': target.our_share, 'low': 'low' in (target.share_level, target.net_level)}
            # «Своя» на надбавці теж отримала + різницю — в історії так і пишемо («надбавка», не «платять нам те саме»)
            note_kind = 'markup' if kind == 'own_markup' else calc.contract_kind(target.is_own, target.price_mode, target.anchor)
            target.message_post(body=calc.contract_note(old, new, day, firm, note_kind, lang), message_type='comment', subtype_xmlid='mail.mt_note')
            row.update({'new_rate_id': target.id, 'share_new': target.our_share})
        return out

    @api.model
    def _contract_catchup(self):
        """А3+ п. 4, догон А6 (розділ 5.4): версії, змінені повз методи ORM (імпорт, SQL, чужі модулі), — у картки договору кожної пари з чинною
        ставкою беруться версії з датою не раніше за «Ставка з» чинної (і майбутні) і проходять той самий А6: що вже збігається, А6 пропускає.
        Вимикач (параметр workset_rates.a6_mode): 'off' — нічого; 'dry' — лише розрахунок (скільки ставок створив би); 'on' — діє.
        → підсумок _contract_to_base + 'mode'."""
        mode = self._a6_mode()
        Version = self.env['hr.version'].sudo().with_context(active_test=False)
        versions = Version.browse()
        if mode == 'off':
            return {'versions': 0, 'created': 0, 'updated': 0, 'linked': 0, 'temp': 0, 'archived': 0, 'restored': 0, 'check': 0, 'rates': [],
                    'mode': mode}
        me = self.with_context(rates_dry=True) if mode == 'dry' else self
        if not self._has_contract():
            return dict(me._contract_to_base(versions), mode=mode)
        for (_person, _cust), (card, rate) in self._contract_cards().items():
            if card:
                versions |= Version.search([('employee_id', '=', card.id), ('active', '=', True), ('date_version', '>=', rate.date_from)],
                                           order='date_version, id')
        return dict(me._contract_to_base(versions), mode=mode)

    @api.model
    def _updown_hits(self):
        """Страж «вгору і назад» (розділ 5.5): картки договору пар із чинною ставкою (у периметрі) → збіги calc.up_and_back за вікно
        (параметр), де тимчасової зміни пари на дні між підвищенням і поверненням немає і повернення не раніше першої ставки пари
        (calc.updown_since: давні випадки до ставок — не шум). → [{'person', 'cust', 'card', 'hit', 'rates'}]"""
        if not self._has_contract():
            return []
        window = self._temp_window()
        skip = self._skip_companies()
        Version = self.env['hr.version'].sudo()
        Raise = self.env[RAISE].sudo()
        R = self.env[RATE].sudo()
        out = []
        for (person, cust), (card, _rate) in self._contract_cards().items():
            if not card or card.company_id.id in skip:
                continue
            vers = []
            for v in Version.search([('employee_id', '=', card.id), ('active', '=', True)], order='date_version, id'):
                info = self._contract_of(v)
                if info['hourly'] and info['wage'] > 0:
                    vers.append({'id': v.id, 'date': calc.iso(v.date_version), 'wage': info['wage'], 'temporary': bool(v.workset_temporary)})
            first = R.search(self._pair_domain(person, cust, None), order='date_from asc, id asc', limit=1)
            for hit in calc.updown_since(calc.up_and_back(vers, window), calc.iso(first.date_from)):
                back_last = calc.add_days(hit['b_date'], -1)
                if Raise.search_count([('person_id', '=', person), ('customer_id.commercial_partner_id', '=', cust), ('state', '!=', 'cancel'),
                                       ('date_from', '<=', back_last), ('date_to', '>=', hit['a_date'])]):
                    continue
                rates = R.search(self._pair_domain(person, cust, None) + ['|', ('contract_version_id', 'in', [hit['a'], hit['b']]),
                                                                          ('date_from', 'in', [hit['a_date'], hit['b_date']])])
                out.append({'person': person, 'cust': cust, 'card': card, 'hit': hit, 'rates': rates})
        return out

    def _updown_prefixes(self):
        # початок причини стража обома мовами — за ним нічна перевірка знімає свою позначку, коли збіг зник
        return tuple(calc.TX['updown_reason'][i].split(':')[0] for i in (0, 1))

    @api.model
    def _updown_mark(self, hits):
        """Ставки зі збігом стража → «Перевірити» з причиною; позначка стража, якої збіг уже не підтверджує (оформили тимчасовою зміною,
        позначили версію), — знімається. Ставку з чужою причиною (перенос тощо, calc.updown_foreign) страж не чіпає: ні позначки, ні зняття.
        Сухий режим — лише рахує. → {'marked', 'cleared'}"""
        lang = self._lang()
        dry = self._dry()
        prefixes = self._updown_prefixes()
        want = {}
        for h in hits:
            for r in h['rates']:
                want.setdefault(r.id, calc.updown_reason(h['hit'], lang))
        marked = cleared = 0
        R = self.env[RATE].sudo().with_context(active_test=False, **{INTERNAL: True})
        for r in R.browse(list(want)):
            if calc.updown_foreign(r.to_check, r.check_reason, prefixes):
                continue
            if not r.to_check or (r.check_reason or '') != want[r.id]:
                marked += 1
                if not dry:
                    r.write({'to_check': True, 'check_reason': want[r.id]})
        for r in R.search([('to_check', '=', True), ('id', 'not in', list(want))]):
            if (r.check_reason or '').startswith(prefixes):
                cleared += 1
                if not dry:
                    r.write({'to_check': False, 'check_reason': False})
        return {'marked': marked, 'cleared': cleared}

    # ——— А3 «щодня», А4 «звірка», А5 «робота» ———
    def _check_admin(self):
        if not self._is_admin():
            raise AccessError(calc.t('only_admin', self._lang()))

    @api.model
    def _cron_daily(self):
        self.rates_daily()

    @api.model
    def rates_daily(self):
        """А3+: загальні ставки — «Ставка до» і статус; ланцюжок чинних і майбутніх ставок (посередники, агент людей, години минулого
        місяця, бонус офісу, частки — лише змінене; ціни й правило ставки ніч НЕ змінює) → майбутні, в яких настала «Ставка з», → діють;
        рядки карти — лише в пар, де змінився статус (ручну правку ніч не чіпає); пара без чинної — рядки назад на картку; контроль усіх
        ставок → тимчасові зміни: місяці, статуси, одна задача доплат (у rates_dry — пропуск)."""
        self._check_admin()
        assignee = self._assignee()
        lang = calc.lang_of(assignee.lang)
        me = self.with_context(lang=assignee.lang)
        today = self._today()
        dry = self._dry()
        rules = self.env[RULE].sudo()._sync_rules(None, today)
        chained = self.env[RATE].sudo().with_context(lang=assignee.lang).search([('state', 'in', ['current', 'future'])])._chain(
            full=False, today=today)
        moved = updated = reverted = 0
        pairs = me._all_pairs()
        for key in pairs:
            res = me._sync_pair(key[0], key[1], today, 'daily', False)
            moved += 1 if res['changed'] else 0
            updated += res['updated']
            reverted += res['reverted']
        raises = {} if dry else self.env[RAISE].sudo()._raises_daily(calc.iso(today))
        # п. 4–5: догон А6 (версії договору повз ORM; вимикач workset_rates.a6_mode: off — нічого, dry і rates_dry — лише число «скільки
        # ставок створить») і страж «вгору і назад» (лише позначки «Перевірити» — працює за будь-якого режиму А6)
        contract = me._contract_catchup()
        updown = me._updown_mark(me._updown_hits())
        a6_dry = contract['mode'] == 'dry'
        summary = calc.t('daily_sum', lang) % (calc.fmt_date(today), len(pairs), moved, updated, reverted, calc.t('dry', lang) if dry else '')
        if contract['mode'] == 'off':
            contract_msg = calc.t('a6_off', lang)
        else:
            contract_msg = calc.t('contract_sum', lang) % (contract['versions'], contract['created'], contract['updated'], contract['linked'],
                                                            contract['temp'], contract['archived'], contract['restored'], contract['check'],
                                                            calc.t('dry', lang) if dry else (calc.t('a6_dry_mode', lang) if a6_dry else ''))
        a6_would = contract['created'] if (a6_dry or dry) else 0
        if a6_dry:
            # число владельцу до включення А6 (розділ 9): у підсумку ночі й у журналі
            summary += calc.t('a6_would', lang) % (contract['created'], contract['updated'], contract['archived'], contract['restored'],
                                                   contract['check'])
        elif contract['mode'] == 'on' and (contract['restored'] or contract['check']):
            # повернення й «Перевірити» — окремо від архіву (ревю № 3 М2b): людині в підсумку ночі видно, скільки ставок змінив договір назад
            summary += calc.t('a6_back_sum', lang) % (contract['restored'], contract['check'])
        if not dry:
            _logger.info(summary)
            _logger.info(contract_msg)
        return {'pairs': len(pairs), 'moved': moved, 'updated': updated, 'reverted': reverted, 'rules': rules, 'chained': chained,
                'raises': raises, 'contract': {k: v for k, v in contract.items() if k != 'rates'}, 'updown': updown, 'dry': dry,
                'a6_mode': contract['mode'], 'a6_would': a6_would, 'message': summary, 'contract_message': contract_msg}

    @api.model
    def rates_link_rules(self, migration=False):
        """Ставки без «Загальної ставки» (перенос М1; ставки, створені раніше за правила) — до правил, що діють на їхню «Ставка з»:
        «Своя» — Ц (тип, фікс) ≠ правилу, з причиною (calc.migration_rate_vals); ціна 0 (перенесена без ціни) — ціна з правила службовим
        записом; далі ланцюжок без зміни «Своєї»; карта проєктів і замовлення не змінюються (А1 не запускається).
        «На руки з» → «Перенос» (крім «Договір»): migration=True — усім ставкам (перенос 19.0.1 → 19.0.2: усі ставки бази — з М1);
        migration='transfer' — лише ставкам, перенесеним з карти проєктів (джерело «Перенесено з проєкту»; посів прода --seed-m2: повторний
        запуск не чіпає ставок, внесених пізніше руками). Лише адміністратор; rates_dry — тільки числа. → {'rates', 'linked', 'own', 'chained', 'dry'}."""
        self._check_admin()
        Rate = self.env[RATE].sudo().with_context(active_test=False)
        rates = Rate.search([('rule_id', '=', False)])
        rules = Rate._rules_index()
        dry = self._dry()
        linked = own = 0
        for r in rates:
            rid = calc.rule_pick(rules, r.line_id.id, cust_of(r.customer_id), calc.iso(r.date_from))
            mig = migration is True or (migration == 'transfer' and r.source == 'migration')
            if not rid and not mig:
                continue
            by_rule = calc.rule_numbers(Rate._rule_dict(self.env[RULE].browse(rid)), r.wage, r.found_by, r._prev_price()) if rid else None
            vals = calc.migration_rate_vals({'price': r.price, 'price_mode': r.price_mode or 'hour', 'fixed_month': r.fixed_month}, by_rule,
                                            r.agent_share, r.agent_source, r.wage_source)
            if not mig:
                vals.pop('wage_source', None)
            if not rid:
                vals.pop('is_own', None)
            linked += 1 if rid else 0
            own += 1 if vals.get('is_own') else 0
            diff = {k: v for k, v in vals.items() if (r[k] or False) != (v or False)}
            if diff and not dry:
                r.with_context(**{INTERNAL: True}).write(diff)
        chained = rates.with_context(rates_dry=dry)._chain(full=True, edited=False) if rates else 0
        return {'rates': len(rates), 'linked': linked, 'own': own, 'chained': chained, 'dry': dry}

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

        # М2b: картки договору пар — для «Договір ≠ база», «не погодинний / > поріг», тимчасових версій; страж «вгору і назад»
        cards = me._contract_cards()
        max_wage = me._max_wage()
        window = me._temp_window()
        has_contract = me._has_contract()
        wagebad_rows, wagebad = [], set()
        tempver_rows = []
        Raise = self.env[RAISE].sudo()
        for (person, cid), (card, r) in cards.items():
            if not card or not has_contract or card.company_id.id in me._skip_companies():
                continue
            ver = card.current_version_id
            info = me._contract_of(ver)
            if ver and info['wage'] > 0 and not calc.wage_ok(info['hourly'], info['wage'], max_wage):
                wagebad.add(r.id)
                what = calc.t('not_hourly' if not info['hourly'] else 'too_high', lang)
                wagebad_rows.append([rate_cell(r), r.customer_id.name or '', card.company_id.name or '—', '%s · %s €' % (what, calc.money(info['wage']))])
            since = calc.add_days(calc.iso(today), -window)
            for v in self.env['hr.version'].sudo().search([('employee_id', '=', card.id), ('active', '=', True), ('workset_temporary', '=', True),
                                                          ('date_version', '>=', since)], order='date_version'):
                day_v = calc.iso(v.date_version)
                # зміна пов'язана з версією або зміна пари діє на дату версії (не скасована)
                pair_on_day = [('person_id', '=', person), ('customer_id.commercial_partner_id', '=', cid), ('date_from', '<=', day_v), ('date_to', '>=', day_v)]
                if Raise.search_count([('state', '!=', 'cancel'), '|', ('version_id', '=', v.id), '&', '&', '&'] + pair_on_day):
                    continue
                tempver_rows.append([rate_cell(r), r.customer_id.name or '', card.company_id.name or '—', calc.fmt_date(day_v),
                                     calc.money(me._contract_of(v)['wage']) + ' €'])
        hits = me._updown_hits()
        updown_ids = set()
        updown_rows = []
        Partner = self.env['res.partner'].sudo()
        for h in hits:
            updown_ids |= set(h['rates'].ids)
            hit = h['hit']
            first = h['rates'][:1]
            # працівник і замовник — пари збігу (ставок за версіями може й не бути: А6 вимкнено, ставки внесено руками з іншими датами)
            person = rate_cell(first) if first else {'text': Partner.browse(h['person']).name or ''}
            updown_rows.append([person, Partner.browse(h['cust']).name or '', h['card'].company_id.name or '—',
                                '%s → %s · %s' % (calc.money(hit['base']), calc.money(hit['up']), calc.fmt_date(hit['a_date'])),
                                '%s · %s' % (calc.money(hit['back']), calc.fmt_date(hit['b_date']))])
        rules_index = me._rules_index()
        min_default = calc.parse_float(me._param(calc.P_MIN_SHARE))
        contract_rows, mism_rows, order_rows, rule_rows, cur_rows = [], [], [], [], []
        low_rows, found_rows, norule_rows = [], [], []
        for r in cur:
            cust = r.customer_id.name or ''
            maps, other = me._pair_maps(r.person_id.id, cust_of(r.customer_id))
            if not r.contract_ok and r.id not in wagebad:
                contract_rows.append([rate_cell(r), cust, r.contract_company_id.name or '—',
                                      (calc.money(r.contract_wage) + ' €') if r.contract_wage else calc.t('no_wage', lang), calc.money(r.wage) + ' €'])
            if r.map_mismatch > 0:
                mism_rows.append([rate_cell(r), cust, str(r.map_mismatch), calc.money(r.wage) + ' €'])
            if not r.order_price_ok:
                prices = [calc.money(m.sale_line_id.price_unit) + ' €' for m in maps if m.sale_line_id and not calc.same_money(m.sale_line_id.price_unit, r.price)]
                order_rows.append([rate_cell(r), cust, calc.money(r.price) + ' €', ', '.join(calc_uniq(prices))])
            # «Частка WorkSET не за правилом»: частка ≤ 0 — завжди; «Своя» — лише коли загальна ставка змінилась понад OWN_STALE_DAYS днів тому
            now_id = calc.rule_pick(rules_index, r.line_id.id, cust_of(r.customer_id), calc.iso(today))
            now_rule = self.env[RULE].sudo().browse(now_id)
            stale = calc.own_stale(r.is_own, r.rule_id.id, now_id, calc.iso(now_rule.date_from) if now_id else '', calc.iso(today))
            if r.our_share <= 0 or stale:
                what = r.with_context(lang=assignee.lang).rule_text or ''
                if stale:
                    what = '%s → %s: %s' % (calc.t('i_own', lang) % (r.own_reason or '—'), calc.t('c_rule_now', lang), now_rule.name or '')
                rule_rows.append([rate_cell(r), cust, r.line_id.name or '—', calc.money(r.our_share) + ' €', what])
            elif 'low' in (r.share_level, r.net_level):
                low = calc.min_share_of(r.rule_id.sudo().min_share, r.line_id.min_share, min_default)
                low_rows.append([rate_cell(r), cust, r.line_id.name or '—', calc.t('anchor_' + r.anchor, lang) if r.anchor else '—',
                                 calc.money(r.our_share) + ' €', calc.money(r.net_share) + ' €', calc.money(low) + ' €'])
            if not r.rule_id:
                norule_rows.append([rate_cell(r), cust, r.line_id.name or calc.t('no_line_rule', lang)])
            if r.found_missing:
                found_rows.append([rate_cell(r), cust, r.line_id.name or '—'])
            for m in other:
                cur_rows.append([rate_cell(r), cust, m.project_id.display_name or '', m.cost_currency_id.name or ''])
        raise_rows = []
        payers = dict(self.env[RAISE]._fields['payer']._description_selection(me.env))
        for x in Raise.search([('state', 'in', ['current', 'due', 'nohours'])], order='date_to, id'):
            alert = calc.raise_alert(x.state, calc.iso(x.date_to), calc.iso(today))
            if not alert:
                continue
            text = calc.t('ra_' + alert, lang) % calc.fmt_date(x.date_to) if alert == 'ending' else calc.t('ra_' + alert, lang)
            raise_rows.append([{'text': x.person_id.name or '', 'href': calc.link(RAISE, x.id)}, x.customer_id.name or '',
                               '%s – %s' % (calc.fmt_date(x.date_from), calc.fmt_date(x.date_to)), text, payers.get(x.payer, '')])
        have = me._current_keys()
        nomap, nocust = me._nomap_lines(have)
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
        # табелі є, а рядка карти в проєкті немає (людину не внесли в карту) — та сама пара без ставки, рядок «немає в карті проєкту · N год»
        in_map = {(m.employee_id.id, m.project_id.id) for m in nomap}
        Emp = self.env['hr.employee'].sudo().with_context(active_test=False)
        Project = self.env['project.project'].sudo().with_context(active_test=False)
        hours_of = {}
        for p in me._norate_pairs(today, have):
            for emp_id, proj_id in p['cards']:
                if (emp_id, proj_id) not in in_map:
                    hours_of[(emp_id, proj_id)] = p
        for (emp_id, proj_id), p in sorted(hours_of.items(), key=lambda x: (-x[1]['hours'], x[0])):
            emp, proj = Emp.browse(emp_id), Project.browse(proj_id)
            note = calc.t('no_contact', lang) if not emp.work_contact_id else calc.t('not_in_map', lang) % calc.num(p['hours'])
            nomap_rows.append([{'text': proj.display_name or '', 'href': calc.link('project.project', proj.id)}, proj.company_id.name or '',
                               proj.partner_id.commercial_partner_id.name or '—', {'text': emp.name or '', 'href': calc.link('hr.employee', emp.id)},
                               '—', note])
        # позначені стражем — у своєму розділі «Вгору і назад», не тут
        check_rows = [[rate_cell(r), r.customer_id.name or '', r.check_reason or ''] for r in R.search([('to_check', '=', True)], order='customer_id, person_id, id')
                      if r.id not in updown_ids and not (r.check_reason or '').startswith(me._updown_prefixes())]
        # лінія «Очікується посередник», а діючої домовленості на ставку немає — лише коли модель домовленостей у базі є
        agent_rows = []
        deals = me._deals()
        if deals is not None:
            for r in cur.filtered(lambda x: x.line_id.agent_expected and not x.agent_client_share):
                agent_rows.append([rate_cell(r), r.customer_id.name or '', r.line_id.name or '', calc.money(r.agent_client_share) + ' €',
                                   r.agent_client_note or ''])
        # М2c (розділ 3.13): пара «лінія × замовник» з якорем лінії «надбавка» і діючими ставками, а правила житла на сьогодні немає ні
        # замовника, ні лінії (ревю М2c-1, № 6: правило одного замовника не закриває всю лінію); тарифні лінії мовчать
        by_pair = {}
        for r in cur.filtered('line_id'):
            key = (r.line_id.id, cust_of(r.customer_id))
            by_pair[key] = by_pair.get(key, 0) + 1
        lines = self.env['workset.line'].sudo().browse(sorted({k[0] for k in by_pair}))
        anchors = {x.id: x.anchor for x in lines}
        housing_rows = []
        for lid, cid in calc.housing_missing([{'line_id': k[0], 'customer_id': k[1], 'anchor': anchors.get(k[0]), 'rates': n}
                                              for k, n in sorted(by_pair.items())],
                                             self.env['workset.line.housing']._rules(), calc.iso(today)):
            ln = lines.browse(lid)
            housing_rows.append([{'text': ln.name or '', 'href': calc.link('workset.line', ln.id)},
                                 self.env['res.partner'].sudo().browse(cid).name or '', str(by_pair[(lid, cid)])])
        days = calc.RECENT_DAYS
        t = lambda k: calc.t(k, lang)  # noqa: E731
        cols_map = [t('c_project'), t('c_firm'), t('c_customer'), t('c_card'), t('c_cost'), t('c_note')]
        cols_pc = [t('c_person'), t('c_customer'), t('c_cfirm')]
        sections = [
            {'key': 'contract', 'title': t('s_contract'), 'hint': t('h_contract'), 'cols': [t('c_person'), t('c_customer'), t('c_cfirm'), t('c_contract'), t('c_wage')], 'rows': contract_rows},
            {'key': 'wagebad', 'title': t('s_wagebad') % calc.money(max_wage), 'hint': t('h_wagebad'), 'cols': cols_pc + [t('c_contract')], 'rows': wagebad_rows},
            {'key': 'tempver', 'title': t('s_tempver'), 'hint': t('h_tempver'), 'cols': cols_pc + [t('c_version'), t('c_contract')], 'rows': tempver_rows},
            {'key': 'updown', 'title': t('s_updown'), 'hint': t('h_updown') % window, 'cols': cols_pc + [t('c_up'), t('c_back')], 'rows': updown_rows},
            {'key': 'low', 'title': t('s_low'), 'hint': t('h_low'),
             'cols': [t('c_person'), t('c_customer'), t('c_line'), t('c_anchor'), t('c_share'), t('c_net'), t('c_min')], 'rows': low_rows},
            {'key': 'raise', 'title': t('s_raise'), 'hint': t('h_raise') % calc.RAISE_SOON_DAYS,
             'cols': [t('c_person'), t('c_customer'), t('c_dates'), t('c_what'), t('c_payer')], 'rows': raise_rows},
            {'key': 'mismatch', 'title': t('s_mism'), 'hint': t('h_mism'), 'cols': [t('c_person'), t('c_customer'), t('c_rows'), t('c_wage')], 'rows': mism_rows},
            {'key': 'nomap', 'title': t('s_nomap') % days, 'hint': t('h_nomap') % (stale, days, days), 'cols': cols_map, 'rows': nomap_rows, 'more': t('more_nomap')},
            {'key': 'nocust', 'title': t('s_nocust') % days, 'hint': t('h_nocust'), 'cols': cols_map, 'rows': nocust_rows},
            {'key': 'currency', 'title': t('s_cur'), 'hint': t('h_cur'), 'cols': [t('c_person'), t('c_customer'), t('c_project'), t('c_cur')], 'rows': cur_rows},
            {'key': 'order', 'title': t('s_order'), 'hint': t('h_order'), 'cols': [t('c_person'), t('c_customer'), t('c_rprice'), t('c_orders')], 'rows': order_rows},
            {'key': 'rule', 'title': t('s_rule'), 'hint': t('h_rule') % calc.OWN_STALE_DAYS, 'cols': [t('c_person'), t('c_customer'), t('c_line'), t('c_share'), t('c_rule')], 'rows': rule_rows},
            {'key': 'norule', 'title': t('s_norule'), 'hint': t('h_norule'), 'cols': [t('c_person'), t('c_customer'), t('c_line')], 'rows': norule_rows},
            {'key': 'found', 'title': t('s_found'), 'hint': t('h_found'), 'cols': [t('c_person'), t('c_customer'), t('c_line')], 'rows': found_rows},
            {'key': 'check', 'title': t('s_check'), 'hint': t('h_check'), 'cols': [t('c_person'), t('c_customer'), t('c_what')], 'rows': check_rows},
            {'key': 'agent', 'title': t('s_agent'), 'hint': t('h_agent'), 'cols': [t('c_person'), t('c_customer'), t('c_line'), t('c_ashare'), t('c_asrc')], 'rows': agent_rows},
            {'key': 'housing', 'title': t('s_housing'), 'hint': t('h_housing'), 'cols': [t('c_line'), t('c_customer'), t('c_rates')], 'rows': housing_rows},
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
