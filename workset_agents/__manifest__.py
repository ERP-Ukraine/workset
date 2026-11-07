# -*- coding: utf-8 -*-
{
    'name': 'Агенти',
    'summary': 'Агенти з підбору кандидатів і посередники замовників: домовленості, нарахування, рішення про виплату, виплати, звірка з конвертом',
    'description': """
Агенти — одне місце вводу: агент (контакт), домовленості з кандидатів і з замовників, люди агента. Щоночі — нарахування «€ за годину»
за оплаченими рахунками замовників, сторно, «Виплачено» за рахунком і журналом переказів; рішення про виплату затверджує власник.
Звірка розрахунку Odoo з конвертом агента по місяцях. Факти (фірми, периметр, журнали, аналітика, учасники) — системні параметри,
їх пише інструмент через API; у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Human Resources',
    'version': '19.0.1.0.0',
    # hr — групи кадрів і «Привів агент» на працівнику; account — рахунки, виписки, група бухгалтерії; project, hr_timesheet — табелі рахунків;
    # mail — чат і активності; workset_rates — лінії workset.line (і через нього sale_timesheet: карта проєктів, табелі рахунку);
    # uom — одиниця «Години» рядків рахунку (є й через account; явно — бо модуль кличе env.ref на неї)
    'depends': ['hr', 'account', 'project', 'hr_timesheet', 'mail', 'uom', 'workset_rates'],
    'data': [
        'security/workset_agents_security.xml',
        'security/ir.model.access.csv',
        'views/agent_views.xml',
        'views/deal_people_views.xml',
        'views/member_views.xml',
        'views/deal_client_views.xml',
        'views/accrual_views.xml',
        'views/payout_views.xml',
        'views/decision_views.xml',
        'views/decision_line_views.xml',
        'views/zvirka_views.xml',
        'views/tax_views.xml',
        'views/run_views.xml',
        'views/paid_wizard_views.xml',
        'views/hr_employee_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
