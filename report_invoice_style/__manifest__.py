{
    'name': 'Report invoice style',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'category': 'Other',
    'version': '1.0',
    'depends': ['account', 'base'],
    'data': [
        'views/report_invoice.xml',
        'views/res_company_views.xml',
        ],

    'auto_install': False,
    'installable': True,
    'application': False,
}
