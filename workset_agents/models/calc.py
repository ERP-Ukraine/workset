# -*- coding: utf-8 -*-
"""Чисті функції нічного розрахунку агентів: без Odoo і без бази — на словниках і рядках «РРРР-ММ-ДД».

Перенос tools/odoo_agents_calc/calc.py ручної збірки (гілка wip/druzya8-agents-vid) один в один: нарахування «€ за годину» за ОПЛАЧЕНИМИ
рахунками замовників — розділ «кандидати» (години людей агента з датами з / по) і розділ «замовники» (усі години рахунків його замовників або
замовників лінії); «разова» — ручні нарахування отримують ключ, день виплати і статус; день виплати — найближче 10-те або 25-те зі зсувом на
робочий день банків Польщі (п. 6.1 договору v1.7), відсічка D−3; виправлення — лише сторно; ключ неповторний (дубль = подвійна виплата).
Що змінилось проти ручної збірки: імена ключів — поля модуля без «x_» (README, таблиця імен); факти (фірми шляхів виплати) — аргументом cfg
(make_cfg), решта — константи модуля; свята банків Польщі рахуються на будь-який рік (було: лише роки в коді, інакше помилка).
Модель (models/agent_run.py) збирає дані з бази й кличе compute; тести без Odoo запускають ці функції напряму (файл не імпортує odoo).
"""
import datetime

# ── сталі розрахунку (устрій, не факти) ──
PAY_DAYS = (10, 25)            # дні виплати (правило 05.11.2026, п. 6.1 договору v1.7)
CUTOFF = 3                     # у виплату дня D іде зароблене не пізніше D − 3
PAID_STATES = ('paid', 'in_payment')   # «оплачений»: проведений і оплачений повністю (in_payment — оплата є, виписка ще не звірена)
LIVE = ('zvirka', 'active')    # домовленість ДІЄ у «Звірці» і в «Діє»
CLIENT_PAYS = False            # етап 1: розділ «замовники» — Odoo рахує, не платить
AUTO_SUFFIX = ':auto'          # хвіст ключа нічного сторно: після такого ніч сама відновлює нарахування
REFUND_TAG = ':refund'         # хвіст ключа поправки кредит-нотою вже виплаченого нарахування (часткове сторно)
NODATE_MARGIN = 31             # табеля немає — дата рахунку важить лише ближче стількох днів до меж періоду
FUND_KIND = 'worker_fund'      # вид домовленості «Фонд працівників» (06.11): не виплачується нікому
SECTION_LABELS = {'people': 'Агенти-кандидати', 'client': 'Агенти-замовники'}
# коди сторожа, які ставить і знімає ніч; порядок — важливість (кілька причин — у сторож перша за цим порядком)
GUARD_ORDER = ('overlap', 'dup_key', 'no_key', 'path_company', 'deal_draft', 'differs', 'no_rate', 'refund_partial', 'no_timesheet',
               'hours_partial', 'hours_unknown', 'no_deal', 'no_note', 'no_amount')
GUARD_TEXT = {
    'overlap': 'одна людина на одну дату в двох домовленостях агентів-кандидатів — подвійна виплата',
    'dup_key': 'ключ нарахування повторюється — подвійна виплата',
    'no_key': 'нарахування «за годину» без свого ключа — розрахунок створив би друге',
    'path_company': 'шлях виплати не збігається з фірмою виплати',
    'deal_draft': 'домовленість повернули в «Чернетка» — до виплати не йде',
    'differs': 'сума не збігається з розрахунком — виправлення лише сторно',
    'no_rate': 'у домовленості немає ставки',
    'refund_partial': 'кредит-нота повертає години без людини — які саме, не видно',
    'no_timesheet': 'годин без табеля — період і люди за датою рахунку',
    'hours_partial': 'частина годин рахунку без людини — перевірте, чи немає там людей агента',
    'hours_unknown': 'у рахунку замовника агента години без людей, а годин його людей 0 — перевірте',
    'no_deal': 'немає чинної домовленості',
    'no_note': 'разова без коментаря і джерела',
    'no_amount': 'сума разової не більше нуля',
}
NIGHT_CODES = GUARD_ORDER
# до оплати: ніч може міняти статус, дату виплати й сторожа; «У рахунку», «Виплачено», «Згоріло», «Скасовано» — не чіпає
PRE = ('expected', 'review', 'due')


class CalcError(Exception):
    """Помилка розрахунку (модель перетворює її на UserError / рядок журналу «Помилка»)."""


def make_cfg(official_company, personal_company):
    """Факти розрахунку з системних параметрів: фірма виплати за «Шляхом виплати», якщо в домовленості фірму не задано."""
    return {'path_company': {'personal_36': int(personal_company or 0), 'official_1': int(official_company or 0)}}


# ── дати ('РРРР-ММ-ДД' рядками) ──
def d_of(s):
    return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def add_days(s, n):
    return (d_of(s) + datetime.timedelta(days=n)).isoformat()


def ymd(y, m, d):
    yy = y + (m - 1) // 12
    mm = (m - 1) % 12 + 1
    return '%04d-%02d-%02d' % (yy, mm, d)


def fmt_date(d):
    return '%s.%s.%s' % (d[8:10], d[5:7], d[0:4]) if d else '—'


def day_diff(a, b):
    return (d_of(b) - d_of(a)).days


def near_edge(day, edges):
    # дата рахунку (табеля немає) ближче NODATE_MARGIN днів до межі періоду домовленості або людини — дата впливає на те, чиї це години
    for e in edges:
        if e and abs(day_diff(day, e)) <= NODATE_MARGIN:
            return True
    return False


def in_period(d, a, b):
    # d у [a, b]; порожня межа — без обмеження
    if not d:
        return False
    if a and d < a:
        return False
    if b and d > b:
        return False
    return True


# ── дрібниці ──
def m2o(v):
    if isinstance(v, (list, tuple)):
        return int(v[0]) if v else 0
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return 0


def ids_of(v):
    out = []
    for x in (v or []):
        i = m2o(x)
        if i and i not in out:
            out.append(i)
    return out


def sval(t):
    return '' if (t is False or t is None) else str(t)


def num(v):
    return float(v or 0)


def round2(x):
    # лише для невід'ємних: години × ставка
    return (x * 100 + 0.5) // 1 / 100.0


def fmt_h(x):
    return ('%.2f' % x).rstrip('0').rstrip('.')


# ── календар банків Польщі (п. 6.1 договору v1.7): будні без державних свят (ustawa o dniach wolnych od pracy, як plbank.py) ──
_HOLIDAYS = {}


def easter(y):
    # західний Великдень (Meeus/Jones/Butcher)
    a = y % 19
    b = y // 100
    c = y % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    n = h + l_ - 7 * m + 114
    return '%04d-%02d-%02d' % (y, n // 31, n % 31 + 1)


def pl_holidays(y):
    if y not in _HOLIDAYS:
        p = easter(y)
        days = ['%04d-01-01' % y, '%04d-01-06' % y, p, add_days(p, 1), '%04d-05-01' % y, '%04d-05-03' % y, add_days(p, 49), add_days(p, 60),
                '%04d-08-15' % y, '%04d-11-01' % y, '%04d-11-11' % y] + (['%04d-12-24' % y] if y >= 2025 else []) + ['%04d-12-25' % y, '%04d-12-26' % y]
        _HOLIDAYS[y] = sorted(set(days))
    return list(_HOLIDAYS[y])


def bank_day(s):
    return d_of(s).weekday() < 5 and s not in pl_holidays(int(s[0:4]))


def shift_bank(s):
    # сама дата, якщо робочий день банків Польщі, інакше наступний такий (не більше 15 кроків)
    k = 0
    while not bank_day(s) and k < 15:
        s = add_days(s, 1)
        k += 1
    return s


def pay_day_for(earned):
    # найближчий день виплати D (10-те або 25-те зі зсувом на робочий день банків Польщі), для якого зароблене не пізніше D − відсічка
    y = int(earned[0:4])
    m = int(earned[5:7])
    for k in range(3):
        for dd in sorted(PAY_DAYS):
            D = shift_bank(ymd(y, m + k, dd))
            if add_days(D, -CUTOFF) >= earned:
                return D
    raise CalcError('не знайдено дня виплати для %s' % earned)


def next_pay_day(day):
    # найближчий день виплати D ≥ day
    y = int(day[0:4])
    m = int(day[5:7])
    for k in range(3):
        for dd in sorted(PAY_DAYS):
            D = shift_bank(ymd(y, m + k, dd))
            if D >= day:
                return D
    raise CalcError('не знайдено дня виплати після %s' % day)


# ── години рахунку ──
def add_to(by, key, h):
    by[key] = by.get(key, 0.0) + h


def row_key(r):
    return (r[0], sval(r[1]))


def merge_rows(by):
    # {(співробітник, дата): години} → [[співробітник, дата або False, години]] по порядку (співробітник 0 — години без людей)
    out = []
    for k in sorted(by, key=row_key):
        if by[k] > 0.0005:
            out.append([k[0], k[1] or False, by[k]])
    return out


def hours_of(lines, maps, sheets):
    # lines — рядки рахунку в годинах [{quantity, sale_line_ids}], maps — {'<рядок замовлення>': [співробітник]} (project.sale.line.employee.map),
    # sheets — табелі цього рахунку [{employee_id, unit_amount, so_line, date}]. Години — за ДАТОЮ РОБОТИ. Основний шлях: рядок → рядок
    # замовлення → рівно один співробітник карти — його години = кількість рядка, за датами його табелів цього рядка замовлення (частки;
    # табеля немає — дата порожня). Запасний: рядки без такого зв'язку — за табелями рахунку, крім табелів уже зв'язаних рядків замовлення;
    # табелів більше, ніж годин цих рядків, — частки. Залишок без людей — співробітник 0.
    # → {'rows': [[співробітник або 0, дата або False, години]], 'total': години всіх рядків, 'free': години без людей}
    by = {}
    total = 0.0
    unres = 0.0
    done_sl = []
    used = []
    for l in lines:
        q = num(l.get('quantity'))
        total += q
        emps = []
        sls = ids_of(l.get('sale_line_ids'))
        for s in sls:
            for e in maps.get(str(s), []):
                if e not in emps:
                    emps.append(e)
        if len(emps) == 1:
            mine = [i for i in range(len(sheets)) if m2o(sheets[i].get('employee_id')) == emps[0] and m2o(sheets[i].get('so_line')) in sls]
            th = 0.0
            for i in mine:
                th += num(sheets[i].get('unit_amount'))
            if th > 0.005:
                for i in mine:
                    add_to(by, (emps[0], sval(sheets[i].get('date'))), q * num(sheets[i].get('unit_amount')) / th)
                    used.append(i)
            else:
                add_to(by, (emps[0], ''), q)
            for s in sls:
                if s not in done_sl:
                    done_sl.append(s)
        else:
            unres += q
    free = unres
    if unres > 0.005:
        rest = [sheets[i] for i in range(len(sheets)) if i not in used and m2o(sheets[i].get('so_line')) not in done_sl and m2o(sheets[i].get('employee_id'))]
        th = 0.0
        for t in rest:
            th += num(t.get('unit_amount'))
        if th > 0.005:
            k = unres / th if th > unres else 1.0
            for t in rest:
                add_to(by, (m2o(t.get('employee_id')), sval(t.get('date'))), num(t.get('unit_amount')) * k)
            free = max(0.0, unres - th)
    if free > 0.005:
        add_to(by, (0, ''), free)
    return {'rows': merge_rows(by), 'total': total, 'free': free}


def net_hours(H, R):
    # години, повернуті кредит-нотою (out_refund до цього рахунку), не нараховуємо. R — hours_of за рядками кредит-нот (без табелів). Години
    # співробітника знімаються з його рядків частками; без співробітника (або більше, ніж у нього) — з усіх рядків частками, і це
    # «refund_free» (сторож: які саме години повернули — не видно). → H з полями refund, refund_free
    if not R or num(R.get('total')) <= 0.005:
        return H
    ref = {}
    left = 0.0
    for r in R.get('rows', []):
        if r[0]:
            ref[r[0]] = ref.get(r[0], 0.0) + num(r[2])
        else:
            left += num(r[2])
    have = {}
    for r in H.get('rows', []):
        if r[0]:
            have[r[0]] = have.get(r[0], 0.0) + num(r[2])
    for e in ref:
        if ref[e] > have.get(e, 0.0):
            left += ref[e] - have.get(e, 0.0)
    by = {}
    for r in H.get('rows', []):
        h = num(r[2])
        if r[0] and have.get(r[0]):
            h = h * (have[r[0]] - min(have[r[0]], ref.get(r[0], 0.0))) / have[r[0]]
        add_to(by, (r[0], sval(r[1])), h)
    tot = 0.0
    for k in by:
        tot += by[k]
    if left > 0.005 and tot > 0.005:
        f = max(0.0, tot - left) / tot
        for k in by:
            by[k] = by[k] * f
    rows = merge_rows(by)
    free = 0.0
    for r in rows:
        if not r[0]:
            free += r[2]
    return {'rows': rows, 'total': max(0.0, num(H.get('total')) - num(R.get('total'))), 'free': free, 'refund': num(R.get('total')), 'refund_free': left,
            'refund_names': R.get('names', [])}


# ── домовленості й нарахування ──
def deal_key(section, did):
    return '%s%d' % ('p' if section == 'people' else 'c', did)


def deal_of(a, deals):
    if a.get('section') == 'client':
        return deals.get(deal_key('client', m2o(a.get('client_deal_id'))))
    return deals.get(deal_key('people', m2o(a.get('people_deal_id'))))


def is_fund(deal):
    # домовленість «Фонд працівників»: фонд на потреби людей — ніколи не до виплати
    return bool(deal) and deal.get('kind') == FUND_KIND


def is_zv_deal(deal):
    # «Лише звірка» — режим mode; стара стадія state = zvirka — теж (запасна ознака)
    return bool(deal) and (deal.get('mode') == 'zvirka' or deal.get('state') == 'zvirka')


def cancel_ok(a, D):
    # нарахування можна скасувати вночі («Звірка», яку домовленість не покриває; фонд зі зміненою ставкою): ще до оплати, поля рядка рішення й
    # виплати прочитані й порожні
    return a.get('state') in PRE and bool(D.get('pay_links')) and not m2o(a.get('pay_line_id')) and not m2o(a.get('payout_id'))


def full_rate_of(emp, day, vers, cur_ver, has_field):
    # «Повна ставка» людини на дату роботи: True / False; None — не визначити. vers — {співробітник: [[дата версії, повна, id версії]]},
    # cur_ver — {співробітник: id поточної версії}. Версія — остання з датою ≤ дня (за рівної дати — більший id); такої немає — найРАНІША після
    # дня; версій з датою немає зовсім — поточна. Поля «Повна ставка» на базі немає — False
    if not emp:
        return None
    if not has_field:
        return False
    best = None
    after = None
    for v in vers.get(emp, []):
        if not sval(v[0]):
            continue
        if sval(v[0]) <= day:
            if best is None or [sval(v[0]), v[2]] > [sval(best[0]), best[2]]:
                best = v
        elif after is None or [sval(v[0]), v[2]] < [sval(after[0]), after[2]]:
            after = v
    if best is None:
        best = after
    if best is None:
        hit = [v for v in vers.get(emp, []) if v[2] == cur_ver.get(emp)]
        best = hit[0] if hit else None
    if best is None:
        return None
    return bool(best[1])


def fund_rates(F, d):
    # розбивка годин фонду за ставками: «<години> × <ставка> неповна + <години> × <ставка> повна»
    out = []
    for k in [['part', 'неповна'], ['full', 'повна'], ['full_cheap', 'повна, «Ставки на повну» немає'], ['unknown', 'не визначено']]:
        if F[k[0]] > 0.005:
            out.append('%s год × %s € %s' % (fmt_h(F[k[0]]), fmt_h(num(d.get('rate_full')) if k[0] == 'full' else num(d.get('rate'))), k[1]))
    return ' + '.join(out) or '0 год'


def target_state(deal, kind):
    # «До виплати» — лише розділ «кандидати» в «Діє» (CLIENT_PAYS: на етапі 1 «замовники» — рахуємо, не платимо); «Звірка» — «Очікується»;
    # закрита домовленість: зароблене раніше статус не міняє (None), сторно вже виплаченого — до виплати. Фонд — «Очікується» завжди
    if not deal:
        return None
    st = deal.get('state')
    if is_fund(deal):
        return 'expected' if (st in LIVE or (st == 'closed' and kind == 'storno')) else None
    pays = deal['section'] == 'people' or CLIENT_PAYS
    if st in ('active', 'zvirka') and is_zv_deal(deal):
        return 'expected'
    if st == 'active':
        return 'due' if pays else 'expected'
    if st == 'closed' and kind == 'storno':
        return 'due' if pays else 'expected'
    return None


def company_of(deal, cfg):
    c = m2o(deal.get('company_id'))
    if c:
        return c
    return cfg['path_company'].get(sval(deal.get('pay_path'))) or False


def path_bad(deal, cfg):
    # «Шлях виплати» ↔ фірма виплати: official_1 — лише фірма official, personal_36 — лише personal; custom і порожня фірма — не перевіряємо
    want = cfg['path_company'].get(sval(deal.get('pay_path')))
    c = m2o(deal.get('company_id'))
    if want and c and c != want:
        return 'шлях «%s», а фірма виплати #%d (треба #%d)' % (sval(deal.get('pay_path')), c, want)
    return ''


def pick_code(codes):
    for c in GUARD_ORDER:
        for x in codes:
            if x[0] == c:
                return x
    return codes[0] if codes else ['', '']


def paid(m):
    return bool(m) and m.get('state') == 'posted' and m.get('payment_state') in PAID_STATES


def acc_order_key(a):
    return a['id']


def deal_order_key(d):
    return (d['section'], d['id'])


def family(accruals, base):
    out = [a for a in accruals if sval(a.get('key')) == base or sval(a.get('key')).startswith(base + '#')]
    return sorted(out, key=acc_order_key)


def guard_note(code, extra):
    t = GUARD_TEXT.get(code, code)
    return (t + (': ' + extra if extra else ''))[0:250]


def eff_amount(a, corr):
    # сума нарахування з поправками кредит-нотами (часткові сторно з хвостом REFUND_TAG)
    s = num(a.get('amount'))
    for c in corr.get(a['id'], []):
        s += num(c.get('amount'))
    return round2(s) if s > 0 else s


def eff_hours(a, corr):
    s = num(a.get('hours'))
    for c in corr.get(a['id'], []):
        s += num(c.get('hours'))
    return round2(s) if s > 0 else s


def storno_vals(a, why, deal, today, has_line, used, part=None):
    # сторно нарахування a: сума й години з мінусом, посилання на нього; ще не в оплаті — обидва «Скасовано», інакше сторно йде в найближчу
    # виплату. part — {amount, hours[, key]}: сторно не всієї суми (залишок після поправок або поправка кредит-нотою: key з хвостом
    # REFUND_TAG — вихідне лишається чинним)
    key = (part or {}).get('key') or 'storno:%s%s' % (sval(a.get('key')) or ('a%d' % a['id']), AUTO_SUFFIX)
    if key in used:
        return None
    is_corr = REFUND_TAG in key
    pre = a.get('state') in PRE and not is_corr
    amt = num((part or {}).get('amount')) if part else num(a.get('amount'))
    hrs = num((part or {}).get('hours')) if part else num(a.get('hours'))
    v = {'section': a.get('section'), 'people_deal_id': m2o(a.get('people_deal_id')) or False, 'client_deal_id': m2o(a.get('client_deal_id')) or False,
         'agent_id': m2o(a.get('agent_id')), 'kind': 'storno', 'person_id': m2o(a.get('person_id')) or False, 'customer_id': m2o(a.get('customer_id')) or False,
         'move_id': m2o(a.get('move_id')) or False, 'hours': -hrs, 'rate': num(a.get('rate')), 'amount': -amt,
         'company_id': m2o(a.get('company_id')) or False, 'earned_date': today, 'storno_id': a['id'], 'key': key, 'note': why[0:250]}
    if has_line:
        v['line_id'] = m2o(a.get('line_id')) or False
    # сторно нарахування фонду — теж фонд (поле є на базі — ключ fund прочитано в нарахування)
    if 'fund' in a and (a.get('fund') or is_fund(deal)):
        v['fund'] = True
    if pre:
        v['state'] = 'cancelled'
        v['pay_date'] = False
        return {'orig': a['id'], 'vals': v, 'orig_vals': {'state': 'cancelled'}, 'orig_before': {'state': a.get('state')}}
    st = target_state(deal, 'storno') or 'review'
    v['state'] = st
    v['pay_date'] = pay_day_for(today)
    if st == 'review':
        v['guard'] = 'no_deal'
        v['guard_note'] = guard_note('no_deal', '')
    return {'orig': a['id'], 'vals': v, 'orig_vals': None, 'orig_before': None}


def uncovered_why(d, mv, H, line_cust):
    # чому домовленість більше не покриває записане нарахування за рахунком: замовника немає серед її замовників (або лінії), робота поза
    # її періодом; інакше — загальне «умови»
    name = sval((mv or {}).get('name')) or '—'
    if d['section'] == 'client':
        custs = ids_of(d.get('customer_ids'))
        if not custs and m2o(d.get('line_id')):
            custs = line_cust.get(m2o(d.get('line_id')), [])
        cust = m2o((mv or {}).get('commercial_partner_id')) or m2o((mv or {}).get('partner_id'))
        if mv and cust not in custs and m2o(mv.get('partner_id')) not in custs:
            return 'замовника #%d рахунку %s у домовленості #%d немає' % (cust, name, d['id'])
    rows = (H or {}).get('rows', [])
    inv = sval((mv or {}).get('invoice_date'))
    if rows and not [r for r in rows if in_period(sval(r[1]) or inv, sval(d.get('date_from')), sval(d.get('date_to')))]:
        return 'робота за рахунком %s поза періодом домовленості #%d (з %s%s)' % (name, d['id'], fmt_date(sval(d.get('date_from'))),
                                                                              (' по ' + fmt_date(sval(d.get('date_to')))) if d.get('date_to') else '')
    return 'умови домовленості #%d (період, люди, замовники) рахунок %s не рахують' % (d['id'], name)


def compute(D, today, cfg):
    # D: deals_people, deals_client, members, line_customers [[лінія, [замовники]]], moves [{id, name, invoice_date, partner_id,
    # commercial_partner_id, company_id, state, payment_state, pay_date}], hours [[рахунок, hours_of(...)]], emp_contact [[співробітник, контакт]],
    # accruals, has_line, pay_links, versions, emp_version, full_rate_field, fund_field, work_dates, state_since.
    # cfg — make_cfg(...). → {creates: [{key, vals}], writes: [{id, vals, before}], stornos: [{orig, vals, orig_vals, orig_before}], notes, summary}
    has_line = bool(D.get('has_line'))
    deals = {}
    order = []
    for d in D.get('deals_people', []):
        x = dict(d)
        x['section'] = 'people'
        deals[deal_key('people', d['id'])] = x
        order.append(x)
    for d in D.get('deals_client', []):
        x = dict(d)
        x['section'] = 'client'
        deals[deal_key('client', d['id'])] = x
        order.append(x)
    order = sorted(order, key=deal_order_key)
    members = {}
    for mm in D.get('members', []):
        k = m2o(mm.get('deal_id'))
        members[k] = members.get(k, []) + [mm]
    line_cust = {}
    for row in D.get('line_customers', []):
        line_cust[row[0]] = ids_of(row[1])
    moves = {}
    for mv in D.get('moves', []):
        moves[mv['id']] = mv
    hours = {}
    for row in D.get('hours', []):
        hours[row[0]] = row[1]
    contact = {}
    for row in D.get('emp_contact', []):
        contact[row[0]] = row[1]
    vers = {}
    for row in D.get('versions', []):
        vers[row[0]] = vers.get(row[0], []) + [[row[1], row[2], row[3]]]
    cur_ver = {}
    for row in D.get('emp_version', []):
        cur_ver[row[0]] = row[1]
    has_full = bool(D.get('full_rate_field'))
    accr = sorted(D.get('accruals', []), key=acc_order_key)
    # сторно: повне (ключ без хвоста REFUND_TAG) знімає нарахування цілком; поправка кредит-нотою (з хвостом) — лише частину
    stornoed = {}
    corr = {}
    for a in accr:
        if a.get('kind') == 'storno' and m2o(a.get('storno_id')):
            if REFUND_TAG in sval(a.get('key')):
                if a.get('state') != 'cancelled':
                    corr[m2o(a.get('storno_id'))] = corr.get(m2o(a.get('storno_id')), []) + [a]
            else:
                stornoed[m2o(a.get('storno_id'))] = a
    since = {}
    for row in D.get('state_since', []):
        since[row[0]] = sval(row[1])[0:10]
    by_key = {}
    for a in accr:
        k = sval(a.get('key'))
        if k:
            by_key[k] = by_key.get(k, []) + [a['id']]
    dup = [k for k in sorted(by_key) if len(by_key[k]) > 1]
    used = []
    for k in by_key:
        used.append(k)
    res = {'creates': [], 'writes': [], 'stornos': [], 'notes': [], 'dup_keys': dup,
           'summary': {'deals': 0, 'moves': 0, 'created_hour': 0, 'created_oneoff': 0, 'review': 0, 'stornos': 0, 'writes': 0}}
    want = {}
    seen = []
    cancelled = []
    act = []
    for a in accr:
        if a.get('state') != 'cancelled' and a['id'] not in stornoed:
            act.append(a['id'])
    # чинні нарахування «за годину» за парою домовленість × рахунок — за посиланнями, не за ключем
    pair = {}
    for a in accr:
        if a['id'] in act and a.get('kind') == 'hour' and m2o(a.get('move_id')):
            sec_a = a.get('section')
            did = m2o(a.get('client_deal_id')) if sec_a == 'client' else m2o(a.get('people_deal_id'))
            pk = '%s:m%d' % (deal_key(sec_a, did), m2o(a.get('move_id')))
            pair[pk] = pair.get(pk, []) + [a]

    # 1) «€ за годину»: рахунок × домовленість (обидва розділи) — нове нарахування, порівняння із записаним
    paid_moves = sorted([mv for mv in D.get('moves', []) if paid(mv)], key=acc_order_key)
    res['summary']['moves'] = len(paid_moves)
    calc = []
    claims = {}
    incomplete = {}
    for d in order:
        if d.get('state') not in LIVE:
            continue
        sec = d['section']
        if not ((sec == 'people' and d.get('kind') == 'hour') or (sec == 'client' and d.get('kind') in ('hour', 'fixed', FUND_KIND))):
            incomplete[deal_key(sec, d['id'])] = 'вид «%s» не рахується за годину' % sval(d.get('kind'))
            continue
        if not d.get('date_from'):
            res['notes'].append('домовленість %s #%d без дати «Діє з» — не рахую' % ('агента-кандидата' if sec == 'people' else 'агента-замовника', d['id']))
            incomplete[deal_key(sec, d['id'])] = 'немає «Діє з»'
            continue
        res['summary']['deals'] += 1
        custs = ids_of(d.get('customer_ids'))
        if not custs and m2o(d.get('line_id')):
            custs = line_cust.get(m2o(d.get('line_id')), [])
        if sec == 'client' and not custs:
            incomplete[deal_key(sec, d['id'])] = ('лінія #%d без замовників' % m2o(d.get('line_id'))) if m2o(d.get('line_id')) else 'немає ні замовників, ні лінії'
        mems = members.get(d['id'], [])
        for mv in paid_moves:
            H = hours.get(mv['id'])
            if not H:
                continue
            inv = sval(mv.get('invoice_date'))
            cust = m2o(mv.get('commercial_partner_id')) or m2o(mv.get('partner_id'))
            if sec == 'client' and cust not in custs and m2o(mv.get('partner_id')) not in custs:
                continue
            h = 0.0
            free = 0.0
            nodate = 0.0
            nodate_far = 0.0
            persons = []
            days = []
            touched = False
            fund = {'amount': 0.0, 'part': 0.0, 'full': 0.0, 'unknown': 0.0, 'full_cheap': 0.0} if is_fund(d) else None
            for row in H.get('rows', []):
                emp = row[0]
                day = sval(row[1]) or inv
                hh = num(row[2])
                if not in_period(day, sval(d.get('date_from')), sval(d.get('date_to'))):
                    continue
                touched = True
                edges = [sval(d.get('date_from')), sval(d.get('date_to'))]
                if sec == 'client':
                    h += hh
                    if day and hh > 0.005:
                        days.append(day)
                    if fund is not None:
                        fr = full_rate_of(emp, day, vers, cur_ver, has_full)
                        if fr and num(d.get('rate_full')) > 0:
                            fund['full'] += hh
                            fund['amount'] += hh * num(d.get('rate_full'))
                        else:
                            fund['unknown' if fr is None else ('full_cheap' if fr else 'part')] += hh
                            fund['amount'] += hh * num(d.get('rate'))
                    if not row[1]:
                        if near_edge(day, edges):
                            nodate += hh
                        else:
                            nodate_far += hh
                    continue
                if not emp:
                    free += hh
                    continue
                p = contact.get(emp)
                mm = [x for x in mems if m2o(x.get('partner_id')) == p and in_period(day, sval(x.get('date_from')), sval(x.get('date_to')))] if p else []
                if not mm:
                    continue
                h += hh
                if day and hh > 0.005:
                    days.append(day)
                if not row[1]:
                    if near_edge(day, edges + [sval(mm[0].get('date_from')), sval(mm[0].get('date_to'))]):
                        nodate += hh
                    else:
                        nodate_far += hh
                if p not in persons:
                    persons.append(p)
                ck = '%d:%d:%s' % (mv['id'], p, day)
                if deal_key(sec, d['id']) not in claims.get(ck, []):
                    claims[ck] = claims.get(ck, []) + [deal_key(sec, d['id'])]
            if touched:
                calc.append({'d': d, 'mv': mv, 'h': h, 'free': free, 'nodate': nodate, 'nodate_far': nodate_far, 'persons': persons, 'cust': cust, 'H': H,
                             'days': sorted(days), 'fund': fund})
    # НЕ чинна домовленість «кандидати» з чинними нарахуваннями за цим рахунком — теж у пошуку перетинів; без «Діє по» — до дати зміни статусу
    for pk in sorted(pair):
        if not pk.startswith('p'):
            continue
        did = int(pk[1:].split(':')[0])
        mid = int(pk.split(':m')[1])
        d = deals.get(deal_key('people', did))
        H = hours.get(mid)
        if not d or d.get('state') in LIVE or not H:
            continue
        mv = moves.get(mid) or {}
        end = sval(d.get('date_to')) or since.get(did, '')
        mems = members.get(did, [])
        for row in H.get('rows', []):
            p = contact.get(row[0]) if row[0] else 0
            day = sval(row[1]) or sval(mv.get('invoice_date'))
            if not p or not in_period(day, sval(d.get('date_from')), end):
                continue
            if not [x for x in mems if m2o(x.get('partner_id')) == p and in_period(day, sval(x.get('date_from')), sval(x.get('date_to')))]:
                continue
            ck = '%d:%d:%s' % (mid, p, day)
            if deal_key('people', did) not in claims.get(ck, []):
                claims[ck] = claims.get(ck, []) + [deal_key('people', did)]
    # одна людина за один день роботи — у двох домовленостях «кандидати» → подвійна виплата; обидва нарахування — «На перевірці» overlap
    over = {}
    for ck in sorted(claims):
        ds = claims[ck]
        if len(ds) > 1:
            parts = ck.split(':')
            for dk in ds:
                over['%s:%s' % (dk, parts[0])] = 'людина #%s %s — у домовленостях %s одночасно' % (parts[1], fmt_date(parts[2]), ', '.join(['#' + x[1:] for x in ds]))
                for a in pair.get('%s:m%s' % (dk, parts[0]), []):
                    want[a['id']] = ['overlap', over['%s:%s' % (dk, parts[0])]]
    # «замовники агента» за домовленістю: там уже є години його людей — для сторожа hours_unknown
    agent_cust = {}
    for c in calc:
        dk = deal_key(c['d']['section'], c['d']['id'])
        if c['h'] > 0.005 and c['cust'] not in agent_cust.get(dk, []):
            agent_cust[dk] = agent_cust.get(dk, []) + [c['cust']]
    for pk in pair:
        for a in pair[pk]:
            dk_a = pk.split(':m')[0]
            if a.get('customer_id') and m2o(a.get('customer_id')) not in agent_cust.get(dk_a, []):
                agent_cust[dk_a] = agent_cust.get(dk_a, []) + [m2o(a.get('customer_id'))]
    for c in calc:
        d = c['d']
        mv = c['mv']
        H = c['H']
        h = c['h']
        persons = c['persons']
        cust = c['cust']
        sec = d['section']
        dk = deal_key(sec, d['id'])
        rate = num(d.get('rate'))
        F = c.get('fund')
        amount = (round2(F['amount']) if F is not None else round2(h * rate)) if h > 0 else 0.0
        base = 'hour:%s:m%d' % (dk, mv['id'])
        seen.append(base)
        codes = []
        if over.get('%s:%d' % (dk, mv['id'])):
            codes.append(['overlap', over['%s:%d' % (dk, mv['id'])]])
        pb = path_bad(d, cfg)
        if pb:
            codes.append(['path_company', pb])
        if rate <= 0:
            codes.append(['no_rate', ''])
        if num(H.get('refund_free')) > 0.005:
            codes.append(['refund_partial', '%s год (%s)' % (fmt_h(num(H.get('refund_free'))), ', '.join(H.get('refund_names') or []))])
        if c['nodate'] > 0.005:
            codes.append(['no_timesheet', '%s год рахунку %s без табеля — період за датою рахунку %s' % (fmt_h(c['nodate']), sval(mv.get('name')), fmt_date(sval(mv.get('invoice_date'))))])
        if sec == 'people' and c['free'] > 0.005:
            if h > 0.005:
                codes.append(['hours_partial', '%s год у рахунку %s без людини' % (fmt_h(c['free']), sval(mv.get('name')))])
            elif cust in agent_cust.get(dk, []):
                codes.append(['hours_unknown', '%s год у рахунку %s без людини, годин людей агента 0' % (fmt_h(c['free']), sval(mv.get('name')))])
        fam = family(accr, base)
        fam_ids = [a['id'] for a in fam]
        live = [a for a in fam if a['id'] in act]
        others = [a for a in pair.get('%s:m%d' % (dk, mv['id']), []) if a['id'] not in fam_ids]
        for a in others:
            want[a['id']] = ['no_key', 'ключ «%s», розрахунок дає «%s» — друге нарахування не створюю' % (sval(a.get('key')) or '—', base)]
        if F is not None and live:
            # фонд: записане з іншою сумою (змінили ставку фонду) і ще не в рішенні / виплаті — сторно «Скасовано» з причиною, потім нове з
            # ключем #N (нижче); у рішенні / виплаті — «На перевірці» differs, нового немає
            keep = [a for a in live if abs(eff_amount(a, corr) - amount) <= 0.005]
            redo = 0
            for a in live:
                if keep and a['id'] == keep[0]['id']:
                    continue
                ea = eff_amount(a, corr)
                if cancel_ok(a, D):
                    s = storno_vals(a, 'Сторно: ставку фонду змінено — розрахунок дає %.2f € (%s), записано %.2f €' % (amount, fund_rates(F, d), ea), d, today, has_line, used,
                                    {'amount': ea, 'hours': eff_hours(a, corr)} if corr.get(a['id']) else None)
                    if s:
                        used.append(s['vals']['key'])
                        res['stornos'].append(s)
                        if s['orig_vals']:
                            cancelled.append(a['id'])
                        redo += 1
                        continue
                want[a['id']] = ['differs', 'розрахунок фонду дає %.2f € (%s), записано %.2f € — уже в рішенні чи виплаті, вирішує людина' % (amount, fund_rates(F, d), ea)]
            if keep:
                want[keep[0]['id']] = pick_code(codes)
                continue
            if redo < len(live):
                continue
        elif live:
            a = live[0]
            ea = eff_amount(a, corr)
            if abs(ea - amount) > 0.005:
                # уже «Виплачено» / «У рахунку» / «Закрито рішенням», а кредит-нота зняла години — поправка сторно-мінусом у найближчу виплату
                if a.get('state') in ('paid', 'in_bill', 'decided') and ea > amount and num(H.get('refund')) > 0.005 and not [x for x in codes if x[0] == 'overlap']:
                    s = storno_vals(a, 'Кредит-нота %s: −%s год, −%.2f € (виплачене не чіпаємо — мінус у найближчу виплату)' % (', '.join(H.get('refund_names') or []),
                                    fmt_h(eff_hours(a, corr) - round2(h)), ea - amount), d, today, has_line, used,
                                    {'amount': ea - amount, 'hours': eff_hours(a, corr) - round2(h), 'key': 'storno:%s%s%d' % (sval(a.get('key')) or 'a%d' % a['id'], REFUND_TAG, len(corr.get(a['id'], [])) + 1)})
                    if s:
                        used.append(s['vals']['key'])
                        res['stornos'].append(s)
                    continue
                codes.append(['differs', 'розрахунок дає %.2f € (%s год × %s €), записано %.2f €' % (amount, fmt_h(h), fmt_h(rate), ea)])
            want[a['id']] = pick_code(codes)
            continue
        if others:
            continue
        unknown = len([x for x in codes if x[0] == 'hours_unknown']) > 0
        if h <= 0.005 and not unknown:
            continue
        code, extra = pick_code(codes)
        if fam:
            last = fam[len(fam) - 1]
            s = stornoed.get(last['id'])
            auto = bool(s) and sval(s.get('key')).endswith(AUTO_SUFFIX)
            if not auto and abs(num(last.get('amount')) - amount) <= 0.005:
                res['notes'].append('%s сторновано вручну, джерело не змінилось — не відновлюю' % base)
                continue
            key = '%s#%d' % (base, len(fam) + 1)
        else:
            key = base
        if key in used:
            res['notes'].append('ключ %s уже є — не створюю (дубль = подвійна виплата)' % key)
            continue
        used.append(key)
        st = 'review' if code else (target_state(d, 'hour') or 'expected')
        earned = sval(mv.get('pay_date')) or today
        v = {'section': sec, 'agent_id': m2o(d.get('agent_id')), 'kind': 'hour', 'person_id': persons[0] if len(persons) == 1 else False,
             'customer_id': cust or False, 'move_id': mv['id'], 'hours': round2(h), 'rate': rate, 'amount': amount, 'earned_date': earned,
             'pay_date': pay_day_for(max(earned, today)), 'company_id': company_of(d, cfg), 'state': st, 'key': key,
             'guard': code or False, 'guard_note': guard_note(code, extra) if code else False,
             'note': 'Рахунок %s: %s год × %s € (оплачено %s)' % (sval(mv.get('name')), fmt_h(h), fmt_h(rate), fmt_date(earned))}
        if F is not None:
            v['rate'] = round2(amount / h) if h > 0.005 else rate
            if D.get('fund_field'):
                v['fund'] = True
            v['note'] = 'Фонд працівників (не виплачується). Рахунок %s: %s (оплачено %s)' % (sval(mv.get('name')), fund_rates(F, d), fmt_date(earned))
            if F['unknown'] > 0.005:
                v['note'] = '%s. Ставку зайнятості не визначено — %s €: %s год' % (v['note'], fmt_h(rate), fmt_h(F['unknown']))
            if not has_full:
                v['note'] = '%s. «Повна ставка» у договорах не ведеться — за неповною' % v['note']
        if c['nodate_far'] > 0.005:
            v['note'] = '%s. %s год без табеля — за датою рахунку %s (далеко від меж періоду, сторож не потрібен)' % (v['note'], fmt_h(c['nodate_far']), fmt_date(sval(mv.get('invoice_date'))))
        if D.get('work_dates') and c['days']:
            v['work_from'] = c['days'][0]
            v['work_to'] = c['days'][len(c['days']) - 1]
        if sec == 'people':
            v['people_deal_id'] = d['id']
        else:
            v['client_deal_id'] = d['id']
        if has_line:
            ln = m2o(d.get('line_id'))
            if not ln:
                hit = [row for row in sorted(line_cust) if cust in line_cust[row]]
                ln = hit[0] if len(hit) == 1 else 0
            v['line_id'] = ln or False
        res['creates'].append({'key': key, 'vals': v})
        res['summary']['created_hour'] += 1
        if st == 'review':
            res['summary']['review'] += 1

    # 2) рахунок більше не оплачений (повернення, скасування, знята оплата) або видалений — сторно; умови змінились — сторож
    for a in accr:
        own_key = sval(a.get('key')).startswith('hour:')
        mid = m2o(a.get('move_id'))
        if a['id'] not in act or a.get('kind') != 'hour' or not (own_key or mid):
            continue
        mv = moves.get(mid)
        why = ''
        if not mid:
            why = 'Сторно: рахунок видалено'
        elif mv and not paid(mv):
            why = 'Сторно: рахунок %s більше не оплачений повністю (%s / %s)' % (sval(mv.get('name')), sval(mv.get('state')), sval(mv.get('payment_state')))
        if why:
            s = storno_vals(a, why, deal_of(a, deals), today, has_line, used, {'amount': eff_amount(a, corr), 'hours': eff_hours(a, corr)} if corr.get(a['id']) else None)
            if s:
                used.append(s['vals']['key'])
                res['stornos'].append(s)
                if s['orig_vals']:
                    cancelled.append(a['id'])
            continue
        d = deal_of(a, deals)
        base = sval(a.get('key')).split('#')[0]
        if own_key and d and d.get('state') in LIVE and base not in seen and a['id'] not in want:
            gap = incomplete.get(deal_key(d['section'], d['id']))
            if gap:
                want[a['id']] = ['differs', 'домовленість неповна (%s) — розрахунок її не рахує, записано %.2f €' % (gap, num(a.get('amount')))]
                continue
            if (is_zv_deal(d) or is_fund(d)) and cancel_ok(a, D):
                s = storno_vals(a, 'Сторно: домовленість більше не покриває рахунок (%s)' % uncovered_why(d, mv, hours.get(mid), line_cust), d, today, has_line, used,
                                {'amount': eff_amount(a, corr), 'hours': eff_hours(a, corr)} if corr.get(a['id']) else None)
                if s:
                    used.append(s['vals']['key'])
                    res['stornos'].append(s)
                    if s['orig_vals']:
                        cancelled.append(a['id'])
                    continue
            want[a['id']] = ['differs', 'за поточними умовами домовленості цей рахунок не рахується (період, люди, замовники), записано %.2f €' % num(a.get('amount'))]
    res['summary']['stornos'] = len(res['stornos'])

    # 3) чекають оплати: статус за домовленістю, день виплати, ключ разових, сторож
    for a in accr:
        if a.get('state') not in PRE or a['id'] in cancelled or a['id'] in stornoed:
            continue
        d = deal_of(a, deals)
        kind = a.get('kind')
        fresh = not sval(a.get('key'))
        codes = []
        if a['id'] in want and want[a['id']][0]:
            codes.append(want[a['id']])
        if kind == 'oneoff':
            if d and d.get('state') == 'draft' and a.get('state') != 'due':
                continue  # домовленість ще «Чернетка» — разова чекає
            if fresh and (not d or d.get('state') not in list(LIVE) + ['draft']):
                codes.append(['no_deal', ''])
            elif not sval(a.get('note')).strip():
                codes.append(['no_note', ''])
            elif num(a.get('amount')) <= 0:
                codes.append(['no_amount', ''])
        if kind == 'hour' and fresh:
            codes.append(['no_key', 'ключ порожній — нарахування «за годину» без ключа не виплачується'])
        if d and d.get('state') == 'draft' and a.get('state') == 'due':
            codes.append(['deal_draft', 'домовленість #%d повернули в «Чернетка»' % d['id']])
        if d and path_bad(d, cfg) and kind in ('oneoff', 'storno'):
            codes.append(['path_company', path_bad(d, cfg)])
        if sval(a.get('key')) in dup:
            codes.append(['dup_key', sval(a.get('key'))])
        code, extra = pick_code(codes)
        cur = sval(a.get('guard'))
        st = a.get('state')
        v = {}
        # підтвердження людиною («На перевірці» → «До виплати») тримається за КОД І ТЕКСТ примітки разом
        note = guard_note(code, extra) if code else ''
        if code and (code != cur or note != sval(a.get('guard_note'))):
            v['guard'] = code
            v['guard_note'] = note
            v['state'] = 'review'
        elif code and st == 'review':
            pass
        else:
            if not code and cur in NIGHT_CODES:
                v['guard'] = False
                v['guard_note'] = False
            if st == 'review' and not (cur in NIGHT_CODES and not code):
                pass  # «На перевірці» поставила людина (або вона підтвердила сторожа) — не знімаємо
            else:
                t = target_state(d, kind)
                if t == 'due' and (a.get('fund') or is_fund(d)):
                    t = 'expected'
                if t and t != st:
                    v['state'] = t
        new = v.get('state', st)
        if D.get('fund_field') and is_fund(d) and not a.get('fund'):
            v['fund'] = True
        if new in ('expected', 'due'):
            earned = sval(a.get('earned_date')) or today
            if not a.get('pay_date') or (new == 'due' and st != 'due'):
                pd = pay_day_for(max(earned, today))
                if pd != sval(a.get('pay_date')):
                    v['pay_date'] = pd
        if not a.get('earned_date') and kind in ('oneoff', 'storno'):
            v['earned_date'] = today
        if d and new != 'review' and not m2o(a.get('company_id')) and company_of(d, cfg):
            v['company_id'] = company_of(d, cfg)
        if fresh and kind == 'oneoff' and new != 'review':
            k = 'oneoff:a%d' % a['id']
            if k not in used:
                used.append(k)
                v['key'] = k
        if v:
            before = {}
            for f in v:
                before[f] = a.get(f, False)
            res['writes'].append({'id': a['id'], 'vals': v, 'before': before})
            if v.get('state') == 'review':
                res['summary']['review'] += 1
            if fresh and kind == 'oneoff' and v.get('key'):
                res['summary']['created_oneoff'] += 1
    res['summary']['writes'] = len(res['writes'])
    return res


# ── вид і стан агента, «Привів агент» (одне правило з вычисляемыми полями моделей) ──
def agent_mode_of(deals, today=''):
    # стан агента — найгірший із живих домовленостей: «лише звірка» гірше «платимо»; живих немає — чернетка, усі закриті — закрито,
    # інакше — немає домовленостей. «Діє» з минулим «Діє по» — для стану закрита. deals — [{state, mode, date_to}]
    ds = deals or []
    live = [d for d in ds if d.get('state') in LIVE and not (today and d.get('date_to') and sval(d.get('date_to'))[0:10] < today)]
    if live:
        return 'zvirka' if [d for d in live if d.get('mode') == 'zvirka' or d.get('state') == 'zvirka'] else 'pay'
    return 'draft' if [d for d in ds if d.get('state') == 'draft'] else ('closed' if ds else 'none')


def agent_kind_of(pd_n, cd_n):
    return 'both' if pd_n and cd_n else ('people' if pd_n else ('client' if cd_n else 'none'))


def best_member(members, today):
    # «Привів агент» працівника: чинний на сьогодні рядок «Люди агента» (спершу жива домовленість), інакше — останній за «Працює з» серед тих,
    # що почались; рядок із майбутнім початком виграє, лише якщо інших немає. members — [{id, date_from, date_to, deal_state}] → id або 0
    best = 0
    top = None
    for m in members or []:
        fr = sval(m.get('date_from'))[0:10]
        to = sval(m.get('date_to'))[0:10]
        now = 1 if (fr <= today and (not to or to >= today)) else 0
        started = 1 if fr <= today else 0
        score = [now, 1 if now and m.get('deal_state') in LIVE else 0, started, fr, m['id']]
        if top is None or score > top:
            top = score
            best = m['id']
    return best


def zv_badge_pct(calc_amount, envelope):
    # відсоток різниці від конверта (для колонки «%» звірки); конверта немає — 0
    env_ = num(envelope)
    if abs(env_) < 0.005:
        return 0.0
    return round((num(calc_amount) - env_) * 100.0 / abs(env_), 1)
