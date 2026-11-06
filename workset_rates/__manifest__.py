# -*- coding: utf-8 -*-
{
    'name': 'Ставки',
    'summary': 'Погодинні ставки працівників у замовників: одна ставка на всі фірми, історія, вартість години в проєктах, звірка',
    'description': """
Ставка «працівник × замовник × Ставка з» — одна на всі фірми, з історією. При збереженні «Вартість години» ставиться в рядки карти
проєктів цього замовника в усіх фірмах; щоночі майбутні ставки стають діючими; щопонеділка — звірка з однією задачею.
Середні в рядках груп списку, полоса підсумків над списком, ліва панель без порожніх значень.
Ланцюжок цін: загальні ставки лінії й тарифи замовників з датами (ставка їх наслідує, «Своя» — з причиною, «Застосувати з дати»),
ціна клієнта й частка лінії, посередник і агент людей, бонуси офісу й чиста частка, фікс за місяць, тимчасові зміни з доплатою по місяцях.
«Договір → база»: нова версія договору дає нову ставку з її дати (позначка «Тимчасово (не база)» — ні), щоночі — догон і страж «вгору і назад».
Житло ліній: як кожна лінія рахує житло людей (правило лінії або замовника з датами) — для прибутковості й розкладу оплат.
«Огляд ставок»: плитки, картки ліній, графіки по місяцях і тривоги на одній сторінці; «Стрічка» — ставки смугами в часі.
Дані (лінії, правила, бонуси, периметр фірм, редактори) пише інструмент через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Human Resources',
    'version': '19.0.4.1.0',
    # hr — картки й групи кадрів; account — група бухгалтерії й customer_rank; project — група адміністраторів проєктів і форма проєкту;
    # sale_timesheet — карта проєктів (project.sale.line.employee.map) і табелі (через hr_timesheet; hourly_cost — hr_hourly_cost).
    # hourly_wage версії договору (hr_payroll, Enterprise) читається м'яко: поля немає — «У договорі» 0.
    # web_gantt (Enterprise) — вид «Стрічка» (тип gantt є лише з ним)
    'depends': ['hr', 'account', 'project', 'sale_timesheet', 'web_gantt'],
    'data': [
        'security/workset_rates_security.xml',
        'security/ir.model.access.csv',
        'views/workset_rate_views.xml',
        'views/workset_rate_edit_views.xml',
        'views/workset_line_views.xml',
        'views/workset_rate_rule_views.xml',
        'views/workset_rate_raise_views.xml',
        'views/workset_office_bonus_views.xml',
        'views/workset_line_housing_views.xml',
        'views/workset_rate_check_views.xml',
        'views/workset_rate_avg_views.xml',
        'views/project_map_views.xml',
        'views/smart_button_views.xml',
        'views/hr_version_views.xml',
        'views/workset_rate_overview_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
        'data/mail_activity_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'workset_rates/static/src/scss/workset_rates.scss',
            'workset_rates/static/src/js/rates_summary.js',
            'workset_rates/static/src/xml/rates_summary.xml',
            'workset_rates/static/src/js/rates_format.js',
            'workset_rates/static/src/js/rates_overview.js',
            'workset_rates/static/src/xml/rates_overview.xml',
            'workset_rates/static/src/js/talk_who_field.js',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
