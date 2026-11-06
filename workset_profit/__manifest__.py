# -*- coding: utf-8 -*-
{
    'name': 'Прибутковість ліній',
    'summary': 'Лінія × місяць: години, частка WorkSET зі ставок, житло, авто, паливо, інструмент, перевезення — до «чисто» €/міс і €/год',
    'description': """
Хранимий підсумок «лінія × місяць»: години з табелів, частка WorkSET зі «Ставок» на кожну дату, мінус наші витрати лінії (житло, авто,
паливо, інструмент, перевезення людей) — чисто за місяць і за годину. Поруч — що заплачено з грошей лінії (партнер) і з фонду працівників,
буфер житла довідково; рядки компанії — «Не розподілено» за статтями, «Офіс», «Власник».
Розмітка аналітики (стаття, вузол плану, рахунок, контрагент → вид і лінія), «Хто платить» по лініях з датами, картки DKV з періодами й
транзакції (брутто EUR), перевезення людей між лініями за прибуттями. Перерахунок — кнопкою, щоночі й інструментом; звірка з аналітикою
до цента. Дані (розмітка, правила, картки, транзакції) пише інструмент штабу через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Accounting',
    'version': '19.0.1.0.0',
    # workset_rates — лінії, ставки (_plan_at), житло ліній (_housing_at), периметр фірм; через нього — account (аналітика, рахунки),
    # hr_timesheet (табелі), project. fleet — машина картки DKV (додаток «Автопарк» на проді встановлений)
    'depends': ['workset_rates', 'fleet'],
    'data': [
        'security/workset_profit_security.xml',
        'security/ir.model.access.csv',
        'views/workset_profit_month_views.xml',
        'views/workset_line_cost_rule_views.xml',
        'views/workset_dkv_views.xml',
        'views/workset_profit_map_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
