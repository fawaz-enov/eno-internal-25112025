# -*- coding: utf-8 -*-
from odoo import api, fields, models

import logging
_logger = logging.getLogger(__name__)


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    allowed_vms_payment_type_ids = fields.Many2many(
        'vms.payment.type',
        string='Allowed Payment Types',
        compute='_compute_allowed_payment_types',
        store=False
    )
    vms_payment_type = fields.Many2one('vms.payment.type', string='Payment Type')

    @api.onchange('journal_id')
    def _onchange_vms_payment_type(self):
        journal = self.journal_id
        if journal and journal.allowed_vms_payment_type:
            self.allowed_vms_payment_type_ids = journal.allowed_vms_payment_type
        else:
            self.allowed_vms_payment_type_ids = [(5, 0, 0)]

    @api.depends('journal_id')
    def _compute_allowed_vms_payment_types(self):
        for rec in self:
            journal = rec.journal_id
            if journal and journal.allowed_vms_payment_type:
                rec.allowed_vms_payment_type_ids = journal.allowed_vms_payment_type
            else:
                rec.allowed_vms_payment_type_ids = [(5, 0, 0)]

    def _create_payment_vals_from_wizard(self, batch_result):
        payment_vals = super(AccountPaymentRegister, self)._create_payment_vals_from_wizard(batch_result)
        if self.vms_payment_type:
            payment_vals.update({'vms_payment_type': self.vms_payment_type.id})
        return payment_vals

    def _create_payment_vals_from_batch(self, batch_result):
        payment_vals = super(AccountPaymentRegister, self)._create_payment_vals_from_batch(batch_result)
        if self.vms_payment_type:
            payment_vals.update({'vms_payment_type': self.vms_payment_type.id})
        return payment_vals


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    vms_payment_type = fields.Many2one('vms.payment.type', string='Payment Type')
