# -*- coding: utf-8 -*-
"""Чисті функції «Фінансів» (круг 1, екран «Власник»): без Odoo і без бази — на словниках, списках і рядках «РРРР-ММ-ДД».

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
from decimal import ROUND_HALF_UP, Decimal

MIN_MONTH = '2026-01-01'   # екрани й перерахунок — лише з січня 2026 (рішення власника 06.11: «цікавить лише 2026 рік»)
RECALC_MONTHS = 4          # нічний перерахунок — 3 останні місяці й поточний (4 разом)
MAX_MONTHS = 36            # перерахунок за період — не більше стільки місяців за раз
TOL_CENTS = 1              # допуск контролю другим способом: 0,01 (одна копійка)
STAGE2_MULT = 9            # переказ партнеру нетто = рядок аналітики статті «II етапу» × 9 (там лежить 10 %)
STAGE2_BRUTTO = 10         # брутто переказу партнеру = рядок аналітики «II етапу» × 10 (показуємо в рядку «за грошима»)
BANK_GAP_DAYS = 14        # «банк без документа» — рядок виписки старший за стільки днів
TRUTH_PCT = 1.0            # поріг «місяць правдивий» (рішення власника 14.10.2026: 1 %), як MC.truthShare
UNPAID_MIN = 0.005         # залишок рахунку менший за це — вважається оплаченим
MAX_ID = 2147483647        # найбільший id запису (integer PostgreSQL): лінія пари більша — пропуск (інакше browse упаде на SQL)

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
    ('excluded_partner_ids', 'ids', False),       # посередники-виняток управлінської аналітики
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
    'not_in_4': ["в 4' не входить", "в 4' не входит"],
    'line_gone': ['лінії немає в базі', 'линии нет в базе'],
    'unknown': ['не розпізнано', 'не распознано'],
    'transit': ['транзит — виключено', 'транзит — исключено'],
    'gross': ['брутто (×10): %s', 'брутто (×10): %s'],
    'cleared': ['розібрано', 'разобрано'],
    'nodoc': ['без документа', 'без документа'],
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


def parse_phrases(text):
    """JSON-масив рядків → [str] (порожні пропускаються, дубль — один раз); не JSON-масив — увесь рядок як одна фраза (сумісність зі старою
    формою параметра); порожнє — []."""
    raw = str(text or '').strip()
    if not raw:
        return []
    try:
        got = json_loads(raw)
    except (TypeError, ValueError):
        got = raw
    if isinstance(got, dict):
        return []
    if isinstance(got, str) or not isinstance(got, list):
        got = [raw if not isinstance(got, str) else got]   # рядок, число, null: вихідний текст — одна фраза
    out = []
    for x in got:
        s = str(x).strip() if isinstance(x, (str, int, float)) and not isinstance(x, bool) else ''
        if s and s not in out:
            out.append(s)
    return out


def pair_line(v):
    """Лінія пари фраз → int або 0: лише JSON-число без дробової частини (не bool), від 1 до MAX_ID. Рядок «5», 5.5, true, null, [5] — 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if isinstance(v, float) and not v.is_integer():
        return 0
    n = int(v)
    return n if 0 < n <= MAX_ID else 0


def parse_phrase_pairs(text):
    """JSON-МАСИВ пар [["фраза", лінія], …] → [(фраза, лінія)] у порядку масиву (порядок = пріоритет збігу). Ті самі правила, що parsePhrasePairs
    у tools/lib/odoo_finance_ladder.js (спільні випадки — tools/tests/fixtures/finance_phrase_pairs_cases.json); помилки тут не буває ніколи:
    не JSON (і NaN / Infinity) чи не масив (зокрема об'єкт) — []; пара — лише масив рівно з двох; фраза — рядок, не порожній після strip;
    лінія — pair_line; інша пара — пропуск. Явну помилку дає посів (--seed інструмента штабу)."""
    try:
        raw = json_loads(text) if text else []
    except (TypeError, ValueError):
        return []
    out = []
    if isinstance(raw, list):
        for pair in raw:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            ph, line = pair[0], pair_line(pair[1])
            if isinstance(ph, str) and ph.strip() and line:
                out.append((ph.strip(), line))
    return out


def or_domain(leaves):
    """[умова, ...] → домен Odoo з «АБО» між ними (одна умова — вона сама, порожньо — [])."""
    return ['|'] * (len(leaves) - 1) + list(leaves) if leaves else []


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


def unplaced_cents(balance, dist, kind_of):
    """Частка рядка P&L «без місця» (центи, зі знаком «−баланс»): розподіл на рекрутинг ('rec') чи посередництво ('med') не рахується;
    немає розподілу — увесь рядок; розподіл менший за 100 % — залишок."""
    placed = 0.0
    for ids, p in dist_keys(dist):
        if any(_at(kind_of, i) in ('rec', 'med') for i in ids):
            placed += p
    return cents(-float(balance or 0) * max(0.0, 100.0 - placed) / 100.0)


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
    return any(str(p).strip() and str(p).strip().lower() in low for p in phrases or [])


def stage2_kind(text, pairs, transit):
    """Що за рядок аналітики «II етапу» за його текстом (name рядка + імена партнерів через пробіл) → ('transit', 0) | ('net', лінія) |
    ('unknown', 0). Транзит перевіряється раніше за все; далі — ПЕРША за порядком масиву пара [фраза, лінія], чия фраза є в тексті (не за
    довжиною: порядок масиву — правило штабу, «Anna Kowalski» має йти до фрази «Kowal»)."""
    if has_phrase(text, transit):
        return 'transit', 0
    low = str(text or '').lower()
    for ph, line in pairs or []:
        if str(ph).strip() and str(ph).strip().lower() in low:
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
