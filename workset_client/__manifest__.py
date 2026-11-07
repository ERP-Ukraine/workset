# -*- coding: utf-8 -*-
{
    'name': 'Умови клієнтів',
    'summary': 'Картка замовника: гроші за годину, хто платить, агент, договір і оплата, що з цього виходить, останні зміни — з джерел, нічого не забувається',
    'description': """
Умови кожного замовника на одному екрані (слово власника 07.11: «карточка, где эти условия будут прописаны и понятны, чтобы ничего не
забывалось»). Картка — представлення: ставки, загальна ставка лінії × замовника, житло — із «Ставок»; «Хто платить», картки DKV, рядки
«лінія × місяць» — із «Прибутковості»; агенти — з модуля «Агенти», якщо він є. Показує, що діє сьогодні після перекриття «замовник
точніший за лінію» і звідки це взято; кожне число відкриває своє джерело, правка — там, де правлять зараз. Своє — лише те, чого ніде
немає: договір (номер, дата, скан), хто погоджує табель, контакт і адреса об'єкта, відсоток за виведення коштів (довідково), особливі
домовленості, коли й ким перевірено; строк оплати — стандартне поле контакту. Список «Умови клієнтів» — з фільтрами «Без договору»,
«Без терміну оплати», «Не перевірено».
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Sales',
    'version': '19.0.1.0.0',
    # workset_rates — ставки, загальні ставки, житло ліній, лінії (і через нього account: строк оплати контакту, project — проєкти
    # замовника); workset_profit — «Хто платить», картки DKV, рядки «лінія × місяць», calc; mail — історія змін своїх полів.
    # «Агенти» (workset_agents) — м'яко: модель workset.agent.deal.client є — читається, немає — «модуль агентів не встановлено»
    'depends': ['workset_rates', 'workset_profit', 'mail'],
    'data': [
        'security/workset_client_security.xml',
        'security/ir.model.access.csv',
        'views/client_terms_views.xml',
        'views/res_partner_views.xml',
        'views/client_screen_views.xml',
        'views/menus.xml',
    ],
    # екран «Умови клієнта» (client action OWL) і рядок списку → екран (js_class workset_client_list)
    'assets': {
        'web.assets_backend': [
            'workset_client/static/src/scss/workset_client.scss',
            'workset_client/static/src/js/client_card.js',
            'workset_client/static/src/xml/client_card.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
