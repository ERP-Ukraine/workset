# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    workset_dispatch_firm = fields.Boolean(string='Фірма для ZUS', help='Фірма, у якій Юля може оформити людину з Виїзду (одна з трьох плашок вибору)')
    workset_name_suffix = fields.Char(string='Позначка в імені', help='Додається до імені нової картки: «ПРІЗВИЩЕ ІМ\'Я | позначка»')
