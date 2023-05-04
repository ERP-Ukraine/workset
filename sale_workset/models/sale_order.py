from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    object_id = fields.Many2one('res.partner')
    execution_start_date = fields.Date()
    execution_end_date = fields.Date()

    def _prepare_invoice(self):
        invoice_vals = super()._prepare_invoice()
        invoice_vals.update({
            'object_id': self.object_id.id,
            'execution_start_date': self.execution_start_date,
            'execution_end_date': self.execution_end_date,
        })
        return invoice_vals
