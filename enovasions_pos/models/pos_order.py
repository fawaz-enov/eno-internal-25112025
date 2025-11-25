# -*- coding: utf-8 -*-
from odoo import api, exceptions, fields, models, _
from odoo.exceptions import ValidationError
from urllib3 import PoolManager
from urllib3.contrib import pyopenssl  # kept if you rely on it elsewhere
from datetime import datetime, timezone
import requests
import base64
import json
import ssl
import os
import io
from odoo.exceptions import UserError
from PIL import Image
import pytz
from dateutil import parser
import sys
from odoo.tools import float_is_zero
import logging
_logger = logging.getLogger(__name__)


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def action_create_payments(self):
        result = super().action_create_payments()
        invoices = self.line_ids.mapped('move_id').filtered(lambda m: m.move_type == 'out_invoice')
        if invoices:
            payments = self.env['account.payment'].search([
                ('invoice_ids', 'in', invoices.ids),
                ('state', '=', 'in_process')
            ])
            for payment in payments:
                payment.action_validate()
        return result


class AccountMoveInherit(models.Model):
    _inherit = 'account.move'

    post_response = fields.Text("Post response", readonly=True, copy=False)
    post_response_json = fields.Json(string="Post Response JSON", compute="_compute_post_response_json", store=False, copy=False)
    qr_code = fields.Image(string="QR Code", attachment=True, store=True, copy=False, readonly=True)
    is_post_status = fields.Boolean(default=False, string="Post Status", copy=False)
    # ****** Copy fields
    copy_post_response = fields.Text("Post Copy Response", readonly=True, copy=False)
    copy_post_response_json = fields.Json(string="Post Copy Response JSON", compute="_compute_post_copy_response_json", store=False, copy=False)
    copy_qr_code = fields.Image(string="QR Code", attachment=True, store=True, copy=False, readonly=True)
    is_copy_post_status = fields.Boolean(default=False, string="Copy Post Status", copy=False)
    # ****** END Copy fields
    system_id = fields.Many2one("branch.systems", string="System", required=True, copy=False)
    invoice_type = fields.Selection([
        ('normal', 'Normal'),
        ('proforma', 'ProForma'), ('copy', 'Copy'), ('training', 'Training'), ('advance', 'ADVANCE')],
        default='normal', string="Invoice Type")
    transaction_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('refund', 'Refund')],
        default='invoice', string="Transaction Type")
    ref_doc_num = fields.Text("Reference Doc No", copy=False, compute="_compute_ref_doc_fields", store=True)
    ref_doc_date = fields.Datetime("Reference Doc Date", copy=False, compute="_compute_ref_doc_fields", store=True)
    origin_doc_num = fields.Text("SDC Invoice No", copy=False)
    origin_doc_date = fields.Datetime("SDC Time", copy=False)
    copy_ref_doc_num = fields.Text("Copy Reference Doc No", copy=False)
    show_send_request_btn = fields.Boolean(compute='_compute_show_send_button', store=True)
    buyer_cost_centerid = fields.Char(string="Buyer Cost CenterId")
    order_type = fields.Selection([
        ('quotation', 'Quotation'),
        ('proforma', 'Proforma'),
        ('training', 'Training'),
        ('advance', 'Advance')
    ], default='quotation', string="Order Type")

    @api.depends('is_post_status', 'state')
    def _compute_show_send_button(self):
        for rec in self:
            rec.show_send_request_btn = (rec.state == 'posted' and rec.is_post_status is False)

    @api.onchange('state', 'is_post_status')
    def _onchange_show_send_button(self):
        for rec in self:
            rec.show_send_request_btn = (rec.state == 'posted' and rec.is_post_status is False)

    def action_post(self):
        res = super(AccountMoveInherit, self).action_post()
        self._onchange_show_send_button()
        return res

    @api.depends('post_response')
    def _compute_ref_doc_fields(self):
        for rec in self:
            if rec.post_response:
                try:
                    response_data = json.loads(rec.post_response)
                except Exception:
                    response_data = {}
                sdc_datetime_str = response_data.get('sdcDateTime')
                if sdc_datetime_str:
                    sdc_datetime_obj = datetime.fromisoformat(sdc_datetime_str)
                    sdc_datetime_utc = sdc_datetime_obj.astimezone(timezone.utc)
                    rec.ref_doc_date = sdc_datetime_utc.replace(tzinfo=None) or False
                rec.ref_doc_num = response_data.get('invoiceNumber', False)

    @api.depends('post_response')
    def _compute_post_response_json(self):
        for record in self:
            try:
                record.post_response_json = json.loads(record.post_response) if record.post_response else {}
            except (json.JSONDecodeError, TypeError):
                record.post_response_json = {}

    def _reverse_moves(self, default_values_list=None, cancel=False):
        self = self.with_context(_reverse_move=True)
        moves = super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)
        for original_move, reversed_move in zip(self, moves):
            reversed_move.system_id = original_move.system_id.id
            reversed_move.order_type = original_move.order_type
            reversed_move.buyer_cost_centerid = original_move.buyer_cost_centerid
            reversed_move.ref_doc_num = original_move.ref_doc_num
            reversed_move.ref_doc_date = original_move.ref_doc_date
        return moves

    def action_generate_qr(self):
        for record in self:
            qr_code_base64 = None
            if record.post_response:
                try:
                    response_data = json.loads(record.post_response)
                    qr_code_base64 = response_data.get("verificationQRCode")
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON format in post_response")
            if qr_code_base64:
                if qr_code_base64.startswith("data:image"):
                    qr_code_base64 = qr_code_base64.split(",")[1]
                try:
                    qr_code_base64 = qr_code_base64.strip()
                    decoded_qr = base64.b64decode(qr_code_base64)
                    image = Image.open(io.BytesIO(decoded_qr))
                    img_io = io.BytesIO()
                    image.save(img_io, format="PNG")
                    img_io.seek(0)
                    record.qr_code = base64.b64encode(img_io.getvalue())
                except Exception as e:
                    record.qr_code = False
                    raise ValueError(f"Error Decoding Base64: {str(e)}")
            else:
                record.qr_code = False
                raise ValueError("No QR Code Found in Response")

    # === FIX: count POS payments & rounding; don't rely solely on matched_payment_ids ===
    def _check_full_payment(self):
        for record in self:
            if record.move_type not in ('out_invoice', 'out_refund'):
                return True

            rounding = record.currency_id.rounding or record.company_id.currency_id.rounding or 0.01
            total_paid = 0.0

            # POS-origin payments (invoice created from POS order)
            if record.pos_order_ids:
                total_paid += sum(record.pos_order_ids.mapped('payment_ids.amount'))

            # Account payments directly on move (posted)
            if record.payment_ids:
                total_paid += sum(record.payment_ids.filtered(lambda p: p.state == 'posted').mapped('amount'))

            # Reconciled (fallback)
            if record.matched_payment_ids:
                total_paid += sum(record.matched_payment_ids.filtered(lambda p: p.state == 'paid').mapped('amount'))

            # Fully paid if residual ~ 0 OR collected >= total (with tolerance)
            if float_is_zero(record.amount_residual, precision_rounding=rounding):
                return True
            if (record.amount_total - total_paid) <= rounding:
                return True

            return False

    def action_send_request(self):
        for record in self:
            if not record.system_id:
                raise ValidationError(_("Please configure the required Branch Certification."))

            # Enforce "must be fully paid when not a charge customer"
            if not self._check_full_payment():
                if not getattr(record.partner_id, 'charge_customer', False):
                    raise ValidationError(_('The invoice has not been fully paid. Please complete the full payment to proceed'))

            _logger.info("record.system_id.pfx_status: %s", record.system_id.pfx_status)
            if record.system_id.pfx_status is True:
                if record.move_type in ['out_invoice', 'out_refund']:
                    # Certificates
                    path = record.system_id.pfx_file_path       

                    cert_file = os.path.join(os.path.dirname(path), "certificate.pem")
                    key_file = os.path.join(os.path.dirname(path), "private_key.pem")

                    print("cert_file",cert_file)
                    print("key_file",key_file)

                    password = record.system_id.pfx_password
                    pac_value = record.system_id.pfx_pac
                    pfx_expiry_date = record.system_id.pfx_expiry_date

                    if not cert_file or not key_file or not password or not pac_value:
                        raise ValidationError(_("Please configure the required certification in Branch Systems."))
                    if pfx_expiry_date and pfx_expiry_date < datetime.now():
                        raise ValidationError(_("Certification in Branch Systems is expired."))

                    # Types
                    if record.order_type == 'advance':
                        invoiceType = "Advance"
                    elif record.order_type == 'training':
                        invoiceType = "Training"
                    else:
                        invoiceType = "Normal"

                    transactionType = "Sale" if record.move_type == 'out_invoice' else "Refund"

                    invoice_data = {
                        'dateAndTimeOfIssue': (record.create_date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z' if record.create_date else None),
                        'cashier': record.invoice_user_id.vat,
                        'buyerId': record.partner_id.id,
                        'buyerCostCenterId': record.buyer_cost_centerid or None,
                        'invoiceType': invoiceType,
                        'transactionType': transactionType,
                        'payment': [],
                        'invoiceNumber': record.name,
                        'referentDocumentNumber': '',
                        'referentDocumentDT': '',
                        'items': []
                    }

                    # Refund references
                    if record.move_type == 'out_refund' and record.reversed_entry_id:
                        invoice_data['referentDocumentNumber'] = record.reversed_entry_id.ref_doc_num
                        invoice_data['referentDocumentDT'] = record.reversed_entry_id.ref_doc_date.strftime('%Y-%m-%d %H:%M:%S') if record.reversed_entry_id.ref_doc_date else ''

                    # Items
                    if record.order_type == "advance":
                        sale_orders = record.invoice_line_ids.mapped('sale_line_ids.order_id').exists()
                        so = sale_orders[:1]
                        if not so and record.invoice_origin:
                            so = record.env['sale.order'].search([('name', '=', record.invoice_origin)], limit=1)

                        if so:
                            for so_line in so.order_line:
                                if so_line.display_type or getattr(so_line, "is_downpayment", False):
                                    continue
                                tax_labels = [t.invoice_label for t in so_line.tax_id]
                                invoice_data["items"].append({
                                    "name": so_line.product_id.name,
                                    "quantity": so_line.product_uom_qty,
                                    "discount": getattr(so_line, "discount", 0.0) or 0.0,
                                    "unitPrice": so_line.price_unit,
                                    "totalAmount": so_line.price_total,
                                    "labels": tax_labels,
                                }) 
                    else:
                        for line in record.invoice_line_ids:
                            if line.product_id.is_charging is not True:
                                tax_labels = []
                                for tax in line.tax_ids:
                                    tax_labels.append(tax.invoice_label)
                                invoice_data["items"].append({
                                    "name": line.product_id.name,
                                    "quantity": line.quantity,
                                    "discount": line.discount,
                                    "unitPrice": line.price_unit,
                                    "totalAmount": line.price_total,
                                    "labels": tax_labels
                                })
                            _logger.info("Tax charging : %s", line.product_id.is_charging)
                            _logger.info("Tax labels : %s", tax_labels if 'tax_labels' in locals() else [])

                    # === FIX: build payments from POS when POS-origin invoice ===
                    pos_payments = record.pos_order_ids.mapped('payment_ids') if record.pos_order_ids else self.env['pos.payment']
                    if pos_payments:
                        for pos_payment in pos_payments:
                            pm_name = (pos_payment.payment_method_id.name or '').strip()
                            if pm_name == 'Cash':
                                type_val = 1
                            elif pm_name == 'Card':
                                type_val = 2
                            else:
                                type_val = 0
                            invoice_data["payment"].append({
                                "amount": pos_payment.amount,
                                "paymentType": type_val
                            })
                    else:
                        # Back-office path
                        if getattr(record.partner_id, 'charge_customer', False) is not True and record.move_type == 'out_invoice':
                            if not record.matched_payment_ids:
                                raise ValidationError(_("Payment Not collected."))
                            for payment in record.matched_payment_ids:
                                if payment.state == 'paid':
                                    vms_payment_type = getattr(payment, 'vms_payment_type', False)
                                    payment_type = vms_payment_type.payment_type if (vms_payment_type and vms_payment_type.payment_type) else False
                                    if payment_type:
                                        invoice_data["payment"].append({
                                            "amount": payment.amount,
                                            "paymentType": int(payment_type)
                                        })
                        else:
                            # charge customer / other
                            for payment in record.matched_payment_ids:
                                if payment.state == 'paid':
                                    vms_payment_type = getattr(payment, 'vms_payment_type', False)
                                    _logger.info("vms payment: %s", vms_payment_type)
                                    payment_type = vms_payment_type.payment_type if (vms_payment_type and vms_payment_type.payment_type) else False
                                    _logger.info("vms payment 2: %s", vms_payment_type)
                                    if payment_type:
                                        invoice_data["payment"].append({
                                            "amount": payment.amount,
                                            "paymentType": int(payment_type)
                                        })

                    _logger.info("FRCS invoice payload: %s", json.dumps(invoice_data, ensure_ascii=False))

                    url = "https://vsdc.sandbox.vms.frcs.org.fj/api/v3/invoices"
                    context = ssl.create_default_context()
                    context.load_cert_chain(certfile=cert_file, keyfile=key_file, password=password)
                    http = PoolManager(ssl_context=context)

                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "PAC": pac_value
                    }

                    response = http.request("POST", url, body=json.dumps(invoice_data), headers=headers)
                    _logger.info("FRCS response status: %s", response.status)

                    if response.status in (200, 201):
                        # decode to text so JSON fields can be parsed later
                        record.post_response = response.data.decode("utf-8") if response.data else ""
                        record.is_post_status = True
                        if record.move_type == 'out_refund' and record.reversed_entry_id:
                            record.origin_doc_num = record.reversed_entry_id.ref_doc_num
                            record.origin_doc_date = record.reversed_entry_id.ref_doc_date
                        record.action_generate_qr()

                        log_message = "✅ %s invoice request successfully sent by %s on %s." % (
                            transactionType,
                            self.env.user.name,
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        )
                        record.message_post(body=log_message)
                        self._onchange_show_send_button()

                        return {
                            'type': 'ir.actions.client',
                            'tag': 'display_notification',
                            'params': {
                                'title': 'Success',
                                'message': '%s invoice posted successfully and QR generated.' % transactionType,
                                'type': 'success',
                                'sticky': False,
                                'next': {
                                    'type': 'ir.actions.client',
                                    'tag': 'reload',
                                }
                            }
                        }
                    else:
                        raise ValidationError(("Request failed with status code. Data: %s") % response.data.decode("utf-8"))
            else:
                raise ValidationError(_("Please Upload PFX for Mapped System."))

    # ********* Function for Copy Sale And Refund process *********
    @api.depends('copy_post_response')
    def _compute_post_copy_response_json(self):
        for record in self:
            try:
                record.copy_post_response_json = json.loads(record.copy_post_response) if record.copy_post_response else {}
            except (json.JSONDecodeError, TypeError):
                record.copy_post_response_json = {}

    def action_generate_copy_qr(self):
        for record in self:
            qr_code_base64 = None
            # FIX: use copy_post_response (was using post_response)
            if record.copy_post_response:
                try:
                    response_data = json.loads(record.copy_post_response)
                    qr_code_base64 = response_data.get("verificationQRCode")
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON format in Copy post response")
            if qr_code_base64:
                if qr_code_base64.startswith("data:image"):
                    qr_code_base64 = qr_code_base64.split(",")[1]
                try:
                    qr_code_base64 = qr_code_base64.strip()
                    decoded_qr = base64.b64decode(qr_code_base64)
                    image = Image.open(io.BytesIO(decoded_qr))
                    img_io = io.BytesIO()
                    image.save(img_io, format="PNG")
                    img_io.seek(0)
                    record.copy_qr_code = base64.b64encode(img_io.getvalue())
                except Exception as e:
                    record.copy_qr_code = False
                    raise ValueError(f"Error Decoding Base64: {str(e)}")
            else:
                record.copy_qr_code = False
                raise ValueError("No QR Code Found in Response")

    def action_send_copy_request(self):
        for record in self:
            if record.system_id and record.system_id.pfx_status is True:
                copy_post_response = record.copy_post_response and json.loads(record.copy_post_response) or False
                if record.is_copy_post_status and copy_post_response:
                    record.copy_ref_doc_num = copy_post_response.get('invoiceNumber', False)

                if record.move_type in ['out_invoice', 'out_refund']:

                    path = record.system_id.pfx_file_path       

                    cert_file = os.path.join(os.path.dirname(path), "certificate.pem")
                    key_file = os.path.join(os.path.dirname(path), "private_key.pem")

                    print("cert_file",cert_file)
                    print("key_file",key_file)

                    password = record.system_id.pfx_password
                    pac_value = record.system_id.pfx_pac
                    pfx_expiry_date = record.system_id.pfx_expiry_date

                    if not cert_file or not key_file or not password or not pac_value:
                        raise ValidationError(_("Please configure the required certification in Branch Systems."))
                    if pfx_expiry_date and pfx_expiry_date < datetime.now():
                        raise ValidationError(_("Certification in Branch Systems is expired."))

                    invoiceType = "Copy"
                    transactionType = "Sale" if record.move_type == 'out_invoice' else "Refund"

                    invoice_data = {
                        'dateAndTimeOfIssue': (record.create_date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z' if record.create_date else None),
                        'cashier': record.invoice_user_id.vat,
                        'buyerId': record.partner_id.id,
                        'buyerCostCenterId': record.buyer_cost_centerid or None,
                        'invoiceType': invoiceType,
                        'transactionType': transactionType,
                        'payment': [],
                        'invoiceNumber': record.name,
                        'referentDocumentNumber': record.ref_doc_num,
                        'referentDocumentDT': record.ref_doc_date.strftime('%Y-%m-%d %H:%M:%S') if record.ref_doc_date else '',
                        'items': []
                    }

                    # Items
                    for line in record.invoice_line_ids:
                        if line.product_id.is_charging is not True:
                            tax_labels = []
                            for tax in line.tax_ids:
                                tax_labels.append(tax.invoice_label)
                            invoice_data["items"].append({
                                "name": line.product_id.name,
                                "quantity": line.quantity,
                                "discount": line.discount,
                                "unitPrice": line.price_unit,
                                "totalAmount": line.price_total,
                                "labels": tax_labels
                            })

                    # POS payments for copy if POS-origin
                    if record.pos_order_ids:
                        for pos_payment in record.pos_order_ids.payment_ids:
                            pm_name = (pos_payment.payment_method_id.name or '').strip()
                            if pm_name == 'Cash':
                                type_val = 1
                            elif pm_name == 'Card':
                                type_val = 2
                            else:
                                type_val = 0
                            invoice_data["payment"].append({
                                "amount": pos_payment.amount,
                                "paymentType": type_val
                            })
                    else:
                        if record.move_type == 'out_invoice':
                            if not record.matched_payment_ids:
                                raise ValidationError(_("Payment Not collected."))
                            for payment in record.matched_payment_ids:
                                if payment.state == 'paid':
                                    vms_payment_type = getattr(payment, 'vms_payment_type', False)
                                    payment_type = vms_payment_type.payment_type if (vms_payment_type and vms_payment_type.payment_type) else False
                                    if payment_type:
                                        invoice_data["payment"].append({
                                            "amount": payment.amount,
                                            "paymentType": int(payment_type)
                                        })
                        else:
                            invoice_data["payment"].append({
                                "amount": record.amount_total,
                                "paymentType": 0
                            })

                    url = "https://vsdc.sandbox.vms.frcs.org.fj/api/v3/invoices"
                    context = ssl.create_default_context()
                    context.load_cert_chain(certfile=cert_file, keyfile=key_file, password=password)
                    http = PoolManager(ssl_context=context)

                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "PAC": pac_value
                    }

                    response = http.request("POST", url, body=json.dumps(invoice_data), headers=headers)
                    if response.status in (200, 201):
                        record.copy_post_response = response.data.decode("utf-8") if response.data else ""
                        record.is_copy_post_status = True
                        record.action_generate_copy_qr()

                        log_message = "✅ %s invoice copy request successfully sent by %s on %s." % (
                            transactionType,
                            self.env.user.name,
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        )
                        record.message_post(body=log_message)

                        return self.env.ref('enovasions_account.action_print_copy_report').report_action(self)
                    else:
                        raise ValidationError(("Request failed with status code. Data: %s") % response.data.decode("utf-8"))
            else:
                raise ValidationError(_("Please configure the required Branch Certification."))

    # ********* END of Copy Sale And Refund process **************

    # === FIX: Post first, then send to FRCS for POS invoices ===
    def _post(self, soft=True):
        """Override to ensure POS invoices are posted before sending to FRCS."""
        res = super(AccountMoveInherit, self)._post(soft)

        for move in self:
            if move.move_type == 'out_invoice' and move.pos_order_ids:
                # ensure system_id comes from POS config
                sys_id = move.pos_order_ids.config_id.system_id
                if not sys_id:
                    raise ValidationError(_("Please set a System on the POS Configuration (Point of Sale → Configuration → your POS)."))
                if not move.system_id:
                    move.system_id = sys_id.id

                # now send to FRCS
                move.action_send_request()

        return res

    def action_print_frcs_report(self):
        return self.env.ref('enovasions_account.action_report_frcs_move').report_action(self)


class AccountMoveLineInherit(models.Model):
    _inherit = 'account.move.line'

    @api.constrains('tax_ids')
    def _check_only_one_tax(self):
        for line in self:
            if len(line.tax_ids) > 1:
                raise ValidationError("You can select only one tax.")

# # -*- coding: utf-8 -*-
# import logging
# from odoo import api, models, _
# from odoo.exceptions import ValidationError
#
# _logger = logging.getLogger(__name__)
#
# class PosOrder(models.Model):
#     _inherit = "pos.order"
#
#     @api.model
#     def pos_get_frcs_invoice_pdf(self, order_key):
#         """Resolve order by numeric id or by name/pos_reference, then return FRCS PDF URL."""
#         order = False
#         # numeric id?
#         try:
#             rec = self.browse(int(order_key))
#             if rec.exists():
#                 order = rec
#         except Exception:
#             pass
#         # fallback: by name/pos_reference
#         if not order:
#             order = self.search([
#                 '|', ('name', '=', order_key),
#                      ('pos_reference', '=', order_key)
#             ], limit=1)
#         if not order:
#             raise ValidationError(_("POS Order not found: %s") % order_key)
#
#         move = order.account_move or order.account_move_id
#         if not move:
#             raise ValidationError(_("No invoice is linked to this POS order. Make sure 'Invoice' was ticked."))
#
#         if move.state != "posted":
#             move.action_post()
#         if not move.is_post_status:
#             move.action_send_request()
#
#         # your custom FRCS report
#         url = f"/report/pdf/enovasions_account.report_frcs_invoice_template/{move.id}?download=1"
#         _logger.info("POS FRCS URL for order %s [%s] → %s", order.display_name, order.id, url)
#         return url

# enovasions_pos/models/pos_order.py
# enovasions_pos/models/pos_order.py

# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-


# enovasions_pos/models/pos_order.py

from odoo import api, models
from odoo.exceptions import UserError
import logging
_logger = logging.getLogger(__name__)

class PosOrder(models.Model):
    _inherit = 'pos.order'

    @api.model
    def pos_get_frcs_invoice_pdf(self, key):
        """Return URL to FRCS report for the order's invoice.
        Accepts either a numeric id or an order 'name' (e.g., 'Order 00003-...').
        """
        try:
            # 1) Resolve the order by id or by name
            order = False
            if isinstance(key, int):
                order = self.browse(key)
            else:
                # try int-like
                try:
                    order = self.browse(int(key))
                except Exception:
                    order = self.search([('name', '=', key)], limit=1)

            if not order:
                raise UserError("POS order not found (key=%s)." % key)

            move = order.account_move
            if not move:
                raise UserError("No customer invoice found for this order yet.")

            # 2) Make sure the report action exists
            report_xmlid = 'enovasions_account.action_report_frcs_invoice_thermal'
            self.sudo().env.ref(report_xmlid)  # will raise if missing

            # 3) Return the report URL
            return "/report/pdf/%s/%s?download=1" % (report_xmlid, move.id)

        except UserError:
            raise  # show the message to the POS user
        except Exception as e:
            # full traceback in server log, friendly message to frontend
            _logger.exception("pos_get_frcs_invoice_pdf failed for key=%s", key)
            raise UserError("FRCS PDF error: %s" % (str(e) or e.__class__.__name__))
