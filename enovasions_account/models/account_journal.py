# -*- coding: utf-8 -*-
from odoo import api, fields, models

import logging
_logger = logging.getLogger(__name__)


class VmsPaymentType(models.Model):
    _name = 'vms.payment.type'
    _description = 'Payment Type'
    _rec_name = 'display_name'

    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    payment_type = fields.Selection([
        ('0', 'Other'),
        ('1', 'Cash'),
        ('2', 'Card'),
        ('3', 'Check'),
        ('4', 'Wire Transfer'),
        ('5', 'Voucher'),
        ('6', 'MobileMoney')
    ], string='Payment Type')
    active = fields.Boolean(string='Is Active?', default=True)

    @api.depends('payment_type')
    def _compute_display_name(self):
        for rec in self:
            payment_type = rec.payment_type
            if payment_type:
                payment_type_label = dict(self.fields_get(['payment_type'])['payment_type']['selection']).get(payment_type)
                if payment_type_label:
                    rec.display_name = payment_type_label


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    allowed_vms_payment_type = fields.Many2many('vms.payment.type', string='Allowed Payment Type')
