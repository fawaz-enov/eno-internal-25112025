from odoo import models, fields

class ProductTemplateInherit(models.Model):
    _inherit = 'product.template'

    is_charging = fields.Boolean(string='Extra Charge Product', default=False)
