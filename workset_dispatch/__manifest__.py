# -*- coding: utf-8 -*-
{
    'name': 'Виїзди',
    'summary': "У день виїзду з Pipedrive заводиться «Виїзд» (заявка підбору); Юля одним кліком обирає фірму — і створюється картка працівника",
    'description': """
«Виїзд» — стандартна заявка підбору (hr.applicant), яку в день «Дата відправки» заводить інструмент штабу (метод workset_dispatch_import).
Модуль не ходить у мережу і не зберігає ключів. Повтори (той самий контакт, телефон, схоже ім'я) шукаються серед усіх карток усіх фірм;
фірма для ZUS обирається плашкою; картка створюється без договору й ставки, Олі йде задача «Договір і ставка».
Дані (параметри, фірми, виконавці) пише інструмент штабу через API — у модулі лише устрій.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Human Resources',
    'version': '19.0.1.0.0',
    # hr — картки й групи кадрів; hr_recruitment — заявки й етапи підбору; mail — задачі (activity) і чат
    'depends': ['hr', 'hr_recruitment', 'mail'],
    'data': [
        'data/stages.xml',
        'views/applicant_views.xml',
        'views/employee_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
