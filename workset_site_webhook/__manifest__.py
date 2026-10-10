# -*- coding: utf-8 -*-
{
    'name': 'WorkSET Site Webhook',
    'summary': 'Notify the website build service after each committed change of hr.job (site vacancies)',
    'description': """
Після коміту створення / зміни / видалення позиції hr.job надсилає POST на сервіс пересборки сайту
(один виклик на транзакцію, у фоновому потоці, помилки лише в лог).

Параметри (Налаштування → Технічні → Системні параметри):
  workset_site_webhook.url    — адреса сервісу, напр. http://2.28.64.19:8787/rebuild (порожньо = вимкнено)
  workset_site_webhook.token  — секрет, іде в заголовку x-hook-token

Нейтралізовані копії бази (staging / dev на Odoo.sh) вебхук не надсилають.
""",
    'author': 'WorkSET',
    'website': 'https://workset.com.ua',
    'license': 'LGPL-3',
    'category': 'Human Resources/Recruitment',
    'version': '1.0',
    'depends': ['hr'],
    'data': [],
    'installable': True,
    'auto_install': False,
    'application': False,
}
