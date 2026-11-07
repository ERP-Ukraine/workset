# -*- coding: utf-8 -*-
"""Чисті функції кроку виплат агентів: без Odoo і без бази (перенос tools/odoo_agents_calc/paid.py і помічників плану payout.py).

«Виплачено» (етап 1б-2а) — paid_plan(P, today): що зробити цієї ночі ДО кроку рішень (своя точка збереження в models/agent_run.py: закриті
виплати не потрапляють у нове рішення):
(1) «Офіційно» — виплата з рахунком постачальника (move_id): payment_state рахунку in_payment → «Сплачено, чекаємо виписку»; paid →
    «Виплачено» датою рядка виписки, яким закрито рахунок (звірка: кредиторка → платіж → «в дорозі» → виписка, settle_of, два кроки; виписки
    немає — дата платежу). Рахунок скасовано / сторновано / видалено / сума ≠ «До переказу» — «На перевірці» з кодом сторожа;
(2) «Особисто» — виплата без рахунку: рядок банківських журналів WS фірми personal з датою ≥ дня рішення, партнер = агент, сума =
    −«До переказу» ±0,01, ще не прив'язаний до іншої виплати (move_line_id — ключ неповторності): один — «Виплачено»; кілька — «На перевірці»;
    після рішення є рядки агента лише з іншою сумою — до D + 3 робочих днів банків Польщі примітка й пошук щоночі, пізніше — «На перевірці».
    Утримання 10 % на аналітиці утримання — лише примітка: знайдено / не знайдено;
(3) нарахування закритої виплати: виплата покрила рядок рішення («У буфер» 0) — «Виплачено», інакше примітка «частково, залишок у буфері».
Рахунок оплачено частково — статус не міняємо, у примітку «оплачено частково N з M».
Що змінилось проти ручної збірки: імена ключів — поля модуля; агент виплати — workset.agent, тому партнер рядка WS порівнюється з контактом
агента (P['agent_partner']); аналітика утримання — P['wh_analytic'] (параметр), не з коду.
Операції плану — {op: create / write / post, model, id | ref, vals, before}; виконує їх models/agent_run.py (apply_ops).
"""
try:
    from . import calc as C
except ImportError:  # тест без Odoo: файл завантажено поза пакетом, тека models — у sys.path
    import calc as C

PO = 'workset.agent.payout'
AC = 'workset.agent.accrual'
DEC = 'workset.agent.decision'
DL = 'workset.agent.decision.line'

PAID_OPEN = ('prep', 'docs', 'bill', 'paid_wait')  # виплати, які ніч може закрити сама («На перевірці» — лише людина)
PAID_WS_DAYS = 3  # стільки робочих днів банків Польщі після D ніч чекає точний рядок WS, поки поруч рядок з іншою сумою
PAID_GUARD = {'bill_gone': 'рахунок постачальника видалено', 'bill_cancel': 'рахунок постачальника скасовано або сторновано',
              'bill_amount': 'сума рахунку постачальника не дорівнює «До переказу»', 'ws_many': 'у журналі WS кілька рядків на цю суму — який саме, вирішує людина',
              'ws_amount': 'у журналі WS після рішення є переказ агенту з іншою сумою — перевірте'}

m2o = C.m2o
sval = C.sval
num = C.num
fmt_date = C.fmt_date


def fmt_eur(x):
    return ('%.2f' % num(x)).replace('.', ',') + ' €'


def money(x):
    return round(num(x) + 0.0, 2)


def uniq_ids(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def m2o_name(v):
    return sval(v[1]) if isinstance(v, (list, tuple)) and len(v) > 1 else ''


def pay_amounts(pay, wh_pct, tax_pct):
    # [утримання, податок, до переказу] від «Сплатити зараз» (брутто)
    wh = money(pay * num(wh_pct) / 100.0)
    tax = money(pay * num(tax_pct) / 100.0)
    return [wh, tax, money(pay - wh - tax)]


def prev_pay_day(D):
    # попередній день виплати строго раніше D
    y = int(D[0:4])
    m = int(D[5:7])
    best = ''
    for k in range(-2, 1):
        for dd in sorted(C.PAY_DAYS):
            x = C.shift_bank(C.ymd(y, m + k, dd))
            if best < x < D:
                best = x
    return best


def pick_journal(journals, code, company):
    # журнал рахунку постачальника фірми official → [id, примітка]. За кодом; коду немає — єдиний журнал закупівель фірми; жодного або два й
    # більше без коду — 0 і примітка (рядок рішення «На перевірці» з причиною; перший-ліпший не беремо)
    hit = [j for j in journals if sval(j.get('code')).strip().upper() == sval(code).strip().upper() and code]
    if len(hit) == 1:
        return [hit[0]['id'], '']
    if len(hit) > 1:
        return [0, 'журналів закупок з кодом %s: %d (%s)' % (code, len(hit), ', '.join(['#%d' % j['id'] for j in hit]))]
    if len(journals) == 1:
        return [journals[0]['id'], 'журналу з кодом %s немає — узято єдиний журнал закупок #%d (%s)' % (code, journals[0]['id'], sval(journals[0].get('code')) or '—')]
    if not journals:
        return [0, 'журналу закупок у фірмі #%d немає' % int(company or 0)]
    return [0, 'журналу з кодом %s немає, а журналів закупок %d (%s) — який брати, вирішує бухгалтерія (параметр bill_journal_code)'
            % (code, len(journals), ', '.join(['#%d %s' % (j['id'], sval(j.get('code')) or '—') for j in journals]))]


# ── план ──
def add_op(res, op, model, rid, vals, refs=None, extra=None):
    o = {'op': op, 'model': model, 'vals': vals}
    if op == 'create':
        o['ref'] = rid
    else:
        o['id'] = rid
    if refs:
        o['refs'] = refs
    if extra:
        for k in extra:
            o[k] = extra[k]
    res['ops'].append(o)
    return o


def paid_ctx(P):
    X = {'aml_by': {}, 'move_by': {}, 'bill_by': {}, 'line_by': {}, 'dec_by': {}, 'acc_of': {}}
    for l in P.get('aml', []):
        X['aml_by'][l['id']] = l
    for m in P.get('moves', []):
        X['move_by'][m['id']] = m
    for b in P.get('bills', []):
        X['bill_by'][b['id']] = b
    for x in P.get('lines', []):
        X['line_by'][x['id']] = x
    for d in P.get('decisions', []):
        X['dec_by'][d['id']] = d
    for a in sorted(P.get('accruals', []), key=C.acc_order_key):
        k = m2o(a.get('payout_id'))
        X['acc_of'][k] = X['acc_of'].get(k, []) + [dict(a)]
    X['partials'] = P.get('partials', [])
    X['aml'] = P.get('aml', [])
    return X


def counterparts(X, frontier, seen):
    # рядки, звірені з frontier (account.partial.reconcile в обидва боки), яких ще не бачили
    out = []
    for p in X['partials']:
        a = m2o(p.get('debit_move_id'))
        b = m2o(p.get('credit_move_id'))
        for xy in [[a, b], [b, a]]:
            if xy[0] in frontier and xy[1] and xy[1] not in seen and xy[1] not in out:
                out.append(xy[1])
    return out


def settle_of(bill, X):
    # → [дата, рядок, 'statement' | 'payment'] — чим закрито рахунок: рядок виписки (через платіж або напряму), інакше — рядок платежу; звірки немає — None
    start = [l['id'] for l in X['aml'] if m2o(l.get('move_id')) == bill and l.get('account_type') == 'liability_payable']
    seen = list(start)
    frontier = start
    fallback = None
    for depth in range(2):
        nxt = counterparts(X, frontier, seen)
        seen = seen + nxt
        found = []
        for lid in nxt:
            l = X['aml_by'].get(lid) or {}
            mv = X['move_by'].get(m2o(l.get('move_id'))) or {}
            day = sval(mv.get('date')) or sval(l.get('date'))
            if m2o(mv.get('statement_line_id')):
                found.append([day, lid, 'statement'])
            elif depth == 0 and (not fallback or day > fallback[0]):
                fallback = [day, lid, 'payment']
        if found:
            return sorted(found)[len(found) - 1]
        mids = uniq_ids([m2o((X['aml_by'].get(lid) or {}).get('move_id')) for lid in nxt])
        frontier = [l['id'] for l in X['aml'] if m2o(l.get('move_id')) in mids and l['id'] not in seen]
        seen = seen + frontier
    return fallback


def since_of(p, X):
    # день, з якого шукаємо переказ: день «Затвердити» рішення виплати, інакше день підготовки, інакше день створення виплати
    d = X['dec_by'].get(m2o(p.get('decision_id'))) or {}
    return (sval(d.get('approved_at')) or sval(d.get('prepared_at')) or sval(p.get('create_date')) or '0000-00-00')[0:10]


def bank_add(day, n):
    # n-й робочий день банків Польщі після day
    k = 0
    while n > 0 and k < 60:
        day = C.add_days(day, 1)
        k += 1
        if C.bank_day(day):
            n -= 1
    return day


def note_add(old, tail):
    old = sval(old)
    if tail in old:
        return old
    return ('%s\n%s' % (old, tail)).strip()


def paid_review(res, p, code, extra):
    text = ('%s: %s' % (PAID_GUARD.get(code, code), extra))[0:250] if extra else PAID_GUARD.get(code, code)
    add_op(res, 'write', PO, p['id'], {'state': 'review', 'guard': code, 'guard_note': text},
           None, {'before': {'state': p.get('state'), 'guard': p.get('guard') or False, 'guard_note': p.get('guard_note') or False}})
    add_op(res, 'post', PO, p['id'], {'body': 'На перевірці (ніч): %s' % text})
    res['summary']['review'] += 1
    res['notes'].append('виплата #%d: %s' % (p['id'], text))


def mark_paid(res, p, X, day, line, via, tail):
    # «Виплачено»: дата, рядок оплати, звідки; нарахування — «Виплачено», якщо виплата покрила рядок рішення (у буфер 0), інакше примітка
    note = note_add(p.get('note'), 'Виплачено %s: %s' % (fmt_date(day), tail))
    vals = {'state': 'paid', 'paid_date': day, 'move_line_id': line or False, 'paid_via': via, 'paid_amount': money(p.get('net')), 'note': note}
    before = {}
    for f in vals:
        before[f] = p.get(f, False)
    add_op(res, 'write', PO, p['id'], vals, None, {'before': before})
    add_op(res, 'post', PO, p['id'], {'body': 'Виплачено (ніч): %s, %s. %s' % (fmt_date(day), fmt_eur(p.get('net')), tail)})
    rest = num((X['line_by'].get(m2o(p.get('pay_line_id'))) or {}).get('buffer_out'))
    for a in X['acc_of'].get(p['id'], []):
        if a.get('state') not in ('due', 'in_bill', 'decided'):
            continue
        if rest > 0.005:
            t = 'частково, залишок у буфері %.2f €' % rest
            n = note_add(a.get('note'), t)
            if n != sval(a.get('note')):
                add_op(res, 'write', AC, a['id'], {'note': n}, None, {'before': {'note': a.get('note') or False}})
        else:
            add_op(res, 'write', AC, a['id'], {'state': 'paid'}, None, {'before': {'state': a.get('state')}})
    res['summary']['paid'] += 1


def paid_plan(P, today):
    # P: payouts, used_lines, decisions, lines, accruals, bills, aml, partials, moves, ws_lines, wh_lines, agent_partner {агент: контакт},
    # wh_analytic (id аналітики утримання). → {ops, notes, summary}
    res = {'ops': [], 'notes': [], 'summary': {'paid': 0, 'wait': 0, 'review': 0, 'partial': 0, 'odd': 0}}
    X = paid_ctx(P)
    used = [x for x in P.get('used_lines', []) if x]
    ws = sorted(P.get('ws_lines', []), key=C.acc_order_key)
    partner_of = {}
    for k in (P.get('agent_partner') or {}):
        partner_of[int(k)] = P['agent_partner'][k]
    wh_an = int(P.get('wh_analytic') or 0)
    for p in sorted(P.get('payouts', []), key=C.acc_order_key):
        st = p.get('state')
        if st not in PAID_OPEN:
            continue
        path = sval(p.get('pay_path'))
        net = num(p.get('net'))
        if path == 'official_1':
            mv = m2o(p.get('move_id'))
            if not mv:
                if st in ('bill', 'paid_wait') and num(p.get('gross')) > 0.005:
                    paid_review(res, p, 'bill_gone', '')
                continue
            bill = X['bill_by'].get(mv)
            if not bill:
                paid_review(res, p, 'bill_gone', 'рахунок #%d' % mv)
            elif bill.get('state') == 'cancel' or bill.get('payment_state') == 'reversed':
                paid_review(res, p, 'bill_cancel', 'рахунок #%d (%s, %s)' % (mv, sval(bill.get('state')), sval(bill.get('payment_state'))))
            elif abs(num(bill.get('amount_total')) - net) > 0.01:
                paid_review(res, p, 'bill_amount', 'рахунок #%d — %s, «До переказу» %s' % (mv, fmt_eur(bill.get('amount_total')), fmt_eur(net)))
            elif bill.get('payment_state') == 'paid':
                s = settle_of(mv, X)
                if s:
                    mark_paid(res, p, X, s[0], s[1], 'statement', 'рахунок #%d закрито %s (рядок #%d)' % (mv, 'випискою' if s[2] == 'statement' else 'платежем без виписки', s[1]))
                else:
                    mark_paid(res, p, X, today, False, 'statement', 'рахунок #%d оплачено, сверку не знайдено — дата ночі' % mv)
            elif bill.get('payment_state') == 'partial':
                total = num(bill.get('amount_total'))
                t = 'рахунок #%d оплачено частково: %s з %s' % (mv, fmt_eur(total - num(bill.get('amount_residual'))), fmt_eur(total))
                n = note_add(p.get('note'), t)
                if n != sval(p.get('note')):
                    add_op(res, 'write', PO, p['id'], {'note': n}, None, {'before': {'note': p.get('note') or False}})
                    add_op(res, 'post', PO, p['id'], {'body': '%s (ніч).' % t})
                    res['notes'].append('виплата #%d: %s' % (p['id'], t))
                res['summary']['partial'] += 1
            elif bill.get('payment_state') == 'in_payment' and st != 'paid_wait':
                add_op(res, 'write', PO, p['id'], {'state': 'paid_wait'}, None, {'before': {'state': st}})
                add_op(res, 'post', PO, p['id'], {'body': 'Сплачено, чекаємо виписку (ніч): рахунок #%d оплачено, виписки ще немає.' % mv})
                res['summary']['wait'] += 1
        elif path == 'personal_36' and st in ('prep', 'docs') and net > 0.005:
            agent = m2o(p.get('agent_id'))
            contact = partner_of.get(agent, agent)
            since = since_of(p, X)
            mine = [l for l in ws if m2o(l.get('partner_id')) == contact and sval(l.get('date')) >= since and l['id'] not in used]
            exact = [l for l in mine if abs(-num(l.get('balance')) - net) <= 0.01]
            if len(exact) == 1:
                l = exact[0]
                used.append(l['id'])
                tail = 'журнал %s, рядок #%d' % (m2o_name(l.get('journal_id')) or '#%d' % m2o(l.get('journal_id')), l['id'])
                wh = num(p.get('wh10'))
                if wh > 0.005:
                    got = [w for w in P.get('wh_lines', []) if m2o(w.get('partner_id')) == contact and sval(w.get('date')) >= since and abs(abs(num(w.get('balance'))) - wh) <= 0.01]
                    if got:
                        tail += '; утримання %s на #%d — рядок #%d' % (fmt_eur(wh), wh_an, got[0]['id'])
                    else:
                        tail += '; утримання %s на #%d не знайдено — перевірте' % (fmt_eur(wh), wh_an)
                        res['notes'].append('виплата #%d: утримання %s на #%d не знайдено' % (p['id'], fmt_eur(wh), wh_an))
                mark_paid(res, p, X, sval(l.get('date')), l['id'], 'ws', tail)
            elif len(exact) > 1:
                paid_review(res, p, 'ws_many', 'рядки %s' % ', '.join(['#%d' % l['id'] for l in exact]))
            else:
                # рядок на суму ІНШОЇ відкритої виплати того ж агента — її, а не «сума не сходиться»
                nets = [num(q.get('net')) for q in P.get('payouts', []) if q['id'] != p['id'] and m2o(q.get('agent_id')) == agent and q.get('state') in ('prep', 'docs')
                        and sval(q.get('pay_path')) == 'personal_36']
                odd = [l for l in mine if not [x for x in nets if abs(-num(l.get('balance')) - x) <= 0.01]]
                if odd:
                    txt = ', '.join(['#%d %s' % (l['id'], fmt_eur(-num(l.get('balance')))) for l in odd[0:3]]) + ', а «До переказу» ' + fmt_eur(net)
                    last = bank_add(sval(p.get('pay_day')) or since, PAID_WS_DAYS)
                    if today > last:
                        paid_review(res, p, 'ws_amount', txt)
                    else:
                        t = 'у журналі WS переказ агенту з іншою сумою: %s — точного рядка ще немає, ніч шукає до %s' % (txt, fmt_date(last))
                        n = note_add(p.get('note'), t)
                        if n != sval(p.get('note')):
                            add_op(res, 'write', PO, p['id'], {'note': n}, None, {'before': {'note': p.get('note') or False}})
                            add_op(res, 'post', PO, p['id'], {'body': '%s (ніч).' % t})
                        res['notes'].append('виплата #%d: %s' % (p['id'], t))
                        res['summary']['odd'] += 1
    return res


def pay_summary(plan, title='рішення'):
    s = plan['summary']
    parts = []
    for kl in [['created', 'нових рішень'], ['auto', 'затверджено автоматично'], ['asked', 'тривог власнику'], ['prepared', 'підготовлено'], ['blocked', 'на перевірці'],
               ['skipped', 'пропущено'], ['payouts', 'виплат'], ['bills', 'рахунків'], ['acts', 'списків офісу'], ['owner_acts', 'активностей власнику'],
               ['moved', 'перенесено нарахувань'], ['lines_new', 'рядків нових'], ['lines_changed', 'оновлено'], ['lines_gone', 'прибрано'],
               ['paid', 'виплачено'], ['wait', 'чекаємо виписку'], ['review', 'виплат на перевірці'], ['partial', 'рахунок оплачено частково'],
               ['odd', 'у WS інша сума — чекаємо']]:
        if s.get(kl[0]):
            parts.append('%s %d' % (kl[1], s[kl[0]]))
    return '%s: %s' % (title, ', '.join(parts) if parts else 'без змін')


def summary_text(res, dry, errs):
    s = res['summary']
    parts = ['домовленостей «за годину» %d' % s['deals'], 'оплачених рахунків %d' % s['moves'], 'нових за годину %d' % s['created_hour'],
             'разових оброблено %d' % s['created_oneoff'], 'змін %d' % s['writes'], 'сторно %d' % s['stornos'], 'на перевірці %d' % s['review']]
    if res['dup_keys']:
        parts.append('ДУБЛІ КЛЮЧІВ: %s' % ', '.join(res['dup_keys'][0:5]))
    text = '%s%s' % ('(лише розрахунок, без запису) ' if dry else '', '; '.join(parts))
    if res['notes']:
        text = '%s. Примітки: %s' % (text, '; '.join(res['notes'][0:5]))
    if errs:
        text = '%s. Не записано: %s' % (text, '; '.join(errs))
    return text[0:2000]


def run_result(errs, groups):
    # «OK» без помилок; усі точки впали — «Помилка»; частина — «Частково»
    return 'OK' if not errs else ('Помилка' if len(errs) >= groups else 'Частково')


def storno_button_vals(rec, linked, today, user_name):
    # кнопка «Сторно» картки нарахування: rec — нарахування {id, kind, state, key, amount, hours, rate, …}, linked — його чинні сторно
    # [{key, amount, hours}] → (vals сторно, pre) або CalcError. Ще не в оплаті — обидва «Скасовано»; уже «У рахунку» / «Виплачено» —
    # сторно «Очікується» (день і статус поставить ніч)
    if rec.get('kind') == 'storno':
        raise C.CalcError('Сторно не сторнують. Помилку в сторно виправляють новим нарахуванням (разовою).')
    if rec.get('state') == 'cancelled':
        raise C.CalcError('Нарахування «Скасовано» — сторнувати нічого.')
    if [x for x in linked if C.REFUND_TAG not in sval(x.get('key'))]:
        raise C.CalcError('Це нарахування вже сторновано.')
    left_amount = num(rec.get('amount')) + sum([num(x.get('amount')) for x in linked])
    left_hours = num(rec.get('hours')) + sum([num(x.get('hours')) for x in linked])
    pre = rec.get('state') in C.PRE
    vals = {'kind': 'storno', 'hours': -left_hours, 'amount': -left_amount, 'earned_date': today, 'storno_id': rec['id'],
            'key': 'storno:' + (sval(rec.get('key')) or ('a%d' % rec['id'])), 'state': 'cancelled' if pre else 'expected',
            'note': 'Сторно вручну: %s, %s. Причина: ' % (user_name, today)}
    return vals, pre


def approve_check(lines):
    # «Затвердити»: lines — рядки рішення [{state, pay_now, available, agent}] у «Чернетка» / «На перевірці» → [помилки, сума, рядків, на перевірці]
    bad = []
    pay = 0.0
    keep = 0
    left = 0
    for line in lines:
        p = num(line.get('pay_now'))
        if line.get('state') == 'review':
            left += 1
            continue
        top = max(0.0, num(line.get('available')))
        if p < -0.005 or p > top + 0.005:
            bad.append('%s: «Сплатити зараз» %.2f €, а можна від 0 до %.2f €' % (line.get('agent') or '?', p, top))
        pay += p
        keep += 1
    return [bad, pay, keep, left]


def stop_last_day(pay_day, stop_days=1):
    # «Зупинити» — до D − stop_days включно (список офісу йде в день D)
    if not pay_day:
        return ''
    return C.add_days(pay_day, -stop_days)


def manual_paid(net, gross, amount, many_total, count):
    # вікно «Позначити виплаченим вручну» для однієї виплати: → [сума, частково, недоплата брутто] або CalcError
    amt = amount if count == 1 else net
    if amt < 0.01 or amt > net + 0.005:
        raise C.CalcError('Сума — від 0,01 до «До переказу» %.2f €. Нічого не позначено.' % net)
    part = amt < net - 0.005
    short = round((net - amt) * gross / net, 2) if part and net > 0.005 else 0.0
    return [round(amt, 2), part, short]


# ══ Крок рішень (3): перенос payout.py ручної збірки — pay_plan(P, today, now) ══
# До кожного дня виплати D — рішення власника. Рядок — агент × розділ × «Шлях виплати» × фірма: нараховано («До виплати» з днем ≤ D) + буфер
# з минулих рішень → пропозиція штабу (довідково) → «Сплатити зараз» (вводить власник, брутто) → решта — у буфер наступного рішення. Без
# «Затвердити» виплата не готується. Посередники й домовленості «Лише звірка» — рядок «Звірка», до виплати не йдуть; «Фонд працівників» — у
# рішення не йде взагалі. pay_plan — що зробити цієї ночі (по порядку): (1) чернетка з минулим D — «Пропущено», нарахування — на наступний
# день; (2) «До виплати» без рішення з минулим днем — на наступний день; (3) «Затверджено» — знову дублі, перетини, шлях ↔ фірма, суми:
# знайшлось — «На перевірці»; чисто — підготовка: виплата, official — чернетка рахунку постачальника, personal — запис «з особистого
# рахунку», custom — «оформити окремо»; нарахування рядка — «Закрито рішенням»; список офісу — активність «Бот «Офіс»»; (4) чернетки й «На
# перевірці» — рядки перезбираються («Сплатити зараз» власника не чіпаємо); (5) ніч D−3 — чернетка рішення на розділ.
# Що змінилось проти ручної збірки: імена — поля модуля; факти (фірми, проценти, рахунки, журнал, аналітика, учётки) — P['cfg'] з
# параметрів; агент — workset.agent: банки й рахунок постачальника — за контактом агента (P['agent_partner']); імена агентів (anames) окремо
# від імен контактів (names) — id різних моделей; активності штабу — операцією 'activity' (поля штабу ставить models/office_bridge.py).
DL_OPEN = ('draft', 'review')  # рядки, у яких власник ще вводить «Сплатити зараз»
UNTRUSTED = 'IBAN не довірений — перевірити'
SECTION_ORDER = ('people', 'client')
ZV_TOL = 1.0
ZV_TOL_PCT = 5.0
ADVICE_ORDER = {'перевірити': 0, 'сплатити': 1, 'притримати': 2}
ADVICE_KEY = {'сплатити': 'pay', 'перевірити': 'check', 'притримати': 'hold'}
KEY_PREFIX = 'agents-pay-'          # ключ активності списку офісу
OWNER_KEY_PREFIX = 'agents-owner-'  # ключ активності власнику
TEXT_LIMIT = 1400                   # межа тексту в бот «Офіс»
OFFICE_BUTTONS = '✅ Переказала / ✍️ Напишу'
OWNER_BUTTONS = '⏹ Зупинити'
STOP_DAYS = 1


def payouts_menu(section):
    return '%s → Виплати' % C.SECTION_LABELS.get(section or 'people', C.SECTION_LABELS['people'])


def lead_digits(s):
    out = ''
    for ch in s:
        if '0' <= ch <= '9':
            out = out + ch
        else:
            break
    return out


def inv_keys(text):
    # номери рахунків замовників у тексті: «PX/2026/138» → «PX/2026/00138»; чеські «20260195», «202600092» (зайвий нуль) → «20260195» / «20260092»
    s = sval(text)
    out = []
    parts = s.replace(' / ', '/').replace(' /', '/').replace('/ ', '/').split('/')
    for i in range(len(parts) - 1):
        a = parts[i]
        j = len(a)
        while j > 0 and 'A' <= a[j - 1] <= 'Z':
            j -= 1
        if not (2 <= len(a) - j <= 4) or (j > 0 and '0' <= a[j - 1] <= '9'):
            continue
        y = lead_digits(parts[i + 1])
        nm = ''
        if len(y) == 4 and parts[i + 1] == y and i + 2 < len(parts):
            nm = lead_digits(parts[i + 2])
        elif 5 <= len(y) <= 9:
            nm = y[4:]
            y = y[0:4]
        if len(y) == 4 and 1 <= len(nm) <= 5:
            k = '%s/%s/%s' % (a[j:], y, ('00000' + nm)[-5:])
            if k not in out:
                out.append(k)
    run = ''
    before = ''
    prev = ''
    for ch in s + ' ':
        if '0' <= ch <= '9':
            if not run:
                before = prev
            run = run + ch
        else:
            if run and before != '/' and run[0:2] == '20':
                k = run if len(run) == 8 else (run[0:4] + run[5:9] if len(run) == 9 and run[4] == '0' else '')
                if k and k not in out:
                    out.append(k)
            run = ''
        prev = ch
    return out


def m2m_ids(v):
    if isinstance(v, (list, tuple)) and len(v) == 1 and isinstance(v[0], (list, tuple)) and len(v[0]) == 3 and v[0][0] == 6:
        return sorted(C.ids_of(v[0][2]))
    return sorted(C.ids_of(v))


def is_m2m_cmd(v):
    return isinstance(v, (list, tuple)) and len(v) == 1 and isinstance(v[0], (list, tuple)) and len(v[0]) == 3 and v[0][0] == 6


def dup_keys(accruals):
    seen = {}
    for a in accruals:
        k = sval(a.get('key'))
        if k:
            seen[k] = seen.get(k, 0) + 1
    return sorted([k for k in seen if seen[k] > 1])


def deal_overlaps(deals, members, with_acc, closed_at):
    # [[домовленість, домовленість, контакт, з, по]]: одна людина у двох домовленостях «кандидати» на одну дату
    live = {}
    for d in deals:
        if (d.get('state') in C.LIVE or d['id'] in with_acc) and d.get('kind') in ('hour', 'v17'):
            live[d['id']] = d
    spans = []
    for mm in members:
        d = live.get(m2o(mm.get('deal_id')))
        if not d:
            continue
        end = sval(d.get('date_to')) or (sval(closed_at.get(d['id'], '')) if d.get('state') not in C.LIVE else '')
        froms = sorted([x for x in [sval(d.get('date_from')), sval(mm.get('date_from'))] if x])
        tos = sorted([x for x in [end, sval(mm.get('date_to'))] if x])
        fr = froms[len(froms) - 1] if froms else '0000-00-00'
        to = tos[0] if tos else '9999-99-99'
        if fr <= to:
            spans.append([m2o(mm.get('partner_id')), d['id'], fr, to])
    out = []
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a = spans[i]
            b = spans[j]
            if a[0] != b[0] or a[1] == b[1] or a[2] > b[3] or b[2] > a[3]:
                continue
            out.append([a[1], b[1], a[0], max(a[2], b[2]), min(a[3], b[3])])
    return out


def line_key(agent, section, path, company, zv):
    return '%d:%s:%s:%d%s' % (agent, section, path or '-', company or 0, ':z' if zv else '')


def fund_acc(a, X):
    return bool(a.get('fund')) or C.is_fund(C.deal_of(a, X['deals']))


def acc_place(a, X):
    # [шлях, фірма, домовленість] нарахування: шлях — з домовленості, фірма — нарахування, інакше за домовленістю / шляхом
    d = C.deal_of(a, X['deals'])
    path = sval(d.get('pay_path')) if d else ''
    comp = m2o(a.get('company_id')) or (C.company_of(d, X['cfg']) if d else 0) or 0
    return [path or '-', comp, d]


def wh_of(deal, path, cfg):
    # утримання за домовленістю, %: «Особисто» — personal_pct, якщо в домовленості не задано інше; решта — як у ній
    p = num(deal.get('withhold_pct')) if deal else 0.0
    if path == 'personal_36' and p <= 0:
        return num(cfg['personal_pct'])
    return p


def tax_of(path, profile, D, cfg):
    # [податок %, чому]: лише «Офіційно»: чинна довідка на день виплати або податкова країна без утримання — 0 %, інакше tax_pct (п. 7.2)
    if path != 'official_1':
        return [0.0, '']
    cert = sval((profile or {}).get('cert_until'))
    if cert and cert >= D:
        return [0.0, 'довідка про резидентство до %s' % fmt_date(cert)]
    if sval((profile or {}).get('country_code')) in cfg['tax_free_countries']:
        return [0.0, 'податкова країна — Польща: податок за польськими правилами, перевірити бухгалтерії']
    why = 'довідка спливла %s' % fmt_date(cert) if cert else ('профілю податків немає' if not profile else 'довідки немає')
    return [num(cfg['tax_pct']), '%s — %s %% за п. 7.2' % (why, C.fmt_h(num(cfg['tax_pct'])))]


def cust_summary(invoices, today):
    out = {}
    for inv in invoices:
        c = m2o(inv.get('commercial_partner_id'))
        if not c:
            continue
        x = out.get(c) or {'unpaid': 0.0, 'unpaid_n': 0, 'overdue': 0.0, 'overdue_n': 0, 'overdue_ids': []}
        r = num(inv.get('amount_residual'))
        x['unpaid'] += r
        x['unpaid_n'] += 1
        due = sval(inv.get('invoice_date_due'))
        if due and due < today:
            x['overdue'] += r
            x['overdue_n'] += 1
            if inv.get('id') and inv['id'] not in x['overdue_ids']:
                x['overdue_ids'].append(inv['id'])
        out[c] = x
    return out


def line_order_key(x):
    return x['key']


def month_end(day):
    return C.add_days(C.ymd(int(day[0:4]), int(day[5:7]) + 1, 1), -1)


def month_name(day):
    return '%s.%s' % (day[5:7], day[0:4])


def months_of(fr, to):
    out = []
    m = month_end(fr or to)
    while m <= month_end(to):
        out.append(m)
        m = month_end(C.add_days(m, 1))
    return out


def work_month(x, due, X, D):
    # → [останній день місяця роботи, примітка]: за датами роботи ДОДАТНИХ нарахувань рядка (і рядка, з якого прийшов буфер); без дат — місяць
    # «зароблено» з приміткою; немає й його — місяць дня виплати
    accs = list(due)
    src = m2o(x.get('buffer_from_id'))
    if src and abs(num(x.get('buffer_in'))) > 0.005:
        accs = accs + [a for a in X['acc'] if m2o(a.get('pay_line_id')) == src and a.get('state') != 'cancelled']
    pos = [a for a in accs if num(a.get('amount')) > 0.005]
    dated = [a for a in pos if sval(a.get('work_to'))]
    undated = [a for a in pos if not sval(a.get('work_to')) and sval(a.get('earned_date'))]
    neg = len([a for a in accs if num(a.get('amount')) < -0.005])
    months = []
    notes = []
    if dated:
        for a in dated:
            for m in months_of(sval(a.get('work_from')), sval(a.get('work_to'))):
                if m not in months:
                    months.append(m)
        if len(pos) > len(dated):
            notes.append('%d нарахувань без дат роботи місяць не зсувають' % (len(pos) - len(dated)))
    else:
        for a in undated:
            m = month_end(sval(a.get('earned_date')))
            if m not in months:
                months.append(m)
        if months:
            notes.append('без дат роботи в %d нарахуваннях — за місяцем «зароблено»' % len(undated))
    if neg and months:
        notes.append('сторно (%d) місяць не зсуває' % neg)
    if not months:
        return [month_end(D), 'місяць роботи невідомий (дат роботи й «зароблено» немає) — місяць дня виплати']
    months = sorted(months)
    if len(months) > 1:
        notes.insert(0, 'місяці роботи: %s' % ', '.join([month_name(m) for m in months]))
    return [months[len(months) - 1], '; '.join(notes)]


def owner_edited(x):
    # правку власника видно за значенням: прапор owner_edit або «Сплатити зараз» ≠ night_pay (write_date рядка не беремо)
    if x.get('owner_edit'):
        return True
    return abs(num(x.get('pay_now')) - num(x.get('night_pay'))) > 0.005


def buf_order_key(e):
    return '%s:%012d' % (e['day'], e['id'])


def zv_period(D, prevD):
    return [C.add_days(prevD, 1 - C.CUTOFF) if prevD else '', C.add_days(D, -C.CUTOFF)]


def zv_in(a, D, prevD):
    earned = sval(a.get('earned_date'))
    if earned:
        p = zv_period(D, prevD)
        return (not p[0] or earned >= p[0]) and earned <= p[1]
    pdte = sval(a.get('pay_date'))
    return bool(pdte) and pdte <= D and (not prevD or pdte > prevD)


def new_line(k, agent, sec, path, company, zv, D, prevD):
    return {'key': k, 'agent': agent, 'section': sec, 'path': path, 'company': company, 'zv': zv, 'accrued': 0.0, 'review': 0.0, 'acc': [], 'deals': [],
            'moves': [], 'buffer': 0.0, 'buffer_from': 0, 'zacc': [], 'period': zv_period(D, prevD)}


def build_lines(X, D, prevD, own_lines, section=''):
    # рядки рішення дня D з нарахувань у пам'яті; own_lines — id рядків цього рішення; section — лише цей розділ
    out = {}
    for a in X['acc']:
        pdte = sval(a.get('pay_date'))
        link = m2o(a.get('pay_line_id'))
        if not pdte or (link and link not in own_lines):
            continue
        if section and a.get('section') != section:
            continue
        if fund_acc(a, X):
            continue
        st = a.get('state')
        sec = a.get('section')
        if st == 'due' and pdte <= D and (sec == 'people' or C.CLIENT_PAYS):
            kind = 'pay'
        elif st == 'expected' and zv_in(a, D, prevD):
            kind = 'zv'
        elif st == 'review' and pdte <= D:
            kind = 'rev'
        else:
            continue
        place = acc_place(a, X)
        agent = m2o(a.get('agent_id'))
        k = line_key(agent, sec, place[0], place[1], kind == 'zv')
        L = out.get(k)
        if not L:
            L = new_line(k, agent, sec, place[0], place[1], kind == 'zv', D, prevD)
            out[k] = L
        if kind == 'rev':
            L['review'] += num(a.get('amount'))
            continue
        L['accrued'] += num(a.get('amount'))
        if kind == 'pay':
            L['acc'].append(a['id'])
        if kind == 'zv':
            L['zacc'].append(a['id'])
        if place[2] and place[2] not in L['deals']:
            L['deals'].append(place[2])
        mv = m2o(a.get('move_id'))
        if mv and [mv, m2o(a.get('customer_id'))] not in L['moves']:
            L['moves'].append([mv, m2o(a.get('customer_id'))])
    # буфер — те, що підготовлені рядки того ж ключа з раніших рішень залишили й ніхто ще не взяв
    for k in sorted(X['buffers']):
        got = [e for e in X['buffers'][k] if e['day'] < D]
        if not got:
            continue
        last = sorted(got, key=buf_order_key)[len(got) - 1]
        amount = money(sum([e['out'] for e in got]) - sum([e['in'] for e in got]))
        if abs(amount) <= 0.005 or (section and last['section'] != section):
            continue
        L = out.get(k)
        if not L:
            L = new_line(k, last['agent'], last['section'], last['path'], last['company'], False, D, prevD)
            out[k] = L
        L['buffer'] = amount
        L['buffer_from'] = last['id']
    return sorted([out[k] for k in out], key=line_order_key)


def line_codes(L, X):
    codes = []
    for aid in L['acc']:
        a = X['acc_by'].get(aid) or {}
        if sval(a.get('key')) in X['dup']:
            codes.append(['dup_key', 'ключ «%s» нарахування #%d повторюється' % (sval(a.get('key')), aid)])
        pdl = m2o(a.get('people_deal_id'))
        if a.get('section') == 'people' and pdl in X['over_deals']:
            codes.append(['overlap', X['over_deals'][pdl]])
    for d in L['deals']:
        pb = C.path_bad(d, X['cfg'])
        if pb:
            codes.append(['path_company', 'домовленість #%d: %s' % (d['id'], pb)])
    out = []
    for c in codes:
        if c not in out:
            out.append(c)
    return out


def line_vals(L, X, D):
    cfg = X['cfg']
    deal = L['deals'][0] if L['deals'] else None
    prof = X['profiles'].get(L['agent'])
    wh = wh_of(deal, L['path'], cfg) if not L['zv'] else 0.0
    tx = tax_of(L['path'], prof, D, cfg) if not L['zv'] else [0.0, '']
    pcts = []
    for d in L['deals']:
        p = wh_of(d, L['path'], cfg)
        if p not in pcts:
            pcts.append(p)
    notes = []
    if wh > 0:
        notes.append('утримання %s %%%s' % (C.fmt_h(wh), ' (особисто · конверт)' if L['path'] == 'personal_36' and deal and num(deal.get('withhold_pct')) <= 0 else ''))
    if len(pcts) > 1:
        notes.append('у домовленостях різні утримання %s — взято %s %%' % (', '.join([C.fmt_h(p) + ' %' for p in pcts]), C.fmt_h(wh)))
    if tx[1]:
        notes.append(tx[1])
    avail = money(L['buffer'] + L['accrued'])
    cust = X['cust_of'].get(L['agent'], [])
    paid_by = {}
    for mv in L['moves']:
        paid_by[mv[1]] = paid_by.get(mv[1], 0.0) + num(X['move_total'].get(mv[0]))
    unpaid = 0.0
    overdue = 0.0
    paid = 0.0
    rows = []
    for c in cust:
        s = X['cust'].get(c) or {'unpaid': 0.0, 'unpaid_n': 0, 'overdue': 0.0, 'overdue_n': 0}
        unpaid += s['unpaid']
        overdue += s['overdue']
        paid += paid_by.get(c, 0.0)
        rows.append('%s: оплачено %s · не оплачено %s (%d рах.) · прострочено %s' % (X['names'].get(c) or ('#%d' % c), fmt_eur(paid_by.get(c, 0.0)), fmt_eur(s['unpaid']),
                                                                                    s['unpaid_n'], fmt_eur(s['overdue'])))
    if L['zv']:
        prop = 0.0
        pnote = 'Звірка: Odoo рахує, не платить (етап 1)'
    else:
        prop = max(0.0, avail)
        bits = ['усе до рішення' if avail > 0.005 else 'платити нічого']
        if overdue > 0.005:
            bits.append('у замовників агента прострочено %s — вирішує власник' % fmt_eur(overdue))
        if L['review'] > 0.005:
            bits.append('ще %s на перевірці — не в сумі' % fmt_eur(L['review']))
        pnote = '; '.join(bits)
    out = {'key': L['key'], 'agent_id': L['agent'], 'section': L['section'], 'pay_path': L['path'] if L['path'] != '-' else False,
           'company_id': L['company'] or False, 'accrued': money(L['accrued']), 'review_amount': money(L['review']), 'buffer_in': money(L['buffer']),
           'buffer_from_id': L['buffer_from'] or False, 'available': avail, 'wh_pct': wh, 'tax_pct': tx[0], 'wh_note': ('; '.join(notes))[0:250] or False,
           'proposal': money(prop), 'proposal_note': pnote[0:250], 'cust_paid': money(paid), 'cust_unpaid': money(unpaid), 'cust_overdue': money(overdue),
           'cust_text': '\n'.join(rows)[0:2000] or False}
    ov = []
    for c in cust:
        for i in (X['cust'].get(c) or {}).get('overdue_ids') or []:
            if i not in ov:
                ov.append(i)
    out['overdue_move_ids'] = [[6, 0, sorted(ov)]]
    if L['zv']:
        zi = zv_info(L, X)
        for k in zi:
            out[k] = zi[k]
    return out


def period_text(p):
    if not p[0]:
        return 'по %s' % fmt_date(p[1])
    return '%s–%s' % (fmt_date(p[0])[0:5] if p[0][0:4] == p[1][0:4] else fmt_date(p[0]), fmt_date(p[1]))


def signed_eur(x):
    return ('+' if x > 0.005 else '') + fmt_eur(x)


def pct_text(diff, base):
    if abs(base) <= 0.005:
        return ''
    p = round(diff * 100.0 / base, 1)
    return ' (%s%s %%)' % ('+' if p > 0 else '', C.fmt_h(p).replace('.', ','))


def key_list(keys, by_key, X):
    out = []
    for k in keys[0:3]:
        c = by_key[k][1]
        out.append('%s · %s' % (k, X['names'].get(c) or ('замовник #%d' % c)) if c else k)
    return ', '.join(out) + (' … ще %d' % (len(keys) - 3) if len(keys) > 3 else '')


def zv_info(L, X):
    # рядок «Звірка» за період рішення → zv_period, zv_env, zv_diff, zv_note, zv_missing: розрахунок Odoo (нарахування періоду) проти рядків
    # конверта агента з номером ТОГО Ж рахунку
    accs = [X['acc_by'][i] for i in L['zacc'] if i in X['acc_by']]
    calc_ = 0.0
    hours = {}
    by_key = {}
    order = []
    nokey = 0.0
    nokey_n = 0
    for a in accs:
        amt = num(a.get('amount'))
        calc_ += amt
        r = C.fmt_h(num(a.get('rate'))).replace('.', ',')
        hours[r] = hours.get(r, 0.0) + num(a.get('hours'))
        ks = inv_keys(m2o_name(a.get('move_id')))
        if not ks:
            nokey += amt
            nokey_n += 1
            continue
        if ks[0] not in by_key:
            by_key[ks[0]] = [0.0, m2o(a.get('customer_id'))]
            order.append(ks[0])
        by_key[ks[0]][0] = by_key[ks[0]][0] + amt
    calc_ = money(calc_)
    env_id = X['env_of'].get(L['agent']) or 0
    got = {}
    env_n = 0
    for l in X['env_lines'].get(env_id) or []:
        for k in inv_keys(l.get('name')):
            if k in by_key:
                got[k] = got.get(k, 0.0) + num(l.get('amount'))
                env_n += 1
                break
    envsum = money(sum([got[k] for k in got]))
    diff = money(calc_ - envsum)
    rates = ['%s год × %s €/год' % (C.fmt_h(round(hours[r], 2)).replace('.', ','), r) for r in hours if hours[r] > 0.005]
    lines = ['Odoo: оплачені рахунки — %d, %s = %s.' % (len(order) + nokey_n, ' + '.join(rates) if rates else 'годин не вказано', fmt_eur(calc_))]
    missing = len(order) + nokey_n if not env_id else len([k for k in order if k not in got])
    if not env_id:
        lines.append('Конверт у картці агента не вказано (поле «Конверт») — звіряти нема з чим.')
    else:
        lines.append('Конверт за ті самі рахунки: %d рядк., %s.' % (env_n, fmt_eur(envsum)))
        lines.append('Збігається.' if abs(diff) <= 0.01 else 'Різниця %s%s.' % (signed_eur(diff), pct_text(diff, envsum)))
        miss = [k for k in order if k not in got]
        if miss:
            lines.append('Немає в конверті — %d рах., %s (%s): офіс вносить частку після оплати; перевірити, чи внесе.'
                         % (len(miss), fmt_eur(sum([by_key[k][0] for k in miss])), key_list(miss, by_key, X)))
        odd = [k for k in order if k in got and abs(got[k] - by_key[k][0]) > 0.01]
        if odd:
            lines.append('Інша сума в конверті — %d рах. (%s): перевірити години або ставку.'
                         % (len(odd), '; '.join(['%s: %s у конверті, %s за годинами' % (k, fmt_eur(got[k]), fmt_eur(by_key[k][0])) for k in odd[0:3]])
                            + (' … ще %d' % (len(odd) - 3) if len(odd) > 3 else '')))
    if nokey_n:
        lines.append('Без номера рахунку — %d нарах., %s: звірити вручну.' % (nokey_n, fmt_eur(nokey)))
    return {'zv_period': period_text(L['period']), 'zv_env': envsum, 'zv_diff': diff, 'zv_note': '\n'.join(lines)[0:2000], 'zv_missing': missing}


def zv_bad(calc_, diff):
    return abs(diff) > max(ZV_TOL, abs(calc_) * ZV_TOL_PCT / 100.0)


def zv_reason(zv):
    for x in zv:
        for s in sval(x.get('zv_note')).split('\n'):
            inner = s.split(' (', 1)[1].split('):')[0] if ' (' in s and '):' in s else ''
            n = lead_digits(s.split('— ', 1)[1]) if '— ' in s else ''
            if s.startswith('Немає в конверті'):
                keys = inner.replace(' · ', ' ')
                if n == '1' and keys:
                    return 'рахунок %s не в конверті' % keys
                return '%s рах. не в конверті%s' % (n or '?', (' (%s)' % keys) if keys else '')
            if s.startswith('Інша сума в конверті'):
                ks = [p.split(':')[0] for p in inner.split('; ')] if inner else []
                return 'інша сума в конверті: %s' % (', '.join(ks) if ks else 'рахунки')
            if s.startswith('Конверт у картці'):
                return 'конверт агента не вказано'
            if s.startswith('Без номера рахунку'):
                return '%s нарах. без номера рахунку' % (n or '?')
    return ''


def agent_advice(ls, X):
    # рядки одного агента → [дія, сума, чому] або None
    pay = [x for x in ls if x.get('state') != 'zvirka']
    zv = [x for x in ls if x.get('state') == 'zvirka']
    prop = money(sum([num(x.get('proposal')) for x in pay]))
    avail = money(sum([num(x.get('available')) for x in pay]))
    rev = money(sum([num(x.get('review_amount')) for x in pay]))
    guard = [sval(x.get('guard_note')) or sval(x.get('guard')) for x in pay if x.get('state') == 'review' or sval(x.get('guard'))]
    overdue = max([0.0] + [num(x.get('cust_overdue')) for x in ls])
    od_n = max([0] + [len(m2m_ids(x.get('overdue_move_ids'))) for x in ls])
    od = ('у замовників прострочено %s%s' % (fmt_eur(overdue), (' (%d рах.)' % od_n) if od_n else '')) if overdue > 0.005 else ''
    zcalc = money(sum([num(x.get('accrued')) for x in zv]))
    zenv = money(sum([num(x.get('zv_env')) for x in zv]))
    zdiff = money(sum([num(x.get('zv_diff')) for x in zv]))
    zfull = bool(zv) and 'zv_diff' in zv[0]
    zbad = zfull and zv_bad(zcalc, zdiff)
    zwhy = (zv_reason(zv) or 'різниця з конвертом %s%s' % (signed_eur(zdiff), pct_text(zdiff, zenv))) if zbad else ''
    if guard:
        a = ['перевірити', avail if avail > 0.005 else rev, 'на перевірці — %s' % guard[0]]
    elif prop > 0.005 and od:
        a = ['перевірити', prop, od]
    elif prop > 0.005:
        a = ['сплатити', prop, '']
    elif pay and avail < -0.005:
        a = ['притримати', -avail, 'до рішення мінус — у буфер наступного рішення']
    elif pay and rev > 0.005:
        a = ['перевірити', rev, 'нарахування на перевірці — до виплати не йдуть']
    elif zbad:
        a = ['перевірити', abs(zdiff), zwhy]
        zwhy = ''
    elif zv:
        a = ['притримати', zcalc, 'звірка збігається — платить офіс' if zfull else 'звірка — не платимо, платить офіс']
    else:
        return None
    extra = []
    if rev > 0.005 and a[0] != 'перевірити':
        extra.append('ще %s на перевірці' % fmt_eur(rev))
    if zwhy:
        extra.append('звірка: ' + zwhy)
    if od and od != a[2]:
        extra.append(od)
    a[2] = '; '.join([s for s in [a[2]] + extra if s])
    a[1] = money(a[1])
    return a


def line_advice(v, X):
    a = agent_advice([v], X)
    if not a:
        return {'advice': False, 'advice_text': False}
    return {'advice': ADVICE_KEY[a[0]], 'advice_text': a[2][0:250] or False}


def advice_key(o):
    return [o[0], -o[1], o[2]]


def advice_lines(lines, X):
    # «Що пропонує штаб» рішення — по агенту один рядок «Дія сума · агент · причина»; «сплатити все» без застережень — не пишемо
    by = {}
    agents = []
    for x in lines:
        if x.get('state') == 'skipped':
            continue
        ag = m2o(x.get('agent_id'))
        if ag not in by:
            by[ag] = []
            agents.append(ag)
        by[ag].append(x)
    out = []
    for ag in agents:
        a = agent_advice(by[ag], X)
        if a and (a[0] != 'сплатити' or a[2]):
            out.append([ADVICE_ORDER[a[0]], a[1], agent_name(X, ag), a])
    return ['%s %s · %s%s' % (o[3][0][0].upper() + o[3][0][1:], fmt_eur(o[3][1]), o[2], (' · ' + o[3][2]) if o[3][2] else '') for o in sorted(out, key=advice_key)]


def new_ref(res, letter):
    res['n'] += 1
    return '%s%d' % (letter, res['n'])


def same_val(a, b):
    if is_m2m_cmd(a) or is_m2m_cmd(b):
        return m2m_ids(a) == m2m_ids(b)
    if isinstance(b, float) or isinstance(a, float):
        return abs(num(a) - num(b)) <= 0.005
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return m2o(a) == m2o(b)
    return (a or False) == (b or False)


def agent_name(X, agent):
    return X['anames'].get(agent) or ('#%d' % agent)


def prep_ctx(P, today):
    # карти для плану: нарахування в пам'яті (копії), домовленості, профілі, банки (за контактом агента), імена, замовники агентів, буфери
    cfg = P.get('cfg') or {}
    deals = {}
    for d in P.get('deals_people', []):
        x = dict(d)
        x['section'] = 'people'
        deals[C.deal_key('people', d['id'])] = x
    for d in P.get('deals_client', []):
        x = dict(d)
        x['section'] = 'client'
        deals[C.deal_key('client', d['id'])] = x
    acc = [dict(a) for a in sorted(P.get('accruals', []), key=C.acc_order_key)]
    acc_by = {}
    for a in acc:
        acc_by[a['id']] = a
    with_acc = uniq_ids([m2o(a.get('people_deal_id')) for a in acc if a.get('kind') == 'hour' and a.get('state') != 'cancelled'])
    closed = {}
    for row in P.get('state_since', []):
        closed[row[0]] = sval(row[1])[0:10]
    over = {}
    for o in deal_overlaps(P.get('deals_people', []), P.get('members', []), with_acc, closed):
        txt = 'людина #%d у домовленостях #%d і #%d одночасно (%s — %s)' % (o[2], o[0], o[1], '…' if o[3] == '0000-00-00' else fmt_date(o[3]),
                                                                         '…' if o[4] == '9999-99-99' else fmt_date(o[4]))
        for did in [o[0], o[1]]:
            if did not in over:
                over[did] = txt
    profiles = {}
    for p in P.get('profiles', []):
        profiles[m2o(p.get('agent_id'))] = p
    agent_of = {}
    partner_of = {}
    for k in (P.get('agent_partner') or {}):
        agent_of[P['agent_partner'][k]] = int(k)
        partner_of[int(k)] = P['agent_partner'][k]
    banks = {}
    for b in sorted(P.get('banks', []), key=C.acc_order_key):
        ag = agent_of.get(m2o(b.get('partner_id')))
        if not ag:
            continue
        cur = banks.get(ag)
        if not cur or (b.get('allow_out_payment') and not cur.get('allow_out_payment')):
            banks[ag] = b
    names = {}
    for row in P.get('names', []):
        names[row[0]] = row[1]
    anames = {}
    for row in P.get('agent_names', []):
        anames[row[0]] = row[1]
    cust_of = {}
    for a in acc:
        c = m2o(a.get('customer_id'))
        ag = m2o(a.get('agent_id'))
        if c and a.get('state') != 'cancelled' and c not in cust_of.get(ag, []):
            cust_of[ag] = cust_of.get(ag, []) + [c]
    for d in P.get('deals_client', []):
        ag = m2o(d.get('agent_id'))
        if d.get('state') in C.LIVE:
            for c in C.ids_of(d.get('customer_ids')):
                if c not in cust_of.get(ag, []):
                    cust_of[ag] = cust_of.get(ag, []) + [c]
    move_total = {}
    for row in P.get('move_totals', []):
        move_total[row[0]] = row[1]
    decs = {}
    for d in P.get('decisions', []):
        decs[d['id']] = dict(d)
    lines = [dict(x) for x in sorted(P.get('lines', []), key=C.acc_order_key)]
    # буфери: підготовлені рядки ключа (за днем їх рішення): Σ «У буфер» − Σ «Буфер з минулого»
    buffers = {}
    for x in lines:
        dc = decs.get(m2o(x.get('decision_id'))) or {}
        if x.get('state') != 'done' or dc.get('state') != 'done':
            continue
        k = sval(x.get('key'))
        if k:
            buffers[k] = buffers.get(k, []) + [{'id': x['id'], 'day': sval(dc.get('pay_day')), 'out': num(x.get('buffer_out')), 'in': num(x.get('buffer_in')),
                                                'agent': m2o(x.get('agent_id')), 'section': x.get('section'), 'path': sval(x.get('pay_path')) or '-',
                                                'company': m2o(x.get('company_id'))}]
    lines_by = {}
    for x in lines:
        lines_by[x['id']] = x
    env_of = {}
    for row in P.get('env_of') or []:
        env_of[row[0]] = row[1]
    env_lines = {}
    for l in P.get('env_lines') or []:
        env_lines[l.get('envelope')] = (env_lines.get(l.get('envelope')) or []) + [l]
    return {'cfg': cfg, 'deals': deals, 'acc': acc, 'acc_by': acc_by, 'dup': dup_keys(acc), 'over_deals': over, 'profiles': profiles, 'banks': banks,
            'names': names, 'anames': anames, 'partner_of': partner_of, 'cust_of': cust_of, 'cust': cust_summary(P.get('invoices', []), today),
            'move_total': move_total, 'decs': decs, 'lines': lines, 'buffers': buffers, 'refs': P.get('refs') or {}, 'act_keys': list(P.get('act_keys') or []),
            'lines_by': lines_by, 'now': '', 'env_of': env_of, 'env_lines': env_lines, 'members': P.get('members') or []}


def acc_set(res, a, vals, refs=None):
    before = {}
    for f in vals:
        before[f] = a.get(f, False)
    add_op(res, 'write', AC, a['id'], vals, refs, {'before': before})
    for f in vals:
        a[f] = vals[f]
    if refs:
        for f in refs:
            a[f] = refs[f]


def refresh(res, X, dec, D, prevD, today):
    # перезібрати рядки рішення-чернетки (і «На перевірці»): правка зміненого, нові рядки, зниклі — видалити; «Сплатити зараз» не чіпаємо
    mine = [x for x in X['lines'] if m2o(x.get('decision_id')) == dec['id'] and x.get('state') in DL_OPEN + ('zvirka',)]
    by_key = {}
    for x in mine:
        by_key[sval(x.get('key'))] = x
    own = [x['id'] for x in X['lines'] if m2o(x.get('decision_id')) == dec['id']]
    built = build_lines(X, D, prevD, own, sval(dec.get('section')))
    keep = []
    link = {}
    seen = []
    for L in built:
        v = line_vals(L, X, D)
        codes = [] if L['zv'] else line_codes(L, X)
        v['state'] = 'zvirka' if L['zv'] else ('review' if codes else 'draft')
        v['guard'] = codes[0][0] if codes else False
        v['guard_note'] = ('; '.join([c[1] for c in codes]))[0:250] if codes else False
        la = line_advice(v, X)
        for k in la:
            v[k] = la[k]
        seen.append(v)
        cur = by_key.get(L['key'])
        if cur:
            keep.append(cur['id'])
            diff = {}
            for f in v:
                if not same_val(cur.get(f, False), v[f]):
                    diff[f] = v[f]
            if X.get('now') and owner_edited(cur) and not cur.get('owner_edit'):
                diff['owner_edit'] = True
            if diff:
                if X.get('now'):
                    diff['seen_at'] = X['now']
                add_op(res, 'write', DL, cur['id'], diff)
                res['summary']['lines_changed'] += 1
            for aid in L['acc']:
                link[aid] = cur['id']
        else:
            ref = new_ref(res, 'l')
            v['decision_id'] = dec['id'] if dec['id'] else False
            v['currency_id'] = X['refs'].get('eur') or False
            if X.get('now'):
                v['seen_at'] = X['now']
                v['night_pay'] = 0.0
                v['owner_edit'] = False
            add_op(res, 'create', DL, ref, v, {'decision_id': dec['ref']} if dec.get('ref') else None)
            res['summary']['lines_new'] += 1
            for aid in L['acc']:
                link[aid] = ref
    for x in mine:
        if x['id'] not in keep:
            add_op(res, 'unlink', DL, x['id'], {})
            res['summary']['lines_gone'] += 1
            if num(x.get('pay_now')) > 0.005:
                res['notes'].append('рішення %s: рядок #%d зник (нарахувань більше немає), «Сплатити зараз» %s не діє' % (fmt_date(D), x['id'], fmt_eur(x.get('pay_now'))))
    text = '\n'.join(advice_lines(seen, X))[0:3000] or False
    if not same_val(dec.get('hq_advice') or False, text):
        if dec.get('ref'):
            add_op(res, 'write', DEC, dec['ref'], {'hq_advice': text}, None, {'id_ref': True})
        else:
            add_op(res, 'write', DEC, dec['id'], {'hq_advice': text})
    mine_ids = [x['id'] for x in mine]
    for a in X['acc']:
        want = link.get(a['id'])
        cur = m2o(a.get('pay_line_id'))
        if want and isinstance(want, str):
            acc_set(res, a, {}, {'pay_line_id': want})
        elif want and cur != want:
            acc_set(res, a, {'pay_line_id': want})
        elif not want and cur and cur in mine_ids:
            acc_set(res, a, {'pay_line_id': False})


def prepare(res, X, dec, today, now):
    # «Затверджено»: перевірка знову і підготовка. Проблема — «На перевірці» (рядки й рішення), нічого не готується
    cfg = X['cfg']
    D = sval(dec.get('pay_day'))
    lines = [x for x in X['lines'] if m2o(x.get('decision_id')) == dec['id']]
    work = []
    bad = []
    for x in lines:
        if x.get('state') != 'approved':
            continue
        accs = [a for a in X['acc'] if m2o(a.get('pay_line_id')) == x['id']]
        due = [a for a in accs if a.get('state') == 'due' and not fund_acc(a, X)]
        L = {'acc': [a['id'] for a in due], 'deals': []}
        for a in due:
            d = acc_place(a, X)[2]
            if d and d not in L['deals']:
                L['deals'].append(d)
        codes = line_codes(L, X)
        total = 0.0
        for a in due:
            total += num(a.get('amount'))
        if len(due) != len(accs) or abs(total - num(x.get('accrued'))) > 0.005:
            codes.append(['changed', 'суми змінились після затвердження: було %s, зараз %s — перевірте й затвердіть знову' % (fmt_eur(x.get('accrued')), fmt_eur(total))])
        pay = num(x.get('pay_now'))
        top = max(0.0, num(x.get('buffer_in')) + total)
        if pay < -0.005 or pay > top + 0.005:
            codes.append(['pay_range', '«Сплатити зараз» %s поза межами 0…%s' % (fmt_eur(pay), fmt_eur(top))])
        if m2o(dec.get('approved_by')) not in cfg['owner_uids']:
            codes.append(['not_owner', 'рішення затвердив не власник (%s) — суму вирішує лише власник' % (m2o_name(dec.get('approved_by')) or '—')])
        miss = [k for k in ['journal', 'fee_account', 'tax_account', 'analytic', 'eur'] if not X['refs'].get(k)]
        if 'journal' in miss and X['refs'].get('journal_note'):
            miss = ['journal (%s)' % X['refs']['journal_note'] if k == 'journal' else k for k in miss]
        if sval(x.get('pay_path')) == 'official_1' and pay > 0.005 and miss:
            codes.append(['bill_setup', 'для рахунку постачальника у фірмі #%d немає: %s' % (cfg['bill_company'], ', '.join(miss))])
        if codes:
            bad.append([x, codes])
        work.append([x, due])
    if bad:
        bad_ids = [b[0]['id'] for b in bad]
        for b in bad:
            add_op(res, 'write', DL, b[0]['id'], {'state': 'review', 'guard': b[1][0][0], 'guard_note': ('; '.join([c[1] for c in b[1]]))[0:250]})
        for w in work:
            if w[0]['id'] not in bad_ids:
                add_op(res, 'write', DL, w[0]['id'], {'state': 'draft'})
        names = ', '.join([agent_name(X, m2o(b[0].get('agent_id'))) for b in bad])
        add_op(res, 'write', DEC, dec['id'], {'state': 'review', 'guard_note': ('не підготовлено, на перевірці: %s' % names)[0:250]})
        res['summary']['blocked'] += 1
        res['notes'].append('рішення %s (#%d) не підготовлено: %s' % (fmt_date(D), dec['id'], '; '.join(['%s — %s' % (agent_name(X, m2o(b[0].get('agent_id'))), b[1][0][1]) for b in bad])[0:400]))
        return
    refs = X['refs']
    items = []
    for w in work:
        x = w[0]
        due = w[1]
        pay = money(x.get('pay_now'))
        path = sval(x.get('pay_path')) or '-'
        agent = m2o(x.get('agent_id'))
        bank = X['banks'].get(agent)
        am = pay_amounts(pay, x.get('wh_pct'), x.get('tax_pct'))
        pref = ''
        bref = ''
        if pay > 0.005:
            pref = new_ref(res, 'p')
            st = 'prep'
            if path == 'official_1':
                st = 'bill' if bank else 'docs'
            elif path == 'personal_36' and not bank:
                st = 'docs'
            note = 'Рішення власника %s (#%d): до рішення %s, сплатити зараз %s, у буфер %s.' % (fmt_date(D), dec['id'], fmt_eur(x.get('available')), fmt_eur(pay),
                                                                                       fmt_eur(num(x.get('available')) - pay))
            note += {'personal_36': ' З особистого рахунку, облік у фірмі особистих переказів — переказує офіс за дорученням.',
                     'official_1': ' Чернетка рахунку постачальника у WorkSET Sp. z o.o. — провести й оплатити пакетом SEPA.',
                     'custom': ' За запитом: оформити окремо.'}.get(path, ' Шлях виплати не задано — оформити окремо.')
            if not bank and path != 'custom':
                note += ' IBAN агента немає — «Чекаємо документи».'
            if bank and not bank.get('allow_out_payment'):
                note += ' ' + UNTRUSTED + '.'
            wm = work_month(x, due, X, D)
            note += ' Місяць роботи: %s%s.' % (month_name(wm[0]), (' (%s)' % wm[1]) if wm[1] else '')
            pv = {'agent_id': agent, 'company_id': m2o(x.get('company_id')) or cfg['path_company'].get(path) or False,
                  'section': x.get('section'), 'pay_path': path if path != '-' else False, 'pay_day': D, 'currency_id': refs.get('eur') or False,
                  'gross': pay, 'wh10': am[0], 'tax20': am[1], 'net': am[2], 'state': st, 'decision_id': dec['id'], 'pay_line_id': x['id'], 'note': note,
                  'work_month': wm[0]}
            if bank:
                pv['bank_ref'] = bank['id']
            add_op(res, 'create', PO, pref, pv)
            res['summary']['payouts'] += 1
            # рахунок — після виплати: payment_reference = номер виплати (за ним звірка виписки знайде рахунок), потім посилання виплати на рахунок
            if path == 'official_1':
                bref = new_ref(res, 'b')
                add_op(res, 'create', 'account.move', bref, bill_vals(x, agent, D, dec['id'], refs, bank, pay, am, X, pref, wm), None,
                       {'company': cfg['bill_company'], 'fmt': True})
                add_op(res, 'write', PO, pref, {}, {'move_id': bref}, {'id_ref': True})
                res['summary']['bills'] += 1
            items.append({'agent': agent_name(X, agent), 'path': path, 'section': x.get('section'), 'gross': pay, 'wh': am[0],
                          'wh_pct': num(x.get('wh_pct')), 'tax': am[1], 'tax_pct': num(x.get('tax_pct')), 'net': am[2], 'buffer': money(num(x.get('available')) - pay),
                          'iban': sval((bank or {}).get('acc_number')), 'trusted': bool((bank or {}).get('allow_out_payment')),
                          'holder': sval((bank or {}).get('acc_holder_name')) or X['anames'].get(agent) or '', 'bill': bref, 'payout': pref})
        # нарахування рядка — «Закрито рішенням»: сума пішла у виплату та/або буфер рядка
        for a in due:
            acc_set(res, a, {'state': 'decided'}, {'payout_id': pref} if pref else None)
            if not pref:
                a['payout_id'] = False
        add_op(res, 'write', DL, x['id'], {'state': 'done', 'guard': False, 'guard_note': False}, {'payout_id': pref} if pref else None)
    add_op(res, 'write', DEC, dec['id'], {'state': 'done', 'prepared_at': now, 'guard_note': False})
    res['summary']['prepared'] += 1
    if items:
        key = '%s%s-d%d' % (KEY_PREFIX, D, dec['id'])
        if key in X['act_keys']:
            res['notes'].append('повідомлення офісу %s уже є — друге не ставлю' % key)
        elif not refs.get('office_type') or not refs.get('cron_id') or not refs.get('cron_model') or not cfg.get('office_uid'):
            res['notes'].append('немає типу «Бот «Офіс»», учётки офісу або планової дії агентів — список офісу за рішенням #%d не поставлено' % dec['id'])
        else:
            text = yulia_text(D, dec['id'], m2o_name(dec.get('approved_by')), items, TEXT_LIMIT, sval(dec.get('section')))
            add_op(res, 'activity', 'mail.activity', new_ref(res, 'a'), {'res_model_id': refs['cron_model'], 'res_id': refs['cron_id'], 'activity_type_id': refs['office_type'],
                                                                       'user_id': cfg['office_uid'], 'date_deadline': max(today, D), 'summary': text_title(text),
                                                                       'note': text_html(text)}, None,
                   {'hq': {'key': key, 'text': text, 'to': cfg.get('office_to') or '', 'buttons': OFFICE_BUTTONS, 'money': True}, 'fmt': True, 'quick': True})
            res['summary']['acts'] += 1


def bill_vals(x, agent, D, dec_id, refs, bank, pay, am, X, pref='', wm=None):
    # чернетка рахунку постачальника (in_invoice) у фірмі official: рядок винагороди на рахунок витрат з аналітикою «Агенты», утримання —
    # мінусовими рядками (податок — на рахунок податкового зобов'язання); не проводиться. payment_reference — номер виплати AGENTS-P<id>
    # («{{p<n>}}» підставляє apply_ops); дата обліку — min(кінець місяця роботи, D), строк оплати — D
    cfg = X['cfg']
    an = {str(refs.get('analytic') or 0): 100}
    lines = [[0, 0, {'name': 'Винагорода агенту за рішенням власника %s (рішення #%d, рядок #%d)' % (fmt_date(D), dec_id, x['id']), 'quantity': 1, 'price_unit': pay,
                     'account_id': refs.get('fee_account') or False, 'analytic_distribution': an, 'tax_ids': [[6, 0, []]]}]]
    if am[1] > 0.005:
        lines.append([0, 0, {'name': 'Утримання %s %% — податок (п. 7.2 договору v1.7)' % C.fmt_h(num(x.get('tax_pct'))), 'quantity': 1, 'price_unit': -am[1],
                             'account_id': refs.get('tax_account') or False, 'tax_ids': [[6, 0, []]]}])
    if am[0] > 0.005:
        lines.append([0, 0, {'name': 'Утримання %s %% за домовленістю' % C.fmt_h(num(x.get('wh_pct'))), 'quantity': 1, 'price_unit': -am[0],
                             'account_id': refs.get('fee_account') or False, 'analytic_distribution': an, 'tax_ids': [[6, 0, []]]}])
    v = {'move_type': 'in_invoice', 'company_id': cfg['bill_company'], 'journal_id': refs.get('journal') or False, 'partner_id': X['partner_of'].get(agent) or False,
         'currency_id': refs.get('eur') or False, 'invoice_date': D, 'ref': 'Агенти: рішення #%d, рядок #%d' % (dec_id, x['id']),
         'payment_reference': ('AGENTS-P{{%s}}' % pref) if pref else 'AGENTS-L%d' % x['id'],
         'narration': 'Чернетка штабу: рішення власника про виплату агенту на %s (#%d). Не проводити без перевірки; рахунок податку — уточнити бухгалтерії.%s'
                      % (fmt_date(D), dec_id, (' ' + UNTRUSTED + '.') if bank and not bank.get('allow_out_payment') else ''),
         'invoice_line_ids': lines}
    if wm:
        bd = min(wm[0], D)
        v['invoice_date'] = bd
        v['date'] = bd
        v['invoice_date_due'] = D
        v['invoice_payment_term_id'] = False
        v['narration'] += ' Місяць роботи %s — дата обліку %s, сплатити %s.%s' % (month_name(wm[0]), fmt_date(bd), fmt_date(D), (' ' + wm[1].capitalize() + '.') if wm[1] else '')
    if bank:
        v['partner_bank_id'] = bank['id']
    return v


def text_title(text):
    first = text.split('\n')[0]
    return first.replace('📋 ', '').replace('💶 ', '').replace('**', '').strip()[0:120]


def text_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('**', '').replace('\n', '<br/>')


def iban_of(i, missing):
    if not i['iban']:
        return missing
    return i['iban'] if i.get('trusted') else '%s (%s)' % (i['iban'], UNTRUSTED)


def yulia_render(head, secs, cut, where, tail):
    body = []
    for s in secs:
        if s[1]:
            body = body + ['', s[0]]
            for it in s[1]:
                body = body + it
    more = ['', '• … ще %d — у Odoo: %s' % (cut, where)] if cut else []
    return '\n'.join(head + body + more + tail)


def yulia_text(D, dec_id, who, items, limit, section=''):
    # список офісу в бот «Офіс»: кому, скільки, реквізити; довгий — ріжеться пунктами з хвоста (хоча б один пункт лишається)
    head = ['📋 **%s: виплати %s**' % (C.SECTION_LABELS.get(section) or 'Агенти', fmt_date(D)), '', 'Рішення #%d затвердив %s.' % (dec_id, who or 'власник')]
    sections = []
    pers = [i for i in items if i['path'] == 'personal_36']
    off = [i for i in items if i['path'] == 'official_1']
    oth = [i for i in items if i['path'] not in ('personal_36', 'official_1')]
    if pers:
        sections.append(['**З особистого рахунку (облік у фірмі особистих переказів):**', [['• %s — **%s**' % (i['agent'], fmt_eur(i['net'])),
                                                                                           '   IBAN: %s · отримувач: %s' % (iban_of(i, 'НЕМАЄ — уточнити в агента'), i['holder']),
                                                                                           '   (сплатити %s, утримання %s %% — %s; у буфер %s)' % (fmt_eur(i['gross']), C.fmt_h(i['wh_pct']),
                                                                                                                                             fmt_eur(i['wh']), fmt_eur(i['buffer']))]
                                                                                          for i in pers]])
    if off:
        sections.append(['**Офіційно, WorkSET Sp. z o.o.:**', [['• %s — чернетка рахунку #{{%s}}: до сплати **%s** (брутто %s, податок %s %% — %s)'
                                                               % (i['agent'], i['bill'], fmt_eur(i['net']), fmt_eur(i['gross']), C.fmt_h(i['tax_pct']), fmt_eur(i['tax'])),
                                                               '   IBAN: %s · перевірити, провести, оплатити пакетом SEPA' % iban_of(i, 'НЕМАЄ — «Чекаємо документи»')]
                                                              for i in off]])
    if oth:
        sections.append(['**За запитом — оформити окремо:**', [['• %s — %s брутто (до переказу %s)' % (i['agent'], fmt_eur(i['gross']), fmt_eur(i['net']))] for i in oth]])
    menus = []
    for sec in SECTION_ORDER:
        if [i for i in items if (i.get('section') or 'people') == sec]:
            menus.append(payouts_menu(sec))
    where = ' або '.join(menus) if menus else payouts_menu('people')
    tail = ['', '👉 Переказали — натисніть «✅ Переказала» і позначте виплату в Odoo (%s → «Позначити виплаченим вручну»); питання — «✍️ Напишу».' % where]
    text = yulia_render(head, sections, 0, where, tail)
    cut = 0
    while len(text) > limit and sum([len(s[1]) for s in sections]) > 1:
        last = [s for s in sections if s[1]][-1]
        last[1] = last[1][0:len(last[1]) - 1]
        cut += 1
        text = yulia_render(head, sections, cut, where, tail)
    return text


def pay_plan(P, today, now):
    # → {ops, notes, summary}: ops по порядку (create — ref; write / unlink — id; activity — активність штабу); apply_ops виконує їх однією точкою
    res = {'ops': [], 'notes': [], 'n': 0,
           'summary': {'skipped': 0, 'moved': 0, 'created': 0, 'lines_new': 0, 'lines_changed': 0, 'lines_gone': 0, 'prepared': 0, 'blocked': 0, 'payouts': 0,
                       'bills': 0, 'acts': 0}}
    X = prep_ctx(P, today)
    X['now'] = now
    decs = sorted([X['decs'][k] for k in X['decs']], key=C.acc_order_key)
    # (1) чернетка або «На перевірці» з минулим днем виплати — «Пропущено», рядки теж
    skipped_days = {}
    for d in decs:
        if d.get('state') in ('draft', 'review') and sval(d.get('pay_day')) < today:
            why = ('день виплати минув, а рішення лишилось «На перевірці» (після «Затвердити» підготовка не пройшла: %s) — нарахування перенесено на наступний день виплати'
                   % (sval(d.get('guard_note')) or 'див. рядки')) if d.get('state') == 'review' else 'день виплати минув без «Затвердити» — нарахування перенесено на наступний день виплати'
            add_op(res, 'write', DEC, d['id'], {'state': 'skipped', 'guard_note': why[0:250]})
            d['state'] = 'skipped'
            res['summary']['skipped'] += 1
            for x in X['lines']:
                if m2o(x.get('decision_id')) == d['id'] and x.get('state') in DL_OPEN + ('zvirka', 'approved'):
                    add_op(res, 'write', DL, x['id'], {'state': 'skipped'})
                    x['state'] = 'skipped'
    for x in X['lines']:
        if x.get('state') == 'skipped':
            skipped_days[x['id']] = sval((X['decs'].get(m2o(x.get('decision_id'))) or {}).get('pay_day'))
    # (2) «До виплати» рядків «Пропущено» і без рішення з минулим днем — на наступний день виплати
    for a in X['acc']:
        link = m2o(a.get('pay_line_id'))
        pdte = sval(a.get('pay_date'))
        if link and link in skipped_days:
            v = {'pay_line_id': False}
            if a.get('state') == 'due':
                nd = C.next_pay_day(max(C.add_days(skipped_days[link], 1), today))
                if nd != pdte:
                    v['pay_date'] = nd
                res['summary']['moved'] += 1
            acc_set(res, a, v)
        elif a.get('state') == 'due' and not link and pdte and pdte < today:
            acc_set(res, a, {'pay_date': C.next_pay_day(today)})
            res['summary']['moved'] += 1
    # (3) «Затверджено» — перевірка й підготовка
    touched = []
    for d in decs:
        if d.get('state') == 'approved':
            prepare(res, X, d, today, now)
            touched.append(d['id'])
    # (4) чернетки (день ще не минув) і «На перевірці» — перезібрати рядки; рішення без розділу не перебираємо
    for d in decs:
        if d['id'] in touched:
            continue
        D = sval(d.get('pay_day'))
        if (d.get('state') == 'draft' and D >= today) or d.get('state') == 'review':
            if not d.get('section'):
                res['notes'].append('рішення #%d без розділу — не перебираю' % d['id'])
                continue
            refresh(res, X, d, D, prev_pay_day(D), today)
    # (5) ніч D−3 (і до D): чернетка рішення найближчого дня виплати на розділ, якщо її ще немає і є що вирішувати; рішення іншого розділу
    # на цей день уже є — створюємо й раніше D−3
    D = C.next_pay_day(today)
    same = [d for d in decs if sval(d.get('pay_day')) == D]
    secs = list(SECTION_ORDER)
    legacy = [d for d in same if not d.get('section')]
    if legacy:
        res['notes'].append('на %s є рішення без розділу (#%s) — нових не створюю' % (fmt_date(D), ', #'.join([str(d['id']) for d in legacy])))
        secs = []
    for s in secs:
        if [d for d in same if d.get('section') == s]:
            continue
        if C.day_diff(today, D) > C.CUTOFF and not same:
            continue
        dec = {'id': 0, 'ref': new_ref(res, 'd'), 'pay_day': D, 'section': s}
        vals = {'pay_day': D, 'state': 'draft', 'currency_id': X['refs'].get('eur') or False, 'section': s}
        mark = len(res['ops'])
        add_op(res, 'create', DEC, dec['ref'], vals)
        refresh(res, X, dec, D, prev_pay_day(D), today)
        if not [o for o in res['ops'][mark:] if o['model'] == DL]:
            res['ops'] = res['ops'][0:mark]  # вирішувати нічого — рішення не створюємо
        else:
            res['summary']['created'] += 1
    res.pop('n')
    return res


# ══ Крок (4): автозатвердження (перенос auto.py) — auto_plan(P, today, now) ══
# За базою ПІСЛЯ кроку рішень цієї ночі. Вимкнено (P['cfg']['auto_approve'] — False: прод або параметр ≠ 1) — власнику активність «ask» на
# кожну чернетку з сумою до виплати > 0 до D−1 (Р-2). Увімкнено — чернетка з D − STOP_DAYS ≥ сьогодні затверджується «як пропонує штаб» і
# одразу готується (prepare), якщо ВСЕ: немає рядків «На перевірці» й сторожа, правок власника (owner_edited, рядок без штампа ночі), ключів,
# зупинених «Зупинити»; у кожного агента вже була виплата «Виплачено» цим шляхом на цей самий довірений IBAN (рахунок не мінявся після неї;
# в історію — лише закриті вночі за рядком виписки / WS або позначені власником); «Сплатити зараз» = пропозиція ≤ «Разом до рішення»; шлях —
# personal або official; Р-4: немає разових і нарахувань, створених / змінених не ніччю (night_uids), домовленість не мінялась після останньої
# виплати. Інакше — одна активність-тривога власнику на набір причин; затверджено — підсумок із «Зупинити».
def hist_ctx(P):
    H = {'aml': {}, 'moves': {}, 'bills': {}, 'partner_of': {}}
    for k in [['aml', 'hist_aml'], ['moves', 'hist_moves'], ['bills', 'hist_bills']]:
        for r in P.get(k[1]) or []:
            H[k[0]][r['id']] = r
    for k in (P.get('agent_partner') or {}):
        H['partner_of'][int(k)] = P['agent_partner'][k]
    H['cfg'] = P.get('cfg') or {}
    return H


def paid_line_ok(h, H):
    # рядок оплати move_line_id справжній: проведений, партнер = контакт агента, журнал WS / виписка / рахунок виплати оплачено, сума = «До переказу»
    l = H['aml'].get(m2o(h.get('move_line_id')))
    if not l or sval(l.get('parent_state')) != 'posted' or m2o(l.get('partner_id')) != H['partner_of'].get(m2o(h.get('agent_id'))):
        return False
    mv = H['moves'].get(m2o(l.get('move_id'))) or {}
    bill = H['bills'].get(m2o(h.get('move_id'))) or {}
    if not (m2o(l.get('journal_id')) in (H['cfg'].get('ws_journals') or []) or m2o(mv.get('statement_line_id')) or bill.get('payment_state') == 'paid'):
        return False
    net = num(h.get('net'))
    return bool([a for a in [abs(num(l.get('amount_currency'))), abs(num(l.get('balance')))] if abs(a - net) <= 0.01])


def trusted_paid(h, H):
    if h.get('state') not in ('paid', 'paid_part'):
        return False
    if sval(h.get('paid_via')) in ('statement', 'ws') and m2o(h.get('move_line_id')) and not h.get('paid_manual'):
        return paid_line_ok(h, H)
    return bool(h.get('paid_manual')) and m2o(h.get('paid_by')) in (H['cfg'].get('owner_uids') or [])


def stopped_keys(X, dec):
    D = sval(dec.get('pay_day'))
    stop = {}
    done = {}
    for x in X['lines']:
        dc = X['decs'].get(m2o(x.get('decision_id'))) or {}
        day = sval(dc.get('pay_day'))
        k = sval(x.get('key'))
        if not k or dc.get('id') == dec['id'] or day >= D:
            continue
        note = sval(x.get('guard_note')) + ' ' + sval(dc.get('guard_note'))
        if x.get('state') == 'skipped' and dc.get('state') == 'skipped' and 'зупинено власником' in note:
            stop[k] = max(stop.get(k, ''), day)
        elif x.get('state') == 'done' and dc.get('state') == 'done':
            done[k] = max(done.get(k, ''), day)
    return [k for k in stop if done.get(k, '') <= stop[k]]


def not_night_accruals(lines, X):
    # Р-4 (а): разові (суму вводить людина) і створені / змінені НЕ ніччю (create_uid / write_uid поза night_uids)
    ids = [x['id'] for x in lines]
    out = []
    seen = []
    for a in X['acc']:
        if m2o(a.get('pay_line_id')) not in ids or a.get('state') == 'cancelled':
            continue
        ag = agent_name(X, m2o(a.get('agent_id')))
        if a.get('kind') == 'oneoff' and [ag, 'oneoff'] not in seen:
            seen.append([ag, 'oneoff'])
            out.append([ag, 'разове нарахування (суму вводить людина) — затвердіть самі', 'oneoff'])
        who = [u for u in [m2o(a.get('create_uid')), m2o(a.get('write_uid'))] if u not in X['cfg'].get('night_uids', [])]
        if who and [ag, 'hand'] not in seen:
            seen.append([ag, 'hand'])
            out.append([ag, 'нарахування #%d створено або змінено не нічним розрахунком (користувач #%d) — затвердіть самі' % (a['id'], who[0]), 'hand'])
    return out


def deal_changed(lines, X, hist):
    # Р-4 (б): домовленість нарахувань рядка (або її люди) змінена ПІСЛЯ останньої виплати «Виплачено» агенту
    out = []
    for x in lines:
        if x.get('state') != 'draft' or num(x.get('proposal')) <= 0.005:
            continue
        agent = m2o(x.get('agent_id'))
        last = max([''] + [sval(h.get('create_date')) for h in hist if m2o(h.get('agent_id')) == agent and h.get('state') in ('paid', 'paid_part')])
        if not last:
            continue
        dkeys = []
        for a in X['acc']:
            if m2o(a.get('pay_line_id')) != x['id'] or a.get('state') == 'cancelled':
                continue
            for k in [C.deal_key('people', m2o(a.get('people_deal_id'))), C.deal_key('client', m2o(a.get('client_deal_id')))]:
                if X['deals'].get(k) and k not in dkeys:
                    dkeys.append(k)
        for k in dkeys:
            d = X['deals'][k]
            wd = sval(d.get('write_date'))
            mem = [m for m in X.get('members') or [] if d.get('section') == 'people' and m2o(m.get('deal_id')) == d['id'] and sval(m.get('write_date')) > last]
            if wd > last or mem:
                out.append([agent_name(X, agent), 'домовленість #%d змінено після останньої виплати агенту (%s) — затвердіть самі'
                            % (d['id'], fmt_date((wd if wd > last else sval(mem[0].get('write_date')))[0:10])), 'deal'])
                break
    return out


def auto_check(lines, X, hist, H=None, dec=None):
    # → None (тиша: платити нічого) | [] (можна) | [[агент, причина, код]]
    H = H or {'aml': {}, 'moves': {}, 'bills': {}, 'partner_of': {}, 'cfg': X['cfg']}
    pay = [x for x in lines if x.get('state') == 'draft' and num(x.get('proposal')) > 0.005]
    if not pay:
        return None
    why = not_night_accruals(lines, X) + deal_changed(lines, X, hist)
    stopped = stopped_keys(X, dec) if dec else []
    for x in lines:
        ag = agent_name(X, m2o(x.get('agent_id')))
        if owner_edited(x):
            why.append([ag, 'ви правили рядок («Сплатити зараз» %s) — затвердіть самі' % fmt_eur(x.get('pay_now')), 'own'])
        elif not sval(x.get('seen_at')):
            why.append([ag, 'рядок без запису нічного розрахунку (правили не вночі) — затвердіть самі', 'own'])
        if sval(x.get('key')) in stopped and x.get('state') == 'draft':
            why.append([ag, 'ви зупинили автоматичне рішення з цими нарахуваннями — затвердіть самі', 'stop'])
        if x.get('state') == 'review' or sval(x.get('guard')):
            why.append([ag, 'на перевірці: %s' % (sval(x.get('guard_note')) or sval(x.get('guard')) or '—'), 'rev'])
        elif x.get('state') == 'draft' and num(x.get('review_amount')) > 0.005:
            why.append([ag, 'ще %s нарахувань на перевірці' % fmt_eur(x.get('review_amount')), 'revamt'])
    for x in pay:
        agent = m2o(x.get('agent_id'))
        ag = agent_name(X, agent)
        path = sval(x.get('pay_path'))
        if path not in ('personal_36', 'official_1'):
            why.append([ag, 'шлях «%s» — оформлює людина' % (path or '—'), 'path'])
            continue
        if num(x.get('proposal')) > num(x.get('available')) + 0.005:
            why.append([ag, 'пропозиція більша за «Разом до рішення»', 'prop'])
            continue
        bank = X['banks'].get(agent)
        if not bank:
            why.append([ag, 'IBAN немає', 'noiban'])
            continue
        if not bank.get('allow_out_payment'):
            why.append([ag, 'IBAN не довірений', 'untrusted'])
            continue
        prev = [h for h in hist if m2o(h.get('agent_id')) == agent and sval(h.get('pay_path')) == path and trusted_paid(h, H)]
        same = [h for h in prev if int(num(h.get('bank_ref'))) == bank['id']]
        if not prev:
            why.append([ag, 'перша виплата агенту цим шляхом', 'first'])
        elif not same:
            why.append([ag, 'IBAN новий — виплачених на цей рахунок ще не було', 'iban'])
        elif sval(bank.get('write_date')) > max([sval(h.get('create_date')) for h in same]):
            why.append([ag, 'рахунок агента змінено після останньої виплати', 'ibanchg'])
    return why


def why_sig(why):
    # підпис набору причин (агент:код) — нова причина дає нову тривогу; той самий набір — ні
    s = ','.join(sorted(uniq_ids(['%s:%s' % (w[0], w[2]) for w in why])))
    h = 7
    for i in range(len(s)):
        h = (h * 31 + ord(s[i])) % 999983
    return '%06d' % h


def owner_act(res, X, dec, kind, text, today, sig=''):
    # активність власнику на картці рішення: одна на рішення, вид (ask / auto) і набір причин (ask); немає типу або учётки — лише рядок у журнал
    cfg = X['cfg']
    key = '%s%s-d%d-%s%s' % (OWNER_KEY_PREFIX, sval(dec.get('pay_day')), dec['id'], kind, ('-' + sig) if sig else '')
    res['notes'].append('власнику (%s): %s' % (key, text_title(text)))
    if key in X['act_keys']:
        return False
    refs = X['refs']
    if not refs.get('owner_type') or not refs.get('dec_model') or not cfg.get('owner_uid'):
        res['notes'].append('немає типу «Зробити», моделі рішення або учётки власника — активність %s не поставлено' % key)
        return False
    add_op(res, 'activity', 'mail.activity', new_ref(res, 'o'), {'res_model_id': refs['dec_model'], 'res_id': dec['id'], 'activity_type_id': refs['owner_type'],
                                                               'user_id': cfg['owner_uid'], 'date_deadline': today, 'summary': text_title(text), 'note': text_html(text)},
           None, {'hq': {'key': key, 'text': text, 'to': cfg.get('owner_to') or '', 'money': True, 'buttons': OWNER_BUTTONS if kind == 'auto' else False}, 'quick': True})
    X['act_keys'].append(key)
    res['summary']['owner_acts'] += 1
    return True


def first_advice(X, dec):
    adv = advice_lines([x for x in X['lines'] if m2o(x.get('decision_id')) == dec['id']], X)
    return adv[0] if adv else ''


def sec_title(dec):
    return C.SECTION_LABELS.get(sval(dec.get('section'))) or 'Агенти'


def sec_menu(dec):
    return 'Агенти → Рішення (%s)' % (C.SECTION_LABELS.get(sval(dec.get('section'))) or C.SECTION_LABELS['people'])


def ask_text(dec, why, total, advice=''):
    D = sval(dec.get('pay_day'))
    rows = ['• %s — %s' % (w[0], w[1]) for w in why[0:8]]
    if len(why) > 8:
        rows.append('• … ще %d — у рішенні' % (len(why) - 8))
    head = ['💶 **%s: рішення на %s — потрібне ваше «Затвердити»**' % (sec_title(dec), fmt_date(D)), '']
    if advice:
        head = head + ['• Штаб пропонує: %s' % advice, '']
    return '\n'.join(head + ['Автоматично не затверджено:'] + rows +
                     ['', 'Разом до рішення: %s.' % fmt_eur(total), '',
                      '👉 Відкрийте рішення #%d в Odoo («%s»), перевірте «Сплатити зараз» і натисніть «Затвердити» до %s.'
                      % (dec['id'], sec_menu(dec), fmt_date(C.add_days(D, -1)))])


def auto_text(dec, items, advice=''):
    D = sval(dec.get('pay_day'))
    gross = sum([i[1] for i in items])
    net = sum([i[2] for i in items])
    rows = ['• %s — %s (до переказу %s)' % (i[0], fmt_eur(i[1]), fmt_eur(i[2])) for i in items[0:8]]
    if len(items) > 8:
        rows.append('• … ще %d — у рішенні' % (len(items) - 8))
    return '\n'.join(['📋 **%s: виплати %s затверджено автоматично**' % (sec_title(dec), fmt_date(D)), ''] + (['Штаб пропонує: %s' % advice, ''] if advice else []) +
                     ['Агентів %d, сплатити %s (до переказу %s):' % (len(items), fmt_eur(gross), fmt_eur(net))] + rows +
                     ['', '👉 Не згодні — «Зупинити» в рішенні #%d до %s включно; далі список піде офісу в день виплати.'
                      % (dec['id'], fmt_date(C.add_days(D, -STOP_DAYS)))])


def manual_asks(res, X, today):
    # Р-2: чернетка з D − STOP_DAYS ≥ сьогодні й сумою до виплати > 0 — тривога «ask» з причиною manual; повтор ночі — ключ уже є
    for d in sorted([X['decs'][k] for k in X['decs']], key=C.acc_order_key):
        D = sval(d.get('pay_day'))
        if d.get('state') != 'draft' or not D or today > C.add_days(D, -STOP_DAYS):
            continue
        lines = [x for x in X['lines'] if m2o(x.get('decision_id')) == d['id']]
        if not [x for x in lines if x.get('state') == 'draft' and num(x.get('proposal')) > 0.005]:
            continue
        why = [['автозатвердження вимкнено', 'потрібне ваше «Затвердити» до %s' % fmt_date(C.add_days(D, -1)), 'manual']]
        total = sum([num(x.get('available')) for x in lines if x.get('state') in ('draft', 'review')])
        if owner_act(res, X, d, 'ask', ask_text(d, why, total, first_advice(X, d)), today, why_sig(why)):
            res['summary']['asked'] += 1


def auto_plan(P, today, now):
    # → {ops, notes, summary}: затвердження (рядки, рішення, історія) і підготовка (prepare) однією точкою збереження
    res = {'ops': [], 'notes': [], 'n': 0, 'summary': {'auto': 0, 'asked': 0, 'prepared': 0, 'blocked': 0, 'payouts': 0, 'bills': 0, 'acts': 0, 'owner_acts': 0}}
    X = prep_ctx(P, today)
    X['now'] = now
    if not X['cfg'].get('auto_approve'):
        manual_asks(res, X, today)
        res.pop('n')
        return res
    hist = P.get('payouts_done') or []
    H = hist_ctx(P)
    owner = X['cfg'].get('owner_uid')
    done = []
    for d in sorted([X['decs'][k] for k in X['decs']], key=C.acc_order_key):
        D = sval(d.get('pay_day'))
        if d.get('state') != 'draft' or not D or today > C.add_days(D, -STOP_DAYS):
            continue
        lines = [x for x in X['lines'] if m2o(x.get('decision_id')) == d['id']]
        why = auto_check(lines, X, hist, H, d)
        if why is None:
            continue
        if why:
            total = sum([num(x.get('available')) for x in lines if x.get('state') in ('draft', 'review')])
            if owner_act(res, X, d, 'ask', ask_text(d, why, total, first_advice(X, d)), today, why_sig(why)):
                res['summary']['asked'] += 1
            continue
        items = []
        for x in lines:
            if x.get('state') != 'draft':
                continue
            p = money(x.get('proposal'))
            add_op(res, 'write', DL, x['id'], {'state': 'approved', 'pay_now': p, 'night_pay': p, 'seen_at': now})
            x['state'] = 'approved'
            x['pay_now'] = p
            x['buffer_out'] = money(num(x.get('available')) - p)
            if p > 0.005:
                items.append([agent_name(X, m2o(x.get('agent_id'))), p, pay_amounts(p, x.get('wh_pct'), x.get('tax_pct'))[2]])
        note = 'автоматично (правило власника 06.11.2026): строки без перевірки, правок і нових IBAN; сплатити = пропозиція штабу'
        add_op(res, 'write', DEC, d['id'], {'state': 'approved', 'approved_by': owner, 'approved_at': now, 'auto': True, 'auto_note': note})
        add_op(res, 'post', DEC, d['id'], {'body': 'Затверджено автоматично від імені власника (правило 06.11.2026): агентів %d, сплатити %s. «Зупинити» — до %s включно.'
                                                    % (len(items), fmt_eur(sum([i[1] for i in items])), fmt_date(C.add_days(D, -STOP_DAYS)))})
        d['state'] = 'approved'
        d['approved_by'] = [owner, 'власник']
        res['summary']['auto'] += 1
        done.append([d, items])
    for di in done:
        d = di[0]
        mark = len(res['ops'])
        prepare(res, X, d, today, now)
        ok = [o for o in res['ops'][mark:] if o['model'] == DEC and o['op'] == 'write' and o['id'] == d['id'] and o['vals'].get('state') == 'done']
        if ok:
            owner_act(res, X, d, 'auto', auto_text(d, di[1], first_advice(X, d)), today)
        else:
            bad = [o for o in res['ops'][mark:] if o['model'] == DL and o['op'] == 'write' and o['vals'].get('state') == 'review']
            why = [[agent_name(X, m2o((X['lines_by'].get(o['id']) or {}).get('agent_id'))), sval(o['vals'].get('guard_note')), 'prep'] for o in bad] or [['—', 'підготовка не пройшла', 'prep']]
            if owner_act(res, X, d, 'ask', ask_text(d, why, sum([num(x.get('available')) for x in X['lines'] if m2o(x.get('decision_id')) == d['id']]), first_advice(X, d)),
                         today, why_sig(why)):
                res['summary']['asked'] += 1
    res.pop('n')
    return res
