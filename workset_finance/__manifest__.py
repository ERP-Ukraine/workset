# -*- coding: utf-8 -*-
{
    'name': 'Фінанси',
    'summary': 'Екрани фінансів за ролями: «Власник» — драбина від доходу за документами до чистого прибутку, власники, II етап, неоплачено',
    'description': """
Хранимий підсумок «місяць драбини» (з січня 2026): рекрутинг за документами, власники в P&L, дохід без аналітики (поправки), Україна —
до часток; частки партнерів (поправки з підставою) — чистий прибуток; за грошима (перекази партнерам нетто); окремо власники, «II етап»,
неоплачено замовниками й Energy, посередництво, перекази на особисті рахунки, якість даних. Кожне число має пометку: факт / розрахунок /
поправка. Перерахунок — кнопкою, щоночі й інструментом штабу; звірка з аналітикою і проводками до цента. Параметри й склад груп пише
інструмент штабу через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Accounting',
    'version': '19.0.1.0.0',
    # workset_rates — периметр фірм (_skip_companies) і лінії (workset.line); account, analytic — проводки й аналітика; hr — розділ кадрів
    # форми користувача для привілею; mail — листування поправок
    'depends': ['workset_rates', 'account', 'hr', 'analytic', 'mail'],
    'data': [
        'security/workset_finance_security.xml',
        'security/ir.model.access.csv',
        'views/workset_finance_month_views.xml',
        'views/workset_finance_item_views.xml',
        'views/workset_finance_adjust_views.xml',
        'views/workset_finance_check_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
