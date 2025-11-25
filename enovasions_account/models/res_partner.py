# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError  # <-- import explicitly

class ResPartnerInherit(models.Model):
    _inherit = 'res.partner'

    charge_customer = fields.Boolean(string='Charge Customer', default=False)
    partner_type = fields.Selection(
        [('domestic', 'Domestic'), ('international', 'International')],
        default='domestic', string="Type"
    )
    cust_ref_num = fields.Char(string="Customer No")

    @api.model_create_multi
    def create(self, vals_list):
        # vals_list is a list of dicts
        try:
            immediate_term_id = self.env.ref('account.account_payment_term_immediate').id
        except ValueError:
            immediate_term_id = False

        for vals in vals_list:
            if not vals.get('charge_customer') and not vals.get('property_payment_term_id'):
                if immediate_term_id:
                    vals['property_payment_term_id'] = immediate_term_id
        return super().create(vals_list)

    def write(self, vals):
        # vals is a single dict for all records
        # If toggling charge_customer to False and no term provided, set Immediate
        if ('charge_customer' in vals and not vals['charge_customer']
                and not vals.get('property_payment_term_id')):
            vals = dict(vals)  # avoid mutating the original dict
            try:
                vals['property_payment_term_id'] = self.env.ref(
                    'account.account_payment_term_immediate'
                ).id
            except ValueError:
                pass
        return super().write(vals)

    @api.constrains('charge_customer', 'property_payment_term_id')
    def _check_payment_term_if_charged(self):
        for rec in self:
            if rec.charge_customer and not rec.property_payment_term_id:
                raise ValidationError(_("Please select a Payment Term for a Charge Customer."))