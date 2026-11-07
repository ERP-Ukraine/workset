# -*- coding: utf-8 -*-
"""Чисті функції «Умов клієнта» (М3, К1): без бази — на словниках, списках і рядках «РРРР-ММ-ДД».

Картка — представлення: умови замовника вже лежать у «Ставках» (workset_rates) і «Прибутковості» (workset_profit), тут лише вибір
«що діє сьогодні» і підсумок періоду. Вибір правила «замовник точніший за лінію» — ті самі функції сусідів, не копії: житло —
rcalc.housing_pick («Ставки»), «Хто платить» — pcalc.cost_rule_pick, картки DKV — pcalc.card_period, загальна ставка — rcalc.rule_pick,
домовленості агентів — rcalc.deal_fits; місяці й гроші — pcalc. Тести без Odoo (tools/odoo_addon_calc.py з alias) підставляють файли
сусідів під їхніми іменами в Odoo; інших файлів модуля цей файл не імпортує. Гроші — EUR, знак як у книгах: витрата — мінус."""
from odoo.addons.workset_profit.models import calc as pcalc
from odoo.addons.workset_rates.models import calc as rcalc

PAY_KINDS = ('housing', 'transport', 'fuel', 'tool', 'auto', 'car_rent', 'fund')  # рядки «Хто платить» — у порядку макета
COST_KINDS = ('transport', 'fuel', 'tool', 'auto')   # з правил «Хто платить» (workset.line.cost.rule); житло — з правил житла «Ставок»
PERIODS = ('month', 'quarter', 'year')               # перемикач «Місяць | Квартал | Рік» блоку «Що з цього виходить»
CHANGES_MAX = 10                                     # «Останні зміни» — стільки найсвіжіших
# «Що з цього виходить»: рядок картки — поля рядка лінії workset.profit.month (житло — разом із тим, що вирахував замовник)
PROFIT_ROWS = (('share', ('share',)), ('car_rent', ('car_rent',)), ('housing', ('housing', 'housing_customer')), ('auto', ('auto',)),
               ('fuel', ('fuel',)), ('tool', ('tool',)), ('transport', ('transport',)))
# «Договір і оплата»: ключ, підпис, що писати, коли порожньо («не внесено» — документ чи текст, «не задано» — налаштування)
CONTRACT = (('contract', 'c_contract', 'e_none'), ('scan', 'c_scan', 'e_none'), ('payment_term', 'c_term', 'e_unset'),
            ('sign', 'c_sign', 'e_unset'), ('site_contact', 'c_contact', 'e_unset'), ('site_address', 'c_address', 'e_none'),
            ('note', 'c_note', 'e_none'))
QUARTERS = ('I', 'II', 'III', 'IV')

TX = {
    'only_viewer': ['Картку «Умови клієнта» бачить лише група «Умови клієнтів: Перегляд».',
                    'Карточку «Условия клиента» видит только группа «Условия клиентов: Просмотр».'],
    'only_editor': ['Умови клієнта править лише група «Умови клієнтів: Редактор».', 'Условия клиента правит только группа «Условия клиентов: Редактор».'],
    'card_title': ['Умови клієнта', 'Условия клиента'],
    'terms_title': ['Умови клієнта: %s', 'Условия клиента: %s'],
    # «Хто платить»
    'k_housing': ['Житло', 'Жильё'], 'k_transport': ['Перевезення людей', 'Перевозка людей'], 'k_fuel': ['Паливо', 'Топливо'],
    'k_tool': ['Інструмент', 'Инструмент'], 'k_auto': ['Авто', 'Авто'], 'k_car_rent': ['Оренда авто', 'Аренда авто'],
    'k_fund': ['Фонд працівників', 'Фонд работников'],
    'no_rule': ['Ми (правила немає)', 'Мы (правила нет)'],
    'not_active': ['не діє', 'не действует'],
    'car_rent_in': ['Замовник — у «Платять нам»', 'Заказчик — в «Платят нам»'],
    'fund_deal': ['Діє — домовленість фонду', 'Действует — договорённость фонда'],
    'fund_rates': ['Діє — частка фонду в ставках', 'Действует — доля фонда в ставках'],
    'cards': ['карти DKV: %s', 'карты DKV: %s'], 'no_cards': ['карти DKV немає', 'карт DKV нет'],
    'lvl_customer': ['правило замовника', 'правило заказчика'], 'lvl_line': ['правило лінії', 'правило линии'],
    'per_hour': ['€ год', '€ ч'],
    # «Агент і хто знайшов»
    'f_text': ['ми %d · лінія %d', 'мы %d · линия %d'], 'f_missing': ['не вказано у %d з %d', 'не указано в %d из %d'],
    'ag_module': ['модуль агентів не встановлено', 'модуль агентов не установлен'], 'ag_none': ['немає', 'нет'],
    # «Договір і оплата»
    'c_contract': ['№ договору', '№ договора'], 'c_scan': ['Скан договору', 'Скан договора'], 'c_term': ['Термін оплати', 'Срок оплаты'],
    'c_sign': ['Табель погоджує', 'Табель согласует'], 'c_contact': ["Контакт на об'єкті", 'Контакт на объекте'],
    'c_address': ["Адреса об'єкта", 'Адрес объекта'], 'c_note': ['Особливі домовленості', 'Особые договорённости'],
    'e_none': ['не внесено', 'не внесено'], 'e_unset': ['не задано', 'не задано'],
    'c_no_date': ['№ %s від %s', '№ %s от %s'], 'c_no': ['№ %s', '№ %s'], 'c_date': ['від %s', 'от %s'], 'c_files': ['файлів: %d', 'файлов: %d'],
    # «Що з цього виходить»
    'q_title': ['%s квартал %s', '%s квартал %s'], 'y_title': ['%s рік', '%s год'],
    'p_share': ['Частка', 'Доля'], 'p_car_rent': ['Оренда авто', 'Аренда авто'], 'p_housing': ['Житло', 'Жильё'], 'p_auto': ['Авто', 'Авто'],
    'p_fuel': ['Паливо', 'Топливо'], 'p_tool': ['Інструмент', 'Инструмент'], 'p_transport': ['Перевезення', 'Перевозка'],
    'p_net': ['Чисто', 'Чисто'], 'p_overhead': ['ZUS і загальні', 'ZUS и общие'], 'p_remain': ['Залишається', 'Остаётся'],
    # «Останні зміни»
    'm_partner': ['Умови', 'Условия'], 'm_rate': ['Ставка', 'Ставка'], 'm_housing': ['Житло', 'Жильё'], 'm_cost': ['Хто платить', 'Кто платит'],
    'm_dkv': ['Картка DKV', 'Карта DKV'], 'ch_created': ['правило створено', 'правило создано'], 'ch_period': ['період картки', 'период карты'],
    'ch_changed': ['змінено', 'изменено'], 'yes': ['так', 'да'], 'no': ['ні', 'нет'],
    # заголовки списків, які відкриває картка (назва замовника чи лінії — з даних)
    'o_partner': ['Замовник: %s', 'Заказчик: %s'], 'o_rates': ['Діючі ставки: %s', 'Действующие ставки: %s'],
    'o_nofound': ['Ставки без «Хто знайшов»: %s', 'Ставки без «Кто нашёл»: %s'], 'o_rule': ['Загальна ставка: %s', 'Общая ставка: %s'],
    'o_housing': ['Житло лінії: %s', 'Жильё линии: %s'], 'o_cost': ['Хто платить · %s: %s', 'Кто платит · %s: %s'],
    'o_cards': ['Картки DKV: %s', 'Карты DKV: %s'], 'o_deals': ['Домовленості агентів: %s', 'Договорённости агентов: %s'],
    'o_fund': ['Ставки з фондом працівників: %s', 'Ставки с фондом работников: %s'], 'o_profit': ['Рядки лінії: %s, %s', 'Строки линии: %s, %s'],
    'o_scan': ['Скан договору: %s', 'Скан договора: %s'], 'o_projects': ['Проєкти: %s', 'Проекты: %s'], 'o_line': ['Лінія: %s', 'Линия: %s'],
    'o_agent': ['Агент лінії: %s', 'Агент линии: %s'],
    # список «Умови клієнтів»
    'l_yes': ['є', 'есть'], 'l_checked_no': ['не перевірено', 'не проверено'],
}


def t(key, lang):
    return TX[key][1 if lang == 'ru' else 0]


def lang_of(code):
    return pcalc.lang_of(code)


def _get(d, key):
    """Значення словника за id — числом (Odoo) або рядком (JSON тестів)."""
    d = d or {}
    return d.get(key) if key in d else d.get(str(key))


# ——— «Гроші за годину» ———
def money_range(values):
    """Діапазон «від — до» грошей діючих ставок («Платять нам», «На руки»): значень немає — None; → {'min', 'max', 'n'}."""
    vals = [round(float(v), 2) for v in values or [] if v is not None and v is not False]
    if not vals:
        return None
    return {'min': min(vals), 'max': max(vals), 'n': len(vals)}


def share_avg(values):
    """Частка WorkSET картки — середнє по всіх діючих ставках замовника (і нижче нуля: картка показує, як є); ставок немає — None."""
    vals = [float(v or 0) for v in values or [] if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def found_info(values, lang='uk'):
    """«Хто знайшов людей» по діючих ставках (values — found_by кожної): «ми N · лінія M» і, якщо в частини не вказано, «не вказано у
    K з N» (жовтим). Лічба — rcalc.found_split «Ставок». → {'us', 'line', 'none', 'total', 'text', 'missing' ('' — усе вказано)}."""
    values = list(values or [])
    s = rcalc.found_split(values)
    total = len(values)
    return dict(s, total=total, text=t('f_text', lang) % (s['us'], s['line']),
                missing=t('f_missing', lang) % (s['none'], total) if s['none'] else '')


def min_share(rules, rule_min, line_id, cust_id, day):
    """«Мінімум частки» картки: загальна ставка пари «лінія × замовник» на day (rcalc.rule_pick: тариф замовника точніший за правило
    лінії) і її мінімум (rule_min — {id: мінімум}). → {'rule_id', 'value'} ; правила немає або мінімум 0 — value None («не задано»)."""
    rid = rcalc.rule_pick(rules or [], line_id, cust_id, day) if line_id else 0
    v = float(_get(rule_min, rid) or 0) if rid else 0.0
    return {'rule_id': rid, 'value': round(v, 2) if v else None}


# ——— «Хто платить» ———
def _row(key, lang, payer, tone='', amount=None, unit='', since='', source='', level='', rule=None):
    """Рядок «Хто платить»: Що · Хто платить (+ сума й одиниця) · З якого числа · Звідки правило; rule — {'model', 'id', 'kind'}: id —
    форма правила-джерела, 0 — список правил цього виду (порожній — теж відповідь); None — без кліку."""
    return {'key': key, 'label': t('k_' + key, lang), 'payer': payer, 'tone': tone, 'amount': amount, 'unit': unit,
            'since': rcalc.fmt_date(since), 'source': source or '', 'level': level,
            'level_text': t('lvl_' + level, lang) if level else '', 'rule': rule}


def housing_row(src, line_id, cust_id, day, labels, lang='uk'):
    """Житло: правило житла «Ставок» пари на day — rcalc.housing_pick (правило замовника точніше за правило лінії). Хто платить — підпис
    «Хто платить» правила (порожньо — «Як рахуємо»), сума й одиниця, «З», «Звідки саме» (порожньо — «Джерело»). Правила немає — «Ми
    (правила немає)» сірим, клік — правила житла лінії."""
    rid = rcalc.housing_pick(src.get('housing_rules') or [], line_id, cust_id, day) if line_id else 0
    if not rid:
        return _row('housing', lang, t('no_rule', lang), 'muted', rule={'model': 'housing', 'id': 0, 'kind': ''} if line_id else None)
    h = _get(src.get('housing'), rid) or {}
    payer = _get(labels.get('housing_payer'), h.get('payer')) if h.get('payer') else _get(labels.get('housing_kind'), h.get('kind'))
    amount = round(float(h['amount']), 2) if h.get('amount') else None
    return _row('housing', lang, payer or '', amount=amount, unit=_get(labels.get('unit'), h.get('unit')) if amount else '',
                since=h.get('date_from'), source=h.get('source_note') or _get(labels.get('source'), h.get('source')) or '',
                level='customer' if h.get('customer_id') else 'line', rule={'model': 'housing', 'id': rid, 'kind': ''})


def card_list(periods, cards, line_id, cust_id, day):
    """Картки DKV замовника або його лінії на day: період картки (pcalc.card_period) «Лінія» з цим замовником, або з цією лінією без
    замовника. → [{'id', 'name'}] за назвою."""
    out = []
    for cid in sorted({p['card_id'] for p in periods or []}):
        p = pcalc.card_period(periods, cid, day)
        if not p or p.get('target') != 'line':
            continue
        if (cust_id and p.get('customer_id') == cust_id) or (line_id and p.get('line_id') == line_id and not p.get('customer_id')):
            out.append({'id': cid, 'name': str(_get(cards, cid) or cid)})
    return sorted(out, key=lambda c: (c['name'], c['id']))


def pays_rows(src, line_id, cust_id, day, labels, lang='uk'):
    """«Хто платить» замовника на day — рядки PAY_KINDS. src — дані, прочитані раз на запит:
      'housing_rules' (workset.line.housing._rules() «Ставок»), 'housing' — {id: {'payer', 'kind', 'amount', 'unit', 'date_from',
      'source', 'source_note', 'customer_id'}}; 'cost_rules' — [{'id', 'line_id', 'customer_id', 'kind', 'payer', 'date_from',
      'date_to', 'note'}]; 'periods' (workset.dkv.card.period._periods()), 'cards' — {id картки: назва}; 'rate_rule' — загальна ставка
      пари {'id', 'car_rent', 'date_from', 'source_note', 'customer_id'} або None; 'fund_deals' — домовленості агентів (dict як
      rcalc.deal_fits), 'fund_rates' — «Фонд працівників» діючих ставок, 'agents' — модуль агентів є.
    labels — підписи значень вибору моделей (перекладені): 'housing_payer', 'housing_kind', 'unit', 'source', 'cost_payer', 'per_hour'.
    Перевезення, паливо, інструмент, авто — pcalc.cost_rule_pick (правило замовника точніше за правило лінії); правила немає — «Ми
    (правила немає)» сірим. Паливо — ще картки DKV (card_list: 'cards' — назви, 'card_ids', 'cards_text'). Оренда авто — «Оренда авто» загальної ставки пари; 0 — «не діє».
    Фонд працівників — домовленість фонду (rcalc.deal_fits) або частка фонду в ставках; немає — «не діє»."""
    out = [housing_row(src, line_id, cust_id, day, labels, lang)]
    rules = src.get('cost_rules') or []
    for kind in COST_KINDS:
        r = pcalc.cost_rule_pick(rules, line_id, cust_id, kind, day) if line_id else None
        if r:
            row = _row(kind, lang, _get(labels.get('cost_payer'), r['payer']) or '', since=r.get('date_from'), source=r.get('note') or '',
                       level='customer' if r.get('customer_id') else 'line', rule={'model': 'cost', 'id': r['id'], 'kind': kind})
        else:
            row = _row(kind, lang, t('no_rule', lang), 'muted', rule={'model': 'cost', 'id': 0, 'kind': kind} if line_id else None)
        if kind == 'fuel':
            cards = card_list(src.get('periods'), src.get('cards'), line_id, cust_id, day)
            row['cards'] = [c['name'] for c in cards]
            row['card_ids'] = [c['id'] for c in cards]
            row['cards_text'] = t('cards', lang) % ', '.join(row['cards']) if cards else t('no_cards', lang)
        out.append(row)
    rr = src.get('rate_rule') or None
    rent = round(float(rr.get('car_rent') or 0), 2) if rr else 0.0
    if rent:
        out.append(_row('car_rent', lang, t('car_rent_in', lang), amount=rent, unit=labels.get('per_hour') or '', since=rr.get('date_from'),
                        source=rr.get('source_note') or '', level='customer' if rr.get('customer_id') else 'line',
                        rule={'model': 'rate_rule', 'id': rr['id'], 'kind': ''}))
    else:
        out.append(_row('car_rent', lang, t('not_active', lang), 'muted', rule={'model': 'rate_rule', 'id': rr['id'], 'kind': ''} if rr else None))
    deals = [d for d in src.get('fund_deals') or [] if rcalc.deal_fits(d, rcalc.FUND_KIND, cust_id, line_id, day)]
    fund_rates = [round(float(v), 2) for v in src.get('fund_rates') or [] if v and float(v) > 0]
    if deals:
        ids = [d['id'] for d in deals]
        out.append(_row('fund', lang, t('fund_deal', lang), amount=round(sum(float(d.get('rate') or 0) for d in deals), 2),
                        unit=labels.get('per_hour') or '', since=min((d.get('date_from') or '') for d in deals),
                        source='; '.join(x for x in (str(d.get('note') or '').strip() for d in deals) if x),
                        level='customer' if any(d.get('customer_ids') for d in deals) else 'line',
                        rule={'model': 'deal', 'id': ids[0] if len(ids) == 1 else 0, 'kind': 'fund', 'ids': ids}))
    elif fund_rates:
        out.append(_row('fund', lang, t('fund_rates', lang), amount=share_avg(fund_rates), unit=labels.get('per_hour') or '',
                        rule={'model': 'fund_rates', 'id': 0, 'kind': ''}))
    else:
        out.append(_row('fund', lang, t('not_active', lang), 'muted'))
    return out


# ——— «Агент і хто знайшов» ———
def agent_items(deals, cust_id, line_id, day):
    """Агент по замовнику на day: домовленості агентів по замовниках «€ за годину» і «Фікс-аванс», що стосуються замовника (rcalc.deal_fits —
    те саме правило, що частка посередника в «Ставках»: замовник серед замовників домовленості, без замовників — лінія). → [{'id', 'name',
    'kind' ('hour' / 'fixed'), 'amount'}] за назвою."""
    out = []
    for d in deals or []:
        for kind in ('hour', 'fixed'):
            if rcalc.deal_fits(d, kind, cust_id, line_id, day):
                amount = d.get('fixed') if kind == 'fixed' else d.get('rate')
                out.append({'id': d['id'], 'name': d.get('agent') or '—', 'kind': kind, 'amount': round(float(amount or 0), 2)})
    return sorted(out, key=lambda x: (x['name'], x['id']))


# ——— «Договір і оплата» ———
def contract_items(values, lang='uk'):
    """Пункти «Договір і оплата»: values — {'contract_no', 'contract_date' («РРРР-ММ-ДД»), 'scan' (скільки файлів), 'payment_term', 'sign',
    'site_contact', 'site_address', 'note'} (назви — уже текстом). Порожнє — 'empty' і що писати жовтим («не внесено» / «не задано»).
    → [{'key', 'label', 'value', 'empty', 'empty_text'}] у порядку CONTRACT."""
    v = values or {}
    no, day = str(v.get('contract_no') or '').strip(), rcalc.fmt_date(v.get('contract_date'))
    if no and day:
        contract = t('c_no_date', lang) % (no, day)
    elif no or day:
        contract = t('c_no', lang) % no if no else t('c_date', lang) % day
    else:
        contract = ''
    text = {'contract': contract, 'scan': t('c_files', lang) % int(v['scan']) if v.get('scan') else ''}
    out = []
    for key, label, empty in CONTRACT:
        value = text[key] if key in text else str(v.get(key) or '').strip()
        out.append({'key': key, 'label': t(label, lang), 'value': value, 'empty': not value, 'empty_text': t(empty, lang)})
    return out


def checked_info(day, who):
    """Чип «Перевірено <дата>»: дата перевірки й хто → {'ok', 'date' («ДД.ММ.РРРР»), 'who'}; дати немає — ok False (жовте «не перевірено»)."""
    return {'ok': bool(day), 'date': rcalc.fmt_date(day), 'who': str(who or '') if day else ''}


# ——— «Що з цього виходить» ———
def period_info(kind, value, today, lang='uk'):
    """Період блоку: kind — «Місяць | Квартал | Рік» (інше — місяць); value — місяць-якір (немає чи хибний — останній закритий,
    pcalc.screen_month: з січня 2026 до поточного). Квартал і рік — календарні, місяці — лише від pcalc.MIN_MONTH до поточного.
    → {'kind', 'value' (якір), 'months', 'title', 'range' (кілька місяців — «липень 2026 — вересень 2026»), 'prev', 'next' (якір
    сусіднього періоду; поза межами — False)}."""
    kind = kind if kind in PERIODS else 'month'
    anchor = pcalc.screen_month(value, today)
    cur = max(pcalc.month_first(today), pcalc.MIN_MONTH)
    year, month = int(anchor[0:4]), int(anchor[5:7])
    if kind == 'quarter':
        start, step = '%04d-%02d-01' % (year, (month - 1) // 3 * 3 + 1), 3
        title = t('q_title', lang) % (QUARTERS[(month - 1) // 3], year)
    elif kind == 'year':
        start, step = '%04d-01-01' % year, 12
        title = t('y_title', lang) % year
    else:
        start, step = anchor, 1
        title = pcalc.month_info(anchor, lang)['title']
    months = pcalc.month_list(max(start, pcalc.MIN_MONTH), min(pcalc.add_months(start, step - 1), cur))
    prev = pcalc.add_months(start, -step)
    nxt = pcalc.add_months(start, step)
    span = ''
    if len(months) > 1:
        span = '%s — %s' % (pcalc.month_info(months[0], lang)['title_in'], pcalc.month_info(months[-1], lang)['title_in'])
    return {'kind': kind, 'value': anchor if kind == 'month' else start, 'months': months, 'title': title, 'range': span,
            'prev': prev if pcalc.add_months(prev, step - 1) >= pcalc.MIN_MONTH else False, 'next': nxt if nxt <= cur else False}


def period_rows(rows, overhead=None, lang='uk'):
    """Підсумок рядків лінії workset.profit.month за місяці періоду: частка, оренда авто, житло (з тим, що вирахував замовник), авто,
    паливо, інструмент, перевезення (нульових, крім частки, немає), чисто; overhead — None (полів «ZUS і загальні» в «Прибутковості» ще
    немає), True (усі місяці з рядками перераховано після круга 4) або False (ні — «Залишається» невідоме: 'oh_missing'). € год — на
    години зі ставкою періоду (pcalc.per_hour; годин немає — None). Частка + рядки = чисто до цента (pcalc.totals).
    → {'has', 'rows': [{'key', 'label', 'amount', 'ph', 'tone' ('neg' — чисто чи «Залишається» нижче нуля)}], 'hours', 'hours_rate',
    'net', 'remain', 'oh_missing'}."""
    rows = rows or []
    tot = pcalc.totals(rows)
    hr = tot['hours_rate']
    out = []
    for key, fields in PROFIT_ROWS:
        c = sum(pcalc.cents(r.get(f)) for r in rows for f in fields)
        if key == 'share' or c:
            out.append({'key': key, 'amount': pcalc.eur(c), 'ph': pcalc.per_hour(pcalc.eur(c), hr), 'tone': ''})
    out.append({'key': 'net', 'amount': tot['net'], 'ph': tot['net_ph'], 'tone': 'neg' if pcalc.cents(tot['net']) < 0 else ''})
    remain = None
    if overhead and rows:
        rt = pcalc.remain_totals(rows)
        if pcalc.cents(rt['overhead']):
            out.append({'key': 'overhead', 'amount': rt['overhead'], 'ph': rt['overhead_ph'], 'tone': ''})
        remain = rt['remain']
        out.append({'key': 'remain', 'amount': remain, 'ph': rt['remain_ph'], 'tone': 'neg' if pcalc.cents(remain) < 0 else ''})
    for r in out:
        r['label'] = t('p_' + r['key'], lang)
    return {'has': bool(rows), 'rows': out if rows else [], 'hours': tot['hours'], 'hours_rate': hr, 'net': tot['net'], 'remain': remain,
            'oh_missing': bool(rows) and overhead is False}


# ——— «Останні зміни» ———
def track_value(ttype, value, lang='uk'):
    """Значення трекінгу Odoo (mail.tracking.value) текстом: дата — «ДД.ММ.РРРР», число — «12,5», так / ні; порожньо — «—»."""
    if ttype == 'boolean':
        return t('yes' if value else 'no', lang)
    if value is None or value is False or value == '':
        return '—'
    if ttype in ('date', 'datetime'):
        return rcalc.fmt_date(str(value)[0:10])
    if ttype in ('float', 'monetary'):
        return pcalc.num_text(value)
    if ttype == 'integer':
        return str(int(value))
    return str(value)


def changes_top(items, limit=CHANGES_MAX):
    """«Останні зміни»: події з усіх джерел (трекінг полів, створення правил) — найсвіжіші limit; items — [{'at' («РРРР-ММ-ДД ГГ:ХХ:СС»
    у поясі користувача), …}], рівні за часом — як прийшли. До кожної — 'date' («ДД.ММ.РРРР»)."""
    ordered = [x for _i, x in sorted(enumerate(items or []), key=lambda p: (str(p[1].get('at') or ''), -p[0]), reverse=True)]
    return [dict(x, date=rcalc.fmt_date(str(x.get('at') or '')[0:10])) for x in ordered[:limit]]
