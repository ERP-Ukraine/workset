# -*- coding: utf-8 -*-
{
    'name': 'Прибутковість ліній',
    'summary': 'Лінія × місяць: години, частка WorkSET зі ставок, житло, авто, паливо, інструмент, перевезення — до «чисто» €/міс і €/год',
    'description': """
Хранимий підсумок «лінія × місяць»: години з табелів, частка WorkSET зі «Ставок» на кожну дату, мінус наші витрати лінії (житло, авто,
паливо, інструмент, перевезення людей) — чисто за місяць і за годину. Поруч — що заплачено з грошей лінії (партнер) і з фонду працівників,
буфер житла довідково; рядки компанії — «Не розподілено» за статтями, «Офіс», «Власник». Нижче «чисто» — − ZUS і PIT-4 (рахунки за місяцем
роботи) − загальні фірм, розподілені за годинами табеля, = «Залишається WorkSET». Екрани — за місяць, квартал, рік або період; по
кліку на «ZUS і загальні» — що туди входить (рахунки за фірмами, загальні за статтями).
Розмітка аналітики (стаття, вузол плану, рахунок, контрагент → вид і лінія), «Хто платить» по лініях з датами, картки DKV з періодами й
транзакції (брутто EUR), перевезення людей між лініями за прибуттями. Перерахунок — кнопкою, щоночі й інструментом; звірка з аналітикою
до цента. Дані (розмітка, правила, картки, транзакції) пише інструмент штабу через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Accounting',
    # 19.0.4 (круг 5) — поле workset.profit.overhead.breakdown (склад для вікна «Що входить»): -u і перерахунок місяців;
    # 19.0.5 (круг 5б) — поле workset.profit.month.card_groups (склад «Рядків» картки лінії за період): -u і --recalc
    'version': '19.0.5.0.0',
    # workset_rates — лінії, ставки (_plan_at), житло ліній (_housing_at), периметр фірм; через нього — account (аналітика, рахунки),
    # hr_timesheet (табелі), project, web (Chart.js для екранів). fleet — машина картки DKV (додаток «Автопарк» на проді встановлений)
    'depends': ['workset_rates', 'fleet'],
    'data': [
        'security/workset_profit_security.xml',
        'security/ir.model.access.csv',
        'views/workset_profit_month_views.xml',
        'views/workset_line_cost_rule_views.xml',
        'views/workset_dkv_views.xml',
        'views/workset_profit_map_views.xml',
        'views/workset_profit_screens_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    # круг 2: екрани «Огляд», картка лінії, «Лінії × місяці» (client actions OWL)
    'assets': {
        'web.assets_backend': [
            'workset_profit/static/src/scss/workset_profit.scss',
            'workset_profit/static/src/js/profit_screens.js',
            'workset_profit/static/src/xml/profit_screens.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
