# -*- coding: utf-8 -*-
"""«Звірка місяця» (М3, круг 2): підсумок звірки, який пише перерахунок (profit_recompute) і кнопка «Звірити». Екрани «Огляд» і «Лінії ×
місяці» показують його («зійшлося» / «розбіжність» і текст), а не звіряють аналітику наново на кожен показ. Один запис на місяць; пише
лише код (права моделі), бачить «Перегляд»."""
from odoo import fields, models

CHECK = 'workset.profit.check'


class WorksetProfitCheck(models.Model):
    _name = CHECK
    _description = 'Звірка місяця'
    _order = 'month desc, id desc'
    _rec_name = 'month'

    month = fields.Date(string='Місяць', required=True, index=True, readonly=True, help='Перше число місяця')
    ok = fields.Boolean(string='Зійшлося', readonly=True)
    text = fields.Text(string='Текст звірки', readonly=True)
    checked_at = fields.Datetime(string='Звірено', readonly=True)
