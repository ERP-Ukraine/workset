# -*- coding: utf-8 -*-
"""Спільне для моделей «Агентів»: списки значень (ключі — як у ручній збірці), підказки, групи, системні параметри.

Підписи — українські (офіс і власник), російські — i18n/ru.po (збирає tools/odoo_addon_po.py). Ключі значень не міняються: їх читають адаптер
«Ставок», інструмент переносу, сторож і дашборди."""
import datetime
from zoneinfo import ZoneInfo

from odoo.exceptions import UserError

from . import calc

YEAR_LABEL = 'за рік'  # підписи сум року картки: рік — поточний за Києвом або параметр card_year (card_year(env))
# було: AGENT_YEAR = 2026# рік картки агента (власнику важливий 2026; так і в ручній збірці)

SECTIONS = [('people', calc.SECTION_LABELS['people']), ('client', calc.SECTION_LABELS['client'])]
DEAL_STATES = [('draft', 'Чернетка'), ('zvirka', 'Звірка'), ('active', 'Діє'), ('closed', 'Закрито')]
DEAL_MODES = [('pay', 'Платимо з Odoo'), ('zvirka', 'Лише звірка')]
MODE_HELP = ('Лише звірка: Odoo рахує і звіряє з конвертом, виплату не готує — платять поза Odoo, як і раніше. '
             'Платимо з Odoo: нарахування йдуть у рішення про виплату')
AGENT_KINDS = [('people', 'Кандидати'), ('client', 'Замовники'), ('both', 'Кандидати і замовники'), ('none', 'Без домовленостей')]
AGENT_MODES = [('pay', 'Платимо'), ('zvirka', 'Лише звірка'), ('draft', 'Чернетка'), ('closed', 'Закрито'), ('none', 'Немає домовленостей')]
PD_KINDS = [('v17', 'Договір v1.7, рубежі'), ('hour', '€ за годину його людей'), ('oneoff', 'Разова')]
CD_KINDS = [('hour', '€ за годину у його замовників'), ('fixed', 'Фікс-аванс із сальдо'), (calc.FUND_KIND, 'Фонд працівників')]
FUND_FULL_HELP = ('Фонд працівників: € за годину людини, у версії договору якої на дату роботи відмічено «Повна ставка» (модуль «Ставки»); '
                  'порожньо — усі години за неповною')
MONTHLY_DAYS = [120 + 30 * i for i in range(21)]
ACC_KINDS = ([('m30', 'Рубіж 30 днів'), ('m60', 'Рубіж 60 днів'), ('m90', 'Рубіж 90 днів')]
             + [('m%d' % d, 'Щомісячна, день %d' % d) for d in MONTHLY_DAYS]
             + [('hour', '€ за годину'), ('oneoff', 'Разова'), ('storno', 'Сторно')])
ACC_STATES = [('expected', 'Очікується'), ('review', 'На перевірці'), ('due', 'До виплати'), ('in_bill', 'У рахунку'), ('decided', 'Закрито рішенням'),
              ('paid', 'Виплачено'), ('burned', 'Згоріло'), ('cancelled', 'Скасовано')]
PAYOUT_STATES = [('prep', 'Готуємо'), ('docs', 'Чекаємо документи'), ('bill', 'Рахунок готовий'), ('paid_wait', 'Сплачено, чекаємо виписку'),
                 ('paid', 'Виплачено'), ('paid_part', 'Виплачено частково'), ('review', 'На перевірці'), ('cancelled', 'Скасовано')]
PAID_VIA = [('statement', 'Виписка'), ('ws', 'Журнал WS'), ('other', 'Інше')]
TAX_STATUSES = [('fop', 'ФОП'), ('company', 'Фірма'), ('person', 'Фізична особа')]
FORM_STATES = [('none', 'Анкети немає'), ('partial', 'Заповнена частково'), ('done', 'Заповнена')]
PAY_PATHS = [('personal_36', 'Особисто · конверт'), ('official_1', 'Офіційно · WorkSET SP. Z O.O.'), ('custom', 'За запитом')]
PAY_PATH_HELP = ('Особисто · конверт — старі домовленості: утримання 10 %, з особистих рахунків директорів, облік у фірмі особистих переказів, '
                 'переказує офіс за дорученням. Офіційно · WorkSET SP. Z O.O. — договір v1.7: фірма офіційних виплат, інвойс або rachunek, '
                 '20 % за п. 7.2. За запитом — окреме оформлення для важливого агента')
DEC_STATES = [('draft', 'Чернетка'), ('approved', 'Затверджено'), ('review', 'На перевірці'), ('done', 'Переказано'), ('skipped', 'Пропущено')]
LINE_STATES = [('draft', 'Чернетка'), ('review', 'На перевірці'), ('approved', 'Затверджено'), ('zvirka', 'Звірка'), ('done', 'Переказано'),
               ('skipped', 'Пропущено')]
ADVICE_KINDS = [('pay', 'Сплатити'), ('check', 'Перевірити'), ('hold', 'Притримати')]
ZV_BADGES = [('ok', 'Збігається'), ('small', 'До 5 %'), ('big', 'Понад 5 %'), ('no_envelope', 'Немає конверта'), ('archive', 'Архів')]
ZV_SOURCES = [('accrual', 'Нарахування ночі'), ('dry', 'Пробний розрахунок')]
RUN_KINDS_HINT = 'night — ніч, manual — вручну, approve — «Затвердити», stop — «Зупинити»'
MONTHS = ['Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень', 'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень']

# групи (бухгалтерія й адміністратори кадрів бачать ПДн: податки, банківські рахунки агентів)
G_OWNER = 'workset_agents.group_owner'
G_EDITOR = 'workset_agents.group_editor'
G_ACCOUNT = 'account.group_account_user'
G_HR_MANAGER = 'hr.group_hr_manager'

# системні параметри (пише інструмент штабу --seed; у коді — лише імена)
P = 'workset_agents.'
P_OFFICIAL = P + 'company_official_id'
P_PERSONAL = P + 'company_personal_id'
P_PERIMETER = P + 'perimeter_company_ids'
P_WS_CODES = P + 'ws_journal_codes'
P_WH_ANALYTIC = P + 'withhold_analytic_id'
P_BILL_JOURNAL = P + 'bill_journal_code'
P_ANALYTIC = P + 'analytic_agents_id'
P_ENVELOPE = P + 'envelope_prefix'
P_OFFICE_TYPE = P + 'office_activity_type_id'
P_OWNER_UID = P + 'owner_uid'
P_OLYA_UID = P + 'olya_uid'
P_IS_COPY = P + 'is_copy'
P_NIGHT_PROD_OK = P + 'night_prod_ok'
P_AUTO_APPROVE = P + 'auto_approve'
P_PROBE_TODAY = P + 'probe_today'
P_AGENT_TAG = P + 'agent_tag_id'
P_CARD_YEAR = P + 'card_year'               # рік сум картки агента; порожньо — поточний за Києвом
P_FEE_ACCOUNT = P + 'fee_account_code'      # рахунок витрат винагороди в рахунку постачальника фірми official
P_TAX_ACCOUNT = P + 'tax_account_code'      # рахунок податку (утримання за п. 7.2) там само
P_OFFICE_TO = P + 'office_to'               # кому в бот «Офіс» іде список переказів (розділ office-desk)
P_OWNER_TO = P + 'owner_to'                 # підпис власника в активностях штабу
# правила виплат (не факти бази): «Особисто» — утримання 10 % за замовчуванням; «Офіційно» — 20 % без чинної довідки (п. 7.2), Польща — 0
PERSONAL_PCT = 10
TAX_PCT = 20
TAX_FREE_COUNTRIES = ('PL',)
ZV_ENV_DAYS = 150  # рядки конверта агента для «Звірки» рішення — за стільки днів назад


# переклад повідомлень — env._ / self.env._ (Odoo 19): модульний «_» у функції без self мови не знаходить — WARNING «no translation language
# detected» у журналі (dev-збірка 07.11)
def param(env, key, required=True, default=''):
    """Системний параметр workset_agents.*: рядок; немає і required — UserError «параметр … не заданий» (модуль фактів не вгадує)."""
    v = (env['ir.config_parameter'].sudo().get_param(key) or '').strip()
    if not v and required:
        raise UserError(env._('Агенти: параметр %s не заданий — запустіть інструмент штабу --seed.') % key)
    return v or default


def param_int(env, key, required=True):
    v = param(env, key, required)
    if not v:
        return 0
    if not v.isdigit():
        raise UserError(env._('Агенти: параметр %s — не число: %s') % (key, v))
    return int(v)


def param_ids(env, key, required=True):
    v = param(env, key, required)
    out = []
    for x in v.split(','):
        x = x.strip()
        if not x:
            continue
        if not x.isdigit():
            raise UserError(env._('Агенти: параметр %s — не список чисел: %s') % (key, v))
        out.append(int(x))
    if required and not out:
        raise UserError(env._('Агенти: параметр %s не заданий — запустіть інструмент штабу --seed.') % key)
    return out


def param_list(env, key, required=True):
    return [x.strip() for x in param(env, key, required).split(',') if x.strip()]


def is_copy(env):
    return param(env, P_IS_COPY, required=False) == '1'


def eur(env):
    return env.ref('base.EUR', raise_if_not_found=False)


def default_eur(self):
    c = eur(self.env)
    return c.id if c else False


def is_owner(env):
    return env.user.has_group(G_OWNER)


def check_owner(env, what):
    if not (env.su or is_owner(env)):
        raise UserError(env._('%s може лише власник.') % what)


def kyiv_now():
    return datetime.datetime.now(ZoneInfo('Europe/Kyiv'))


def kyiv_today():
    return kyiv_now().strftime('%Y-%m-%d')


def card_year(env):
    """Рік сум картки агента: параметр card_year (напр. для показу минулого року), порожньо — поточний рік за Києвом."""
    v = param(env, P_CARD_YEAR, required=False)
    if v.isdigit() and len(v) == 4:
        return int(v)
    return int(kyiv_today()[0:4])


def year_bounds(env):
    year = card_year(env)
    return '%04d-01-01' % year, '%04d-01-01' % (year + 1)
