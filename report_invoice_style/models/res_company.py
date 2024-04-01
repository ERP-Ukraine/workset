from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    modify_invoice = fields.Boolean()
