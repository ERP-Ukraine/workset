# -*- coding: utf-8 -*-
"""Чисті функції «Ставок»: без Odoo і без бази — на словниках і рядках «РРРР-ММ-ДД».

Модель (models/workset_rate.py) збирає дані з бази й кличе ці функції; тести без Odoo запускають їх напряму
(файл не імпортує odoo). Тексти для людей — українською й російською: обирає мова того, хто діє (lang_of).
"""
import datetime

# Вікна в днях — одне місце на модуль
RECENT_DAYS = 60   # «працював за 60 днів»: рядки карти без ставки — у «Звірці» лише ті, де є табелі за стільки днів
WORK_DAYS = 21     # «Працює»: години в табелях у цього замовника не старші за стільки днів від опорної дати
DUE_WORKDAYS = 2   # строк задачі «Звірки» — стільки робочих днів
HTML_LIMIT = 100   # рядків на розділ у записі «Звірки»; більше — «Показано N з M»
TZ = 'Europe/Kyiv'

# Домовленості з посередниками (модуль посередників; імена моделі — лише в agent_deals.py): діючі — ці стани. Одна константа на модуль
LIVE_DEAL_STATES = ('zvirka', 'active')

# Системні параметри (пише інструмент штабу; у коді модуля — лише імена)
P_OUT = 'workset_rates.out_of_perimeter_company_ids'   # id фірм поза периметром рекрутингу через кому
P_ASSIGNEE = 'workset_rates.check_assignee_uid'         # хто отримує задачу «Звірки»
P_WORK_REF = 'workset_rates.work_ref'                   # опорна дата «Працює» — лише на нейтралізованій копії
P_MIN_SHARE = 'workset_rates.min_share_default'         # мінімум частки WorkSET, якщо в лінії не задано
P_GOOD_SHARE = 'workset_rates.good_share_default'       # «добра» частка, якщо в лінії не задано

TX = {
    'auto': ['Ставку збережено', 'Ставка сохранена'],
    'map': ['Працівника додано в проєкт', 'Работника добавили в проект'],
    'button': ['Застосовано вручну', 'Применено вручную'],
    'daily': ['Нічна перевірка: ставка стала діючою', 'Ночная проверка: ставка стала действующей'],
    'unlink': ['Ставку пари видалено', 'Ставка пары удалена'],
    'applied': ['%s: «Вартість години» = %s € — оновлено рядків: %d; проєкти: %s; фірми: %s.',
                '%s: «Стоимость часа» = %s € — обновлено строк: %d; проекты: %s; фирмы: %s.'],
    'reverted': ['%s: діючої ставки в пари більше немає — «Вартість години» повернуто на картку працівника, рядків: %d; проєкти: %s.',
                 '%s: действующей ставки у пары больше нет — «Стоимость часа» возвращена на карточку работника, строк: %d; проекты: %s.'],
    'wage0': ['«На руки» має бути більше 0 €/год — ставку не збережено.', '«На руки» должно быть больше 0 €/час — ставка не сохранена.'],
    'dup': ['Для пари «працівник × замовник» уже є ставка з «Ставка з» %s (№ %d). Змініть дату або відкрийте наявну ставку.',
            'Для пары «работник × заказчик» уже есть ставка с «Ставка с» %s (№ %d). Измените дату или откройте существующую ставку.'],
    'only_admin': ['Лише системний адміністратор.', 'Только системный администратор.'],
    'check_only_done': ['У звірці можна змінити лише «Розібрано» (поля: %s — пише автоматика).', 'В сверке можно изменить только «Разобрано» (поля: %s — пишет автоматика).'],
    'work_sum': ['Ставки: робота — пар %d, змінено ставок %d; години не старші за %d дн. від %s%s', 'Ставки: работа — пар %d, изменено ставок %d; часы не старше %d дн. от %s%s'],
    'btn_done': ['Оновлено рядків у проєктах: %d з %d.', 'Обновлено строк в проектах: %d из %d.'],
    'btn_none': ['Діючої ставки в пари немає — рядки проєктів не змінено.', 'Действующей ставки у пары нет — строки проектов не изменены.'],
    'btn_same': ['Усі рядки в проєктах (%d) уже за ставкою — змінювати нічого.', 'Все строки в проектах (%d) уже по ставке — менять нечего.'],
    'btn_note': ['Застосовано вручну: усі рядки в проєктах (%d) уже за ставкою.', 'Применено вручную: все строки в проектах (%d) уже по ставке.'],
    'cards': ['Договір у картках', 'Договор в карточках'],
    'lines': ['Рядки в проєктах', 'Строки в проектах'],
    'no_cards': ['Карток із цим робочим контактом немає.', 'Карточек с этим рабочим контактом нет.'],
    'no_lines': ['Рядків карти в проєктах цього замовника немає.', 'Строк карты в проектах этого заказчика нет.'],
    'c_card': ['Картка', 'Карточка'], 'c_firm': ['Фірма', 'Фирма'], 'c_state': ['Стан', 'Состояние'], 'c_contract': ['У договорі, €/год', 'В договоре, €/час'],
    'c_match': ['Збігається', 'Совпадает'], 'c_project': ['Проєкт', 'Проект'], 'c_cost': ['Вартість години', 'Стоимость часа'], 'c_order': ['Ціна в замовленні', 'Цена в заказе'],
    'c_person': ['Працівник', 'Работник'], 'c_customer': ['Замовник', 'Заказчик'], 'c_cfirm': ['Фірма договору', 'Фирма договора'], 'c_wage': ['На руки', 'На руки'],
    'c_rows': ['Рядків', 'Строк'], 'c_rprice': ['Ціна ставки', 'Цена ставки'], 'c_orders': ['У замовленнях', 'В заказах'], 'c_line': ['Лінія', 'Линия'],
    'c_share': ['Частка WorkSET', 'Доля WorkSET'], 'c_rule': ['Правило', 'Правило'], 'c_what': ['Що перевірити', 'Что проверить'], 'c_note': ['Примітка', 'Примечание'],
    'c_cur': ['Валюта', 'Валюта'],
    'active': ['діє', 'действует'], 'archived': ['в архіві', 'в архиве'], 'no_wage': ['ставки в договорі немає', 'ставки в договоре нет'], 'chosen': [' — з неї «У договорі»', ' — с неё «В договоре»'],
    'no_flag': [' (без ручної правки)', ' (без ручной правки)'], 'no_order': ['немає рядка замовлення', 'нет строки заказа'],
    'other_cur': ['інша валюта — автоматика не змінює', 'другая валюта — автоматика не меняет'],
    'from_card': ['з картки', 'с карточки'], 'manual': ['вартість з ручною правкою без ставки', 'стоимость с ручной правкой без ставки'], 'no_contact': ['у картці немає робочого контакту', 'в карточке нет рабочего контакта'],
    's_contract': ['Договір ≠ ставка', 'Договор ≠ ставка'],
    'h_contract': ['У договорі картки інша погодинна ставка, ніж «На руки» у ставці: виправте договір або ставку.',
                   'В договоре карточки другая почасовая ставка, чем «На руки» в ставке: исправьте договор или ставку.'],
    's_mism': ['Вартість години в проєкті ≠ ставка', 'Стоимость часа в проекте ≠ ставка'],
    'h_mism': ['Рядок карти правили вручну: кнопка «Застосувати зараз» на ставці поверне ставку в усі проєкти.',
               'Строку карты правили вручную: кнопка «Применить сейчас» на ставке вернёт ставку во все проекты.'],
    's_nomap': ['Проєкти без ставки (табелі за %d днів)', 'Проекты без ставки (табели за %d дней)'],
    'h_nomap': ['Працівник у проєкті, а діючої ставки пари «працівник × замовник» немає — заведіть ставку. Вартість години зараз — з картки працівника або введена вручну (стовпчик «Примітка»). Ще %d рядків без табелів за %d днів — у меню «Звірка → Проєкти без ставки», фільтр «Без табелів %d днів».',
                'Работник в проекте, а действующей ставки пары «работник × заказчик» нет — заведите ставку. Стоимость часа сейчас — с карточки работника или введена вручную (столбец «Примечание»). Ещё %d строк без табелей за %d дней — в меню «Сверка → Проекты без ставки», фильтр «Без табелей %d дней».'],
    's_nocust': ['Проєкти без замовника (табелі за %d днів)', 'Проекты без заказчика (табели за %d дней)'],
    'h_nocust': ['У проєкті не вказано замовника: ставку не знайти — укажіть замовника в проєкті.', 'В проекте не указан заказчик: ставку не найти — укажите заказчика в проекте.'],
    's_cur': ['Рядки в іншій валюті', 'Строки в другой валюте'],
    'h_cur': ['Ставка в євро, а картка працівника — в іншій валюті: автоматика ці рядки не змінює, вартість години — вручну.',
              'Ставка в евро, а карточка работника — в другой валюте: автоматика эти строки не меняет, стоимость часа — вручную.'],
    's_order': ['Ціна в замовленні ≠ ставка', 'Цена в заказе ≠ ставка'],
    'h_order': ['Ціна рядка замовлення відрізняється від «Ціна» ставки. Це лише звірка: замовлення автоматика не змінює.',
                'Цена строки заказа отличается от «Цена» ставки. Это только сверка: заказы автоматика не меняет.'],
    's_rule': ['Частка WorkSET не за правилом', 'Доля WorkSET не по правилу'],
    'h_rule': ['Частка WorkSET відрізняється від правила лінії або не більша за 0.', 'Доля WorkSET отличается от правила линии или не больше 0.'],
    's_check': ['Перевірити після переносу', 'Проверить после переноса'],
    'h_check': ['Ставки, перенесені з проєктів з розбіжностями: перевірте й зніміть позначку «Перевірити».',
                'Ставки, перенесённые из проектов с расхождениями: проверьте и снимите отметку «Проверить».'],
    'shown': ['Показано %d з %d. Усі: %s.', 'Показано %d из %d. Все: %s.'], 'more': ['меню «Ставки → Звірка»', 'меню «Ставки → Сверка»'],
    'more_nomap': ['меню «Ставки → Звірка → Проєкти без ставки»', 'меню «Ставки → Сверка → Проекты без ставки»'],
    'check_title': ['Звірка ставок %s', 'Сверка ставок %s'],
    'act_summary': ['%s: %d %s', '%s: %d %s'],
    'act_note': ['<p>Що перевірити — у записі звірки, по розділах: договір, проєкти, валюта, ціна в замовленні, правило лінії, перенос.</p>',
                 '<p>Что проверить — в записи сверки, по разделам: договор, проекты, валюта, цена в заказе, правило линии, перенос.</p>'],
    'replaced': ['замінено новою звіркою', 'заменено новой сверкой'], 'clean': ['розбіжностей немає', 'расхождений нет'],
    'clean_body': ['<p>Розбіжностей немає.</p>', '<p>Расхождений нет.</p>'],
    'no_rates': ['ставок ще немає — звірку не складаю', 'ставок ещё нет — сверку не составляю'],
    'daily_sum': ['Ставки: щодня %s — пар %d, зі зміною стану %d, оновлено рядків карти %d, повернуто на картку %d%s',
                  'Ставки: ежедневно %s — пар %d, со сменой состояния %d, обновлено строк карты %d, возвращено на карточку %d%s'],
    'check_msg': ['Звірка %s: розбіжностей %d%s', 'Сверка %s: расхождений %d%s'],
    'dry': [' (лише розрахунок)', ' (только расчёт)'],
    'issue': [['розбіжність', 'розбіжності', 'розбіжностей'], ['расхождение', 'расхождения', 'расхождений']],
    'rate_word': [['ставка', 'ставки', 'ставок'], ['ставка', 'ставки', 'ставок']],
    'others': ['ще %d %s', 'ещё %d %s'],
    'no_line': ['Без лінії', 'Без линии'],
    'found_line': ['знайшла лінія', 'нашла линия'], 'found_us': ['знайшли ми', 'нашли мы'],
    'rule_txt': ['+%s €/год, %s', '+%s €/час, %s'],
    'no_rule': ['для «Хто знайшов» правила в лінії немає — ціна вручну', 'для «Кто нашёл» правила в линии нет — цена вручную'],
    'unit': ['€/год', '€/час'],
    'i_rule': ['не за правилом лінії (+%s)', 'не по правилу линии (+%s)'], 'i_share': ['частка WorkSET ≤ 0', 'доля WorkSET ≤ 0'],
    'i_contract': ['договір ≠ ставка', 'договор ≠ ставка'], 'i_map': ['рядків у проєктах не за ставкою: %d', 'строк в проектах не по ставке: %d'],
    'i_order': ['ціна в замовленні ≠ ставка', 'цена в заказе ≠ ставка'], 'i_check': ['перевірити після переносу', 'проверить после переноса'],
    'card_active': ['діє', 'действует'], 'card_archived': ['архів', 'архив'],
    'agent_none': ['модуля посередників немає', 'модуля посредников нет'],
    'agent_zero': ['немає', 'нет'],
    'agent_deals': ['за домовленістю %s (посередник)', 'по договорённости %s (посредник)'],
    'agent_line': ['з лінії (домовленості немає)', 'из линии (договорённости нет)'],
    'agent_manual': ['введено вручну', 'введено вручную'],
    'no_payroll': ['модуля зарплат немає', 'модуля зарплат нет'],
    's_agent': ['Лінія з посередником, домовленості немає', 'Линия с посредником, договорённости нет'],
    'h_agent': ['У лінії є агент, а діючої домовленості з посередником на цю ставку немає: частка посередника взята з лінії або введена вручну. Заведіть домовленість у «Агенти по замовниках».',
                'У линии есть агент, а действующей договорённости с посредником на эту ставку нет: доля посредника взята из линии или введена вручную. Заведите договорённость в «Агенты по заказчикам».'],
    'c_agent': ['Агент лінії', 'Агент линии'], 'c_ashare': ['Частка посередника', 'Доля посредника'], 'c_asrc': ['Звідки', 'Откуда'],
    'new_rate': ['Нова ставка', 'Новая ставка'], 'maps_title': ['Рядки в проєктах', 'Строки в проектах'],
    'line_rates': ['Ставки лінії', 'Ставки линии'], 'cust_rates': ['Ставки замовника', 'Ставки заказчика'], 'person_rates': ['Ставки працівника', 'Ставки работника'],
    'nomap_title': ['Проєкти без ставки', 'Проекты без ставки'],
}


def lang_of(code):
    """Код мови Odoo → 'ru' / 'uk' (усе, крім російської, — українською: uk_UA, en_US тощо)."""
    return 'ru' if str(code or '').startswith('ru') else 'uk'


def t(key, lang):
    return TX[key][1 if lang == 'ru' else 0]


def sig(s):
    """Відбиток тексту (FNV-1a, 32 біти, + довжина): вкладка «Контроль» порівнюється ним — html Odoo чистить при записі."""
    h = 2166136261
    for ch in str(s or ''):
        h = ((h ^ ord(ch)) * 16777619) % 4294967296
    return '%08x-%d' % (h, len(str(s or '')))


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


def fmt_date(day):
    s = iso(day)
    if not s:
        return ''
    return '%s.%s.%s' % (s[8:10], s[5:7], s[0:4])


def money(x):
    """14 → «14», 14.5 → «14,5», 14.25 → «14,25»."""
    v = round(float(x or 0), 2)
    if abs(v - round(v)) < 0.005:
        return '%d' % int(round(v))
    return ('%.2f' % v).rstrip('0').replace('.', ',')


def num(x):
    return ('%g' % round(float(x or 0), 2)).replace('.', ',')


def same_money(a, b):
    return abs(float(a or 0) - float(b or 0)) < 0.005


def parse_ids(text):
    """«12, 15,x,» → [12, 15]: список id із системного параметра (сміття пропускається)."""
    out = []
    for part in str(text or '').replace(';', ',').split(','):
        part = part.strip()
        if part.isdigit() and int(part) > 0 and int(part) not in out:
            out.append(int(part))
    return out


def parse_float(text, default=0.0):
    try:
        return float(str(text).replace(',', '.'))
    except (TypeError, ValueError):
        return default


def pair_plan(rates, today):
    """rates — діючі (не в архіві) ставки однієї пари: [{'id', 'date_from'}]; today — «РРРР-ММ-ДД» за Києвом.
    «Ставка до» — день перед «Ставка з» наступної ставки пари (в останньої — порожньо); чинна — остання зі «Ставка з» ≤ сьогодні,
    раніше за неї — історія, пізніше за сьогодні — майбутня. → {'states': {id: {'date_to', 'state'}}, 'current': id чинної або 0}"""
    rows = sorted(rates, key=lambda r: (r['date_from'], r['id']))
    cur = 0
    for r in rows:
        if r['date_from'] <= today:
            cur = r['id']
    out = {}
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if r['date_from'] > today:
            state = 'future'
        elif r['id'] == cur:
            state = 'current'
        else:
            state = 'past'
        out[r['id']] = {'date_to': add_days(nxt['date_from'], -1) if nxt else '', 'state': state}
    return {'states': out, 'current': cur}


def lines_to_write(maps, wage):
    """maps — [{'id', 'cost', 'changed'}] рядки карти пари → id рядків, де «Вартість години» ≠ «на руки» або немає прапорця ручної правки
    (без прапорця sale_timesheet перерахував би cost з hourly_cost картки)."""
    return [m['id'] for m in maps if not same_money(m['cost'], wage) or not m['changed']]


def lines_to_revert(maps, wages):
    """Пара втратила чинну ставку: рядки, де стоїть її «на руки» з прапорцем (це поставила автоматика), — назад на картку;
    рядки, правлені вручну на іншу суму, не чіпаємо."""
    out = []
    for m in maps:
        if m['changed'] and any(same_money(m['cost'], w) for w in wages):
            out.append(m['id'])
    return out


def work_state(last, ref, days, date_from=''):
    """«Працює зараз» за табелями: last — дата останніх годин у цього замовника або ''; ref — опорна дата; days — вікно.
    'work' — години не старші за days днів (межа включно), 'z_idle' — старші. Годин не було: 'work_new' («Новий»), якщо «Ставка з»
    діючої ставки не старша за вікно, інакше 'z_none'."""
    if not last:
        if date_from and date_from >= add_days(ref, -days):
            return 'work_new'
        return 'z_none'
    if last >= add_days(ref, -days):
        return 'work'
    return 'z_idle'


def work_group(state):
    """Група роботи для панелі: «Працюють» = Працює + Новий."""
    if state in ('work', 'work_new'):
        return 'g1_work'
    if state == 'z_idle':
        return 'g2_idle'
    if state == 'z_none':
        return 'g3_none'
    return ''


def pick_card(cards):
    """cards — картки людини в усіх фірмах. Договір беремо з тієї, через яку людина працює в цього замовника:
    рядок карти в замовника й останній табель у нього → рядок карти → діюча → менший id."""
    best = None
    for c in cards:
        k = (1 if c['at_customer'] else 0, c['last_ts'] or '', 1 if c['active'] else 0, -c['id'])
        if best is None or k > best[0]:
            best = (k, c)
    return best[1] if best else None


def control_vals(wage, price, card, maps, has_contract=True):
    """Контроль чинної ставки: договір вибраної картки, рядки карти (вартість і прапорець), ціна рядка замовлення (лише звірка).
    has_contract False — модуля зарплат (hourly_wage версії договору) немає: договір не звіряється, «Договір збігається»."""
    contract = float(card['wage'] or 0) if (card and has_contract) else 0.0
    mism = 0
    order_ok = True
    for m in maps:
        if not same_money(m['cost'], wage) or not m['changed']:
            mism += 1
        if m.get('order_price') is not None and not same_money(m['order_price'], price):
            order_ok = False
    ok = (bool(card) and same_money(contract, wage)) if has_contract else True
    return {'contract_wage': contract, 'contract_ok': ok, 'map_count': len(maps),
            'map_mismatch': mism, 'order_price_ok': order_ok}


def neutral_control():
    """Ставка не чинна: контролю немає — нейтральні значення, у «Звірку» не потрапляє."""
    return {'contract_wage': 0.0, 'contract_ok': True, 'map_count': 0, 'map_mismatch': 0, 'order_price_ok': True}


def workdays_after(day, n):
    """«РРРР-ММ-ДД» + n робочих днів (пн–пт)."""
    d = day
    left = n
    while left > 0:
        d = add_days(d, 1)
        if to_date(d).weekday() < 5:
            left -= 1
    return d


def link(model, rid):
    """Адреса запису в клієнті Odoo 19."""
    return '/odoo/%s/%d' % (model, rid)


def esc(s):
    return str(s if s is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def cell(c):
    if isinstance(c, dict):
        if c.get('href'):
            return '<a href="%s">%s</a>' % (esc(c['href']), esc(c.get('text')))
        return esc(c.get('text'))
    return esc(c)


def table_html(cols, rows):
    head = ''.join(['<th>%s</th>' % esc(c) for c in cols])
    body = ''.join(['<tr>%s</tr>' % ''.join(['<td>%s</td>' % cell(c) for c in r]) for r in rows])
    return '<table class="table table-sm table-striped"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (head, body)


def sections_html(sections, limit, lang):
    """sections — [{'title', 'hint', 'cols', 'rows', 'more'}]: порожні розділи пропускаються; рядків більше limit — «Показано N з M»."""
    parts = []
    for s in sections:
        rows = s['rows']
        if not rows:
            continue
        parts.append('<h3>%s — %d</h3>' % (esc(s['title']), len(rows)))
        if s.get('hint'):
            parts.append('<p>%s</p>' % esc(s['hint']))
        parts.append(table_html(s['cols'], rows[0:limit]))
        if len(rows) > limit:
            parts.append('<p>%s</p>' % esc(t('shown', lang) % (limit, len(rows), s.get('more') or t('more', lang))))
    return ''.join(parts)


def issue_count(sections):
    return sum(len(s['rows']) for s in sections)


def plural(n, one, few, many):
    """Форми числа (однакові правила в української й російської): 1 розбіжність, 2 розбіжності, 5 розбіжностей."""
    k = abs(int(n)) % 100
    if 11 <= k <= 14:
        return many
    k = k % 10
    if k == 1:
        return one
    if 2 <= k <= 4:
        return few
    return many


def issues_word(n, lang):
    w = t('issue', lang)
    return plural(n, w[0], w[1], w[2])


def others_note(n, lang):
    """«ще 3 ставки» — діючі ставки працівника в інших замовників; 0 — порожньо."""
    if not n:
        return ''
    w = t('rate_word', lang)
    return t('others', lang) % (n, plural(n, w[0], w[1], w[2]))


def mark(ok):
    return '✓' if ok else '✗'


def control_html(cards, maps, other, wage, price, lang):
    """Вкладка «Контроль» чинної ставки: договір у кожній картці людини й рядки карти всіх фірм (у євро — їх веде автоматика,
    в іншій валюті — лише показ)."""
    rows = []
    for c in cards:
        if c['wage'] is None:  # модуля зарплат немає — договір не звіряється
            rows.append([{'text': c['name'], 'href': link('hr.employee', c['id'])}, c['company'], t('active' if c['active'] else 'archived', lang), t('no_payroll', lang), '—'])
            continue
        rows.append([{'text': c['name'], 'href': link('hr.employee', c['id'])}, c['company'], t('active' if c['active'] else 'archived', lang),
                     money(c['wage']) + ' €' if c['wage'] else t('no_wage', lang), mark(same_money(c['wage'], wage)) + (t('chosen', lang) if c['chosen'] else '')])
    mrows = []
    for m in maps:
        ok = same_money(m['cost'], wage) and m['changed']
        op = m.get('order_price')
        mrows.append([{'text': m['project'], 'href': link('project.project', m['project_id'])}, m['company'], m['employee'], money(m['cost']) + ' €' + ('' if m['changed'] else t('no_flag', lang)),
                      (money(op) + ' € ' + mark(same_money(op, price))) if op is not None else t('no_order', lang), mark(ok)])
    for m in other:
        mrows.append([{'text': m['project'], 'href': link('project.project', m['project_id'])}, m['company'], m['employee'], '%s %s' % (money(m['cost']), m.get('currency') or ''),
                      '—', t('other_cur', lang)])
    out = '<h4>%s — %d</h4>' % (esc(t('cards', lang)), len(cards))
    out += table_html([t('c_card', lang), t('c_firm', lang), t('c_state', lang), t('c_contract', lang), t('c_match', lang)], rows) if rows else '<p>%s</p>' % esc(t('no_cards', lang))
    out += '<h4>%s — %d</h4>' % (esc(t('lines', lang)), len(maps) + len(other))
    out += table_html([t('c_project', lang), t('c_firm', lang), t('c_card', lang), t('c_cost', lang), t('c_order', lang), t('c_match', lang)], mrows) if mrows else '<p>%s</p>' % esc(t('no_lines', lang))
    return out


def rule_share(found_by, share_line_found, share_we_found):
    """Частка WorkSET за правилом лінії за «Хто знайшов»: 0 — правила немає."""
    if found_by == 'line':
        return float(share_line_found or 0.0)
    if found_by == 'us':
        return float(share_we_found or 0.0)
    return 0.0


def rule_text(has_line, found_by, rule, lang):
    if not has_line:
        return ''
    if rule:
        who = t('found_line', lang) if found_by == 'line' else t('found_us', lang)
        return t('rule_txt', lang) % (num(rule), who)
    return t('no_rule', lang)


def share_level(share, rule, line_min, line_good, min_default, good_default):
    """Рівень частки WorkSET: low — ≤ 0 або нижче мінімуму (лінії; немає — правило лінії, де воно є, інакше параметр за замовчуванням);
    good — не нижче «доброї» (лінії; немає — параметр; 0 — «доброї» немає); інакше mid."""
    share = float(share or 0.0)
    low = line_min if line_min else (rule if rule > 0 else (min_default or 0.0))
    good = line_good if line_good else (good_default or 0.0)
    if share <= 0 or share < low - 0.005:
        return 'low'
    if good and share >= good - 0.005:
        return 'good'
    return 'mid'


def formula(wage, share, agent, price, lang):
    """«на руки + частка = ціна €/год» (з часткою посередника — три доданки)."""
    vals = [wage or 0.0, share or 0.0]
    if agent:
        vals.append(agent)
    return '%s = %s %s' % (' + '.join([num(v) for v in vals]), num(price), t('unit', lang))


def issues(r, lang):
    """«Перевірити» — один рядок з усім, що не так у діючої або майбутньої ставки; r — словник полів ставки."""
    out = []
    if r['active'] and r['state'] in ('current', 'future'):
        if not r['rule_ok']:
            out.append(t('i_rule', lang) % num(r['rule_share']))
        if (r['our_share'] or 0.0) <= 0:
            out.append(t('i_share', lang))
        if r['state'] == 'current':
            if not r['contract_ok']:
                out.append(t('i_contract', lang))
            if r['map_mismatch']:
                out.append(t('i_map', lang) % r['map_mismatch'])
            if not r['order_price_ok']:
                out.append(t('i_order', lang))
        if r['to_check']:
            out.append(t('i_check', lang) + ((': ' + r['check_reason']) if r['check_reason'] else ''))
    return ' · '.join(out)


def agent_share_for(deals, cust_id, line_id, day):
    """Частка посередників ставки: сума «€/год» діючих домовленостей (стан у LIVE_DEAL_STATES), чинних на day («з» ≤ day ≤ «по»),
    де замовник ставки серед замовників домовленості або лінія ставки — лінія домовленості.
    deals — [{'id', 'rate', 'state', 'customer_ids', 'line_id', 'date_from', 'date_to'}] → (сума, id домовленостей)."""
    total = 0.0
    used = []
    for d in deals:
        if d['state'] not in LIVE_DEAL_STATES:
            continue
        if (d.get('kind') or 'hour') != 'hour':
            continue  # фіксована сума (fixed) — не погодинна частка

        if d.get('date_from') and day and d['date_from'] > day:
            continue
        if d.get('date_to') and day and d['date_to'] < day:
            continue
        if cust_id in (d.get('customer_ids') or []) or (line_id and d.get('line_id') == line_id):
            total += float(d.get('rate') or 0.0)
            used.append(d['id'])
    return round(total, 2), used


def agent_day(state, date_from, date_to, today):
    """Дата, на яку шукати домовленість: діюча й майбутня — сьогодні (майбутня — її «Ставка з», якщо вона пізніше);
    минула — її «Ставка до» (немає — «Ставка з»). Рядки «РРРР-ММ-ДД»."""
    if state == 'past':
        return date_to or date_from or today
    if date_from and date_from > today:
        return date_from
    return today


AGENT_SOURCES = ('deal', 'line', 'manual', 'none')


def agent_choice(deals, cust_id, line_id, day, line_agent, line_share, source='', manual=0.0):
    """Частка посередника ставки з джерелом. source — збережене джерело ставки: 'manual' (число поставили руками) — число
    лишається як є, ланцюжок не діє (скидається лише зміною замовника, лінії чи дати — тоді source порожнє). Інакше ланцюжок:
    (а) домовленості (погодинні, діючі на day, за замовником або лінією) → (б) агент і частка лінії → (в) 0 ('none').
    Джерело перестало діяти (домовленість закрили, у лінії прибрали агента) — наступний рівень, а не збережене число.
    deals None — моделі посередників немає. → {'value', 'source': deal|line|manual|none, 'ids', 'module': є модель}"""
    module = deals is not None
    if source == 'manual':
        return {'value': round(float(manual or 0.0), 2), 'source': 'manual', 'ids': [], 'module': module}
    if module:
        total, used = agent_share_for(deals, cust_id, line_id, day)
        if used:
            return {'value': total, 'source': 'deal', 'ids': used, 'module': True}
    if line_agent and line_share:
        return {'value': round(float(line_share), 2), 'source': 'line', 'ids': [], 'module': module}
    return {'value': 0.0, 'source': 'none', 'ids': [], 'module': module}


def create_source(given, choice):
    """Джерело частки посередника при створенні: число передали явно (given не None) і воно ≠ ланцюжку — «вручну»;
    збігається або не передали — джерело ланцюжка."""
    if given is not None and not same_money(given, choice['value']):
        return 'manual'
    return choice['source']


def agent_note(source, ids, module, lang):
    """Пояснення до частки посередника — за збереженим джерелом."""
    if source == 'deal':
        return t('agent_deals', lang) % (', '.join('№ %d' % i for i in ids) or '—')
    if source == 'line':
        return t('agent_line', lang)
    if source == 'manual':
        return t('agent_manual', lang)
    return t('agent_zero', lang) if module else t('agent_none', lang)


def panel_values(values, order_counts=None, keep_zero=False):
    """Значення лівої панелі без порожніх: прибрати ті, де лічильник 0 (keep_zero — залишити всі, напр. «Робота»), і впорядкувати за
    order_counts {id: число тих, хто працює} — більше вище; при рівності — за лічильником, далі — порядок, що дав Odoo."""
    rows = [v for v in values if keep_zero or v.get('__count', 1)]
    if order_counts is None:
        return rows
    counts = {str(k): n for k, n in order_counts.items()}  # ключі — id числом (Odoo) або рядком (JSON тестів)
    pos = {}
    for i, v in enumerate(rows):
        pos[v['id']] = i
    return sorted(rows, key=lambda v: (-(counts.get(str(v['id']), 0)), -(v.get('__count') or 0), pos[v['id']]))


def summary(rows):
    """Полоса підсумків над списком: rows — [{'work', 'wage', 'price', 'share'}] ставок поточного пошуку.
    Працюють — «Працює» + «Новий»; середні — по тих, хто працює; середня частка — без ставок з часткою ≤ 0 (їх число — «Без частки»)."""
    n = 0
    wage = 0.0
    price = 0.0
    share = 0.0
    ns = 0
    zero = 0
    for r in rows:
        if r['work'] not in ('work', 'work_new'):
            continue
        n += 1
        wage += r['wage'] or 0.0
        price += r['price'] or 0.0
        if (r['share'] or 0.0) > 0:
            share += r['share']
            ns += 1
        else:
            zero += 1
    return {'total': len(rows), 'working': n, 'avg_wage': round(wage / n, 2) if n else 0.0, 'avg_price': round(price / n, 2) if n else 0.0,
            'avg_share': round(share / ns, 2) if ns else 0.0, 'zero_share': zero}
