# -*- coding: utf-8 -*-
"""Чисті функції «Прибутковості ліній» (М3): без Odoo і без бази — на словниках, списках і рядках «РРРР-ММ-ДД».

Модель (models/workset_profit_month.py) читає базу, кличе ці функції й пише підсумок «лінія × місяць»; тести без Odoo запускають їх
напряму (файл не імпортує odoo й інших файлів модуля). Гроші — EUR, знак як у книгах: витрата — мінус, надходження — плюс. Усередині
розкладу гроші — цілі центи: сума по рядках місяця збігається з аналітикою до цента.

Ключ рядка підсумку — список [вид рядка, лінія, замовник, рахунок аналітики, рахунок, картка DKV, тег] (0 / '' — немає):
  ['line', L, …] — лінія; ['noline', 0, C, …] — години замовника без лінії; ['unallocated', …] — «Не розподілено» (за статтею, карткою
  або тегом); ['office', …] — «Офіс»; ['owner', 0, 0, A, …] — «Власник» за рахунком аналітики; ['ignore', …] — «Не враховано» (розмітка
  «ігнор»); ['dkv', …] — рахунки DKV, замість яких рахуються їхні транзакції.
"""
import datetime
import math
import re
import unicodedata
from fractions import Fraction

STINT_GAP_DAYS = 45   # прибуття: розрив між сусідніми датами табелів людини більший — новий період роботи (як tools/lib/odoo_arrivals.js)
RECALC_MONTHS = 3     # плановий перерахунок — стільки останніх місяців разом із поточним
MAX_MONTHS = 36       # перерахунок за період — не більше стільки місяців за раз
TZ = 'Europe/Kyiv'

# Системні параметри (пише інструмент штабу; у коді — лише імена)
P_ROOT_PLANS = 'workset_profit.root_plan_ids'   # id кореневих планів аналітики (план рекрутингу) через кому; порожньо — корені вузлів ліній
P_SPLIT = 'workset_profit.split_by_period'      # ділити суму рядка рахунку за періодом послуги: так / ні (немає — так)
SHARED_PHONE_NAMES = 2  # телефон у карток із стількома різними іменами й більше — спільний (бригадир, офіс): людей не склеює (odoo_arrivals.js)

# Види розмітки аналітики (ключі; підписи — у models/workset_profit_map.py)
MAP_KEYS = ('housing', 'housing_buffer', 'fuel', 'tool', 'auto', 'transport', 'dkv_bill', 'partner', 'owner', 'office', 'ignore')
COST_KINDS = ('auto', 'fuel', 'tool', 'transport')         # «Хто платить» — workset.line.cost.rule (житло — правило житла лінії «Ставок»)
NET_COSTS = ('housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport')  # наші витрати лінії — у «чисто»
NOT_OURS = ('housing_buffer', 'partner')                   # без лінії — «Не розподілено», але не в «чисто» компанії
PAYER_KEYS = ('we', 'partner', 'worker_fund', 'customer')
TARGET_KEYS = ('line', 'office', 'transport')              # період картки DKV
# Житло, яке вираховує замовник, одиниця правила житла «Ставок» «€ за місяць (об'єкт)» (month): 'object' — одна сума на місяць на «лінія ×
# замовник»; 'person' — з кожної людини з годинами в місяці. «€ за місяць за особу» (month_person, «Ставки» 06.11) — завжди з людини.
# Як рахувати month — лише цей рядок (рішення coordinator 06.11: month — на об'єкт, «з людини» — лише month_person)
MONTH_UNIT = 'object'
# поля рядка, куди лягають гроші аналітики й DKV: їхня сума в рядку = aal_amount + dkv_amount (звірка рядка)
SPREAD = ('housing', 'auto', 'fuel', 'tool', 'transport', 'partner_paid', 'fund_paid', 'housing_buffer', 'amount')
# зі ставок і правил (не з аналітики й не з DKV): години × €/год; житло, яке вираховує замовник (правило житла лінії × одиниця)
RATE_MONEY = ('share', 'car_rent', 'fund_in', 'housing_customer')
MONEY = RATE_MONEY + SPREAD + ('aal_amount', 'dkv_amount')
HOURS = ('hours_total', 'hours_rate', 'hours_no_rate')
ROW_KINDS = ('line', 'noline', 'unallocated', 'office', 'owner', 'ignore', 'dkv')

# Екрани (круг 2): «Огляд», картка лінії, «Лінії × місяці»
MIN_MONTH = '2026-01-01'   # екрани — лише з січня 2026 (рішення власника 06.11 ~14:5x: «меня интересует только 2026 год»)
MATRIX_MONTHS = 12         # «Лінії × місяці» за замовчуванням — стільки останніх закритих місяців, але не раніше MIN_MONTH
MATRIX_MAX = 24            # період таблиці — не більше стільки місяців (останні)
CARD_ROWS = 12             # «Рядки за місяць» картки лінії — стільки найбільших, решта — «Показано N з M»
P_DROP = 'workset_profit.drop_alert'                    # жовта клітинка: чисто €/год упало до минулого місяця більше, ніж на стільки
DROP_DEFAULT = 1.5
P_HOUSING_SHIFT = 'workset_profit.housing_shift_ratio'  # «Житло не в своєму місяці»: житло на годину > стільки × медіана інших місяців
HOUSING_SHIFT_DEFAULT = 2.0
CHAIN = ('share', 'car_rent', 'housing', 'housing_customer', 'auto', 'fuel', 'tool', 'transport')  # ланцюжок лінії до «чисто»
SPLIT = ('net', 'housing', 'auto', 'fuel', 'tool', 'transport')   # «Куди йде частка»: частини смуги лінії
ASIDE = ('partner_paid', 'fund_in', 'fund_paid', 'fund_left', 'housing_buffer')  # «Не в нашому чисто» картки лінії
# вісь «Чисто по місяцях» «Огляду» (круг 3а): від min(AXIS_LOW, AXIS_PCT-й перцентиль) до max(AXIS_HIGH, (100 − AXIS_PCT)-й), кратно AXIS_STEP;
# що поза віссю — викид: крапка біля краю з підписом «лінія місяць число» (один збитковий місяць не стискає решту в нитку)
AXIS_LOW = -10
AXIS_HIGH = 25
AXIS_PCT = 5
AXIS_STEP = 5

# Підказка виду за назвою статті: (будь-що з, усе з, вид). Лише підказка: рахує розмітка (правило workset.profit.analytic.map)
HINTS = (
    (('жил', 'житл'), ('буфер',), 'housing_buffer'),
    (('жил', 'житл'), (), 'housing'),
    (('топлив', 'палив'), (), 'fuel'),
    (('инструмент', 'інструмент', 'спецодеж', 'спецодяг'), (), 'tool'),
    (('транспорт', 'вожден', 'лизинг', 'лізинг'), (), 'auto'),
)

TX = {
    'k_housing': ['житло', 'жильё'], 'k_housing_buffer': ['буфер житла', 'буфер жилья'], 'k_fuel': ['паливо', 'топливо'],
    'k_tool': ['інструмент', 'инструмент'], 'k_auto': ['авто', 'авто'], 'k_transport': ['перевезення', 'перевозка'],
    'k_dkv_bill': ['рахунок DKV', 'счёт DKV'], 'k_partner': ['гроші лінії', 'деньги линии'], 'k_owner': ['власник', 'собственник'],
    'k_office': ['офіс', 'офис'], 'k_ignore': ['ігнор', 'игнор'],
    'n_noline': ['Без лінії · %s', 'Без линии · %s'],
    'n_nocust': ['проєкт без замовника', 'проект без заказчика'],
    'n_dkv': ['DKV: рахунки, розкладені транзакціями', 'DKV: счета, разложенные транзакциями'],
    'n_owner': ['Власник · %s', 'Собственник · %s'],
    'n_office': ['Офіс · %s', 'Офис · %s'],
    'n_ignore': ['Не враховано · %s', 'Не учтено · %s'],
    'n_un': ['Не розподілено · %s', 'Не распределено · %s'],
    'n_dkv_bill': ['DKV: рахунок без транзакцій · %s', 'DKV: счёт без транзакций · %s'],
    'n_dkv_card': ['DKV: картка без періоду · %s', 'DKV: карта без периода · %s'],
    'n_transport_none': ['перевезення без прибуттів', 'перевозка без прибытий'],
    'n_dkv_diff': ['DKV різниця: рахунок ≠ транзакціям · %s', 'DKV разница: счёт ≠ транзакциям · %s'],
    'v_raw': ['Аналітика плану за датою (окремий підрахунок), %s: у книгах %s, у розрахунку %s, різниця %s.',
              'Аналитика плана по дате (отдельный подсчёт), %s: в книгах %s, в расчёте %s, разница %s.'],
    'v_moved': ['Період послуги переніс між місяцями: %s.', 'Период услуги перенёс между месяцами: %s.'],
    'v_nobill': ['Транзакції DKV без рахунку в Odoo: %d, сума %s.', 'Транзакции DKV без счёта в Odoo: %d, сумма %s.'],
    'v_double': ['Увага: є і рахунки DKV без транзакцій (%s), і транзакції без рахунку — можливий подвійний рахунок: привʼяжіть транзакції до рахунку.',
                 'Внимание: есть и счета DKV без транзакций (%s), и транзакции без счёта — возможен двойной счёт: привяжите транзакции к счёту.'],
    'n_no_line': ['%s (%s без лінії)', '%s (%s без линии)'],
    'n_hint': ['%s (підказка: %s)', '%s (подсказка: %s)'],
    'v_ok': ['Звірка %s: зійшлося до цента — аналітика %s, DKV %s.', 'Сверка %s: сошлось до цента — аналитика %s, DKV %s.'],
    'v_bad': ['Звірка %s: розбіжність.', 'Сверка %s: расхождение.'],
    'v_aal': ['Аналітика: у рядках %s, у книгах %s, різниця %s.', 'Аналитика: в строках %s, в книгах %s, разница %s.'],
    'v_dkv': ['DKV: у рядках %s, транзакцій %s, різниця %s.', 'DKV: в строках %s, транзакций %s, разница %s.'],
    'v_rows': ['Рядки, де стовпці не дають суми рядка: %s.', 'Строки, где столбцы не дают суммы строки: %s.'],
    'v_none': ['Звірка %s: перерахунку ще не було.', 'Сверка %s: пересчёта ещё не было.'],
    'only_editor': ['Перераховувати може лише редактор прибутковості.', 'Пересчитывать может только редактор прибыльности.'],
    'map_empty': ['Розмітка: вкажіть вузол плану, статтю аналітики, рахунок або контрагента.',
                  'Разметка: укажите узел плана, статью аналитики, счёт или контрагента.'],
    'map_clash': ['Розмітка: для цього ключа вже є правило № %d з датами, що перетинаються.',
                  'Разметка: для этого ключа уже есть правило № %d с пересекающимися датами.'],
    'rule_clash': ['«Хто платить»: для лінії, замовника й виду вже є правило № %d з датами, що перетинаються.',
                   '«Кто платит»: для линии, заказчика и вида уже есть правило № %d с пересекающимися датами.'],
    'period_clash': ['Картка DKV: період перетинається з періодом № %d цієї картки.', 'Карта DKV: период пересекается с периодом № %d этой карты.'],
    'period_line': ['Картка DKV: для «Лінія» вкажіть лінію.', 'Карта DKV: для «Линия» укажите линию.'],
    'dates': ['«До» раніше за «З».', '«До» раньше «С».'],
    'card_dup': ['Картка DKV з номером %s уже є: %s.', 'Карта DKV с номером %s уже есть: %s.'],
    'txn_dup': ['Транзакція DKV з ключем %s уже є.', 'Транзакция DKV с ключом %s уже есть.'],
    'a_txns': ['Транзакції DKV', 'Транзакции DKV'],
    'recalc_done': ['Перераховано місяців: %d, рядків: %d.', 'Пересчитано месяцев: %d, строк: %d.'],
    'title': ['Прибутковість', 'Прибыльность'],
    # ——— екрани (круг 2) ———
    'only_viewer': ['Екрани «Прибутковості» бачить лише група «Прибутковість: перегляд».',
                    'Экраны «Прибыльности» видит только группа «Прибыльность: просмотр».'],
    'card_before': ['Картка лінії — лише з січня 2026; рядок %s відкрийте у списку.', 'Карточка линии — только с января 2026; строку %s откройте в списке.'],
    's_share': ['Частка WorkSET', 'Доля WorkSET'], 's_car_rent': ['Оренда авто', 'Аренда авто'], 's_housing': ['Житло', 'Жильё'],
    's_housing_customer': ['Житло (вирахував замовник)', 'Жильё (вычел заказчик)'], 's_auto': ['Авто', 'Авто'], 's_fuel': ['Паливо', 'Топливо'],
    's_tool': ['Інструмент', 'Инструмент'], 's_transport': ['Перевезення', 'Перевозка'], 's_net': ['Чисто', 'Чисто'],
    'a_partner_paid': ['З грошей лінії', 'Из денег линии'], 'a_fund_in': ['У фонд працівників', 'В фонд работников'],
    'a_fund_paid': ['З фонду працівників', 'Из фонда работников'], 'a_fund_left': ['Фонд: залишок місяця', 'Фонд: остаток месяца'],
    'a_housing_buffer': ['Буфер житла з оплати (довідково)', 'Буфер жилья из оплаты (справочно)'],
    'r_timesheets': ['Табелі — %s год, людей: %d', 'Табели — %s ч, человек: %d'],
    'r_card': ['DKV %s — транзакцій: %d', 'DKV %s — транзакций: %d'],
    'r_pool': ['Перевезення людей — частка лінії за прибуттями', 'Перевозка людей — доля линии по прибытиям'],
    'r_rent': ['Оренда авто — за ставками', 'Аренда авто — по ставкам'],
    'r_hc': ['Житло, яке вираховує замовник, — за правилом житла лінії', 'Жильё, которое вычитает заказчик, — по правилу жилья линии'],
    'r_hc_title': ['Правила житла лінії', 'Правила жилья линии'],
    'r_rows': ['Рядки: %s · %s', 'Строки: %s · %s'],
    't_unalloc': ['Не розподілено · %s', 'Не распределено · %s'], 't_no_rate': ['Години без ставки · %s', 'Часы без ставки · %s'],
    't_noline': ['Години без лінії · %s', 'Часы без линии · %s'], 't_shift': ['Житло не в своєму місяці · %s', 'Жильё не в своём месяце · %s'],
    't_month': ['Рядки розрахунку · %s', 'Строки расчёта · %s'],
    'v_none_short': ['звірки ще не було', 'сверки ещё не было'],
    'l_all': ['Усі лінії', 'Все линии'], 'l_net': ['Чисто, €/год', 'Чисто, €/ч'], 'l_share': ['Частка, €/год', 'Доля, €/ч'],
    'l_hour': ['€/год', '€/ч'], 'l_month': ['€/міс', '€/мес'],
    # круг 3а: легенда «Огляду», чип «Власники», згорнутий «Хто платить»
    'l_each': ['Кожна лінія', 'Каждая линия'], 't_owners': ['Власники · %s', 'Собственники · %s'],
    't_no_rate_line': ['Години без ставки · %s · %s', 'Часы без ставки · %s · %s'],
    'p_all_we': ['Усе платимо ми (у «чисто»)', 'Всё платим мы (в «чисто»)'],
    'p_all_partner': ['Усе — партнер (гроші лінії)', 'Всё — партнёр (деньги линии)'],
    'p_all_worker_fund': ['Усе — з фонду працівників', 'Всё — из фонда работников'],
    'p_all_customer': ['Усе — замовник', 'Всё — заказчик'],
    'months': [('січень', 'лютий', 'березень', 'квітень', 'травень', 'червень', 'липень', 'серпень', 'вересень', 'жовтень', 'листопад',
                'грудень'),
               ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь')],
    'months_short': [('Січ', 'Лют', 'Бер', 'Кві', 'Тра', 'Чер', 'Лип', 'Сер', 'Вер', 'Жов', 'Лис', 'Гру'),
                     ('Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек')],
}


def cust_of(partner):
    """Замовник і контрагент порівнюються за комерційним контрагентом (як у «Ставках»): запис res.partner → id або 0."""
    return partner.commercial_partner_id.id if partner else 0


def lang_of(code):
    """Код мови Odoo → 'ru' / 'uk' (усе, крім російської, — українською)."""
    return 'ru' if str(code or '').startswith('ru') else 'uk'


def t(key, lang):
    return TX[key][1 if lang == 'ru' else 0]


# ——— дати ———
def iso(d):
    """Дата Odoo (datetime.date), рядок або False → «РРРР-ММ-ДД» / ''."""
    if not d:
        return ''
    if isinstance(d, str):
        return d[0:10]
    return d.isoformat()[0:10]


def to_date(day):
    return datetime.date(int(day[0:4]), int(day[5:7]), int(day[8:10]))


def add_days(day, n):
    return (to_date(day) + datetime.timedelta(days=n)).isoformat()


def month_first(day):
    return iso(day)[0:8] + '01'


def month_last(day):
    nxt = add_days(month_first(day), 32)[0:8] + '01'
    return add_days(nxt, -1)


def next_month(day):
    return add_days(month_first(day), 32)[0:8] + '01'


def month_list(date_from, date_to):
    """Перші числа місяців від місяця date_from до місяця date_to включно (date_to раніше — порожньо)."""
    out = []
    m, end = month_first(date_from), month_first(date_to)
    while m <= end:
        out.append(m)
        m = next_month(m)
    return out


def months_back(today, n=RECALC_MONTHS):
    """Останні n місяців разом із місяцем today, від давнішого: ['РРРР-ММ-01', …]."""
    out = [month_first(today)]
    while len(out) < n:
        out.insert(0, month_first(add_days(out[0], -1)))
    return out


def in_dates(day, date_from, date_to):
    return (not date_from or date_from <= day) and (not date_to or day <= date_to)


def overlaps(a_from, a_to, b_from, b_to):
    """Два відрізки дат (порожнє «до» — без кінця) перетинаються."""
    far = '9999-12-31'
    return (a_from or '') <= (b_to or far) and (b_from or '') <= (a_to or far)


def fmt_month(day):
    s = iso(day)
    return '%s.%s' % (s[5:7], s[0:4]) if s else ''


# ——— гроші ———
def cents(x):
    """Сума → цілі центи (половина — від нуля): 2.675 → 268, −0.005 → −1."""
    v = float(x or 0)
    sign = -1 if v < 0 else 1
    return sign * int(abs(v) * 100 + 0.5 + 1e-7)


def eur(c):
    return round(c / 100.0, 2)


def money_text(x):
    """1234.5 → «1234,50», −7 → «−7,00»."""
    v = round(float(x or 0), 2)
    s = '%.2f' % abs(v)
    return ('−' if v < 0 else '') + s.replace('.', ',')


def parse_ids(text):
    """«12, 15,x,» → [12, 15]: список id із системного параметра (сміття пропускається)."""
    out = []
    for part in str(text or '').replace(';', ',').split(','):
        part = part.strip()
        if part.isdigit() and int(part) > 0 and int(part) not in out:
            out.append(int(part))
    return out


def split_cents(total, weights):
    """total — центи (ціле, будь-який знак); weights — [[ключ, вага]] (вага ≤ 0 — пропуск) → [[ключ, центи]]: пропорційно вазі, сума = total;
    залишок центів — найбільшим дробовим частинам, рівні — за порядком у списку. Ваг немає — []."""
    ws = [(k, Fraction(w)) for k, w in weights if w and w > 0]
    if not ws:
        return []
    whole = sum(w for _k, w in ws)
    sign = -1 if total < 0 else 1
    left = abs(int(total))
    parts = []
    for i, (k, w) in enumerate(ws):
        exact = Fraction(left) * w / whole
        parts.append([k, int(exact), exact - int(exact), i])
    rest = left - sum(p[1] for p in parts)
    for p in sorted(parts, key=lambda p: (-p[2], p[3]))[:rest]:
        p[1] += 1
    return [[p[0], sign * p[1]] for p in parts]


def split_days(total, start, end):
    """total — центи; період послуги «з – по» → [[«РРРР-ММ-01», центи]] за числом днів періоду в кожному місяці (сума = total)."""
    weights = []
    m = month_first(start)
    while m <= end:
        a, b = max(m, start), min(month_last(m), end)
        weights.append([m, (to_date(b) - to_date(a)).days + 1])
        m = next_month(m)
    return split_cents(total, weights)


def month_part(amount, day, start, end, first):
    """Частина суми рядка аналітики, що припадає на місяць first: є період послуги рядка рахунку (start ≤ end) — за днями періоду в місяці
    (частини всіх місяців дають суму рядка до цента); немає — уся сума, якщо дата рядка в цьому місяці, інакше 0."""
    if start and end and iso(start) <= iso(end):
        for m, c in split_days(cents(amount), iso(start), iso(end)):
            if m == first:
                return eur(c)
        return 0.0
    return round(float(amount or 0), 2) if month_first(day) == first else 0.0


def parse_flag(text, default=True):
    """Так / ні з системного параметра: «так», «yes», «1», «true», «on», «да» — True; «ні», «no», «0», «false», «off», «нет» — False;
    порожньо або інше — default."""
    s = str(text or '').strip().lower()
    if s in ('так', 'yes', 'y', '1', 'true', 'on', 'да'):
        return True
    if s in ('ні', 'нi', 'no', 'n', '0', 'false', 'off', 'нет'):
        return False
    return default


def service_period(split_on, start, end, deferral):
    """Чи ділити рядок аналітики за періодом послуги рядка рахунку → (з, по) або ('', '') — місяць за датою рядка.
    Не ділимо: параметр «ділити за періодом» — ні; періоду немає або він хибний; у рахунку є проводки відстрочки Odoo (deferral —
    рахунок породив проводки відстрочки або сам є такою проводкою): вони вже розносять суму по місяцях своїми датами — поділ дав би
    подвійний рахунок."""
    start, end = iso(start), iso(end)
    if not split_on or deferral or not start or not end or start > end:
        return '', ''
    return start, end


# ——— розмітка аналітики ———
def kind_hint(*names):
    """Підказка виду за назвою статті (першою, де щось знайшлося: рахунок, потім стаття аналітики) → ключ виду або ''."""
    for name in names:
        s = str(name or '').lower()
        if not s:
            continue
        for anyof, allof, kind in HINTS:
            if any(a in s for a in anyof) and all(b in s for b in allof):
                return kind
    return ''


def plan_chain(parent_path):
    """parent_path вузла плану аналітики («1/5/9/») → [1, 5, 9] — від кореня до вузла."""
    return [int(p) for p in str(parent_path or '').split('/') if p.isdigit()]


def pairs_map(pairs):
    """[[ключ, значення], …] → {ключ: значення}; ключ, що повторюється, — перше значення (лінії йдуть за порядком)."""
    out = {}
    for k, v in pairs or []:
        if k and k not in out:
            out[k] = v
    return out


def map_pick(rules, item):
    """Найточніше правило розмітки для суми аналітики. rules — [{'id', 'plan_id', 'analytic_id', 'account_id', 'partner_id', 'date_from',
    'date_to'}] (активні); item — {'plans': [вузли від кореня до вузла статті], 'analytic_id', 'account_id', 'partner_id', 'date'}.
    Правило бере суму, коли збігаються всі його заповнені ключі. Точніше: контрагент > стаття аналітики > рахунок > вузол плану (ключі
    додаються), далі — глибший вузол, пізніша «З». → id правила або 0."""
    best, best_key = 0, None
    plans = item.get('plans') or []
    for r in rules or []:
        if not (r.get('plan_id') or r.get('analytic_id') or r.get('account_id') or r.get('partner_id')):
            continue
        if not in_dates(item.get('date') or '', r.get('date_from') or '', r.get('date_to') or ''):
            continue
        if r.get('analytic_id') and r['analytic_id'] != item.get('analytic_id'):
            continue
        if r.get('account_id') and r['account_id'] != item.get('account_id'):
            continue
        if r.get('partner_id') and r['partner_id'] != item.get('partner_id'):
            continue
        depth = -1
        if r.get('plan_id'):
            if r['plan_id'] not in plans:
                continue
            depth = plans.index(r['plan_id'])
        score = (8 if r.get('partner_id') else 0) + (4 if r.get('analytic_id') else 0) + (2 if r.get('account_id') else 0) \
            + (1 if r.get('plan_id') else 0)
        key = (score, depth, r.get('date_from') or '', -r['id'])
        if best_key is None or key > best_key:
            best, best_key = r['id'], key
    return best


def map_clash(rules, rule):
    """Інше активне правило з тими самими ключами (вузол, стаття, рахунок, контрагент) і датами, що перетинаються → id або 0."""
    keys = ('plan_id', 'analytic_id', 'account_id', 'partner_id')
    for r in rules or []:
        if r['id'] == rule.get('id'):
            continue
        if all((r.get(k) or 0) == (rule.get(k) or 0) for k in keys) and \
                overlaps(r.get('date_from'), r.get('date_to'), rule.get('date_from'), rule.get('date_to')):
            return r['id']
    return 0


def line_of_plans(plans, line_nodes):
    """Лінія за вузлом статті: найглибший вузол ланцюжка, що є серед «Вузлів аналітики» якоїсь лінії. line_nodes — [[вузол, лінія]]."""
    nodes = pairs_map(line_nodes)
    for p in reversed(plans or []):
        if p in nodes:
            return nodes[p]
    return 0


def classify(item, rule, line_nodes, cust_lines):
    """Сума аналітики → {'kind', 'line_id', 'cust', 'rule_id', 'hint'}. rule — словник правила розмітки або None (тоді вид порожній —
    «Не розподілено», з підказкою за назвою). Лінія: з правила, інакше — за вузлом статті, інакше — за замовником (з правила або
    контрагент статті аналітики). cust_lines — [[замовник, лінія]]."""
    hint = kind_hint(item.get('account_name'), item.get('analytic_name'))
    if not rule:
        return {'kind': '', 'line_id': 0, 'cust': item.get('cust') or 0, 'rule_id': 0, 'hint': hint}
    cust = rule.get('customer_id') or item.get('cust') or 0
    line = rule.get('line_id') or line_of_plans(item.get('plans'), line_nodes) or pairs_map(cust_lines).get(cust, 0)
    return {'kind': rule['kind'], 'line_id': line, 'cust': cust, 'rule_id': rule['id'], 'hint': hint}


# ——— хто платить ———
def cost_rule_pick(rules, line_id, cust_id, kind, day):
    """«Хто платить» на day: правило замовника точніше за правило лінії (замовник порожній); серед рівних — пізніша «З».
    rules — [{'id', 'line_id', 'customer_id', 'kind', 'payer', 'date_from', 'date_to'}] → словник правила або None."""
    best = None
    for r in rules or []:
        if r['line_id'] != line_id or r['kind'] != kind or not in_dates(day, r.get('date_from') or '', r.get('date_to') or ''):
            continue
        if r.get('customer_id') and r['customer_id'] != cust_id:
            continue
        key = (1 if r.get('customer_id') else 0, r.get('date_from') or '', r['id'])
        if best is None or key > best[0]:
            best = (key, r)
    return best[1] if best else None


def cost_payer(rules, line_id, cust_id, kind, day):
    """Хто платить витрату виду kind лінії на day; правила немає — 'we' (витрата в наших книгах — наша)."""
    r = cost_rule_pick(rules, line_id, cust_id, kind, day)
    return (r or {}).get('payer') or 'we'


def cost_clash(rules, rule):
    """Інше правило «Хто платить» тієї самої лінії, замовника (порожній — порожній) і виду з датами, що перетинаються → id або 0."""
    for r in rules or []:
        if r['id'] == rule.get('id'):
            continue
        if r['line_id'] == rule['line_id'] and (r.get('customer_id') or 0) == (rule.get('customer_id') or 0) and r['kind'] == rule['kind'] \
                and overlaps(r.get('date_from'), r.get('date_to'), rule.get('date_from'), rule.get('date_to')):
            return r['id']
    return 0


def housing_payer(payer):
    """«Хто платить» правила житла лінії «Ставок» (we_buffer / partner / customer; worker_fund — якщо «Ставки» його додадуть) → наш ключ."""
    return {'we_buffer': 'we', 'partner': 'partner', 'customer': 'customer', 'worker_fund': 'worker_fund'}.get(payer or '', 'we')


def payer_field(kind, payer):
    """Куди йде витрата лінії: гроші партнера — «З грошей лінії», фонд працівників — «З фонду працівників», інакше (ми, замовник) — свій
    стовпець виду (у «чисто»)."""
    if payer == 'partner':
        return 'partner_paid'
    if payer == 'worker_fund':
        return 'fund_paid'
    return kind


# ——— куди йде сума ———
def key_of(kind, line=0, cust=0, analytic=0, account=0, card=0, tag=''):
    return [kind, line or 0, cust or 0, analytic or 0, account or 0, card or 0, tag or '']


def target(c, item, payer='', covered=False):
    """Куди йде сума аналітики з класифікацією c (classify) → {'key', 'field'} або {'pool': True} (перевезення без лінії — ділиться за
    прибуттями). covered — сума рахунку DKV, для якого є транзакції: окремий рядок 'dkv', не в «чисто» (рахуються транзакції)."""
    a, g = item.get('analytic_id') or 0, item.get('account_id') or 0
    kind = c.get('kind') or ''
    if covered:
        return {'key': key_of('dkv'), 'field': 'amount'}
    if not kind:
        return {'key': key_of('unallocated', analytic=a, account=g), 'field': 'amount'}
    if kind == 'dkv_bill':
        return {'key': key_of('unallocated', analytic=a, account=g, tag='dkv_bill'), 'field': 'amount'}
    if kind in ('office', 'owner', 'ignore'):
        return {'key': key_of(kind, analytic=a), 'field': 'amount'}
    line = c.get('line_id') or 0
    if not line:
        if kind == 'transport':
            return {'pool': True}
        return {'key': key_of('unallocated', analytic=a, account=g, tag=kind), 'field': 'amount'}
    if kind == 'housing_buffer':
        field = 'housing_buffer'
    elif kind == 'partner':
        field = 'partner_paid'
    else:
        field = payer_field(kind, payer)
    return {'key': key_of('line', line=line), 'field': field}


def entry(tgt, amount, src, hint=''):
    """Запис розкладу: {'key', 'field', 'amount', 'src' ('aal' — аналітика, 'dkv' — транзакція DKV), 'hint'}."""
    return {'key': list(tgt['key']), 'field': tgt['field'], 'amount': round(float(amount or 0), 2), 'src': src, 'hint': hint or ''}


# ——— DKV ———
def card_period(periods, card_id, day):
    """Період картки DKV на day (найпізніша «З» ≤ day, «До» порожнє або ≥ day) → словник або None.
    periods — [{'id', 'card_id', 'target', 'line_id', 'customer_id', 'date_from', 'date_to'}]."""
    best = None
    for p in periods or []:
        if p['card_id'] != card_id or not in_dates(day, p.get('date_from') or '', p.get('date_to') or ''):
            continue
        key = (p.get('date_from') or '', p['id'])
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def period_clash(periods, period):
    for p in periods or []:
        if p['id'] != period.get('id') and p['card_id'] == period['card_id'] and \
                overlaps(p.get('date_from'), p.get('date_to'), period.get('date_from'), period.get('date_to')):
            return p['id']
    return 0


def dkv_target(period, card_id, payer=''):
    """Куди йде транзакція картки (брутто EUR, витрата): лінія — «Паливо» за «Хто платить» (payer), офіс — рядок «Офіс» картки,
    перевезення — у розподіл за прибуттями ({'pool': True}), періоду немає — «Не розподілено: картка без періоду»."""
    if not period or (period.get('target') == 'line' and not period.get('line_id')):
        return {'key': key_of('unallocated', card=card_id, tag='dkv_card'), 'field': 'amount'}
    if period['target'] == 'office':
        return {'key': key_of('office', card=card_id), 'field': 'amount'}
    if period['target'] == 'transport':
        return {'pool': True}
    return {'key': key_of('line', line=period['line_id']), 'field': payer_field('fuel', payer)}


def dkv_bill_entries(items, total, gross):
    """Аналітика одного рахунку DKV, для якого є транзакції, за місяць. items — [{'amount', 'analytic_id', 'account_id'}] (частини
    місяця); total — уся аналітика рахунку (EUR, усі місяці); gross — брутто привʼязаних транзакцій (усі дати, додатне).
    Різниця рахунку «книги − транзакції» (total + gross; частково завантажені транзакції — мінус) — у місяць пропорційно його частці
    аналітики рахунку — рядком «Не розподілено: DKV різниця» (за статтею); решта — рядок 'dkv' (рахується транзакціями).
    Сума записів = сума items (до цента). → записи (entry)."""
    vals = [cents(i['amount']) for i in items or []]
    month = sum(vals)
    whole = cents(total)
    diff = whole + cents(gross)
    if not vals or not month:
        return []
    if not whole or month == whole:
        portion = diff
    else:
        parts = split_cents(diff, [[0, abs(month)], [1, abs(whole - month)]])
        portion = parts[0][1] if parts else 0
    shares = dict(split_cents(portion, [[k, abs(v)] for k, v in enumerate(vals)])) if portion else {}
    out = []
    for k, (it, v) in enumerate(zip(items, vals)):
        p = shares.get(k, 0)
        if v - p:
            out.append(entry({'key': key_of('dkv'), 'field': 'amount'}, eur(v - p), 'aal'))
        if p:
            out.append(entry({'key': key_of('unallocated', analytic=it.get('analytic_id'), account=it.get('account_id'), tag='dkv_diff'),
                              'field': 'amount'}, eur(p), 'aal'))
    return out


# ——— прибуття й перевезення ———
def phone9(p):
    """Останні 9 цифр номера; коротше або порожньо — ''."""
    d = re.sub(r'\D', '', '' if p is None or p is False else str(p))
    return d[-9:] if len(d) >= 9 else ''


def name_norm(name):
    """Ключ порівняння імен (як nameNorm у tools/lib/odoo_arrivals.js): без приписки фірми (усе від першої «|»), NFC, нижній регістр, ё→е,
    апострофи ’ ʼ ´ ` → ', крапки, коми й дужки — у пробіл, пробіли стиснуто."""
    s = '' if name is False or name is None else str(name)
    s = s.split('|', 1)[0]
    s = unicodedata.normalize('NFC', s).lower().replace('ё', 'е')
    s = re.sub(r"[’ʼ´`]", "'", s)
    s = re.sub(r'[.,()\[\]{}]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def glue_people(emps, shared_names=SHARED_PHONE_NAMES):
    """Картки працівників → одна людина (як buildPersons у tools/lib/odoo_arrivals.js): склеюють спільний телефон (9 цифр; крім телефонів,
    записаних у карток із shared_names і більше різними іменами), однакове ім'я без приписки фірми (« | TF», « | PA») і спільний робочий
    контакт. emps — [{'id', 'name', 'phones': [...], 'contact'}] → [[картка, людина]] (людина — найменший id групи), за id картки."""
    rows = sorted([e for e in emps or [] if isinstance(e.get('id'), int)], key=lambda e: e['id'])
    names_of = {}
    for e in rows:
        n = name_norm(e.get('name')) or ('#%d' % e['id'])
        for p in {phone9(x) for x in e.get('phones') or []} - {''}:
            names_of.setdefault(p, set()).add(n)
    shared = {p for p, ns in names_of.items() if len(ns) >= shared_names}
    parent = {e['id']: e['id'] for e in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    seen = {}
    for e in rows:
        keys = ['p:' + p for p in sorted({phone9(x) for x in e.get('phones') or []} - {''} - shared)]
        n = name_norm(e.get('name'))
        if n:
            keys.append('n:' + n)
        if e.get('contact'):
            keys.append('c:%d' % e['contact'])
        for k in keys:
            if k in seen:
                a, b = find(seen[k]), find(e['id'])
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                seen[k] = e['id']
    return [[e['id'], find(e['id'])] for e in rows]


def arrivals(rows, first, last, gap=STINT_GAP_DAYS):
    """Прибуття місяця: початок періоду роботи людини за табелями (перша дата або дата після розриву > gap днів) у межах first – last.
    rows — [{'person', 'date', 'cust', 'hours'}] табелів від first − gap − 1 до last (person — ключ людини, не 0). Замовник прибуття —
    того дня, де в людини найбільше годин (рівно — замовник перед «без замовника», далі менший id). → [{'person', 'date', 'cust'}]."""
    by_person = {}
    for r in rows or []:
        if not r.get('person'):
            continue
        day = by_person.setdefault(r['person'], {}).setdefault(iso(r['date']), {})
        day[r.get('cust') or 0] = day.get(r.get('cust') or 0, 0.0) + float(r.get('hours') or 0)
    out = []
    for person in sorted(by_person):
        prev = ''
        for d in sorted(by_person[person]):
            start = not prev or (to_date(d) - to_date(prev)).days > gap
            if start and first <= d <= last:
                custs = by_person[person][d]
                cust = sorted(custs.items(), key=lambda kv: (-kv[1], kv[0] == 0, kv[0]))[0][0]
                out.append({'person': person, 'date': d, 'cust': cust})
            prev = d
    return out


def arrival_weights(arr, cust_lines):
    """Прибуття → [[лінія, число]] за лініями замовників (замовник без лінії — не рахується), за id лінії."""
    lines = pairs_map(cust_lines)
    n = {}
    for a in arr or []:
        line = lines.get(a.get('cust') or 0, 0)
        if line:
            n[line] = n.get(line, 0) + 1
    return [[k, n[k]] for k in sorted(n)]


def pool_entries(pool, weights, cost_rules):
    """Перевезення без лінії (рейси карток «перевезення» й аналітика «перевезення» без лінії) → записи по лініях пропорційно прибуттям
    (центи — точно); поле — за «Хто платить» лінії (вид transport) на дату суми. Прибуттів немає — «Не розподілено: перевезення без
    прибуттів». pool — [{'amount', 'src', 'date'}]."""
    out = []
    for p in pool or []:
        total = cents(p['amount'])
        parts = split_cents(total, weights)
        if not parts:
            out.append(entry({'key': key_of('unallocated', tag='transport_none'), 'field': 'amount'}, eur(total), p['src']))
            continue
        for line, c in parts:
            if not c:
                continue
            payer = cost_payer(cost_rules, line, 0, 'transport', iso(p.get('date')))
            out.append(entry({'key': key_of('line', line=line), 'field': payer_field('transport', payer)}, eur(c), p['src']))
    return out


# ——— години й частка ———
def segments(rows, rates, first, last):
    """Години пар місяця, поділені днями нових ставок усередині місяця (кожен відрізок рахується за ставкою на свій перший день).
    rows — [{'person', 'cust', 'date', 'hours'}] табелів місяця (person — контакт картки, 0 — немає); rates — [{'person', 'cust',
    'date_from'}] ставок пар. → [{'person', 'cust', 'from', 'to', 'hours'}]; відрізок без годин — не повертається."""
    cuts = {}
    for r in rates or []:
        d = iso(r['date_from'])
        if first < d <= last:
            cuts.setdefault((r['person'], r['cust']), set()).add(d)
    by_pair = {}
    for r in rows or []:
        by_pair.setdefault((r.get('person') or 0, r.get('cust') or 0), []).append((iso(r['date']), float(r.get('hours') or 0)))
    out = []
    for key in sorted(by_pair):
        starts = [first] + sorted(cuts.get(key, ()))
        for i, s in enumerate(starts):
            e = add_days(starts[i + 1], -1) if i + 1 < len(starts) else last
            h = round(sum(x for d, x in by_pair[key] if s <= d <= e), 2)
            if h > 0:
                out.append({'person': key[0], 'cust': key[1], 'from': s, 'to': e, 'hours': h})
    return out


def hour_rows(segs, plans, cust_lines):
    """Години й гроші зі ставок по рядках. plans — [{'person', 'cust', 'from', 'plan'}]: план ставки (workset.rate._plan_at) на перший
    день відрізка, None — ставки немає (години — «без ставки»). Почасова ставка: частка = години × net_share; «Фікс за місяць» —
    fixed_month × частку годин пари в місяці − бонус офісу × години. Оренда авто = години × car_rent_share (наш дохід), у фонд = години ×
    worker_fund_share (не наше). Ключа в плані немає — 0. Замовник без лінії — рядок ['noline', 0, замовник, …].
    → [{'key', 'hours_total', 'hours_rate', 'hours_no_rate', 'share', 'car_rent', 'fund_in'}]."""
    pm = {(p['person'], p['cust'], p['from']): p.get('plan') for p in plans or []}
    lines = pairs_map(cust_lines)
    pair_hours = {}
    for s in segs or []:
        pair_hours[(s['person'], s['cust'])] = pair_hours.get((s['person'], s['cust']), 0.0) + s['hours']
    rows = {}
    for s in segs or []:
        line = lines.get(s['cust'], 0)
        key = tuple(key_of('line', line=line) if line else key_of('noline', cust=s['cust']))
        row = rows.setdefault(key, {'hours_total': 0.0, 'hours_rate': 0.0, 'hours_no_rate': 0.0, 'share': 0.0, 'car_rent': 0.0, 'fund_in': 0.0})
        h = float(s['hours'])
        row['hours_total'] += h
        plan = pm.get((s['person'], s['cust'], s['from'])) if s['person'] else None
        if not plan:
            row['hours_no_rate'] += h
            continue
        row['hours_rate'] += h
        if (plan.get('price_mode') or 'hour') == 'fixed_month':
            total = pair_hours[(s['person'], s['cust'])]
            row['share'] += float(plan.get('fixed_month') or 0) * h / total - float(plan.get('office_bonus') or 0) * h
        else:
            row['share'] += h * float(plan.get('net_share') or 0)
        row['car_rent'] += h * float(plan.get('car_rent_share') or 0)
        row['fund_in'] += h * float(plan.get('worker_fund_share') or 0)
    out = []
    for key in sorted(rows):
        r = rows[key]
        out.append(dict({k: round(r[k], 2) for k in r}, key=list(key)))
    return out


def housing_days(rows, segs, plans, cust_lines):
    """Людино-дні місяця для житла, яке вираховує замовник: rows — [{'person', 'glue', 'cust', 'date', 'hours'}] табелів місяця (person —
    контакт для ставки, glue — склеєна людина); дохід дня — години × «Платять нам» плану ставки відрізка (ставки немає — 0).
    Лише замовники ліній. → [{'line', 'cust', 'person' (склеєна), 'date', 'hours', 'revenue'}] за лінією, замовником, людиною, днем."""
    lines = pairs_map(cust_lines)
    pm = {(p['person'], p['cust'], p['from']): p.get('plan') for p in plans or []}
    seg_of = {}
    for s in segs or []:
        seg_of.setdefault((s['person'], s['cust']), []).append(s)
    out = {}
    for r in rows or []:
        line = lines.get(r.get('cust') or 0, 0)
        if not line:
            continue
        d = iso(r['date'])
        price = 0.0
        for s in seg_of.get((r.get('person') or 0, r['cust']), []):
            if s['from'] <= d <= s['to']:
                plan = pm.get((s['person'], s['cust'], s['from'])) if s['person'] else None
                price = float((plan or {}).get('price') or 0)
        k = (line, r['cust'], r.get('glue') or r.get('person') or 0, d)
        row = out.setdefault(k, {'line': k[0], 'cust': k[1], 'person': k[2], 'date': d, 'hours': 0.0, 'revenue': 0.0})
        row['hours'] += float(r.get('hours') or 0)
        row['revenue'] += float(r.get('hours') or 0) * price
    return [out[k] for k in sorted(out)]


def customer_housing(days, day_rules, month_unit=None):
    """Житло, яке вираховує замовник («Хто платить» правила житла лінії «Ставок» — customer), — мінусом у «Житло (вирахував замовник)»
    лінії. day_rules — [{'line', 'cust', 'date', 'payer', 'amount', 'unit'}] — правило _housing_at на день. Одиниці: month_person —
    сума за людину на місяць (людина з годинами в місяці в цього замовника, раз — у її перший день, коли правило діє); month — за
    month_unit (немає — MONTH_UNIT): 'object' — сума раз на місяць на «лінія × замовник» (перший день із годинами, коли правило діє),
    'person' — як month_person; day_person — сума × людино-дні з годинами; hour — сума × години; pct — % від «Платять нам» × години.
    Інше або без суми — 0. → [[лінія, сума]]."""
    rules = {(r['line'], r['cust'], iso(r['date'])): r for r in day_rules or []}
    out = {}
    counted = set()
    for d in sorted(days or [], key=lambda x: (x['date'], x['line'], x['cust'], x['person'])):
        r = rules.get((d['line'], d['cust'], d['date']))
        if not r or r.get('payer') != 'customer' or not r.get('amount'):
            continue
        amount, unit = float(r['amount']), r.get('unit') or ''
        if unit in ('month', 'month_person'):
            per = 'person' if unit == 'month_person' else (month_unit or MONTH_UNIT)
            k = (d['line'], d['cust'], d['person']) if per == 'person' else (d['line'], d['cust'])
            if k in counted:
                continue
            counted.add(k)
            v = amount
        elif unit == 'day_person':
            v = amount
        elif unit == 'hour':
            v = amount * float(d['hours'] or 0)
        elif unit == 'pct':
            v = amount / 100.0 * float(d['revenue'] or 0)
        else:
            continue
        out[d['line']] = out.get(d['line'], 0) - cents(v)
    return [[k, eur(out[k])] for k in sorted(out)]


# ——— підсумок місяця ———
def assemble(hours, entries):
    """Рядки «лінія × місяць» і рядки компанії з годин (hour_rows) і записів розкладу (entry) → [рядок]: ключ розкладено в поля
    row_kind, line_id, customer_id, analytic_id, account_id, card_id, tag; гроші — EUR; «чисто»: лінія — частка + оренда авто + наші
    витрати (житло, житло, яке вирахував замовник, авто, паливо, інструмент, перевезення); офіс і власник — сума; «Не розподілено» — сума (крім буфера житла й грошей
    лінії без лінії); «Не враховано» і рахунки DKV — 0. «Чисто €/год» — на години зі ставкою; «Фонд: залишок» = у фонд + з фонду."""
    rows = {}

    def get(key):
        return rows.setdefault(tuple(key), dict({f: 0 for f in MONEY}, **{h: 0.0 for h in HOURS}, hint=''))
    for h in hours or []:
        r = get(h['key'])
        for f in HOURS:
            r[f] += float(h.get(f) or 0)
        for f in RATE_MONEY:
            r[f] += cents(h.get(f))
    for e in entries or []:
        r = get(e['key'])
        c = cents(e['amount'])
        r[e['field']] += c
        r['aal_amount' if e['src'] == 'aal' else 'dkv_amount'] += c
        if e.get('hint') and not r['hint']:
            r['hint'] = e['hint']
    out = []
    for key in sorted(rows):
        r = rows[key]
        kind, tag = key[0], key[6]
        if kind in ('line', 'noline'):
            net = sum(r[f] for f in ('share', 'car_rent') + NET_COSTS)
        elif kind in ('office', 'owner'):
            net = r['amount']
        elif kind == 'unallocated':
            net = 0 if tag in NOT_OURS else r['amount']
        else:
            net = 0
        hours_rate = round(r['hours_rate'], 2)
        row = {'row_kind': kind, 'line_id': key[1], 'customer_id': key[2], 'analytic_id': key[3], 'account_id': key[4], 'card_id': key[5],
               'tag': tag, 'hint': r['hint'], 'net': eur(net), 'net_per_hour': round(eur(net) / hours_rate, 2) if hours_rate else 0.0,
               'fund_left': eur(r['fund_in'] + r['fund_paid'])}
        row.update({f: round(r[f], 2) for f in HOURS})
        row.update({f: eur(r[f]) for f in MONEY})
        out.append(row)
    return out


def kind_label(kind, lang='uk'):
    return t('k_' + kind, lang) if ('k_' + str(kind)) in TX else str(kind or '')


def row_name(row, names, lang='uk'):
    """Назва рядка підсумку. names — {'line', 'customer', 'analytic', 'account', 'card'} — назви записів ключа (немає — '')."""
    kind, tag = row['row_kind'], row.get('tag') or ''
    if kind == 'line':
        return names.get('line') or '—'
    if kind == 'noline':
        return t('n_noline', lang) % (names.get('customer') or t('n_nocust', lang))
    if kind == 'dkv':
        return t('n_dkv', lang)
    if kind == 'owner':
        return t('n_owner', lang) % (names.get('analytic') or '—')
    if kind == 'office':
        return t('n_office', lang) % (names.get('analytic') or names.get('card') or '—')
    if kind == 'ignore':
        return t('n_ignore', lang) % (names.get('analytic') or '—')
    if tag == 'dkv_bill':
        return t('n_un', lang) % (t('n_dkv_bill', lang) % (names.get('analytic') or '—'))
    if tag == 'dkv_card':
        return t('n_un', lang) % (t('n_dkv_card', lang) % (names.get('card') or '—'))
    if tag == 'transport_none':
        return t('n_un', lang) % t('n_transport_none', lang)
    if tag == 'dkv_diff':
        return t('n_un', lang) % (t('n_dkv_diff', lang) % (names.get('analytic') or '—'))
    base = ' · '.join(x for x in (names.get('analytic'), names.get('account')) if x) or '—'
    if tag:
        base = t('n_no_line', lang) % (base, kind_label(tag, lang))
    elif row.get('hint'):
        base = t('n_hint', lang) % (base, kind_label(row['hint'], lang))
    return t('n_un', lang) % base


def verify(rows, aal_expected, dkv_expected, month, lang='uk', raw=None, moved=0.0, nobill=None):
    """Звірка місяця: сума грошей аналітики в рядках = аналітика плану за місяць (та сама вибірка, частини періодів і курс) до цента;
    гроші DKV у рядках = мінус брутто транзакцій місяця; у кожному рядку стовпці SPREAD дають aal_amount + dkv_amount.
    raw — окремий підрахунок аналітики плану за датою (read_group, не через розклад) по валютах фірм: [{'currency', 'books', 'calc'}]
    (calc — ті самі рядки в розкладі, повні суми, у валюті фірми); різниця — розбіжність. moved — скільки EUR періоди послуги перенесли
    між місяцями (довідково). nobill — {'n', 'amount'}: транзакції DKV місяця без рахунку в Odoo (довідково; разом із рядками «рахунок DKV
    без транзакцій» — попередження про можливий подвійний рахунок).
    rows — [{'name', 'tag', поля SPREAD, 'aal_amount', 'dkv_amount'}] → {'ok', 'aal', 'aal_expected', 'dkv', 'dkv_expected', 'bad',
    'raw_bad', 'text'}."""
    aal = sum(cents(r.get('aal_amount')) for r in rows or [])
    dkv = sum(cents(r.get('dkv_amount')) for r in rows or [])
    bad = [r.get('name') or '—' for r in rows or []
           if sum(cents(r.get(f)) for f in SPREAD) != cents(r.get('aal_amount')) + cents(r.get('dkv_amount'))]
    raw_bad = [x for x in raw or [] if cents(x.get('books')) != cents(x.get('calc'))]
    want_aal, want_dkv = cents(aal_expected), cents(dkv_expected)
    ok = aal == want_aal and dkv == want_dkv and not bad and not raw_bad
    m = fmt_month(month)
    if ok:
        parts = [t('v_ok', lang) % (m, money_text(eur(aal)), money_text(eur(dkv)))]
    else:
        parts = [t('v_bad', lang) % m]
        if aal != want_aal:
            parts.append(t('v_aal', lang) % (money_text(eur(aal)), money_text(eur(want_aal)), money_text(eur(aal - want_aal))))
        if dkv != want_dkv:
            parts.append(t('v_dkv', lang) % (money_text(eur(dkv)), money_text(eur(want_dkv)), money_text(eur(dkv - want_dkv))))
        for x in raw_bad:
            parts.append(t('v_raw', lang) % (x.get('currency') or '—', money_text(x.get('books')), money_text(x.get('calc')),
                                             money_text(eur(cents(x.get('calc')) - cents(x.get('books'))))))
        if bad:
            parts.append(t('v_rows', lang) % ', '.join(bad[:5]))
    if cents(moved):
        parts.append(t('v_moved', lang) % money_text(moved))
    nb = nobill or {}
    if nb.get('n'):
        parts.append(t('v_nobill', lang) % (nb['n'], money_text(nb.get('amount'))))
        unbilled = sum(cents(r.get('amount')) for r in rows or [] if (r.get('tag') or '') == 'dkv_bill')
        if unbilled:
            parts.append(t('v_double', lang) % money_text(eur(unbilled)))
    return {'ok': ok, 'aal': eur(aal), 'aal_expected': eur(want_aal), 'dkv': eur(dkv), 'dkv_expected': eur(want_dkv), 'bad': bad,
            'raw_bad': raw_bad, 'text': ' '.join(parts)}


# ——— екрани (круг 2): місяці ———
def add_months(day, n):
    """Перше число місяця, на n місяців пізніше (n < 0 — раніше) за місяць day."""
    s = month_first(day)
    k = int(s[0:4]) * 12 + int(s[5:7]) - 1 + int(n)
    return '%04d-%02d-01' % (k // 12, k % 12 + 1)


def valid_day(value):
    """«РРРР-ММ-ДД» або «РРРР-ММ» (тоді перше число) → «РРРР-ММ-ДД»; хибне або порожнє — ''."""
    s = str(value or '').strip()[0:10]
    if re.match(r'^\d{4}-\d{2}$', s):
        s += '-01'
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return ''
    try:
        to_date(s)
    except ValueError:
        return ''
    return s


def last_closed(today):
    """Останній закритий місяць (минулий щодо today), не раніше MIN_MONTH."""
    return max(MIN_MONTH, add_months(today, -1))


def screen_month(value, today):
    """Місяць екрана: перше число місяця value; раніше MIN_MONTH — MIN_MONTH, пізніше поточного — поточний; немає або хибне — останній
    закритий (last_closed)."""
    day = valid_day(value)
    if not day:
        return last_closed(today)
    return min(max(month_first(day), MIN_MONTH), max(month_first(today), MIN_MONTH))


def screen_period(date_from, date_to, today, n=MATRIX_MONTHS, cap=MATRIX_MAX):
    """Місяці «Лінії × місяці»: від date_from до date_to (screen_month кожен; переплутані — міняються місцями); немає «до» — останній
    закритий, немає «з» — n місяців до «до», не раніше MIN_MONTH; довше cap — останні cap. → ['РРРР-ММ-01', …]."""
    to = screen_month(date_to, today) if valid_day(date_to) else last_closed(today)
    frm = screen_month(date_from, today) if valid_day(date_from) else max(MIN_MONTH, add_months(to, -(n - 1)))
    if frm > to:
        frm, to = to, frm
    return month_list(frm, to)[-cap:]


def month_label(day, lang='uk', short=False):
    """«вересень 2026» (short — «Вер»), мовою користувача."""
    s = iso(day)
    i = int(s[5:7]) - 1
    names = TX['months_short' if short else 'months'][1 if lang == 'ru' else 0]
    return names[i] if short else '%s %s' % (names[i], s[0:4])


def months_text(n, lang='uk'):
    """6 → «6 місяців» (1 — «місяць», 2–4 — «місяці», 11–14 — «місяців»), російською — «месяц / месяца / месяцев»."""
    n = int(n)
    forms = ('месяц', 'месяца', 'месяцев') if lang == 'ru' else ('місяць', 'місяці', 'місяців')
    if n % 10 == 1 and n % 100 != 11:
        word = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = forms[1]
    else:
        word = forms[2]
    return '%d %s' % (n, word)


def month_info(day, lang='uk'):
    """Місяць для екрана → {'value', 'label' («вересень 2026»), 'title' («Вересень 2026»), 'name' («вересень»), 'short' («Вер»)}."""
    label = month_label(day, lang)
    return {'value': month_first(day), 'label': label, 'title': label[0:1].upper() + label[1:], 'name': label.split(' ')[0],
            'short': month_label(day, lang, True)}


def month_axis(days, lang='uk', short=True):
    """Підписи місяців на осі графіка й у шапці таблиці: «Вер» (short) або «Вересень»; період зачіпає більше одного календарного року —
    з коротким роком: «Січ '27», «Грудень '26» (інакше з 2027 «Січ» повториться). days — ['РРРР-ММ-ДД', …] → [підпис]."""
    days = [iso(d) for d in (days or [])]
    many = len({d[0:4] for d in days}) > 1
    out = []
    for d in days:
        s = month_label(d, lang, short)
        s = s[0:1].upper() + s[1:] if short else s.split(' ')[0][0:1].upper() + s.split(' ')[0][1:]
        out.append("%s '%s" % (s, d[2:4]) if many else s)
    return out


def parse_id(value):
    """id запису з params / localStorage: ціле > 0 або рядок із цифр → int; інше (текст, bool, дробове, від'ємне, порожньо) — 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else 0
    s = str(value or '').strip()
    return int(s) if re.match(r'^\d{1,18}$', s) and int(s) > 0 else 0


def housing_from(payers):
    """Чип плитки «житло не з наших грошей»: платники правил житла лінії на день (лінії й її замовників, ключі «Ставок») → 'partner'
    (є «Платить партнер» — з буфера лінії), 'worker_fund' (є фонд працівників), інакше ''."""
    keys = {housing_payer(p) for p in (payers or []) if p}
    return 'partner' if 'partner' in keys else ('worker_fund' if 'worker_fund' in keys else '')


def parse_num(text, default):
    """Число з системного параметра («1,5», «2») → float; порожньо, хибне або від'ємне — default."""
    try:
        v = float(str(text).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


# ——— екрани: числа ———
def num_text(x):
    """Число для тексту рядка: 2113 → «2 113», 12.5 → «12,5», −1234.25 → «−1 234,25» (тисячі — вузький пробіл, без зайвих нулів)."""
    v = round(float(x or 0), 2)
    whole, frac = ('%.2f' % abs(v)).split('.')
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    s = ' '.join([whole] + groups)
    frac = frac.rstrip('0')
    return ('−' if v < 0 else '') + s + (',' + frac if frac else '')


def per_hour(amount, hours):
    """Гроші на годину (на години зі ставкою): годин немає — None (екран пише «—», а не «0,00 € год»)."""
    return round(float(amount or 0) / float(hours), 2) if hours else None


def totals(rows):
    """Підсумок рядків ліній місяця (або однієї лінії) → {'hours', 'hours_rate', 'hours_no_rate', 'share', 'costs', 'net', 'share_ph',
    'costs_ph', 'net_ph'}: витрати — усе між часткою й «чисто» (оренда авто плюсом; житло, житло замовника, авто, паливо, інструмент,
    перевезення — мінусом), частка + витрати = чисто до цента; €/год — на години зі ставкою (їх немає — None)."""
    rows = rows or []
    hours_rate = round(sum(float(r.get('hours_rate') or 0) for r in rows), 2)
    share = sum(cents(r.get('share')) for r in rows)
    net = sum(cents(r.get('net')) for r in rows)
    return {'hours': round(sum(float(r.get('hours_total') or 0) for r in rows), 2), 'hours_rate': hours_rate,
            'hours_no_rate': round(sum(float(r.get('hours_no_rate') or 0) for r in rows), 2), 'share': eur(share), 'costs': eur(net - share),
            'net': eur(net), 'share_ph': per_hour(eur(share), hours_rate), 'costs_ph': per_hour(eur(net - share), hours_rate),
            'net_ph': per_hour(eur(net), hours_rate)}


def chain_steps(row):
    """Ланцюжок лінії за місяць: частка → оренда авто → житло → житло (вирахував замовник) → авто → паливо → інструмент → перевезення →
    чисто. Нульові кроки (крім частки й «чисто») не показуються. Крок — {'key', 'amount' (€ за місяць), 'ph' (€/год на години зі ставкою;
    їх немає — None)};
    «чисто» — збережене чисто рядка: кроки до нього дають його до цента (розбіжність — помилка розрахунку, її ловить тест)."""
    hr = float(row.get('hours_rate') or 0)
    out = []
    for f in CHAIN:
        c = cents(row.get(f))
        if f == 'share' or c:
            out.append({'key': f, 'amount': eur(c), 'ph': per_hour(eur(c), hr)})
    net = cents(row.get('net'))
    out.append({'key': 'net', 'amount': eur(net), 'ph': per_hour(eur(net), hr)})
    return out


def waterfall(steps):
    """Геометрія водоспаду картки лінії з кроків chain_steps: перший стовпець (частка) і «чисто» — від нуля, між ними — від поточного
    підсумку до наступного. Шкала — від min(0, підсумків) до max(0, підсумків). → {'bars': [крок + {'lo', 'hi', 'dir' ('total' / 'down' /
    'up'), 'bottom', 'height', 'end'}], 'zero'}: bottom, height і zero (де нуль) — % висоти поля; end — рівень підсумку після кроку (%),
    звідти пунктир до наступного стовпця (круг 3а)."""
    run = 0
    raw = []
    for i, s in enumerate(steps or []):
        c = cents(s.get('amount'))
        if i == 0 or s.get('key') == 'net':
            lo, hi = min(0, c), max(0, c)
            run = c
            d = 'total'
        else:
            lo, hi = min(run, run + c), max(run, run + c)
            d = 'down' if c < 0 else 'up'
            run += c
        raw.append((s, lo, hi, d, run))
    vals = [0] + [x[1] for x in raw] + [x[2] for x in raw]
    bottom, span = min(vals), max(vals) - min(vals)

    def pct(v):
        return round(v * 100.0 / span, 2) if span else 0.0
    bars = [dict(s, lo=eur(lo), hi=eur(hi), dir=d, bottom=pct(lo - bottom), height=pct(hi - lo), end=pct(r - bottom))
            for s, lo, hi, d, r in raw]
    return {'bars': bars, 'zero': pct(-bottom)}


def share_split(row):
    """«Куди йде частка» лінії: частка + оренда авто розкладені на чисто, житло (разом із тим, що вирахував замовник), авто, паливо,
    інструмент, перевезення. Частина — витрата додатним числом; «чисто» нижче нуля — 0 (смуга — лише витрати). → [{'key', 'amount',
    'pct'}] у порядку SPLIT, без нульових; pct — частка суми частин у % (сума — рівно 100); нічого немає — []."""
    def spend(*fields):
        return max(-sum(cents(row.get(f)) for f in fields), 0)
    parts = [('net', max(cents(row.get('net')), 0)), ('housing', spend('housing', 'housing_customer')), ('auto', spend('auto')),
             ('fuel', spend('fuel')), ('tool', spend('tool')), ('transport', spend('transport'))]
    parts = [(k, v) for k, v in parts if v > 0]
    pct = dict(split_cents(10000, parts))
    return [{'key': k, 'amount': eur(v), 'pct': pct.get(k, 0) / 100.0} for k, v in parts]


def cell_tone(net, net_ph, prev_ph, threshold=DROP_DEFAULT):
    """Колір клітинки «Лінії × місяці»: 'neg' — чисто нижче нуля; 'drop' — чисто €/год упало до минулого місяця більше, ніж на threshold
    (рівно — ні; минулого місяця немає, prev_ph None — ні); інакше ''. Червоне важливіше за жовте. Годин зі ставкою немає (net_ph None —
    клітинка «—»): заливки ніколи немає; чисто місяця нижче нуля — 'neg_text' (дрібно червоним сума за місяць), інакше '' (круг 3а)."""
    if net_ph is None:
        return 'neg_text' if cents(net) < 0 else ''
    if cents(net) < 0:
        return 'neg'
    if prev_ph is not None and cents(prev_ph) - cents(net_ph) > cents(threshold):
        return 'drop'
    return ''


def trend(cur_ph, prev_ph):
    """Стрілка плитки до минулого місяця: → {'dir': 'up' / 'down' / 'same', 'delta' (модуль різниці €/год)}; минулого або цього
    (годин зі ставкою немає — None) немає — None: стрілки від нуля немає."""
    if prev_ph is None or cur_ph is None:
        return None
    d = cents(cur_ph) - cents(prev_ph)
    return {'dir': 'up' if d > 0 else ('down' if d < 0 else 'same'), 'delta': eur(abs(d))}


def housing_shift(rows, month, ratio=HOUSING_SHIFT_DEFAULT):
    """«Житло не в своєму місяці» — лінії, у яких наше житло місяця month не схоже на інші місяці лінії: житло є, а годин у місяці немає;
    або житло на годину більше за ratio × медіану житла на годину інших місяців лінії (лише місяці з житлом і годинами; таких треба ≥ 2).
    Типово — рахунок за два місяці одним рядком або оренда в місяці майже без годин. rows — [{'line', 'month', 'housing', 'hours'}] →
    [лінія] за id."""
    by_line = {}
    for r in rows or []:
        by_line.setdefault(r['line'], []).append(r)
    out = []
    for line in sorted(by_line):
        mine = [r for r in by_line[line] if month_first(r['month']) == month_first(month)]
        h = abs(sum(cents(r.get('housing')) for r in mine))
        hours = sum(float(r.get('hours') or 0) for r in mine)
        if not h:
            continue
        if not hours:
            out.append(line)
            continue
        others = sorted(abs(cents(r.get('housing'))) / float(r['hours']) for r in by_line[line]
                        if month_first(r['month']) != month_first(month) and cents(r.get('housing')) and float(r.get('hours') or 0))
        if len(others) < 2:
            continue
        mid = len(others) // 2
        median = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2.0
        if h / hours > ratio * median:
            out.append(line)
    return out


# ——— екрани (круг 3а): вісь графіка, порядок плиток, «Хто платить» ———
def percentile(values, p):
    """Перцентиль p (0–100) чисел values (None пропускаються) з лінійною інтерполяцією між сусідніми; чисел немає — None."""
    v = sorted(float(x) for x in (values or []) if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * float(p) / 100.0
    f = int(math.floor(k))
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def axis_range(values, low=AXIS_LOW, high=AXIS_HIGH, pct=AXIS_PCT, step=AXIS_STEP):
    """Вісь Y «Чисто по місяцях» без викидів: від min(low, pct-й перцентиль) до max(high, (100 − pct)-й), межі — кратні step назовні.
    → {'min', 'max', 'out': [індекси values поза віссю]} (None — не число, не викид)."""
    vals = [x for x in (values or []) if x is not None]
    lo, hi = float(low), float(high)
    if vals:
        lo = min(lo, percentile(vals, pct))
        hi = max(hi, percentile(vals, 100 - pct))
    lo = int(math.floor(lo / step) * step)
    hi = int(math.ceil(hi / step) * step)
    return {'min': lo, 'max': hi, 'out': [i for i, x in enumerate(values or []) if x is not None and (x < lo or x > hi)]}


def chart_axis(series, labels, **kw):
    """Вісь графіка «Огляду» на всі ряди разом (series — [{'name', 'values'}], ряд за рядом; labels — підписи місяців осі): axis_range
    + викиди з підписом «назва місяць число» (місяць — з малої, число — ціле). → {'min', 'max', 'out': [{'s' (номер ряду),
    'i' (номер місяця), 'value', 'text'}]}."""
    flat = []
    for s, ser in enumerate(series or []):
        for i, v in enumerate(ser.get('values') or []):
            flat.append((s, i, v))
    rng = axis_range([v for _s, _i, v in flat], **kw)
    out = []
    for k in rng['out']:
        s, i, v = flat[k]
        month = str(labels[i]) if i < len(labels or []) else ''
        out.append({'s': s, 'i': i, 'value': v,
                    'text': ' '.join(x for x in (series[s].get('name') or '', month[0:1].lower() + month[1:], num_text(round(v))) if x)})
    return {'min': rng['min'], 'max': rng['max'], 'out': out}


def tile_order(tiles):
    """Плитки «Огляду» — за «чисто € год» за зростанням (збиткові першими); «—» (net_ph None) — у кінці; рівні — як були."""
    return [t for _k, t in sorted(enumerate(tiles or []), key=lambda x: (x[1].get('net_ph') is None, x[1].get('net_ph') or 0, x[0]))]


def _low(text):
    s = str(text or '')
    return s[0:1].lower() + s[1:]


def pays_fold(items, lang='uk'):
    """«Хто платить» картки лінії згорнуто: платник більшості видів — одним рядком («Усе платимо ми (у «чисто»)»), нижче — лише винятки:
    вид з іншим платником і замовники лінії зі своїм правилом. items — [{'label', 'payer_key' (ключ PAYER_KEYS), 'payer' (підпис), 'except':
    [{'name', 'payer_key', 'payer'}], 'cards'}]. Рівно — перший ключ PAYER_KEYS (ми). → {'head', 'rows': [{'label', 'payer'}] (платник з
    малої), 'cards': [картки DKV палива]}; видів немає — head ''."""
    items = items or []
    keys = [i.get('payer_key') or 'we' for i in items]
    if not keys:
        return {'head': '', 'rows': [], 'cards': []}
    common = max(PAYER_KEYS, key=lambda k: (keys.count(k), -PAYER_KEYS.index(k)))
    rows, cards = [], []
    for it, k in zip(items, keys):
        if k != common:
            rows.append({'label': it.get('label') or '', 'payer': _low(it.get('payer'))})
        for ex in it.get('except') or []:
            rows.append({'label': '%s · %s' % (it.get('label') or '', ex.get('name') or ''), 'payer': _low(ex.get('payer'))})
        cards += [c for c in it.get('cards') or [] if c not in cards]
    return {'head': t('p_all_' + common, lang), 'rows': rows, 'cards': cards}
