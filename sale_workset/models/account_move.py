from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    hide_qty_price = fields.Boolean(default=True)
    object_id = fields.Many2one('res.partner')
    execution_start_date = fields.Date()
    execution_end_date = fields.Date()
