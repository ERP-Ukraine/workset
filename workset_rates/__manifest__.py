# -*- coding: utf-8 -*-
{
    'name': 'Ставки',
    'summary': 'Погодинні ставки працівників у замовників: одна ставка на всі фірми, історія, вартість години в проєктах, звірка',
    'description': """
Ставка «працівник × замовник × Ставка з» — одна на всі фірми, з історією. При збереженні «Вартість години» ставиться в рядки карти
проєктів цього замовника в усіх фірмах; щоночі майбутні ставки стають діючими; щопонеділка — звірка з однією задачею.
Середні в рядках груп списку, полоса підсумків над списком, ліва панель без порожніх значень.
Дані (лінії, периметр фірм, редактори) пише інструмент через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Human Resources',
    'version': '19.0.1.0.0',
    # hr — картки й групи кадрів; account — група бухгалтерії й customer_rank; project — група адміністраторів проєктів і форма проєкту;
    # sale_timesheet — карта проєктів (project.sale.line.employee.map) і табелі (через hr_timesheet; hourly_cost — hr_hourly_cost).
    # hourly_wage версії договору (hr_payroll, Enterprise) читається м'яко: поля немає — «У договорі» 0
    'depends': ['hr', 'account', 'project', 'sale_timesheet'],
    'data': [
        'security/workset_rates_security.xml',
        'security/ir.model.access.csv',
        'views/workset_rate_views.xml',
        'views/workset_line_views.xml',
        'views/workset_rate_check_views.xml',
        'views/workset_rate_avg_views.xml',
        'views/project_map_views.xml',
        'views/smart_button_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'workset_rates/static/src/scss/workset_rates.scss',
            'workset_rates/static/src/js/rates_summary.js',
            'workset_rates/static/src/xml/rates_summary.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
