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
SPLIT_LEGEND_MIN = 0.5     # «Куди йде частка»: пункт легенди — лише частина, що хоч в одній лінії місяця займає ≥ стільки % смуги (круг 3б)
FALL_THIN_PCT = 2          # водоспад: крок, менший за стільки % першого стовпця (частки), — тонкий: екран дає йому мінімальну висоту (круг 3б)
# вісь «Чисто по місяцях» «Огляду» (круг 3а): від min(AXIS_LOW, AXIS_PCT-й перцентиль) до max(AXIS_HIGH, (100 − AXIS_PCT)-й), кратно AXIS_STEP;
# що поза віссю — викид: крапка біля краю з підписом «лінія місяць число» (один збитковий місяць не стискає решту в нитку)
AXIS_LOW = -10
AXIS_HIGH = 25
AXIS_PCT = 5
AXIS_STEP = 5

# Круг 4: «− ZUS − загальні = Залишається WorkSET» (рішення власника 05.11 «загальні: А» — версія 1, за годинами табеля). Рахунки ZUS і
# PIT-4 польських фірм — як tools/lib/odoo_contrib_redate.js штабу: партнер ZUS («Zakład Ubezpieczeń Społecznych») або податкова
# («Urząd Skarbowy») з «PIT-4» у ref / назвах рядків; місяць роботи — єдина мітка «ММ/РРРР» (ММ.РРРР, ММ/РР) у тих самих текстах; мітки
# немає, їх дві чи діапазон «ММ-ММ/РРРР» — місяць дати проводки (і рядок «ZUS без мітки місяця» у звірці)
ZUS_RE = re.compile(r'Ubezpiecze[nń]\s+Spo[łl]eczn', re.I)
TAX_RE = re.compile(r'Skarbow', re.I)
PIT4_RE = re.compile(r'PIT[-\s]?4', re.I)
LABEL_RE = re.compile(r'(?<![\d./])(0[1-9]|1[0-2])\s?[/.]\s?(20\d{2}|\d{2})(?!\d)(?![/.]\d)', re.A)
RANGE_RE = re.compile(r'(?<![\d./])(0[1-9]|1[0-2])\s?[-–—]\s?(0[1-9]|1[0-2])\s?[/.]\s?(20\d{2}|\d{2})(?!\d)', re.A)
CONTRIB_TYPES = ('in_invoice', 'in_refund')   # рахунки постачальника (і повернення) — як MOVE_TYPES інструмента штабу
CONTRIB_AHEAD = 1     # проводка — не раніше за стільки місяців до місяця роботи (мітка «на місяць вперед»)
CONTRIB_BACK = 12     # і не пізніше за стільки місяців після нього: вікно пошуку рахунків місяця роботи
OVERHEAD_KINDS = ('line', 'noline')   # хто несе загальні: години ліній і години без лінії
OVERHEAD_FIELDS = ('zus_amount', 'office_amount', 'remain', 'remain_per_hour')
REMAIN = ('zus', 'office', 'remain')  # водоспад після «чисто»: − ZUS, PIT-4 → − Загальні фірм → = Залишається

# Круг 5 (слово власника 07.11 «период который я хочу выбрать и год, не только информация за месяц»): період екранів A і B
SPAN_KINDS = ('month', 'quarter', 'year', 'range')   # місяць · квартал · рік · «з–по» (як у C)
QUARTER_NAMES = ('I', 'II', 'III', 'IV')
# рядок лінії за період — суми цих полів (гроші — до цента); € год — середньозважене (сума ÷ години зі ставкою), як за місяць
SUM_FIELDS = HOURS + MONEY + ('net', 'fund_left', 'zus_amount', 'office_amount', 'remain')

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
    # круг 3б: правий блок «Не в нашому чисто» картки — свій підпис (calc.aside_label); у «Рядках» — як вище. Круг 5: буфер наповнюється
    # з оплат замовника (журнал WS «Передача ліквідності»), а не з оплати людей
    'b_housing_buffer': ['Відкладено на житло з оплати замовника — не в чисто', 'Отложено на жильё из оплаты заказчика — не в чисто'],
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
    'l_all': ['Усі лінії', 'Все линии'], 'l_net': ['Чисто, € год', 'Чисто, € ч'], 'l_share': ['Частка, € год', 'Доля, € ч'],
    'l_hour': ['€ год', '€ ч'], 'l_month': ['€/міс', '€/мес'],
    # круг 3а: легенда «Огляду», чип «Власники», згорнутий «Хто платить»
    'l_each': ['Кожна лінія', 'Каждая линия'], 't_owners': ['Власники · %s', 'Собственники · %s'],
    't_no_rate_line': ['Години без ставки · %s · %s', 'Часы без ставки · %s · %s'],
    'p_all_we': ['Усе платимо ми (у «чисто»)', 'Всё платим мы (в «чисто»)'],
    'p_all_partner': ['Усе — партнер (гроші лінії)', 'Всё — партнёр (деньги линии)'],
    'p_all_worker_fund': ['Усе — з фонду працівників', 'Всё — из фонда работников'],
    'p_all_customer': ['Усе — замовник', 'Всё — заказчик'],
    # круг 3в: заголовки списків, які відкривають числа екранів (назва лінії й місяць — з даних)
    'o_ts': ['Табелі: %s, %s', 'Табели: %s, %s'], 'o_lines': ['Рядки ліній: %s', 'Строки линий: %s'],
    'o_costs': ['Витрати ліній: %s', 'Расходы линий: %s'], 'o_line': ['Рядок лінії: %s, %s', 'Строка линии: %s, %s'],
    'o_kind': ['%s: %s, %s', '%s: %s, %s'], 'o_rule': ['Хто платить · %s: %s', 'Кто платит · %s: %s'],
    'o_rules': ['Правила «Хто платить»: %s', 'Правила «Кто платит»: %s'], 'o_check': ['Звірка: %s', 'Сверка: %s'],
    't_owner': ['Власник %s · %s', 'Собственник %s · %s'],
    # круг 4: «− ZUS − загальні = Залишається WorkSET»
    's_zus': ['ZUS, PIT-4', 'ZUS, PIT-4'], 's_office': ['Загальні фірм', 'Общие фирм'], 's_remain': ['Залишається', 'Остаётся'],
    'l_remain': ['Залишається, € год', 'Остаётся, € ч'],
    'r_zus': ['ZUS, PIT-4 · частка лінії', 'ZUS, PIT-4 · доля линии'], 'r_office': ['Загальні фірм · частка лінії', 'Общие фирм · доля линии'],
    'r_hours': ['за годинами табеля: %s з %s год', 'по часам табеля: %s из %s ч'],
    'o_zus': ['Рахунки ZUS і PIT-4: %s', 'Счета ZUS и PIT-4: %s'], 'o_office': ['Загальні фірм («Офіс»): %s', 'Общие фирм («Офис»): %s'],
    'o_unlabeled': ['ZUS без мітки місяця: %s', 'ZUS без метки месяца: %s'],
    'o_costs_aal': ['Витрати ліній — аналітика: %s', 'Расходы линий — аналитика: %s'],
    'o_costs_txn': ['Витрати ліній — транзакції DKV: %s', 'Расходы линий — транзакции DKV: %s'],
    'v_overhead_ok': ['ZUS і PIT-4 %s, загальні фірм %s — розподілено за годинами до цента.',
                      'ZUS и PIT-4 %s, общие фирм %s — распределено по часам до цента.'],
    'v_zus': ['ZUS і PIT-4: у рядках %s, за рахунками %s, різниця %s.', 'ZUS и PIT-4: в строках %s, по счетам %s, разница %s.'],
    'v_office': ['Загальні фірм: у рядках %s, в «Офісі» %s, різниця %s.', 'Общие фирм: в строках %s, в «Офисе» %s, разница %s.'],
    'v_unlabeled': ['ZUS без мітки місяця: %d, сума %s — місяць за датою проводки.',
                    'ZUS без метки месяца: %d, сумма %s — месяц по дате проводки.'],
    'v_outside': ['ZUS/PIT-4 з міткою поза вікном: %d, сума %s — жоден місяць їх не рахує (перевірити мітку).',
                  'ZUS/PIT-4 с меткой вне окна: %d, сумма %s — ни один месяц их не считает (проверить метку).'],
    'oh_missing': ['Місяць не перераховано після оновлення — натисніть «Перерахувати»',
                   'Месяц не пересчитан после обновления — нажмите «Пересчитать»'],
    'v_zus_elsewhere': ['Аналітика рахунків ZUS / PIT-4 поза «Офісом»: %s — можливий подвійний рахунок (розмітити «ігнор»).',
                        'Аналитика счетов ZUS / PIT-4 вне «Офиса»: %s — возможен двойной счёт (разметить «игнор»).'],
    # круг 5: період екранів A і B (місяць, квартал, рік, «з–по»); вікно «Що входить у ZUS і загальні»
    'span_text': ['за період: %s', 'за период: %s'],
    'span_quarter': ['%s квартал %s', '%s квартал %s'], 'span_year': ['%s рік', '%s год'],
    'trend_month': ['до минулого місяця, € год', 'к прошлому месяцу, € ч'],
    'trend_span': ['до попереднього періоду, € год', 'к предыдущему периоду, € ч'],
    'v_period': ['зійшлося %d з %d', 'сошлось %d из %d'],
    'v_period_bad': ['розбіжність: %s', 'расхождение: %s'], 'v_period_none': ['звірки не було: %s', 'сверки не было: %s'],
    'o_checks': ['Звірки: %s', 'Сверки: %s'],
    'oh_title': ['Що входить у ZUS і загальні: %s', 'Что входит в ZUS и общие: %s'], 'oh_open': ['Що входить', 'Что входит'],
    'oh_dkv': ['DKV · офіс', 'DKV · офис'], 'oh_part_line': ['Частка лінії', 'Доля линии'], 'oh_part_lines': ['Частка ліній', 'Доля линий'],
    'oh_hint': ['версія 1: за годинами табеля', 'версия 1: по часам табеля'],
    'o_zus_firm': ['%s · %s: %s', '%s · %s: %s'], 'o_office_item': ['Загальні фірм · %s: %s', 'Общие фирм · %s: %s'],
    'o_outside': ['ZUS/PIT-4 з міткою поза вікном: %s', 'ZUS/PIT-4 с меткой вне окна: %s'],
    'records': [('запис', 'записи', 'записів'), ('запись', 'записи', 'записей')],
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


# ——— ZUS, PIT-4 і загальні (круг 4) ———
def labels_of(texts):
    """Мітки місяця роботи «ММ/РРРР», «ММ.РРРР», «ММ/РР» у текстах (ref і назви рядків рахунку) → ['РРРР-ММ'] без повторів за зростанням;
    дати «12.10.2026», «26/09/2026» — не мітки (як labelsOf у tools/lib/odoo_contrib_redate.js)."""
    out = set()
    for text in texts or []:
        for m in LABEL_RE.finditer(str(text or '')):
            out.add('%s-%s' % ('20' + m.group(2) if len(m.group(2)) == 2 else m.group(2), m.group(1)))
    return sorted(out)


def has_range(texts):
    """Діапазон місяців «ММ-ММ/РРРР» («ZUS 07-08/2030») — який місяць роботи, неясно (як hasRange інструмента штабу)."""
    return any(RANGE_RE.search(str(t or '')) for t in texts or [])


def contrib_kind(partner_name, texts):
    """Вид внеску рахунку: 'ZUS' — партнер ZUS; 'PIT-4' — податкова з «PIT-4» у текстах; CIT, VAT та інше — '' (як kindOf)."""
    name = str(partner_name or '')
    if ZUS_RE.search(name):
        return 'ZUS'
    if TAX_RE.search(name) and any(PIT4_RE.search(str(t or '')) for t in texts or []):
        return 'PIT-4'
    return ''


def contrib_month(texts, day):
    """Місяць роботи рахунку ZUS / PIT-4 → {'month' (перше число), 'labeled'}: єдина мітка без діапазону — її місяць; немає, дві або
    діапазон — місяць дати проводки day (labeled False — рядок «ZUS без мітки місяця» у звірці)."""
    labels = labels_of(texts)
    if len(labels) == 1 and not has_range(texts):
        return {'month': labels[0] + '-01', 'labeled': True}
    return {'month': month_first(day), 'labeled': False}


def contrib_window(first):
    """Дати проводок, серед яких шукати рахунки місяця роботи first: від CONTRIB_AHEAD місяців до до кінця місяця через CONTRIB_BACK."""
    return add_months(first, -CONTRIB_AHEAD), month_last(add_months(first, CONTRIB_BACK))


def contrib_outside(texts, day):
    """Рахунок з єдиною міткою, проведений поза вікном її місяця (calc.contrib_window: раніше за місяць до місяця роботи або пізніше за рік
    після) — його не знайде перерахунок жодного місяця (круг 4б: показати в звірці місяця проводки). → місяць мітки «РРРР-ММ-01» або ''."""
    work = contrib_month(texts, day)
    if not work['labeled']:
        return ''
    lo, hi = contrib_window(work['month'])
    return '' if lo <= iso(day) <= hi else work['month']


def contrib_sum(items):
    """Рахунки ZUS / PIT-4 місяця роботи → {'zus' (усі, EUR; витрата — мінус), 'pit4' (з них PIT-4), 'move_ids', 'unlabeled_ids',
    'unlabeled' (сума без мітки)}. items — [{'move_id', 'kind', 'amount', 'labeled'}]."""
    items = items or []
    return {'zus': eur(sum(cents(i['amount']) for i in items)), 'pit4': eur(sum(cents(i['amount']) for i in items if i['kind'] == 'PIT-4')),
            'move_ids': sorted({i['move_id'] for i in items}), 'unlabeled_ids': sorted({i['move_id'] for i in items if not i['labeled']}),
            'unlabeled': eur(sum(cents(i['amount']) for i in items if not i['labeled']))}


def overhead_split(total, weights):
    """total — центи (будь-який знак); weights — [[ключ, години]] (≤ 0 — пропуск) → [[ключ, центи]]: кожному — total × години ÷ усі
    години, округлено до цента (половина — від нуля, як cents); залишок округлення — ключу з найбільшими годинами (рівні — перший у
    списку): сума частин = total до цента. Округлення дало більше за total (кілька половин угору) — зайві центи знімаються з найбільших
    частин по одному, знак частини не перевертається. Годин немає — []."""
    ws = [(k, Fraction(str(w))) for k, w in weights or [] if w and float(w) > 0]
    if not ws:
        return []
    whole = sum(w for _k, w in ws)
    sign = -1 if total < 0 else 1
    left = abs(int(total))
    parts = [[k, int(Fraction(left) * w / whole + Fraction(1, 2))] for k, w in ws]
    order = sorted(range(len(ws)), key=lambda i: (-ws[i][1], i))
    rest = left - sum(p[1] for p in parts)
    if rest >= 0:
        parts[order[0]][1] += rest
    while rest < 0:
        for i in order:
            if rest < 0 and parts[i][1] > 0:
                parts[i][1] -= 1
                rest += 1
    return [[k, sign * c] for k, c in parts]


def overhead(rows, zus, office):
    """Загальні місяця за годинами табеля (версія 1): zus — ZUS і PIT-4 місяця роботи, office — загальні фірм (рядки «Офіс» без рахунків
    ZUS / PIT-4), EUR, витрата — мінус. Ділиться на години (hours_total) рядків ліній і «без лінії» (overhead_split — окремо ZUS і
    окремо загальні). rows — рядки assemble → (рядки з полями OVERHEAD_FIELDS: zus_amount, office_amount — частки (мінус), remain =
    net + обидві частки (лише лінія й «без лінії»), remain_per_hour — на години зі ставкою (годин немає — None, круг 4б); підсумок {'zus', 'office',
    'total', 'hours', 'per_hour', 'zus_rest', 'office_rest'}: rest — що не розподілено, бо годин місяця немає). Чисто й інші поля не
    змінюються."""
    rows = [dict(r) for r in rows or []]
    weights = [[i, float(r.get('hours_total') or 0)] for i, r in enumerate(rows)
               if r.get('row_kind') in OVERHEAD_KINDS and float(r.get('hours_total') or 0) > 0]
    zc, oc = cents(zus), cents(office)
    zs, ofs = dict(overhead_split(zc, weights)), dict(overhead_split(oc, weights))
    for i, r in enumerate(rows):
        z, o = zs.get(i, 0), ofs.get(i, 0)
        mine = r.get('row_kind') in OVERHEAD_KINDS
        remain = cents(r.get('net')) + z + o if mine else 0
        hr = round(float(r.get('hours_rate') or 0), 2)
        r.update({'zus_amount': eur(z), 'office_amount': eur(o), 'remain': eur(remain),
                  'remain_per_hour': round(eur(remain) / hr, 2) if (mine and hr) else None})
    hours = round(sum(w for _k, w in weights), 2)
    return rows, {'zus': eur(zc), 'office': eur(oc), 'total': eur(zc + oc), 'hours': hours,
                  'per_hour': round(eur(zc + oc) / hours, 4) if hours else 0.0,
                  'zus_rest': eur(zc - sum(zs.values())), 'office_rest': eur(oc - sum(ofs.values()))}


def office_rows_in(rows, dup):
    """Рядки «Офіс», з яких складено «Загальні фірм» (круг 4б: клік = число): rows — [{'id', 'analytic_id', 'amount'}] рядків «Офіс»
    місяця, dup — {стаття: центи аналітики рахунків ZUS / PIT-4 у її рядку «Офіс»} (або [[стаття, центи]]). Рядок цілком із рахунків
    ZUS / PIT-4 — не входить (він уже в ZUS); решта — входить. → {'ids', 'partial' (рядки, де ZUS лише частина: у списку весь рядок)}."""
    dup = dict(dup.items() if isinstance(dup, dict) else (dup or []))
    ids, partial = [], []
    for r in rows or []:
        d = dup.get(r.get('analytic_id') or 0, 0)
        if d and d == cents(r.get('amount')):
            continue
        ids.append(r['id'])
        if d:
            partial.append(r['id'])
    return {'ids': ids, 'partial': partial}


def remain_steps(row):
    """Водоспад після «чисто» (картка лінії, спливаюче вікно C): − ZUS, PIT-4 → − Загальні фірм → = Залишається; нульові кроки не
    показуються, обох часток немає — [] («Залишається» = «Чисто», другий стовпець не потрібен). Крок — як у chain_steps."""
    hr = float(row.get('hours_rate') or 0)
    z, o = cents(row.get('zus_amount')), cents(row.get('office_amount'))
    if not z and not o:
        return []
    out = [{'key': k, 'amount': eur(c), 'ph': per_hour(eur(c), hr)} for k, c in (('zus', z), ('office', o)) if c]
    r = cents(row.get('remain'))
    out.append({'key': 'remain', 'amount': eur(r), 'ph': per_hour(eur(r), hr)})
    return out


def remain_totals(rows):
    """До підсумку рядків (calc.totals) — «ZUS і загальні» і «Залишається»: {'zus', 'office', 'overhead' (обидві частки), 'remain',
    'zus_ph', 'office_ph', 'overhead_ph', 'remain_ph'}; € год — на години зі ставкою (немає — None): чисто + ZUS і загальні = залишається
    до цента за місяць."""
    rows = rows or []
    hr = round(sum(float(r.get('hours_rate') or 0) for r in rows), 2)
    z = sum(cents(r.get('zus_amount')) for r in rows)
    o = sum(cents(r.get('office_amount')) for r in rows)
    rem = sum(cents(r.get('remain')) for r in rows)
    return {'zus': eur(z), 'office': eur(o), 'overhead': eur(z + o), 'remain': eur(rem), 'zus_ph': per_hour(eur(z), hr),
            'office_ph': per_hour(eur(o), hr), 'overhead_ph': per_hour(eur(z + o), hr), 'remain_ph': per_hour(eur(rem), hr)}


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


def verify(rows, aal_expected, dkv_expected, month, lang='uk', raw=None, moved=0.0, nobill=None, overhead=None):
    """Звірка місяця: сума грошей аналітики в рядках = аналітика плану за місяць (та сама вибірка, частини періодів і курс) до цента;
    гроші DKV у рядках = мінус брутто транзакцій місяця; у кожному рядку стовпці SPREAD дають aal_amount + dkv_amount.
    raw — окремий підрахунок аналітики плану за датою (read_group, не через розклад) по валютах фірм: [{'currency', 'books', 'calc'}]
    (calc — ті самі рядки в розкладі, повні суми, у валюті фірми); різниця — розбіжність. moved — скільки EUR періоди послуги перенесли
    між місяцями (довідково). nobill — {'n', 'amount'}: транзакції DKV місяця без рахунку в Odoo (довідково; разом із рядками «рахунок DKV
    без транзакцій» — попередження про можливий подвійний рахунок).
    overhead (круг 4; немає — не звіряється) — {'zus_books' (ZUS і PIT-4 місяця за рахунками), 'zus_rows' (частки рядків + не
    розподілене), 'office_books' (рядки «Офіс» без рахунків ZUS / PIT-4), 'office_rows', 'unlabeled_n', 'unlabeled', 'elsewhere'
    (аналітика рахунків ZUS / PIT-4 у рядках не «Офіс»), 'outside_n', 'outside' (круг 4б: рахунки місяця проводки з міткою поза вікном
    пошуку — contrib_outside)}: розподілене = книгам до цента; без мітки, «поза Офісом» і «поза вікном» — довідково.
    rows — [{'name', 'tag', поля SPREAD, 'aal_amount', 'dkv_amount'}] → {'ok', 'aal', 'aal_expected', 'dkv', 'dkv_expected', 'bad',
    'raw_bad', 'text'}."""
    aal = sum(cents(r.get('aal_amount')) for r in rows or [])
    dkv = sum(cents(r.get('dkv_amount')) for r in rows or [])
    bad = [r.get('name') or '—' for r in rows or []
           if sum(cents(r.get(f)) for f in SPREAD) != cents(r.get('aal_amount')) + cents(r.get('dkv_amount'))]
    raw_bad = [x for x in raw or [] if cents(x.get('books')) != cents(x.get('calc'))]
    want_aal, want_dkv = cents(aal_expected), cents(dkv_expected)
    oh = overhead or {}
    zus_diff = cents(oh.get('zus_rows')) - cents(oh.get('zus_books'))
    office_diff = cents(oh.get('office_rows')) - cents(oh.get('office_books'))
    ok = aal == want_aal and dkv == want_dkv and not bad and not raw_bad and not zus_diff and not office_diff
    m = fmt_month(month)
    if ok:
        parts = [t('v_ok', lang) % (m, money_text(eur(aal)), money_text(eur(dkv)))]
        if cents(oh.get('zus_books')) or cents(oh.get('office_books')):
            parts.append(t('v_overhead_ok', lang) % (money_text(oh.get('zus_books')), money_text(oh.get('office_books'))))
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
        if zus_diff:
            parts.append(t('v_zus', lang) % (money_text(oh.get('zus_rows')), money_text(oh.get('zus_books')), money_text(eur(zus_diff))))
        if office_diff:
            parts.append(t('v_office', lang) % (money_text(oh.get('office_rows')), money_text(oh.get('office_books')),
                                                money_text(eur(office_diff))))
    if oh.get('unlabeled_n'):
        parts.append(t('v_unlabeled', lang) % (oh['unlabeled_n'], money_text(oh.get('unlabeled'))))
    if cents(oh.get('elsewhere')):
        parts.append(t('v_zus_elsewhere', lang) % money_text(oh.get('elsewhere')))
    if oh.get('outside_n'):
        parts.append(t('v_outside', lang) % (oh['outside_n'], money_text(oh.get('outside'))))
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


def _plural(n, forms):
    """Число й слово у формі за числом: forms — (1, 2–4, 5+); 11–14 — як 5+."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        word = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = forms[1]
    else:
        word = forms[2]
    return '%d %s' % (n, word)


def months_text(n, lang='uk'):
    """6 → «6 місяців» (1 — «місяць», 2–4 — «місяці», 11–14 — «місяців»), російською — «месяц / месяца / месяцев»."""
    return _plural(n, ('месяц', 'месяца', 'месяцев') if lang == 'ru' else ('місяць', 'місяці', 'місяців'))


def records_text(n, lang='uk'):
    """3 → «3 записи», 5 → «5 записів» (російською — «записи / записей»): число записів групи «Рядків» картки (круг 5)."""
    return _plural(n, t('records', lang))


def month_info(day, lang='uk'):
    """Місяць для екрана → {'value', 'label' («вересень 2026»), 'title' («Вересень 2026» — кнопка, шапка), 'title_in' («вересень 2026» —
    усередині фрази: «Рядки за вересень 2026», круг 3б), 'name' («вересень»), 'short' («Вер»)}."""
    label = month_label(day, lang)
    return {'value': month_first(day), 'label': label, 'title': label[0:1].upper() + label[1:], 'title_in': label[0:1].lower() + label[1:],
            'name': label.split(' ')[0], 'short': month_label(day, lang, True)}


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
    'up'), 'bottom', 'height', 'end', 'thin'}], 'zero'}: bottom, height і zero (де нуль) — % висоти поля; end — рівень підсумку після кроку
    (%), звідти пунктир до наступного стовпця (круг 3а); thin — крок (не частка й не «чисто») за модулем менший за FALL_THIN_PCT % першого
    стовпця: екран малює його мінімальної висоти, щоб було видно (круг 3б)."""
    run = 0
    first = 0
    raw = []
    for i, s in enumerate(steps or []):
        c = cents(s.get('amount'))
        # «Залишається» (круг 4, remain_steps) — теж підсумок від нуля, як «чисто»
        if i == 0 or s.get('key') in ('net', 'remain'):
            lo, hi = min(0, c), max(0, c)
            run = c
            d = 'total'
            if i == 0:
                first = c
        else:
            lo, hi = min(run, run + c), max(run, run + c)
            d = 'down' if c < 0 else 'up'
            run += c
        raw.append((s, lo, hi, d, run, d != 'total' and abs(c) * 100 < FALL_THIN_PCT * abs(first)))
    vals = [0] + [x[1] for x in raw] + [x[2] for x in raw]
    bottom, span = min(vals), max(vals) - min(vals)

    def pct(v):
        return round(v * 100.0 / span, 2) if span else 0.0
    bars = [dict(s, lo=eur(lo), hi=eur(hi), dir=d, bottom=pct(lo - bottom), height=pct(hi - lo), end=pct(r - bottom), thin=thin)
            for s, lo, hi, d, r, thin in raw]
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


def kind_refs(entries, refs):
    """Записи кожного виду кожної лінії місяця (круг 3в: крок водоспаду й сегмент «Куди йде частка» відкривають свої записи): entries і
    refs — розклад місяця (_month_entries) → {лінія: {поле: {'aal': [id], 'txn': [id]}}} (id без повторів, за зростанням); записи не
    лінії (без лінії, компанія) не беруться."""
    out = {}
    for e, ref in zip(entries or [], refs or []):
        key = e.get('key') or ()
        if not key or key[0] != 'line' or not key[1]:
            continue
        g = out.setdefault(key[1], {}).setdefault(e.get('field'), {'aal': set(), 'txn': set()})
        g['aal'].update((ref or {}).get('aal') or [])
        g['txn'].update((ref or {}).get('txn') or [])
    return {line: {f: {'aal': sorted(g['aal']), 'txn': sorted(g['txn'])} for f, g in by_field.items()} for line, by_field in out.items()}


def split_legend(splits, min_pct=SPLIT_LEGEND_MIN):
    """Легенда «Куди йде частка» (круг 3б): splits — [share_split лінії] за місяць → ключі SPLIT (у їх порядку), що хоч в одній лінії
    займають ≥ min_pct % смуги; частина без помітного сегмента (паливо 0,2 % в одній лінії) у легенду не йде."""
    seen = {p.get('key') for s in splits or [] for p in s or [] if float(p.get('pct') or 0) >= min_pct}
    return [k for k in SPLIT if k in seen]


def whole_hours(x):
    """Години на чипі — ціле, як години скрізь на екранах (круг 3б): 710.5 → 711, 12.49 → 12 (половина — від нуля, як cents)."""
    v = float(x or 0)
    return (-1 if v < 0 else 1) * int(abs(v) + 0.5 + 1e-7)


def aside_label(field, lang='uk'):
    """Підпис «Не в нашому чисто» у правому блоці картки лінії: свій ('b_<поле>', буфер житла — «… — не в чисто»), інакше — той самий,
    що в «Рядках» ('a_<поле>')."""
    return t('b_' + field if 'b_' + field in TX else 'a_' + field, lang)


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
    малої; є в пункті чи винятку 'open' — і він, круг 3в), 'cards': [картки DKV палива]}; видів немає — head ''."""
    items = items or []
    keys = [i.get('payer_key') or 'we' for i in items]
    if not keys:
        return {'head': '', 'rows': [], 'cards': []}
    common = max(PAYER_KEYS, key=lambda k: (keys.count(k), -PAYER_KEYS.index(k)))
    rows, cards = [], []

    def row(label, src):
        # опис списку для кліку (круг 3в: правило, за яким визначено платника) — як є, коли він є в пункті
        out = {'label': label, 'payer': _low(src.get('payer'))}
        if 'open' in src:
            out['open'] = src['open']
        return out
    for it, k in zip(items, keys):
        if k != common:
            rows.append(row(it.get('label') or '', it))
        for ex in it.get('except') or []:
            rows.append(row('%s · %s' % (it.get('label') or '', ex.get('name') or ''), ex))
        cards += [c for c in it.get('cards') or [] if c not in cards]
    return {'head': t('p_all_' + common, lang), 'rows': rows, 'cards': cards}


# ——— круг 5: період екранів A і B (місяць, квартал, рік, «з–по») ———
def quarter_first(day):
    """Перше число кварталу місяця day: «2026-08-15» → «2026-07-01»."""
    s = month_first(day)
    return add_months(s, -((int(s[5:7]) - 1) % 3))


def screen_span(kind, date_from, date_to, today, cap=MATRIX_MAX):
    """Період екрана A чи B: kind — 'month' (місяць date_from), 'quarter' / 'year' (квартал / рік, у якому місяць date_from), 'range' (з
    date_from по date_to, як у C: немає «з» — з січня року «по»; немає «по» — останній закритий; переплутані — місцями; довше cap —
    останні cap); інший kind — місяць. Місяць — screen_month (лише від MIN_MONTH до поточного; немає — останній закритий). Круг 5б: квартал
    і рік — їхні місяці від MIN_MONTH до останнього закритого (last_closed — той самий, що вид за замовчуванням і графік): поточний
    неповний місяць — лише явно, «Місяць» чи «Період»; місяць у поточному (якір) — як останній закритий: «Рік» у листопаді — січень …
    жовтень, у січні наступного року — увесь минулий. → {'kind', 'from', 'to', 'months'} (перші числа місяців)."""
    kind = kind if kind in SPAN_KINDS else 'month'
    cur = max(month_first(today), MIN_MONTH)
    if kind == 'range':
        to = screen_month(date_to, today) if valid_day(date_to) else last_closed(today)
        frm = screen_month(date_from, today) if valid_day(date_from) else max(MIN_MONTH, to[0:4] + '-01-01')
        if frm > to:
            frm, to = to, frm
        months = month_list(frm, to)[-cap:]
    else:
        anchor = screen_month(date_from if valid_day(date_from) else date_to, today)
        top = cur
        if kind == 'month':
            lo = hi = anchor
        else:
            # круг 5б (замір на копії 07.11: «Рік» — «січень — листопад», «Звірка 10 з 11» через неповний листопад)
            top = last_closed(today)
            anchor = min(anchor, top)
            if kind == 'quarter':
                lo = quarter_first(anchor)
                hi = add_months(lo, 2)
            else:
                lo, hi = anchor[0:4] + '-01-01', anchor[0:4] + '-12-01'
        months = month_list(max(lo, MIN_MONTH), min(hi, top))
    return {'kind': kind, 'from': months[0], 'to': months[-1], 'months': months}


def span_step(span, n, today):
    """Сусідній період для стрілок ‹ ›: на n кроків свого виду — місяць, квартал, рік; «з–по» — на свою довжину. → {'kind', 'from', 'to',
    'value' (= from)} або False: місяця цілком поза MIN_MONTH … поточний місяць немає, кварталу й року (круг 5б) — поза MIN_MONTH … останній
    закритий (screen_span: у них поточного неповного місяця немає); «з–по» — коли хоч частина поза MIN_MONTH … поточний (стрілка не вкорочує
    період)."""
    kind = span['kind']
    cur = max(month_first(today), MIN_MONTH)
    if kind == 'range':
        size = len(month_list(span['from'], span['to']))
        lo, hi = add_months(span['from'], n * size), add_months(span['to'], n * size)
        ok = lo >= MIN_MONTH and hi <= cur
    else:
        top = cur
        if kind == 'month':
            lo = hi = add_months(span['from'], n)
        elif kind == 'quarter':
            lo = add_months(quarter_first(span['from']), 3 * n)
            hi = add_months(lo, 2)
            top = last_closed(today)
        else:
            lo = '%04d-01-01' % (int(span['from'][0:4]) + n)
            hi = lo[0:4] + '-12-01'
            top = last_closed(today)
        ok = hi >= MIN_MONTH and lo <= top
    return {'kind': kind, 'from': lo, 'to': hi, 'value': lo} if ok else False


def range_text(frm, to, lang='uk'):
    """Місяці періоду всередині фрази: «вересень 2026» (один), «січень — вересень 2026» (один рік), «грудень 2026 — березень 2027»."""
    a, b = month_info(frm, lang), month_info(to, lang)
    if month_first(frm) == month_first(to):
        return b['title_in']
    return '%s — %s' % (a['name'] if frm[0:4] == to[0:4] else a['title_in'], b['title_in'])


def span_info(span, lang='uk'):
    """Підписи періоду → {'kind', 'from', 'to', 'months', 'title' (кнопка: «Вересень 2026», «III квартал 2026», «2026 рік», «Січень —
    вересень 2026»), 'title_in' (усередині фрази: «Рядки за січень — вересень 2026»), 'text' (під перемикачем: «за період: січень —
    вересень 2026»; місяць — ''), 'trend' (підказка стрілки плитки: до минулого місяця / до попереднього періоду)}."""
    kind, frm, to = span['kind'], span['from'], span['to']
    rng = range_text(frm, to, lang)
    if kind == 'month':
        title = month_info(frm, lang)['title']
    elif kind == 'quarter':
        title = t('span_quarter', lang) % (QUARTER_NAMES[(int(frm[5:7]) - 1) // 3], frm[0:4])
    elif kind == 'year':
        title = t('span_year', lang) % frm[0:4]
    else:
        title = rng[0:1].upper() + rng[1:]
    title_in = title if kind in ('quarter', 'year') else title[0:1].lower() + title[1:]
    return {'kind': kind, 'from': frm, 'to': to, 'months': list(span['months']), 'title': title, 'title_in': title_in,
            'text': '' if kind == 'month' else t('span_text', lang) % rng, 'trend': t('trend_month' if kind == 'month' else 'trend_span', lang)}


def span_choices(data_months, span, today, lang='uk'):
    """Що вибирати в перемикачі періоду: 'months' — місяці з даними й обраний (від нових), 'years' — роки з даними й обраний, 'quarters' —
    I–IV року періоду, 'options' — усі місяці від MIN_MONTH до поточного («з–по», як у C). Круг 5б: квартали й роки — лише ті, де є
    закритий місяць (MIN_MONTH … last_closed; рік останнього закритого — завжди): квартал і рік поточного неповного місяця не беруть."""
    cur = max(month_first(today), MIN_MONTH)
    top = last_closed(today)
    have = sorted({month_first(m) for m in data_months or [] if MIN_MONTH <= month_first(m) <= cur} | {span['from']})
    year = span['from'][0:4]
    quarters = []
    for i, name in enumerate(QUARTER_NAMES):
        lo = '%s-%02d-01' % (year, 3 * i + 1)
        if add_months(lo, 2) >= MIN_MONTH and lo <= top:
            quarters.append({'value': lo, 'title': t('span_quarter', lang) % (name, year)})
    years = sorted({m[0:4] for m in have if m <= top} | {top[0:4]}, reverse=True)
    return {'months': [month_info(m, lang) for m in reversed(have)], 'years': [{'value': y + '-01-01', 'title': t('span_year', lang) % y} for y in years],
            'quarters': quarters, 'options': [month_info(m, lang) for m in month_list(MIN_MONTH, cur)]}


def span_domain(months):
    """Місяці періоду в домені «Рядків розрахунку»: один — ('month', '=', …), як і раніше; кілька — від першого до останнього; жодного —
    нічого (порожній 'in')."""
    months = sorted(months or [])
    if not months:
        return [('month', 'in', [])]
    if len(months) == 1:
        return [('month', '=', months[0])]
    return [('month', '>=', months[0]), ('month', '<=', months[-1])]


def span_band(axis, months):
    """Підсвітка обраного періоду на графіку «Чисто по місяцях»: місяці осі axis, що є в періоді → [перший, останній] номер на осі; жодного
    (період — поточний неповний місяць, якого на осі немає) — False."""
    sel = set(months or [])
    idx = [i for i, m in enumerate(axis or []) if m in sel]
    return [idx[0], idx[-1]] if idx else False


def rows_sum(rows):
    """Рядки однієї лінії (або всіх) за місяці періоду → один рядок періоду: години й гроші SUM_FIELDS — суми (гроші — до цента), «€ год» —
    середньозважене: сума ÷ години зі ставкою, як за місяць (годин немає — None); 'months' — місяці рядків."""
    rows = list(rows or [])
    out = {f: (round(sum(float(r.get(f) or 0) for r in rows), 2) if f in HOURS else eur(sum(cents(r.get(f)) for r in rows))) for f in SUM_FIELDS}
    out['net_per_hour'] = per_hour(out['net'], out['hours_rate'])
    out['remain_per_hour'] = per_hour(out['remain'], out['hours_rate'])
    out['months'] = sorted({iso(r.get('month')) for r in rows if r.get('month')})
    return out


def checks_summary(months, states, lang='uk'):
    """«Звірка» за період: months — місяці періоду, states — {місяць: 'ok' / 'bad'} («Звірка місяця»; звірки не було — немає) →
    {'state' ('ok' — зійшлися всі місяці, інакше 'bad'), 'ok', 'total', 'short' («зійшлося 9 з 9»), 'text' (і місяці з розбіжністю чи без
    звірки)}."""
    states = states or {}
    ok = [m for m in months if states.get(m) == 'ok']
    bad = [m for m in months if states.get(m) == 'bad']
    none = [m for m in months if states.get(m) not in ('ok', 'bad')]
    short = t('v_period', lang) % (len(ok), len(months))
    parts = [short]
    if bad:
        parts.append(t('v_period_bad', lang) % ', '.join(month_info(m, lang)['title_in'] for m in bad))
    if none:
        parts.append(t('v_period_none', lang) % ', '.join(month_info(m, lang)['title_in'] for m in none))
    return {'state': 'ok' if len(ok) == len(months) else 'bad', 'ok': len(ok), 'total': len(months), 'short': short, 'text': '; '.join(parts)}


# ——— круг 5: «Що входить у ZUS і загальні» (слово власника 07.11 «хочу видеть что туда входит») ———
def overhead_breakdown(items, by_analytic):
    """Склад «ZUS і загальних» місяця, який пише перерахунок у workset.profit.overhead (поле breakdown), — щоб вікно «Що входить» не шукало
    рахунки й не розкладало місяць наново: items — рахунки ZUS / PIT-4 місяця роботи (_contrib_items: 'move_id', 'kind', 'amount'),
    by_analytic — {стаття: центи аналітики рахунків ZUS / PIT-4 у її рядку «Офіс»} → {'moves': [[рахунок, вид, сума EUR]], 'office_dup':
    [[стаття, центи]]} (за id; нульових немає)."""
    return {'moves': [[i['move_id'], i['kind'], eur(cents(i['amount']))] for i in sorted(items or [], key=lambda i: i['move_id'])],
            'office_dup': [[int(a), int(c)] for a, c in sorted((by_analytic or {}).items()) if c]}


def zus_groups(items):
    """ZUS і PIT-4 за фірмами: items — рахунки місяців періоду [{'move_id', 'company', 'company_name', 'kind' ('ZUS' / 'PIT-4'), 'amount',
    'labeled'}] → {'rows': [{'company', 'name', 'zus' (лише ZUS), 'pit4', 'total', 'n' (рахунків), 'ids', 'zus_ids', 'pit4_ids'}] (за назвою
    фірми), 'total': те саме за всіма, 'unlabeled': {'n', 'amount', 'ids'} (без мітки місяця — уже в сумі)}; гроші — до цента: рядки = підсумку
    = сумі рахунків."""
    def blank(**kw):
        return dict({'zus': 0, 'pit4': 0, 'total': 0, 'ids': set(), 'zus_ids': set(), 'pit4_ids': set()}, **kw)
    by, tot, unl = {}, blank(), {'amount': 0, 'ids': set()}
    for it in items or []:
        c = cents(it.get('amount'))
        side = 'pit4' if it.get('kind') == 'PIT-4' else 'zus'
        g = by.setdefault(it.get('company') or 0, blank(company=it.get('company') or 0, name=it.get('company_name') or '—'))
        for x in (g, tot):
            x[side] += c
            x['total'] += c
            x['ids'].add(it['move_id'])
            x[side + '_ids'].add(it['move_id'])
        if not it.get('labeled', True):
            unl['amount'] += c
            unl['ids'].add(it['move_id'])

    def done(x):
        return dict(x, zus=eur(x['zus']), pit4=eur(x['pit4']), total=eur(x['total']), n=len(x['ids']), ids=sorted(x['ids']),
                    zus_ids=sorted(x['zus_ids']), pit4_ids=sorted(x['pit4_ids']))
    return {'rows': sorted((done(g) for g in by.values()), key=lambda r: (r['name'], r['company'])), 'total': done(tot),
            'unlabeled': {'n': len(unl['ids']), 'amount': eur(unl['amount']), 'ids': sorted(unl['ids'])}}


def office_split(rows, dup):
    """Рядки «Офіс» одного місяця → частини «Загальних фірм»: rows — [{'id', 'analytic_id', 'card_id', 'amount'}], dup — {стаття: центи
    аналітики рахунків ZUS / PIT-4 у її рядку «Офіс»} (або [[стаття, центи]]). Рядок цілком із них — не входить (office_rows_in), решта —
    сума рядка без цієї аналітики (вона вже в ZUS). → [{'id', 'key' (стаття; картка DKV з метою «офіс» — 'dkv'), 'cents'}]: сума частин =
    загальні місяця («Офіс» − office_dup) до цента."""
    dup = dict(dup.items() if isinstance(dup, dict) else (dup or []))
    kept = set(office_rows_in(rows, dup)['ids'])
    out = []
    for r in rows or []:
        if r['id'] not in kept:
            continue
        a = r.get('analytic_id') or 0
        out.append({'id': r['id'], 'key': a or ('dkv' if r.get('card_id') else 0), 'cents': cents(r.get('amount')) - (dup.get(a, 0) if a else 0)})
    return out


def office_groups(items):
    """Загальні фірм за статтями: items — частини рядків «Офіс» місяців періоду (office_split) → {'rows': [{'key', 'amount', 'n' (рядків
    розрахунку), 'ids'}] (від найбільшої витрати; картки DKV — одним рядком 'dkv'), 'total': {'amount', 'n', 'ids'}}; сума рядків = загальні."""
    by = {}
    for it in items or []:
        g = by.setdefault(it.get('key') or 0, {'key': it.get('key') or 0, 'cents': 0, 'ids': set()})
        g['cents'] += int(it.get('cents') or 0)
        g['ids'].add(it['id'])
    rows = sorted(({'key': g['key'], 'amount': eur(g['cents']), 'n': len(g['ids']), 'ids': sorted(g['ids'])} for g in by.values()),
                  key=lambda r: (r['amount'], str(r['key'])))
    ids = sorted({i for g in by.values() for i in g['ids']})
    return {'rows': rows, 'total': {'amount': eur(sum(g['cents'] for g in by.values())), 'n': len(ids), 'ids': ids}}


def overhead_how(ohs, part=None):
    """«Як поділено» (версія 1 — за годинами табеля): ohs — підсумки місяців [{'zus', 'office', 'hours'}] → {'hours', 'zus', 'office',
    'total', 'zus_ph', 'office_ph', 'per_hour'} (€ на годину — до 4 знаків; годин немає — None) і 'part': частка лінії (або всіх ліній) part —
    {'hours', 'zus', 'office', 'hours_rate', 'screen'} → {'hours', 'zus', 'office', 'total', 'ph' (частка ÷ її години табеля), 'exact'
    (години × ph дають частку до цента: «=», інакше «≈»), круг 5б — 'hours_rate', 'rate_ph' (частка ÷ години зі ставкою — те саме число й
    округлення, що «ZUS і загальні € год» екрана: remain_totals; годин зі ставкою немає — None), 'screen' (число стоїть на екрані, з якого
    клікнули)}; part немає — False."""
    ohs = list(ohs or [])
    hours = round(sum(float(o.get('hours') or 0) for o in ohs), 2)
    zc = sum(cents(x.get('zus')) for x in ohs)
    oc = sum(cents(x.get('office')) for x in ohs)

    def ph(c, h):
        return round(eur(c) / float(h), 4) if h else None
    out = {'hours': hours, 'zus': eur(zc), 'office': eur(oc), 'total': eur(zc + oc), 'zus_ph': ph(zc, hours), 'office_ph': ph(oc, hours),
           'per_hour': ph(zc + oc, hours), 'part': False}
    if part:
        h = round(float(part.get('hours') or 0), 2)
        pz, po = cents(part.get('zus')), cents(part.get('office'))
        rate = ph(pz + po, h)
        hr = round(float(part.get('hours_rate') or 0), 2)
        out['part'] = {'hours': h, 'zus': eur(pz), 'office': eur(po), 'total': eur(pz + po), 'ph': rate,
                       'exact': rate is not None and cents(rate * h) == pz + po,
                       'hours_rate': hr, 'rate_ph': per_hour(eur(pz + po), hr), 'screen': bool(part.get('screen'))}
    return out


# ——— круг 5б: «Рядки» картки лінії за період — склад місяця, який пише перерахунок ———
def card_groups(entries, refs):
    """Склад «Рядків» картки лінії за місяць (перерахунок пише його в рядок лінії — поле card_groups; ті самі записи розкладу, з яких
    числа рядка): записи розкладу (_month_entries: entries і refs), що лягли на лінію, — за групою «Рядків»: перевезення за прибуттями
    ['pool', поле], картка DKV ['card', картка, поле], стаття й рахунок аналітики ['aal', стаття, рахунок, поле] → {лінія: {'groups': [{'k':
    ключ, 'c': центи, 'aal': [id], 'txn': [id]}]}} — групи в порядку першого запису, id без повторів за зростанням; записи не лінії (без
    лінії, компанія) не беруться. Картка за кілька місяців лише складає групи місяців (groups_merge) — не розкладає кожен місяць наново."""
    by = {}
    for e, ref in zip(entries or [], refs or []):
        key = e.get('key') or ()
        if not key or key[0] != 'line' or not key[1]:
            continue
        ref = ref or {}
        if ref.get('pool'):
            k = ('pool', e['field'])
        elif ref.get('txn'):
            k = ('card', ref.get('card') or 0, e['field'])
        else:
            k = ('aal', ref.get('analytic') or 0, ref.get('account') or 0, e['field'])
        g = by.setdefault(key[1], {}).setdefault(k, {'c': 0, 'aal': set(), 'txn': set()})
        g['c'] += cents(e['amount'])
        g['aal'].update(ref.get('aal') or [])
        g['txn'].update(ref.get('txn') or [])
    return {line: {'groups': [{'k': list(k), 'c': g['c'], 'aal': sorted(g['aal']), 'txn': sorted(g['txn'])} for k, g in gs.items()]}
            for line, gs in by.items()}


def groups_merge(months):
    """Групи «Рядків» лінії за кілька місяців: months — [групи місяця (card_groups … ['groups'])] за зростанням місяців → [{'k', 'amount'
    (центи — сума місяців), 'aal', 'txn' (id без повторів за зростанням)}] у порядку першої появи (той самий порядок, що в розкладі місяців
    підряд): «Рядки» за період = сума «Рядків» місяців по одному."""
    out = {}
    for groups in months or []:
        for g in groups or []:
            x = out.setdefault(tuple(g['k']), {'k': list(g['k']), 'amount': 0, 'aal': set(), 'txn': set()})
            x['amount'] += int(g.get('c') or 0)
            x['aal'].update(g.get('aal') or [])
            x['txn'].update(g.get('txn') or [])
    return [dict(x, aal=sorted(x['aal']), txn=sorted(x['txn'])) for x in out.values()]


def groups_kinds(groups):
    """Записи кожного виду лінії з груп «Рядків» (groups_merge) → {поле: {'aal': [id], 'txn': [id]}} — те саме, що kind_refs розкладу для
    однієї лінії (крок водоспаду й сегмент відкривають ті самі записи, що група)."""
    out = {}
    for g in groups or []:
        x = out.setdefault(g['k'][-1], {'aal': set(), 'txn': set()})
        x['aal'].update(g.get('aal') or [])
        x['txn'].update(g.get('txn') or [])
    return {f: {'aal': sorted(x['aal']), 'txn': sorted(x['txn'])} for f, x in out.items()}
