# -*- coding: utf-8 -*-
"""Чисті функції «Фінансів» (круг 1 — драбина, круг 2 — екран «Власник»): без Odoo і без бази — на словниках, списках і рядках «РРРР-ММ-ДД».

Модель (models/workset_finance_month.py) читає базу, кличе ці функції й пише підсумок «місяць драбини»; тести без Odoo запускають їх
напряму (файл не імпортує odoo й інших файлів модуля). Гроші — EUR, знак як у книгах (витрата — мінус), крім «довідкових» ступенів, які
показуються додатним числом (доли, власники, перекази). Усередині — цілі центи: драбина сходиться до цента.

Драбина власника (ключі полів місяця):
  base = s1_docs + s1a_owners_in_pl + s1g_no_analytic + s2_ukraine          («до часток»)
  s4_net = base − s3_shares                                                  («чистий прибуток бізнесу»)
  s4c_cash = base − cash_out                                                 («за грошима»: cash_out — перекази партнерам нетто)
Окремо, не в драбині: s5_owners (власники), s6_stage2 («II етап»), s7_* (неоплачено).
"""
import datetime
import json
import re
from decimal import ROUND_HALF_UP, Decimal

MIN_MONTH = '2026-01-01'   # екрани й перерахунок — лише з січня 2026 (рішення власника 06.11: «цікавить лише 2026 рік»)
RECALC_MONTHS = 4          # нічний перерахунок — 3 останні місяці й поточний (4 разом)
MAX_MONTHS = 36            # перерахунок за період — не більше стільки місяців за раз
TOL_CENTS = 1              # допуск контролю другим способом: 0,01 (одна копійка)
STAGE2_MULT = 9            # переказ партнеру нетто = рядок аналітики статті «II етапу» × 9 (там лежить 10 %)
STAGE2_BRUTTO = 10         # брутто переказу партнеру = рядок аналітики «II етапу» × 10 (показуємо в рядку «за грошима»)
BILLS_LOOKBACK_MONTHS = 6  # «є рахунки постачальника» — з 1 липня минулого року (як BILLS_LOOKBACK_MONTHS сторожа tools/lib/odoo_pnl_guard.js)
TRUTH_PCT = 1.0            # поріг «місяць правдивий» (рішення власника 14.10.2026: 1 %), як MC.truthShare
UNPAID_MIN = 0.005         # залишок рахунку менший за це — вважається оплаченим
MAX_ID = 2147483647        # найбільший id запису (integer PostgreSQL): лінія пари більша — пропуск (інакше browse упаде на SQL)
# Пробільні символи країв фрази — ОДИН набір із PHRASE_WS у tools/lib/odoo_finance_ladder.js: str.strip() Python і String.prototype.trim() JS
# ріжуть різне (strip — ще \x1c–\x1f і \x85, trim — ще BOM U+FEFF), тому обидві сторони беруть об'єднання (круг 1д). Пробіл нульової ширини
# (U+200B) не пробільний ні там, ні там — лишається у фразі.
PHRASE_WS = ''.join(chr(c) for c in (*range(0x09, 0x0e), *range(0x1c, 0x21), 0x85, 0xa0, 0x1680, *range(0x2000, 0x200b), 0x2028, 0x2029, 0x202f,
                                     0x205f, 0x3000, 0xfeff))
# Фраза-число (сума, рік, номер документа: «123», «12,5», «1 234,56») — не фраза: такий підрядок знайшовся б у чужих рядках. Межа — стільки
# цифр; довший ряд цифр (хвіст рахунку чи картки, від 8 цифр) — ідентифікатор, фраза. Як PHRASE_NUM_MAX_DIGITS у JS-драбині.
PHRASE_NUM_MAX_DIGITS = 7
_NUM_RE = re.compile(r'[+-]?[0-9]+(?:[.,][0-9]+)?')

PL_TYPES = ('income', 'income_other', 'expense', 'expense_other', 'expense_depreciation', 'expense_direct_cost')  # tools/lib/odoo_pnl_types.js
INCOME_TYPES = ('income', 'income_other')
OUT_TYPES = ('out_invoice', 'out_refund')   # «не оплачено» (ступінь 7): рахунки й повернення клієнтам
EXT_TYPES = ('out_invoice', 'out_refund', 'out_receipt')   # оборот із зовнішніми: усі out_*, і чеки продажу, як /^out_/ у MC.truthTurnover
PAID_STATES = ('paid', 'in_payment')
UNPAID_SKIP = ('paid', 'in_payment', 'reversed')

# Системні параметри (пише інструмент штабу, --seed; у коді — лише імена). (ім'я без префікса, вид, обов'язковий)
P_PREFIX = 'workset_finance.'
PARAM_SPECS = (
    ('recruting_plan_id', 'id', True),            # корінь плану рекрутингу
    ('owners_plan_id', 'id', True),               # вузол плану «власники» всередині рекрутингу
    ('owners_share_analytic_ids', 'ids', False),  # статті власників, що є «часткою» (решта — витрати компанії)
    ('ukraine_company_id', 'id', True),           # фірма Україна
    ('ukraine_plan_id', 'id', True),              # вузол плану «Україна»
    ('liquidity_account_id', 'id', True),         # рахунок ліквідності (передача в Україну)
    ('stage2_analytic_id', 'id', True),           # стаття аналітики «II етап»
    ('stage2_account_id', 'id', True),            # рахунок «II етапу»
    ('stage2_partner_lines', 'phrasepairs', False),  # JSON-масив пар [фраза, id лінії]; збіг — перша пара за порядком масиву; приватний посів
    ('stage2_transit_phrases', 'phrases', False),  # JSON-масив фраз транзиту «II етапу»: у «за грошима» не входять; приватний посів
    ('an_envelope_analytic_id', 'id', False),     # конверт AN
    ('mediation_plan_id', 'id', False),           # план посередництва
    ('energy_partner_ids', 'ids', False),         # контрагенти групи Energy (неоплачені — окремо від замовників)
    ('excluded_partner_ids', 'ids', False),       # посередники й «лише списання» — виняток управлінської аналітики (X.noAnalyticsIds)
    ('mediator_partner_ids', 'ids', False),       # посередники (X.MGMT_EXCLUDED_IDS): оборот без них, у банку — не «без документа»
    ('quality_transit_move_ids', 'ids', False),   # документи «транзиту Energy» (account.move): поза кошиками «якості даних»
    ('transfer_phrases', 'phrases', False),       # JSON-масив фраз банку «на особистий рахунок» (звичайний рядок — одна фраза)
    ('transfer_partner_ids', 'ids', False),       # одержувачі переказів на особисті рахунки
    ('zus_partner_ids', 'ids', False),            # контрагенти ZUS / PIT-4
    ('payroll_journal_ids', 'ids', False),        # журнали зарплати
)
P_ALL = tuple(P_PREFIX + n for n, _k, _r in PARAM_SPECS)

# Ступені: (ключ поля, рівень). Для кожного є <ключ>_src (fact / calc / adjust).
STEPS = ('s1_docs', 's1a_owners_in_pl', 's1g_no_analytic', 's2_ukraine', 'base', 's3_shares', 's4_net', 's4c_cash', 's5_owners', 's6_stage2',
         's7_unpaid_customers', 's7_unpaid_energy')
ADJUST_STEPS = ('s1g', 's3')   # ступені, які править людина поправкою
# усі числа, що віддає перерахунок (і порівнює зовнішня драбина JS): ключі, крім *_src
MONEY = STEPS + ('s5_owners_share', 'revenue', 'payroll', 'contrib_zus', 'cash_out', 'mediation_income', 'mediation_cost', 'mediation_net',
                 'g_cand_amount', 'q_ic', 'q_unplaced', 'q_nodoc', 'q_ext')
KINDS = ('step', 'owner', 'owner_share', 'transfer', 'stage2', 'cash', 'cash_other', 'unpaid', 'quality', 'ukraine', 'candidate', 'adjust')

TX = {
    'param_missing': ['Параметр %s не посіяно: запустіть --seed інструмента штабу.', 'Параметр %s не посеян: запустите --seed инструмента штаба.'],
    'only_editor': ['Перераховувати може лише редактор «Фінансів» («Поправки»).', 'Пересчитывать может только редактор «Финансов» («Поправки»).'],
    'only_group': ['Цей екран бачить лише група «Фінанси: %s».', 'Этот экран видит только группа «Финансы: %s».'],
    'month_range': ['Місяць %s: «Фінанси» рахують лише з січня 2026.', 'Месяц %s: «Финансы» считают только с января 2026.'],
    'month_first': ['Місяць має бути першим числом.', 'Месяц должен быть первым числом.'],
    'basis': ['Поправка без підстави не буває: вкажіть «Підстава».', 'Поправка без основания не бывает: укажите «Основание».'],
    'recalc_done': ['Перераховано місяців: %d.', 'Пересчитано месяцев: %d.'],
    'v_ok': ['Звірка %s: зійшлося — ступінь 1 за аналітикою і за проводками, драбина сходиться.',
             'Сверка %s: сошлось — ступень 1 по аналитике и по проводкам, лестница сходится.'],
    'v_bad': ['Звірка %s: не зійшлася.', 'Сверка %s: не сошлась.'],
    'v_control': ['Ступінь 1: аналітика %s, проводки (інший спосіб) %s, різниця %s.', 'Ступень 1: аналитика %s, проводки (другой способ) %s, разница %s.'],
    'v_ladder': ['Драбина не сходиться: до часток %s проти суми ступенів %s, чистий прибуток %s проти %s.',
                 'Лестница не сходится: до долей %s против суммы ступеней %s, чистая прибыль %s против %s.'],
    'v_cand': ['1г: кандидатів %d на %s, поправка 1г %s.', '1г: кандидатов %d на %s, поправка 1г %s.'],
    'v_cand_diff': ['Поправка 1г ≠ кандидати.', 'Поправка 1г ≠ кандидаты.'],
    'v_quality': ['Якість даних: %s %% (поріг %s %%).', 'Качество данных: %s %% (порог %s %%).'],
    'v_none': ['Звірка %s: перерахунку ще не було.', 'Сверка %s: пересчёта ещё не было.'],
    'month_many': ['Забагато місяців за раз: %d (не більше %d).', 'Слишком много месяцев за раз: %d (не больше %d).'],
    'line_missing': ['Лінія партнера в карті «II етапу» не знайдена: %d (перекази рахуються, лінії в рядку немає).',
                     'Линия партнёра в карте «II этапа» не найдена: %d (переводы считаются, линии в строке нет).'],
    'phrase_number': ['Параметр %s: фраз-чисел пропущено %d (сума, рік чи номер до %d цифр збігся б із чужими рядками) — виправте посів.',
                      'Параметр %s: фраз-чисел пропущено %d (сумма, год или номер до %d цифр совпал бы с чужими строками) — исправьте посев.'],
    'not_in_4': ["в 4' не входить", "в 4' не входит"],
    'line_gone': ['лінії немає в базі', 'линии нет в базе'],
    'unknown': ['не розпізнано', 'не распознано'],
    'transit': ['транзит — виключено', 'транзит — исключено'],
    'gross': ['брутто (×10): %s', 'брутто (×10): %s'],
    'cleared': ['розібрано', 'разобрано'],
    'nodoc': ['без документа', 'без документа'],
    'nodoc_zusTax': ['Банк без документа: ZUS і податки', 'Банк без документа: ZUS и налоги'],
    'nodoc_noPartner': ['Банк без документа: без партнера', 'Банк без документа: без партнёра'],
    'nodoc_noBillsFirm': ['Банк без документа: фірма без рахунків', 'Банк без документа: фирма без счетов'],
    'nodoc_noBillsPerson': ['Банк без документа: фізособа без рахунків', 'Банк без документа: физлицо без счетов'],
    'no_analytic_row': ['проводка без рядка аналітики статті — взято рядок рахунку', 'проводка без строки аналитики статьи — взята строка счёта'],
    'stage2_other': ["«II етап»: рядків рахунку з іншою статтею аналітики (не в 4'): %d на %s.",
                     "«II этап»: строк счёта с другой статьёй аналитики (не в 4'): %d на %s."],
    'mediator_missing': ['Параметр %s не посіяно: посередники рахуються в обороті із зовнішніми й у «банку без документа» — запустіть --seed.',
                         'Параметр %s не посеян: посредники считаются в обороте с внешними и в «банке без документа» — запустите --seed.'],
    # круг 2, екран «Власник»: рядки таблиці «Драбина» (r_), стовпці водоспаду (w_), ряди графіка (l_), підказки (h_), заголовки списків кліку (o_)
    'r_s1_docs': ['Рекрутинг за документами', 'Рекрутинг по документам'],
    'r_s1a_owners_in_pl': ['Власники в P&L', 'Владельцы в P&L'],
    'r_s1g_no_analytic': ['Дохід без аналітики', 'Доход без аналитики'],
    'r_s2_ukraine': ['− Україна', '− Украина'],
    'r_base': ['До часток', 'До долей'],
    'r_s3_shares': ['− Частки партнерів', '− Доли партнёров'],
    'r_s4_net': ['Чистий прибуток', 'Чистая прибыль'],
    'r_s4c_cash': ['За грошима', 'По деньгам'],
    'w_s1_docs': ['Рекрутинг', 'Рекрутинг'],
    'w_s1a_owners_in_pl': ['Власники в P&L', 'Владельцы в P&L'],
    'w_s1g_no_analytic': ['Дохід без аналітики', 'Доход без аналитики'],
    'w_s2_ukraine': ['Україна', 'Украина'],
    'w_base': ['До часток', 'До долей'],
    'w_s3_shares': ['Частки партнерів', 'Доли партнёров'],
    'w_s4_net': ['Чистий прибуток', 'Чистая прибыль'],
    'l_net': ['чистий прибуток', 'чистая прибыль'],
    'l_base': ['до часток', 'до долей'],
    'h_calc': ['розрахунок штабу', 'расчёт штаба'],
    'h_adjust': ['поправка: %s', 'поправка: %s'],
    'h_open': ['Відкрити записи', 'Открыть записи'],
    'h_partial': ['неповний, по %s', 'неполный, по %s'],
    'o_of': ['%s · %s', '%s · %s'],
    'o_recruiting': ['Рекрутинг разом (1 + 1а + 1г)', 'Рекрутинг итого (1 + 1а + 1г)'],
    'o_cash': ["За грошима: що віднімається (4')", "По деньгам: что вычитается (4')"],
    'o_adjust': ['Поправки', 'Поправки'],
    'o_quality': ['Якість даних: кошики', 'Качество данных: корзины'],
    'o_check': ['Звірка', 'Сверка'],
    'o_owners': ['Власники', 'Владельцы'],
    'o_transfers': ['Перекази на особисті рахунки', 'Переводы на личные счета'],
    'o_stage2': ['II етап', 'II этап'],
    'o_unpaid_customer': ['Не оплачено замовниками', 'Не оплачено заказчиками'],
    'o_unpaid_energy': ['Не оплачено %s', 'Не оплачено %s'],   # %s — назва групи Energy з Odoo (energy_name), не з коду
    'o_months': ['Місяці драбини', 'Месяцы лестницы'],
    'o_profit': ['Прибутковість ліній', 'Прибыльность линий'],
    'q_ic': ['Міжфірмові', 'Межфирменные'],
    'q_unplaced': ['Без місця', 'Без места'],
    'h_quality': ['Не розібрано %s € із %s € обороту, поріг %s %% — клік: три кошики', 'Не разобрано %s € из %s € оборота, порог %s %% — клик: три корзины'],
}


def lang_of(code):
    return 1 if str(code or '').lower().startswith('ru') else 0


def t(key, lang=0):
    return TX[key][lang]


# ——— дати й місяці ———
def iso(d):
    """date / datetime / «РРРР-ММ-ДД…» → «РРРР-ММ-ДД»; порожнє → ''."""
    if not d:
        return ''
    if isinstance(d, (datetime.date, datetime.datetime)):
        return d.strftime('%Y-%m-%d')
    return str(d)[0:10]


def to_date(s):
    return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def month_first(d):
    return iso(d)[0:7] + '-01'


def month_last(first):
    y, m = int(first[0:4]), int(first[5:7])
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - datetime.timedelta(days=1)).strftime('%Y-%m-%d')


def add_days(day, n):
    return (to_date(iso(day)) + datetime.timedelta(days=n)).strftime('%Y-%m-%d')


def add_months(first, n):
    y, m = int(first[0:4]), int(first[5:7]) - 1 + n
    return '%04d-%02d-01' % (y + m // 12, m % 12 + 1)


def month_list(a, b):
    """Місяці від a до b включно (першими числами); b раніше за a — порожньо."""
    out, cur, end = [], month_first(a), month_first(b)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def months_back(today, n=RECALC_MONTHS):
    """n останніх місяців разом із поточним, але не раніше MIN_MONTH."""
    cur = month_first(today)
    return [m for m in month_list(add_months(cur, -(n - 1)), cur) if m >= MIN_MONTH]


def month_ok(first):
    """True: перше число місяця й не раніше січня 2026."""
    s = iso(first)
    return len(s) == 10 and s[8:10] == '01' and s >= MIN_MONTH


def month_label(first, lang=0):
    names = (['січень', 'лютий', 'березень', 'квітень', 'травень', 'червень', 'липень', 'серпень', 'вересень', 'жовтень', 'листопад', 'грудень'],
             ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'])
    s = iso(first)
    return '%s %s' % (names[lang][int(s[5:7]) - 1], s[0:4]) if len(s) >= 7 else ''


# ——— числа ———
def cents(x):
    """Євро → цілі центи, половина — від нуля (як Decimal ROUND_HALF_UP), без плаваючої похибки."""
    d = Decimal(str(round(float(x or 0), 6))) * 100
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def eur(c):
    return round(c / 100.0, 2)


def pct(part, whole):
    """Частка в % з одним знаком; whole не додатний — None."""
    w = float(whole or 0)
    return round(float(part or 0) / w * 100, 1) if w > 0 else None


def money_text(x):
    s = ('%.2f' % abs(float(x or 0))).replace('.', ',')
    return ('−' if float(x or 0) < 0 else '') + s


def parse_ids(text):
    """«12, 15,x,» → [12, 15] (сміття пропускається, дубль — один раз)."""
    out = []
    for part in str(text or '').replace(';', ',').split(','):
        part = part.strip()
        if part.isdigit() and int(part) > 0 and int(part) not in out:
            out.append(int(part))
    return out


def parse_map(text):
    """JSON {"партнер": лінія} → {int: int}; не JSON чи не об'єкт — порожній словник; нулі й сміття пропускаються."""
    try:
        raw = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                if int(k) > 0 and int(v) > 0:
                    out[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return out


def _no_constant(name):
    raise ValueError('JSON: %s' % name)


def json_loads(text):
    """json.loads без NaN / Infinity / -Infinity: JSON.parse у JS їх не приймає — інакше Python прийняв би те, що драбина JS відкидає."""
    return json.loads(text, parse_constant=_no_constant)


def phrase_strip(s):
    """Краї фрази без пробільних символів PHRASE_WS (не str.strip(): у JS trim() інший набір) — як phraseStrip у JS-драбині."""
    return str(s).strip(PHRASE_WS)


def is_number_phrase(s):
    """True: фраза — число: без пробільних (PHRASE_WS) це цифри зі знаком і, може, дробовою частиною через «.» чи «,» («123», «12,5»,
    «1 234,56»), і цифр не більше PHRASE_NUM_MAX_DIGITS. Як isNumberPhrase у JS-драбині."""
    compact = ''.join(ch for ch in str(s) if ch not in PHRASE_WS)
    return bool(_NUM_RE.fullmatch(compact)) and sum(ch in '0123456789' for ch in compact) <= PHRASE_NUM_MAX_DIGITS


def _phrase(x):
    """Значення з JSON → (фраза, це число): фраза — рядок без пробільних країв, не порожній і не фраза-число; число JSON (не bool) і рядок-число →
    ('', True); інше → ('', False)."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return '', True
    if not isinstance(x, str):
        return '', False
    s = phrase_strip(x)
    if s and is_number_phrase(s):
        return '', True
    return s, False


def _phrases(text):
    """parse_phrases → (фрази, скільки фраз-чисел пропущено)."""
    raw = phrase_strip(text or '')
    if not raw:
        return [], 0
    try:
        got = json_loads(raw)
    except (TypeError, ValueError):
        got = raw
    if isinstance(got, dict):
        return [], 0
    if isinstance(got, str) or not isinstance(got, list):
        got = [raw if not isinstance(got, str) else got]   # рядок, число, null: вихідний текст — одна фраза
    out, nums = [], 0
    for x in got:
        s, num = _phrase(x)
        nums += num
        if s and s not in out:
            out.append(s)
    return out, nums


def parse_phrases(text):
    """JSON-масив рядків → [str] (порожні пропускаються, дубль — один раз); не JSON-масив — увесь рядок як одна фраза (сумісність зі старою
    формою параметра); порожнє — []. Фраза — лише рядок; краї ріже PHRASE_WS; фраза-число (is_number_phrase) і число JSON — пропуск (скільки —
    number_phrases, примітка звірки). Ті самі правила, що parsePhrases JS-драбини (випадки — tools/tests/fixtures/finance_phrase_pairs_cases.json)."""
    return _phrases(text)[0]


def pair_line(v):
    """Лінія пари фраз → int або 0: лише JSON-число без дробової частини (не bool), від 1 до MAX_ID. Рядок «5», 5.5, true, null, [5] — 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if isinstance(v, float) and not v.is_integer():
        return 0
    n = int(v)
    return n if 0 < n <= MAX_ID else 0


def _phrase_pairs(text):
    """parse_phrase_pairs → (пари, скільки фраз-чисел пропущено)."""
    try:
        raw = json_loads(text) if text else []
    except (TypeError, ValueError):
        return [], 0
    out, nums = [], 0
    if isinstance(raw, list):
        for pair in raw:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            (ph, num), line = _phrase(pair[0]), pair_line(pair[1])
            nums += num
            if ph and line:
                out.append((ph, line))
    return out, nums


def parse_phrase_pairs(text):
    """JSON-МАСИВ пар [["фраза", лінія], …] → [(фраза, лінія)] у порядку масиву (порядок = пріоритет збігу). Ті самі правила, що parsePhrasePairs
    у tools/lib/odoo_finance_ladder.js (спільні випадки — tools/tests/fixtures/finance_phrase_pairs_cases.json); помилки тут не буває ніколи:
    не JSON (і NaN / Infinity) чи не масив (зокрема об'єкт) — []; пара — лише масив рівно з двох; фраза — рядок, не порожній після phrase_strip
    і не фраза-число (is_number_phrase; скільки пропущено — number_phrases, примітка звірки); лінія — pair_line; інша пара — пропуск. Явну
    помилку дає посів (--seed інструмента штабу)."""
    return _phrase_pairs(text)[0]


def number_phrases(kind, text):
    """Скільки фраз-чисел пропустив розбір параметра виду phrasepairs / phrases → int (інший вид — 0)."""
    if kind == 'phrasepairs':
        return _phrase_pairs(text)[1]
    if kind == 'phrases':
        return _phrases(text)[1]
    return 0


def param_notes(raw, lang=0):
    """Примітки звірки про параметри: raw — {ім'я без префікса: текст параметра} → [рядок] у порядку PARAM_SPECS: «фраз-чисел пропущено»
    (розбір їх пропускає мовчки — помилки в ньому не буває, тож звірка називає параметр і скільки) і (круг 2, ревю 1е) «посередників не
    посіяно», коли mediator_partner_ids передано й він порожній: забутий --seed інакше мовчки пускав би посередників в оборот і в «банк без
    документа»."""
    out = []
    raw = raw or {}
    for name, kind, _req in PARAM_SPECS:
        n = number_phrases(kind, raw.get(name) or '') if name in raw else 0
        if n:
            out.append(t('phrase_number', lang) % (P_PREFIX + name, n, PHRASE_NUM_MAX_DIGITS))
        if name == 'mediator_partner_ids' and name in raw and not parse_ids(raw.get(name)):
            out.append(t('mediator_missing', lang) % (P_PREFIX + name))
    return out


def or_domain(leaves):
    """[умова, ...] → домен Odoo з «АБО» між ними (одна умова — вона сама, порожньо — [])."""
    return ['|'] * (len(leaves) - 1) + list(leaves) if leaves else []


def norm_domain(dom):
    """Домен Odoo (польський запис) → той самий з явними '&' між виразами верхнього рівня (як normalize_domain Odoo): [A, B, C] →
    ['&', '&', A, B, C]. Умова — список або кортеж; '&' і '|' беруть два вирази, '!' — один."""
    out, expected = [], 1
    for tok in dom or []:
        if expected == 0:
            out[0:0] = ['&']
            expected = 1
        if isinstance(tok, (list, tuple)):
            expected -= 1
        elif tok in ('&', '|'):
            expected += 1
        out.append(tok)
    return out


def or_domains(domains):
    """Кілька доменів (кожен — список умов з неявним «І») → один домен з «АБО» між ними (круг 2б: «Разом» таблиці — записи ступеня за всі
    місяці колонок, сума списку = число). Порожній домен серед них (це «усі записи») або доменів немає — None: клік тоді — рядки деталі."""
    doms = [norm_domain(d) for d in domains or []]
    if not doms or any(not d for d in doms):
        return None
    return ['|'] * (len(doms) - 1) + [tok for d in doms for tok in d]


def click_companies(perimeter, user_companies, current=0):
    """Фірми списку кліку (контекст allowed_company_ids, круг 2б): периметр ∩ фірми користувача — інакше список покаже лише фірми,
    відмічені в перемикачі, і не зійдеться з числом (число — весь периметр); поточна фірма — першою, якщо вона серед них. → [id]."""
    got = sorted(set(perimeter or []) & set(user_companies or []))
    return [current] + [c for c in got if c != current] if current in got else got


def param_value(kind, raw):
    """Значення параметра за видом: id → int (0 — немає), ids → [int], map → {int: int}, phrasepairs → [(фраза, int)], phrases → [str], text → str, num → float (немає — None)."""
    if kind == 'id':
        got = parse_ids(raw)
        return got[0] if got else 0
    if kind == 'ids':
        return parse_ids(raw)
    if kind == 'map':
        return parse_map(raw)
    if kind == 'phrases':
        return parse_phrases(raw)
    if kind == 'phrasepairs':
        return parse_phrase_pairs(raw)
    if kind == 'num':
        try:
            return float(str(raw).replace(',', '.')) if str(raw or '').strip() else None
        except ValueError:
            return None
    return str(raw or '').strip()


def missing_params(values):
    """Імена обов'язкових параметрів, яких немає (id = 0) → [повне ім'я]; values — {ім'я без префікса: значення}."""
    return [P_PREFIX + n for n, kind, req in PARAM_SPECS if req and not values.get(n)]


# ——— драбина ———
def ladder(v):
    """Драбина з ступенів у центах-через-євро: v — {s1_docs, s1a_owners_in_pl, s1g_no_analytic, s2_ukraine, s3_shares, cash_out} (є не всі —
    0) → {base, s4_net, s4c_cash} (євро, до цента)."""
    c = {k: cents(v.get(k)) for k in ('s1_docs', 's1a_owners_in_pl', 's1g_no_analytic', 's2_ukraine', 's3_shares', 'cash_out')}
    base = c['s1_docs'] + c['s1a_owners_in_pl'] + c['s1g_no_analytic'] + c['s2_ukraine']
    return {'base': eur(base), 's4_net': eur(base - c['s3_shares']), 's4c_cash': eur(base - c['cash_out'])}


def step_src(step, adjust_cents=0):
    """Звідки число: fact — книги Odoo, calc — розрахунок штабу, adjust — є активна поправка (для 1г і 3 — лише вона й можлива)."""
    if step in ('s1g_no_analytic', 's3_shares'):
        return 'adjust' if adjust_cents else 'calc'
    if step in ('base', 's4_net', 's4c_cash'):
        return 'adjust' if adjust_cents else 'calc'
    return 'fact'


def sources(adjust_g, adjust_3):
    """Усі *_src місяця. adjust_g, adjust_3 — сума активних поправок ступенів 1г і 3 (євро). База й «за грошима» — 'adjust', якщо змінила
    поправка 1г; ступінь 4 — якщо будь-яка з двох; інакше розрахунок (calc)."""
    g, s3 = cents(adjust_g), cents(adjust_3)
    out = {k: step_src(k) for k in STEPS}
    out['s1g_no_analytic'] = step_src('s1g_no_analytic', g)
    out['s3_shares'] = step_src('s3_shares', s3)
    out['base'] = step_src('base', g)
    out['s4_net'] = step_src('s4_net', g or s3)
    out['s4c_cash'] = step_src('s4c_cash', g)
    return out


def waterfall(v):
    """Водоспад ступенів для екрана: [{'key', 'amount', 'kind'}]; нульові ступені приховано (add — доданок, sub — віднімання від «до часток»,
    total — проміжний підсумок, кінцевий рядок s4_net завжди)."""
    rows = []
    for key in ('s1_docs', 's1a_owners_in_pl', 's1g_no_analytic', 's2_ukraine'):
        if cents(v.get(key)):
            rows.append({'key': key, 'amount': eur(cents(v.get(key))), 'kind': 'add'})
    rows.append({'key': 'base', 'amount': eur(cents(v.get('base'))), 'kind': 'total'})
    if cents(v.get('s3_shares')):
        rows.append({'key': 's3_shares', 'amount': eur(-cents(v.get('s3_shares'))), 'kind': 'sub'})
    rows.append({'key': 's4_net', 'amount': eur(cents(v.get('s4_net'))), 'kind': 'total'})
    return rows


def sum_cents(values):
    return sum(cents(x) for x in values)


def split_adjust(rows):
    """Активні поправки місяця [{'step', 'line', 'amount'}] → {'s1g': центи, 's3': центи, 'lines': {лінія: центи ступеня 3}}."""
    out = {'s1g': 0, 's3': 0, 'lines': {}}
    for r in rows:
        c = cents(r.get('amount'))
        if r.get('step') in ('s1g', 's3'):
            out[r['step']] += c
        if r.get('step') == 's3':
            out['lines'][r.get('line') or 0] = out['lines'].get(r.get('line') or 0, 0) + c
    return out


# ——— розподіл по аналітиці (контроль другим способом) ———
def dist_keys(dist):
    """analytic_distribution {"12,34": 60.0} → [([12, 34], 60.0)]."""
    out = []
    for k, p in (dist or {}).items():
        ids = [int(x) for x in str(k).split(',') if str(x).strip().isdigit()]
        if ids:
            out.append((ids, float(p or 0)))
    return out


def _at(mapping, i):
    """mapping[i] с ключом-числом или ключом-строкой (JSON не знает числовых ключей) — нет: None."""
    return mapping.get(i, mapping.get(str(i)))


def rec_part_cents(balance, dist, root_of, root):
    """Частка рядка проводки, що припала на рекрутинг (за розподілом), у центах: перший ключ із рахунком кореня; сума = −баланс × % / 100,
    округлення по кожному ключу до цента (як це робить Odoo в рядках аналітики)."""
    total = 0
    for ids, p in dist_keys(dist):
        if any(_at(root_of, i) == root for i in ids):
            total += cents(-float(balance or 0) * p / 100.0)
    return total


def has_root(dist, root_of, root):
    """True: в розподілі є стаття з коренем плану root (частка рядка рахунку рекрутингу — будь-яка додатна)."""
    return any(p > 0 and any(_at(root_of, i) == root for i in ids) for ids, p in dist_keys(dist))


# ——— «без місця» — як PG.identity сторожа P&L (tools/lib/odoo_pnl_guard.js) і закриття місяця (--check) ———
def _is_index(k):
    """Ключ, який Object.entries у JS ставить першим (канонічне ціле від 0 до 2³² − 2)."""
    s = str(k)
    return s.isdigit() and (s == '0' or not s.startswith('0')) and int(s) < 4294967295


def _js_items(d):
    """Пари словника в порядку Object.entries у JS: спершу ключі-цілі за зростанням, потім решта в порядку вставки — суми відсотків до біта
    такі самі, як у сторожа (float додається в тому ж порядку)."""
    return [(k, d[k]) for k in sorted((k for k in d if _is_index(k)), key=int)] + [(k, d[k]) for k in d if not _is_index(k)]


def dist_shares(dist, kind_of):
    """Відсотки рядка за кошиками, як distShares сторожа: kind_of — {стаття: 'rec' | 'med' | ''} статей, що є в базі (немає ключа — стаття
    видалена). Ключ зі статтею рекрутингу → rec; інакше з посередництвом → med; усі статті ключа видалені → broken; інакше (інший план,
    ключ без id) → other. → {'rec', 'med', 'other', 'broken', 'total'}."""
    s = {'rec': 0.0, 'med': 0.0, 'other': 0.0, 'broken': 0.0, 'total': 0.0}
    if not isinstance(dist, dict):
        return s
    for k, pct in _js_items(dist):
        try:
            p = float(pct or 0)
        except (TypeError, ValueError):
            p = 0.0
        ids = [int(x.strip()) for x in str(k).split(',') if x.strip().isdigit() and int(x.strip()) > 0]
        kinds = [_at(kind_of, i) for i in ids]
        if 'rec' in kinds:
            s['rec'] += p
        elif 'med' in kinds:
            s['med'] += p
        elif kinds and all(x is None for x in kinds):
            s['broken'] += p
        else:
            s['other'] += p
    s['total'] = s['rec'] + s['med'] + s['other'] + s['broken']
    return s


def identity_bucket(move, partner, transit, ic, out, mgmt):
    """Кошик нерозподіленого залишку рядка, як PG.identity: документ транзиту → 'transit'; партнер — своя фірма → 'ic'; фірма поза периметром
    → 'excl'; посередник чи «лише списання» (excluded_partner_ids) → 'theseus'; решта → 'ext' (лише він — «без місця»)."""
    if move in transit:
        return 'transit'
    if partner in ic:
        return 'ic'
    if partner in out:
        return 'excl'
    return 'theseus' if partner in mgmt else 'ext'


def identity_unplaced(rows, kind_of):
    """«Без місця» за правилом PG.identity: rows — [(id рядка, баланс, розподіл, кошик identity_bucket)] P&L-рядків місяця (порядок — за id, як
    у закритті місяця). v = −баланс; частки статей іншого плану (other) і видалених (broken) — «без місця» за БУДЬ-ЯКОГО партнера; нерозподілений
    залишок v × (100 − total) / 100 — лише в кошику 'ext' і лише коли |залишок| ≥ 1e-9. Три суми ведуться окремо й складаються в кінці, як у JS
    (ext + other + broken) → {'unplaced': євро float зі знаком, 'gross': сума модулів по рядках, 'ids': [рядки з внеском]}. Сума зі знаком —
    правило закриття місяця (MC.truthShare бере модуль від неї); gross — довідково, як unplacedGross сторожа."""
    ext = other = broken = gross = 0.0
    ids = []
    for rid, balance, dist, bucket in rows:
        v = -float(balance or 0)
        s = dist_shares(dist, kind_of)
        oth = v * s['other'] / 100
        brk = v * s['broken'] / 100
        other += oth
        broken += brk
        part = abs(oth) + abs(brk)
        rest = v * (100 - s['total']) / 100
        if abs(rest) >= 1e-9 and bucket == 'ext':
            ext += rest
            part += abs(rest)
        gross += part
        if part:
            ids.append(rid)
    return {'unplaced': ext + other + broken, 'gross': gross, 'ids': ids}


# ——— «банк без документа» — як PG.bankNoDoc сторожа і закриття місяця (--check) ———
NO_DOC_CATS = ('zusTax', 'noPartner', 'noBillsFirm', 'noBillsPerson')   # «без документа»; решта видів — документ є або це не витрата на боці
BANK_CATS = NO_DOC_CATS + ('salary', 'worker', 'withBills', 'ic', 'theseus')
# \s у JS — ті самі символи, що trim(): PHRASE_WS без \x1c–\x1f і \x85
_JS_WS = ''.join(ch for ch in PHRASE_WS if ch not in '\x1c\x1d\x1e\x1f\x85')
_JS_WS_RE = re.compile('[%s]+' % re.escape(_JS_WS))
# Призначення платежу без партнера: обов'язкові платежі й зарплата за ключовими словами (ZUS_TAX_RE, SALARY_RE сторожа). У JS прапорець /i
# без u: межа слова \b — ASCII, регістр — без урахування; тут те саме: re.ASCII і текст у нижньому регістрі (non-ASCII літери шаблону — малі).
_ZUS_TAX_RE = re.compile(r'zus|ubezpiecz|składk|skladk|skarbow|podat|\bpit\b|\bcit\b|\bvat\b|daň|\bdan\b', re.ASCII | re.IGNORECASE)
_SALARY_RE = re.compile(r'wynagr|wypłat|wyplat|zarp|salary|mzda|lista płac|lista plac|diety|nocleg|zaliczk|premi', re.ASCII | re.IGNORECASE)


def norm_ref(ref):
    """Призначення платежу без зайвих пробілів — як normRef сторожа (trim і \\s+ → пробіл за набором JS)."""
    return _JS_WS_RE.sub(' ', str(ref or '').strip(_JS_WS))


def bank_kind(partner, commercial, company, ref, ctx):
    """Вид несвіреної витрати банку, як bankNoDoc сторожа: без партнера — 'zusTax' / 'salary' (за призначенням) / 'noPartner'; своя фірма — 'ic';
    посередник — 'theseus'; працівник — 'worker'; є проведені рахунки постачальника в цій фірмі — 'withBills'; фірма — 'noBillsFirm'; фізособа —
    'noBillsPerson'. ctx — {'our', 'mgmt', 'emp': множини партнерів, 'bills': {«комерційний|фірма»}, 'is_company': {комерційний: bool}}."""
    if not partner:
        low = norm_ref(ref).lower()
        return 'zusTax' if _ZUS_TAX_RE.search(low) else ('salary' if _SALARY_RE.search(low) else 'noPartner')
    if commercial in ctx['our']:
        return 'ic'
    if commercial in ctx['mgmt']:
        return 'theseus'
    if partner in ctx['emp'] or commercial in ctx['emp']:
        return 'worker'
    if '%s|%s' % (commercial, company) in ctx['bills']:
        return 'withBills'
    return 'noBillsFirm' if _at(ctx['is_company'], commercial) else 'noBillsPerson'


def dedupe_statement_lines(lines, commercial):
    """Повторно завантажені рядки виписки → (лишені, дублі), як dedupeStatementLines сторожа: та сама фірма, журнал, дата, сума й комерційний
    партнер (партнер обов'язковий), призначення одного — початок призначення іншого (або рівне; не коротше 8 знаків), рядки завантажено в РІЗНІ
    дні (create_date). lines — [{'id', 'partner', 'company', 'journal', 'date', 'amount', 'ref', 'created'}] (created — «РРРР-ММ-ДД ГГ:ХХ:СС»
    UTC); commercial — {партнер: комерційний}."""
    groups, kept, dups = {}, [], []
    for l in lines or []:
        p = (_at(commercial, l['partner']) or l['partner']) if l.get('partner') else 0
        if not p:
            kept.append(l)
            continue
        groups.setdefault((l['company'], l['journal'], l['date'], l['amount'], p), []).append(l)
    for g in groups.values():
        seen = []
        for l in sorted(g, key=lambda x: (str(x.get('created') or ''), x['id'])):
            r = norm_ref(l.get('ref'))
            day = str(l.get('created') or '')[0:10]
            if len(r) >= 8 and day and any((r.startswith(k) or k.startswith(r)) and d and d != day for k, d in seen):
                dups.append(l)
            else:
                kept.append(l)
                if len(r) >= 8:
                    seen.append((r, day))
    return kept, dups


def bank_nodoc(lines, eur, ctx):
    """«Банк без документа» місяця, як bankNoDoc сторожа: lines — несвірені рядки виписок фірм периметра за місяць (dedupe_statement_lines);
    eur — {рядок: сума в EUR = balance рядка проводки на рахунку журналу}; рахуються лише витрати (EUR < 0), рядок без суми в EUR — пропуск.
    ctx — {'commercial': {партнер: комерційний}} + поля bank_kind. → {'cats': {вид: {'n', 'sum', 'ids'}}, 'nodoc': {'n', 'sum'} (сума видів
    NO_DOC_CATS, євро float зі знаком: витрата — мінус), 'dups': {'n', 'sum'}}."""
    commercial = ctx.get('commercial') or {}
    kctx = {'our': set(ctx.get('our') or ()), 'mgmt': set(ctx.get('mgmt') or ()), 'emp': set(ctx.get('emp') or ()), 'bills': set(ctx.get('bills') or ()),
            'is_company': ctx.get('is_company') or {}}
    kept, dups = dedupe_statement_lines(lines, commercial)
    cats = {k: {'n': 0, 'sum': 0.0, 'ids': []} for k in BANK_CATS}
    dup = {'n': 0, 'sum': 0.0}
    for l in dups:
        v = _at(eur, l['id'])
        if v is not None and v < 0:
            dup['n'] += 1
            dup['sum'] += v
    for l in kept:
        v = _at(eur, l['id'])
        if v is None or v >= 0:
            continue
        p = l.get('partner') or 0
        c = (_at(commercial, p) or p) if p else 0
        cat = cats[bank_kind(p, c, l.get('company'), l.get('ref'), kctx)]
        cat['n'] += 1
        cat['sum'] += v
        cat['ids'].append(l['id'])
    total = 0
    for k in NO_DOC_CATS:
        total += cats[k]['sum']
    return {'cats': cats, 'nodoc': {'n': sum(cats[k]['n'] for k in NO_DOC_CATS), 'sum': total}, 'dups': dup}


def bills_from(last):
    """Початок пошуку рахунків постачальника для місяця з останнім днем last — 1 січня його року мінус BILLS_LOOKBACK_MONTHS (як periods().billsFrom
    сторожа)."""
    return add_months(iso(last)[0:4] + '-01-01', -BILLS_LOOKBACK_MONTHS)


def transfer_groups(rows):
    """Перекази на особисті рахунки: rows — [(рядок виписки, партнер, свірено, сума в EUR або None)] → [(партнер, свірено, центи «−EUR», [рядки])]
    за партнером і статусом (сума додатна для витрати; рядок без суми в EUR — 0, але в списку лишається)."""
    by = {}
    for rid, partner, rec, v in rows:
        g = by.setdefault((partner or 0, bool(rec)), [0, []])
        g[0] += cents(-(v or 0))
        g[1].append(rid)
    return [(k[0], k[1], v[0], v[1]) for k, v in sorted(by.items())]


def partner_sets(companies, excluded):
    """«Свої» партнери, як partnerSets у tools/lib/odoo_pnl_guard.js (і закриття місяця): companies — [(id фірми, id партнера)] усіх фірм, і
    архівних; excluded — id фірм поза периметром → {'ic': [партнери інших фірм — «міжфірмові»], 'out': [партнери фірм поза периметром —
    окремий кошик, не «міжфірмові»]}. Партнер двох фірм (у периметрі й поза ним) — в обох списках; «міжфірмові» перевіряються раніше."""
    skip = set(excluded or [])
    ic, out = set(), set()
    for cid, pid in companies or []:
        if pid:
            (out if cid in skip else ic).add(pid)
    return {'ic': sorted(ic), 'out': sorted(out)}


def unpaid_bucket(partner, company_partners, energy_partners, out_partners=()):
    """Куди неоплачений рахунок: 'energy' (перевіряємо першим: контрагент групи Energy з параметра, хоч би й фірма в Odoo), 'interco'
    (контрагент — своя фірма периметра чи архівна; не показуємо), 'excl' (партнер фірми поза периметром, не з Energy; не показуємо, як і до
    круга 1г), 'customer'."""
    if partner in energy_partners:
        return 'energy'
    if partner in company_partners:
        return 'interco'
    return 'excl' if partner in out_partners else 'customer'


def has_phrase(text, phrases):
    """True: є хоч одна непорожня фраза, що є підрядком text (без урахування регістру, як ilike; .lower() — паритет з JS)."""
    low = str(text or '').lower()
    return any(phrase_strip(p) and phrase_strip(p).lower() in low for p in phrases or [])


def stage2_kind(text, pairs, transit):
    """Що за рядок аналітики «II етапу» за його текстом (name рядка + імена партнерів через пробіл) → ('transit', 0) | ('net', лінія) |
    ('unknown', 0). Транзит перевіряється раніше за все; далі — ПЕРША за порядком масиву пара [фраза, лінія], чия фраза є в тексті (не за
    довжиною: порядок масиву — правило штабу, «Anna Kowalski» має йти до фрази «Kowal»)."""
    if has_phrase(text, transit):
        return 'transit', 0
    low = str(text or '').lower()
    for ph, line in pairs or []:
        if phrase_strip(ph) and phrase_strip(ph).lower() in low:
            return 'net', line
    return 'unknown', 0


def stage2_cash(rows, pairs, transit):
    """«Перекази партнерам нетто» (ступінь 4'): rows — [(id рядка, текст, amount у центах)] аналітики статті «II етапу» за місяць (текст = name
    рядка + ім'я партнера рядка чи проводки; одержувач — у тексті), pairs — [(фраза, лінія)] у порядку пріоритету, transit — [фраза транзиту].
    Рядок статті — додатна частка 10 %: брутто = +сума як є, нетто = брутто × 9. → {'groups': [{'status': 'net' | 'unknown' | 'transit', 'line',
    'gross', 'net', 'count', 'ids'}], 'partner_net': центи лише за 'net'}: по групі на лінію, одна група 'unknown', одна 'transit'; розпізнані на
    лінію йдуть у 4', решта — ні."""
    by = {}
    for rid, name, amount in rows:
        status, line = stage2_kind(name, pairs, transit)
        g = by.setdefault((status, line), {'status': status, 'line': line, 'gross': 0, 'count': 0, 'ids': []})
        g['gross'] += int(amount)
        g['count'] += 1
        g['ids'].append(rid)
    order = {'net': 0, 'unknown': 1, 'transit': 2}
    groups = sorted(by.values(), key=lambda g: (order[g['status']], g['line']))
    total = 0
    for g in groups:
        g['net'] = g['gross'] * STAGE2_MULT
        total += g['net'] if g['status'] == 'net' else 0
    return {'groups': groups, 'partner_net': total}


def stage2_other(rows, an_id=0):
    """Рядки рахунку «II етапу» проводок без рядка статті З аналітикою іншої статті (круг 2, ревю 1е): rows — [(id, analytic_distribution,
    центи балансу)]; у 4' вони не входять (лише рядки без аналітики, як документ v2), звірка каже, скільки й на яку суму; рядки з конвертом AN
    (an_id) не рахуються — вони в 4' рядком конверта. → {'n', 'cents', 'ids'}. Як stage2Other JS-драбини."""
    out = {'n': 0, 'cents': 0, 'ids': []}
    for rid, dist, c in rows or []:
        if not dist or (an_id and any(an_id in ids for ids, _p in dist_keys(dist))):
            continue
        out['n'] += 1
        out['cents'] += int(c)
        out['ids'].append(rid)
    return out


def truth_share(ic, unplaced, nodoc, ext, threshold=TRUTH_PCT):
    """«Місяць правдивий»: (|міжфірмові| + |без місця| + |банк без документа|) / оборот із зовнішніми × 100; як MC.truthShare."""
    parts = abs(float(ic or 0)) + abs(float(unplaced or 0)) + abs(float(nodoc or 0))
    e = float(ext or 0)
    share = parts / e * 100 if e > 0 else None
    return {'share': None if share is None else round(share, 1), 'ok': share is not None and share < threshold, 'parts': round(parts, 2)}


# ——— звірка ———
def check_result(first, v, control_cents, cand, lang=0, notes=None):
    """Звірка місяця: v — числа місяця (MONEY), control_cents — ступінь 1 другим способом (центи), cand — {'count', 'amount'} кандидатів 1г,
    notes — готові рядки-примітки (на «зійшлося» не впливають) → {'ok', 'text', 'control_diff', 'ladder_ok'}. Зійшлося: контроль ≤ TOL_CENTS
    і драбина = сума ступенів."""
    label = month_label(first, lang)
    s1 = cents(v.get('s1_docs'))
    diff = s1 - control_cents
    parts = cents(v.get('s1_docs')) + cents(v.get('s1a_owners_in_pl')) + cents(v.get('s1g_no_analytic')) + cents(v.get('s2_ukraine'))
    net = cents(v.get('base')) - cents(v.get('s3_shares'))
    ladder_ok = cents(v.get('base')) == parts and cents(v.get('s4_net')) == net
    ok = abs(diff) <= TOL_CENTS and ladder_ok
    lines = [t('v_ok' if ok else 'v_bad', lang) % label]
    if abs(diff) > TOL_CENTS:
        lines.append(t('v_control', lang) % (money_text(eur(s1)), money_text(eur(control_cents)), money_text(eur(diff))))
    if not ladder_ok:
        lines.append(t('v_ladder', lang) % (money_text(v.get('base')), money_text(eur(parts)), money_text(v.get('s4_net')), money_text(eur(net))))
    if cand and cand.get('count'):
        lines.append(t('v_cand', lang) % (cand['count'], money_text(cand['amount']), money_text(v.get('s1g_no_analytic'))))
        if cents(cand['amount']) != cents(v.get('s1g_no_analytic')):
            lines.append(t('v_cand_diff', lang))
    q = pct_quality(v)
    if q is not None:
        lines.append(t('v_quality', lang) % (str(q).replace('.', ','), str(TRUTH_PCT).replace('.', ',')))
    lines.extend(notes or [])
    return {'ok': ok, 'text': ' '.join(lines), 'control_diff': eur(diff), 'ladder_ok': ladder_ok}


def pct_quality(v):
    return truth_share(v.get('q_ic'), v.get('q_unplaced'), v.get('q_nodoc'), v.get('q_ext'))['share']


# ——— екран «Власник» (круг 2; макет A + таблиця макета B): плитки, чипи, водоспад, ряд року, власники, перекази, «Драбина» ———
MONTHS_SHORT = (('Січ', 'Лют', 'Бер', 'Кві', 'Тра', 'Чер', 'Лип', 'Сер', 'Вер', 'Жов', 'Лис', 'Гру'),
                ('Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'))
# рядки таблиці «Драбина»: (поле місяця, номер рядка, вид рядка, знак на екрані). Частки (3) у полі — додатне число, на екрані — мінусом
# (віднімаються від «до часток»); вид рядка — стиль: row — звичайний, bold — «до часток», accent — чистий прибуток, muted — «за грошима»
LADDER_ROWS = (('s1_docs', '1', 'row', 1), ('s1a_owners_in_pl', '1а', 'row', 1), ('s1g_no_analytic', '1г', 'row', 1), ('s2_ukraine', '2', 'row', 1),
               ('base', '=', 'bold', 1), ('s3_shares', '3', 'row', -1), ('s4_net', '4', 'accent', 1), ('s4c_cash', '4′', 'muted', 1))
# п'ять плиток місяця: чистий прибуток (4), за грошима (4'), рекрутинг за документами (1 + 1а + 1г), Україна (2), частки партнерів (−3)
TILES = ('s4_net', 's4c_cash', 'recruiting', 's2_ukraine', 's3_shares')
RECRUITING = ('s1_docs', 's1a_owners_in_pl', 's1g_no_analytic')
# ступені з поправкою: чиї підстави пояснюють число з пометкою «поправка» (база й 4' змінює лише 1г, чистий прибуток — 1г і 3)
ADJUST_OF = {'s1g_no_analytic': ('s1g',), 's3_shares': ('s3',), 'base': ('s1g',), 's4_net': ('s1g', 's3'), 's4c_cash': ('s1g',)}
FALL_THIN_PCT = 2     # крок водоспаду, менший за стільки % найбільшого підсумку, — тонкий: екран малює його мінімальної висоти, щоб було видно
TRANSFER_ROWS = 8     # «Перекази на особисті рахунки»: стільки найбільших, решта — рядком «Показано N з M»


def month_info(first, lang=0, partial=''):
    """Місяць для екрана → {'value', 'title' («Вересень 2026» — кнопка вибору), 'title_in' («вересень 2026» — усередині фрази), 'name'
    («вересень» — заголовок картки), 'short' («Вер» — вісь графіка й шапка таблиці), 'partial' (неповний місяць — «ДД.ММ», по який день;
    повний — ''), 'hint' (підказка колонки: назва й «неповний, по ДД.ММ»)}."""
    s = iso(first)
    label = month_label(s, lang)
    title = label[0:1].upper() + label[1:]
    return {'value': s, 'title': title, 'title_in': label, 'name': label.split(' ')[0], 'short': MONTHS_SHORT[lang][int(s[5:7]) - 1],
            'partial': partial or '', 'hint': title + (' · ' + t('h_partial', lang) % partial if partial else '')}


def partial_day(first, today, stamp=''):
    """Неповний місяць (круг 2б): числа місяця first пораховано до його кінця → «ДД.ММ» — по який день (день перерахунку за Києвом stamp;
    його немає або він раніше за сам місяць — today), інакше ''. Неповний — поточний місяць і минулий, який востаннє перераховували ще до
    його кінця (нічний перерахунок останнього дня йде вже наступною датою за Києвом). День раніше за сам місяць (місяць ще не почався) — ''."""
    first = iso(first)
    day = iso(stamp) if iso(stamp) >= first else iso(today)
    if not day or day < first or day > month_last(first):
        return ''
    return day[8:10] + '.' + day[5:7]


def src_max(srcs):
    """Позначка суми кількох чисел: є поправка — 'adjust', є розрахунок — 'calc', усі з Odoo (чи порожньо) — ''."""
    s = set(srcs or [])
    return 'adjust' if 'adjust' in s else ('calc' if 'calc' in s else '')


def int_text(x):
    """Євро цілими для тексту підказки: 55057,4 → «55 057», −438 → «−438» (мінус — «−»)."""
    c = cents(x)
    whole = (abs(c) + 50) // 100
    return ('−' if c < 0 and whole else '') + '{:,}'.format(whole).replace(',', ' ')


def common_name(names):
    """Назви фірм (чи партнерів) групи → підпис: спільний початок слів усіх назв («Firm X s.r.o.», «Firm X GmbH» → «Firm X»), одна назва —
    вона сама, спільного початку немає — перша; порожньо — ''. Для «Не оплачено <група Energy>»: назва — дані Odoo, у коді модуля її немає."""
    lst = [' '.join(str(n or '').split()) for n in names or []]
    lst = [n for n in lst if n]
    if not lst:
        return ''
    common = []
    for parts in zip(*[n.split(' ') for n in lst]):
        if any(p != parts[0] for p in parts):
            break
        common.append(parts[0])
    return ' '.join(common) or lst[0]


def _thousands(x):
    """Євро → тисячі з одним знаком (Decimal; половина — від нуля, як cents)."""
    return (Decimal(cents(x)) / 100000).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def k_value(x):
    """Євро → тисячі з одним знаком для графіка: 69 349,99 → 69.3; −50 → −0.1; 49,99 → 0.0."""
    return float(_thousands(x))


def k_text(x):
    """Число таблиці «Драбина, тис. €»: одна цифра після коми, кома — роздільник, тисячі тисяч — через пробіл, мінус — «−»; нуль (до цента) —
    «—»; не нуль, але за модулем менше 0,05 тис. — «0,0» без знака."""
    if not cents(x):
        return '—'
    q = _thousands(x)
    whole, frac = format(abs(q), 'f').split('.')
    return ('−' if q < 0 else '') + '{:,}'.format(int(whole)).replace(',', ' ') + ',' + frac


def default_month(rows, today):
    """Місяць екрана за замовчуванням: rows — [(місяць, стан)] записів місяців. Останній закритий (стан closed); закритих немає (на проді
    блокувань поки немає) — останній завершений календарний місяць (минулий щодо today, не раніше MIN_MONTH): із записів — останній не пізніше
    за нього; записів до нього немає — він сам (екран скаже «немає даних»)."""
    closed = [iso(m) for m, s in rows if s == 'closed' and iso(m) >= MIN_MONTH]
    if closed:
        return max(closed)
    prev = max(MIN_MONTH, add_months(month_first(today), -1))
    got = [iso(m) for m, _s in rows if MIN_MONTH <= iso(m) <= prev]
    return max(got) if got else prev


def month_choices(rows, lang=0):
    """Вибір місяця: місяці записів (не раніше MIN_MONTH), від останнього → [{'value', 'title'}]."""
    ms = sorted({iso(m) for m, _s in rows if iso(m) >= MIN_MONTH}, reverse=True)
    return [{'value': m, 'title': month_info(m, lang)['title']} for m in ms]


def year_rows(rows, first):
    """Записи місяців року місяця first (не раніше MIN_MONTH), за місяцем."""
    y = iso(first)[0:4]
    return sorted([r for r in rows if iso(r['month'])[0:4] == y and iso(r['month']) >= MIN_MONTH], key=lambda r: iso(r['month']))


def ytd_rows(rows, first):
    """Записи місяців від січня року first до first включно (круг 2б: колонки таблиці, ряд графіка, «Разом» і «з початку року» плиток —
    один набір; місяці після обраного не показуються)."""
    return [r for r in year_rows(rows, first) if iso(r['month']) <= iso(first)]


def tile_cents(row, key):
    """Число плитки в центах: «рекрутинг разом» = 1 + 1а + 1г, частки — мінусом, решта — поле як є."""
    if key == 'recruiting':
        return sum(cents(row.get(k)) for k in RECRUITING)
    if key == 's3_shares':
        return -cents(row.get(key))
    return cents(row.get(key))


def tile_src(rows, key):
    """Позначка плитки (круг 2б: видно без наведення — «з поправкою» / «розрахунок штабу»): з чого складаються її числа (місяць і «з
    початку року» — rows) — src_max позначок ненульових доданків (рекрутинг — 1, 1а, 1г; решта — саме поле); усі з Odoo — ''."""
    return src_max((r.get(f + '_src') or 'fact') for r in rows for f in (RECRUITING if key == 'recruiting' else (key,)) if cents(r.get(f)))


def owner_tiles(rows, first):
    """П'ять плиток місяця first і «з початку року»: rows — записи місяців (словники з полями ступенів, *_src і 'month'); «з початку року»
    — Σ місяців того самого року від січня до first включно (місяця без запису немає — нуль; той самий набір, що колонки таблиці й «Разом»).
    → [{'key', 'value' (євро; запису місяця немає — None), 'ytd', 'src' (tile_src)}] у порядку TILES."""
    first = iso(first)
    cur = next((r for r in rows if iso(r['month']) == first), None)
    ytd = ytd_rows(rows, first)
    return [{'key': k, 'value': eur(tile_cents(cur, k)) if cur else None, 'ytd': eur(sum(tile_cents(r, k) for r in ytd)), 'src': tile_src(ytd, k)}
            for k in TILES]


def ladder_table(rows, first, lang=0, partial=None):
    """Таблиця «Драбина <рік>, тис. €»: rows — записи місяців (поля ступенів, *_src, 'month'); колонки — лише пораховані місяці року first
    від січня ДО first включно (круг 2б: місяці після обраного не показуються — «Разом» = «з початку року» плиток), «Разом» — Σ колонок до
    цента. partial — {місяць: «ДД.ММ»} неповних місяців (partial_day). → {'months': [month_info], 'selected': номер колонки first (немає —
    −1), 'rows': [{'key', 'num', 'label', 'style', 'cells': [{'month', 'value', 'text', 'src'}], 'total': {'value', 'text', 'src'}}]}; src
    нуля — '' (пояснювати нічого), src «Разом» — src_max клітинок; значення частки (3) — мінусом."""
    rs = ytd_rows(rows, first)
    months = [iso(r['month']) for r in rs]
    out = []
    for key, num, style, sign in LADDER_ROWS:
        cells, tot = [], 0
        for r in rs:
            c = sign * cents(r.get(key))
            tot += c
            cells.append({'month': iso(r['month']), 'value': eur(c), 'text': k_text(eur(c)), 'src': (r.get(key + '_src') or 'fact') if c else ''})
        total = {'value': eur(tot), 'text': k_text(eur(tot)), 'src': src_max(c['src'] for c in cells) if tot else ''}
        out.append({'key': key, 'num': num, 'label': t('r_' + key, lang), 'style': style, 'cells': cells, 'total': total})
    return {'months': [month_info(m, lang, (partial or {}).get(m, '')) for m in months],
            'selected': months.index(iso(first)) if iso(first) in months else -1, 'rows': out}


def chart_series(table, lang=0):
    """Ряд «Чистий прибуток <рік>, тис. €» з таблиці ladder_table (ті самі колонки): стовпці — ступінь 4, лінія — «до часток»; тисячі з одним
    знаком, підпис — текст клітинки таблиці → {'months', 'labels', 'net', 'net_text', 'base', 'base_text', 'selected', 'net_label', 'base_label'}."""
    by = {r['key']: r['cells'] for r in table['rows']}
    return {'months': [m['value'] for m in table['months']], 'labels': [m['short'] for m in table['months']],
            'net': [k_value(c['value']) for c in by['s4_net']], 'net_text': [c['text'] for c in by['s4_net']],
            'base': [k_value(c['value']) for c in by['base']], 'base_text': [c['text'] for c in by['base']],
            'selected': table['selected'], 'net_label': t('l_net', lang), 'base_label': t('l_base', lang)}


def src_hint(key, src, bases, lang=0):
    """Підказка числа таблиці за пометкою: факт — '' (з Odoo), розрахунок — «розрахунок штабу», поправка — «поправка: <підстави>» (bases —
    {'s1g': [текст], 's3': [текст]} активних поправок місяця; ступені — ADJUST_OF; підстави без повторів, рядки — через «; »)."""
    if src == 'calc':
        return t('h_calc', lang)
    if src != 'adjust':
        return ''
    texts = []
    for step in ADJUST_OF.get(key, ()):
        for b in (bases or {}).get(step) or []:
            b = ' '.join(str(b or '').split())
            if b and b not in texts:
                texts.append(b)
    return t('h_adjust', lang) % ('; '.join(texts) or '—')


def fall_bars(v, lang=0):
    """Водоспад «Як складається прибуток» з рядків waterfall(v) (нульові ступені сховано): перший стовпець, «до часток» і «чистий прибуток» —
    від нуля, решта — від поточного підсумку (вгору — плюс, вниз — мінус). Шкала — від min(0, …) до max(0, …). → {'bars': [рядок + {'label',
    'dir' ('total' / 'up' / 'down'), 'bottom', 'height', 'end', 'thin', 'src'}], 'zero', 'ok'}: bottom, height, zero — % висоти поля; end —
    рівень підсумку після кроку (звідти пунктир до наступного стовпця); thin — крок, менший за FALL_THIN_PCT % найбільшого підсумку; src —
    позначка стовпця (поле <ключ>_src місяця: 'calc' / 'adjust'; з Odoo — '', круг 2б); ok — доданки дають «до часток», а «до часток» мінус
    частки — «чистий прибуток» до цента."""
    rows = waterfall(v)
    run = adds = subs = base = 0
    ok = True
    raw = []
    for i, r in enumerate(rows):
        c = cents(r['amount'])
        if i == 0 or r['kind'] == 'total':
            lo, hi, d = min(0, c), max(0, c), 'total'
            run = c
        else:
            lo, hi, d = min(run, run + c), max(run, run + c), 'up' if c > 0 else 'down'
            run += c
        if r['kind'] == 'add':
            adds += c
        elif r['kind'] == 'sub':
            subs += c
        elif r['key'] == 'base':
            ok = ok and c == adds
            base = c
        elif r['key'] == 's4_net':
            ok = ok and c == base + subs
        raw.append((r, c, lo, hi, d, run))
    top = max([abs(c) for r, c, *_x in raw if r['kind'] == 'total'] or [0])
    vals = [0] + [x[2] for x in raw] + [x[3] for x in raw]
    bottom, span = min(vals), max(vals) - min(vals)

    def pct(x):
        return round((x - bottom) * 100.0 / span, 2) if span else 0.0
    bars = [dict(r, label=t('w_' + r['key'], lang), dir=d, bottom=pct(lo), height=round(pct(hi) - pct(lo), 2), end=pct(e),
                 thin=d != 'total' and abs(c) * 100 < FALL_THIN_PCT * top, src=src_max([v.get(r['key'] + '_src')]))
            for r, c, lo, hi, d, e in raw]
    return {'bars': bars, 'zero': pct(0), 'ok': ok}


def quality_chip(v, lang=0):
    """Чип «Якість даних N %» — доля як у закриття місяця й сторожа (truth_share, круг 1е): оборот не порахований або кошики порожні — None
    (чипа немає, сказати нічого); інакше {'pct', 'bad' (доля ≥ порогу — червона крапка), 'parts' (не розібрано: Σ|кошиків|), 'ext' (оборот
    із зовнішніми), 'threshold', 'hint'}. Поріг і «не розібрано X € із Y €» — у підказці (hint) і вікні кошиків, не в чипі (круг 2б: ряд
    чипів — в один рядок, як у макеті)."""
    q = truth_share(v.get('q_ic'), v.get('q_unplaced'), v.get('q_nodoc'), v.get('q_ext'))
    if q['share'] is None or not cents(q['parts']):
        return None
    ext = eur(cents(v.get('q_ext')))
    return {'pct': q['share'], 'bad': not q['ok'], 'parts': q['parts'], 'ext': ext, 'threshold': TRUTH_PCT,
            'hint': t('h_quality', lang) % (int_text(q['parts']), int_text(ext), ('%g' % TRUTH_PCT).replace('.', ','))}


def quality_label(note, lang=0):
    """Підпис кошика «якості даних» за кодом примітки рядка деталі ('ic', 'unplaced', 'nodoc:<вид>') мовою користувача — не збережений
    текст рядка (його писали мовою того, хто перераховував); невідомий код — ''."""
    s = str(note or '')
    if s in ('ic', 'unplaced'):
        return t('q_' + s, lang)
    kind = s[len('nodoc:'):] if s.startswith('nodoc:') else ''
    return t('nodoc_' + kind, lang) if kind in NO_DOC_CATS else ''


def quality_baskets(items, lang=0):
    """Вікно «три кошики» чипа «Якість даних»: items — рядки деталі виду quality [{'id', 'note', 'amount', 'count'}] → [{'id', 'key', 'label',
    'amount', 'count'}] у порядку: міжфірмові, без місця, банк без документа за видами (NO_DOC_CATS); нульові й невідомі пропущено."""
    order = ['ic', 'unplaced'] + ['nodoc:' + k for k in NO_DOC_CATS]
    out = [{'id': it['id'], 'key': it.get('note'), 'label': quality_label(it.get('note'), lang), 'amount': eur(cents(it.get('amount'))),
            'count': it.get('count') or 0} for it in items or [] if it.get('note') in order and cents(it.get('amount'))]
    return sorted(out, key=lambda b: order.index(b['key']))


def transfer_status(note):
    """Статус рядка переказу на особистий рахунок за приміткою рядка деталі (перерахунок пише її мовою того, хто перераховував) → 'cleared'
    («розібрано»), 'nodoc' («без документа») або ''."""
    s = str(note or '')
    if s in TX['nodoc']:
        return 'nodoc'
    return 'cleared' if s in TX['cleared'] else ''


def transfer_rows(items, limit=TRANSFER_ROWS):
    """«Перекази на особисті рахунки · місяць»: items — рядки деталі виду transfer [{'id', 'name', 'amount', 'note'}] (одержувач × статус) →
    {'rows': [{'id', 'name', 'amount', 'status'}] — від більшої суми, не більше limit; 'total': усього рядків; 'nodoc': сума «без документа»}."""
    rows = [{'id': it['id'], 'name': it.get('name') or '—', 'amount': eur(cents(it.get('amount'))), 'status': transfer_status(it.get('note'))}
            for it in items or [] if cents(it.get('amount'))]
    rows.sort(key=lambda r: (-abs(cents(r['amount'])), r['name'], r['id']))
    nodoc = eur(sum(cents(r['amount']) for r in rows if r['status'] == 'nodoc'))
    return {'rows': rows[:limit], 'total': len(rows), 'nodoc': nodoc}


def owner_groups(items, key_of):
    """«Власники · місяць»: items — рядки деталі видів owner / owner_share [{'id', 'kind', 'analytic', 'name', 'amount'}], key_of — {стаття:
    [ключ, код]} (ключ власника й підпис; статті немає — рядок сам собі ключ, код — його назва). Рядок на власника: усього, «частка»
    (owner_share) і «витрати компанії» (owner) окремо, ids рядків деталі; нульові рядки деталі пропущено; від більшого за модулем; pct —
    довжина смуги від найбільшого → [{'code', 'total', 'share', 'company', 'ids', 'pct'}]."""
    by = {}
    for it in items or []:
        c = cents(it.get('amount'))
        if not c:
            continue
        k, code = _at(key_of or {}, it.get('analytic')) or ('i%s' % it['id'], it.get('name') or '—')
        g = by.setdefault(k, {'code': code, 'total': 0, 'share': 0, 'company': 0, 'ids': []})
        g['total'] += c
        g['share' if it.get('kind') == 'owner_share' else 'company'] += c
        g['ids'].append(it['id'])
    rows = sorted(by.values(), key=lambda g: (-abs(g['total']), g['code']))
    top = max([abs(g['total']) for g in rows] or [0])
    return [dict(g, total=eur(g['total']), share=eur(g['share']), company=eur(g['company']), pct=round(abs(g['total']) * 100.0 / top, 1) if top else 0.0)
            for g in rows]


def ids_union(domains):
    """Домени рядків деталі виду [('id', 'in', [...])] (JSON-текст або список) → усі id разом, за зростанням (один список замість кількох);
    хоч один домен іншого виду чи не JSON — None (тоді клік — список рядків деталі)."""
    out = set()
    for d in domains or []:
        try:
            dom = json.loads(d) if isinstance(d, str) else d
        except ValueError:
            return None
        if not (isinstance(dom, list) and len(dom) == 1 and isinstance(dom[0], (list, tuple)) and len(dom[0]) == 3
                and list(dom[0][0:2]) == ['id', 'in'] and isinstance(dom[0][2], list)):
            return None
        out.update(int(x) for x in dom[0][2])
    return sorted(out)
