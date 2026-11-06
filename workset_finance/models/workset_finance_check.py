# -*- coding: utf-8 -*-
"""«Звірка» (Фінанси, круг 1): підсумок звірки місяця, який пише перерахунок (finance_recompute). Екрани показують його («зійшлося» /
«не зійшлася» і текст), а не звіряють наново на кожен показ. Один запис на місяць (унікальність month в базі; пише _store_check під замком
місяця); пише лише код (права моделі), бачить «Власник»."""
from odoo import fields, models

CHECK = 'workset.finance.check'


class WorksetFinanceCheck(models.Model):
    _name = CHECK
    _description = 'Фінанси: звірка місяця'
    _order = 'month desc, id desc'
    _rec_name = 'month'

    month = fields.Date(string='Місяць', required=True, index=True, readonly=True, help='Перше число місяця')
    ok = fields.Boolean(string='Зійшлося', readonly=True)
    text = fields.Text(string='Текст звірки', readonly=True)
    checked_at = fields.Datetime(string='Звірено', readonly=True)

    # Odoo 19: обмеження бази — models.Constraint (не _sql_constraints)
    _month_uniq = models.Constraint('UNIQUE(month)', 'Звірка цього місяця вже є.')
