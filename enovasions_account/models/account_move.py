# -*- coding: utf-8 -*-
from odoo import api, exceptions, fields, models, _
from odoo.exceptions import ValidationError
from urllib3 import PoolManager
from urllib3.contrib import pyopenssl
from datetime import datetime, timezone
import requests
import base64
import json
import ssl
import os
import io
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

    post_response = fields.Text("Post response",readonly=True,copy=False)
    post_response_json = fields.Json(string="Post Response JSON", compute="_compute_post_response_json", store=False,copy=False)
    qr_code =fields.Image(string="QR Code",attachment=True,store=True,copy=False,readonly=True)
    is_post_status = fields.Boolean(default=False,string="Post Status",copy=False)
    #******Copy fields
    copy_post_response = fields.Text("Post Copy Response",readonly=True,copy=False)
    copy_post_response_json = fields.Json(string="Post Copy Response JSON", compute="_compute_post_copy_response_json", store=False,copy=False)
    copy_qr_code =fields.Image(string="QR Code",attachment=True,store=True,copy=False,readonly=True)
    is_copy_post_status = fields.Boolean(default=False,string="Copy Post Status",copy=False)
    #******END Coy fields
    system_id = fields.Many2one("branch.systems",string="System",required=True,copy=False)
    invoice_type = fields.Selection([
            ('normal', 'Normal'),
            ('proforma', 'ProForma'),('copy', 'Copy'),('training', 'Training'),('advance','ADVANCE')], 
            default='normal', string="Invoice Type")
    transaction_type = fields.Selection([
            ('invoice', 'Invoice'),
            ('refund', 'Refund')], 
            default='invoice', string="Transaction Type")
    ref_doc_num = fields.Text("Reference Doc No", copy=False, compute="_compute_ref_doc_fields", store=True)
    ref_doc_date= fields.Datetime("Reference Doc Date", copy=False, compute="_compute_ref_doc_fields", store=True)
    origin_doc_num = fields.Text("SDC Invoice No", copy=False)
    origin_doc_date= fields.Datetime("SDC Time", copy=False)
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
            rec.move_type in ('out_invoice', 'out_refund')
            rec.show_send_request_btn = (rec.state == 'posted' and rec.is_post_status == False)

    @api.onchange('state', 'is_post_status')
    def _onchange_show_send_button(self):
        for rec in self:
            rec.show_send_request_btn = (rec.state == 'posted' and rec.is_post_status == False)

    def action_post(self):
        res = super(AccountMoveInherit, self).action_post()
        self._onchange_show_send_button()
        return res

    @api.depends('post_response')
    def _compute_ref_doc_fields(self):
        for rec in self:
            if rec.post_response:
                response_data = json.loads(rec.post_response)
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
                # Remove 'data:image/png;base64,' prefix if present
                if qr_code_base64.startswith("data:image"):
                    qr_code_base64 = qr_code_base64.split(",")[1]

                try:
                    # Strip spaces and fix padding if necessary
                    qr_code_base64 = qr_code_base64.strip()
                    # missing_padding = len(qr_code_base64) % 4
                    # if missing_padding:
                    #     qr_code_base64 += "=" * (4 - missing_padding)

                    # Decode Base64
                    decoded_qr = base64.b64decode(qr_code_base64)

                    # Convert to image using PIL (Python Imaging Library)
                    image = Image.open(io.BytesIO(decoded_qr))

                    # Convert the image to PNG format (Odoo prefers PNG images)
                    img_io = io.BytesIO()
                    image.save(img_io, format="PNG")
                    img_io.seek(0)

                    # Store in Odoo's Image field
                    record.qr_code = base64.b64encode(img_io.getvalue())
                except Exception as e:
                    record.qr_code = False
                    raise ValueError(f"Error Decoding Base64: {str(e)}")
            else:
                record.qr_code = False
                raise ValueError("No QR Code Found in Response")

    def _check_full_payment(self):
        for record in self:
            if record.payment_ids:
                paid_payments = record.payment_ids.filtered(lambda p: p.state == 'posted')
                total_paid = sum(paid_payments.mapped('amount'))
            elif record.matched_payment_ids:
                paid_payments = record.matched_payment_ids.filtered(lambda p: p.state == 'paid')
                total_paid = sum(paid_payments.mapped('amount'))
            else:
                total_paid = 0

            if record.amount_total > 0 and record.move_type in ['out_invoice','out_refund']:
                if not record.amount_residual:
                    return True

                if total_paid >= record.amount_total:
                    return True

            return False

    def action_send_request(self):
        for record in self:
            if record.system_id:
                if not (self._check_full_payment()):
                    if not record.partner_id.charge_customer:
                        raise ValidationError('The invoice has not been fully paid. Please complete the full payment to proceed')

                if record.system_id.pfx_status == True:
                    if record.move_type in ['out_invoice','out_refund']:
                        # Fetch values from system parameters
                        # path = record.system_id.pfx_file_path

                        # base_path = "C:/Program Files/Odoo 18.0.2025090/VSDC/"

                        # cert_file = os.path.join(base_path, "certificate.pem")
                        # key_file = os.path.join(base_path, "private_key.pem")

                        # print("cert_file",cert_file)
                        # print("key_file",key_file)

                        # password = record.system_id.pfx_password
                        # pac_value = record.system_id.pfx_pac
                        # pfx_expiry_date = record.system_id.pfx_expiry_date

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

                        if pfx_expiry_date < datetime.now():
                            raise ValidationError(_("Certification in Branch Systems is expired."))
                        
                        if record.order_type =='advance': 
                            invoiceType = "Advance"
                        elif record.order_type =='training': 
                            invoiceType = "Training"
                        else: 
                            invoiceType = "Normal"   

                        if record.move_type =='out_invoice':
                            transactionType = "Sale"

                        if record.move_type =='out_refund':
                            transactionType = "Refund"    

                        invoice_data = {
                            'dateAndTimeOfIssue':record.create_date.strftime('%Y-%m-%d %H:%M:%S') if record.create_date else None,
                            'cashier':record.invoice_user_id.vat,
                            'buyerId':record.partner_id.id,
                            'buyerCostCenterId':record.buyer_cost_centerid or None,
                            'invoiceType':invoiceType,
                            'transactionType':transactionType, 
                            'payment': [],              
                            'invoiceNumber': record.name,
                            'referentDocumentNumber':'', #passig null
                            'referentDocumentDT':'', #hardcoded value
                            'items': []
                        }
                        
                        if record.move_type == 'out_refund':
                            if record.reversed_entry_id:
                                invoice_data['referentDocumentNumber'] = record.reversed_entry_id.ref_doc_num
                                invoice_data['referentDocumentDT'] = record.reversed_entry_id.ref_doc_date.strftime('%Y-%m-%d %H:%M:%S') if record.reversed_entry_id.ref_doc_date else '' 
                          
                        # Add line items (invoice lines)
                        for line in record.invoice_line_ids:
                            if  line.product_id.is_charging != True: 
                                tax_labels =[]
                                # Add tax lines (tax_ids)
                                for tax in line.tax_ids:
                                    tax_labels.append(tax.invoice_label)
                                # print("tax_labels",tax_labels)  

                                invoice_data["items"].append({
                                    # "gtin":"XX",
                                    "name": line.product_id.name,
                                    "quantity": line.quantity,
                                    "discount": line.discount,
                                    "unitPrice": line.price_unit,
                                    "totalAmount": line.price_total,
                                    "labels":tax_labels
                                })

                        # Add payment (matched_payment_ids) for Normal Sale & POS
                        if record.pos_payment_ids:
                            payments = record.pos_payment_ids
                        elif record.payment_ids:
                            payments = record.payment_ids
                        else:
                            payments = False

                        if record.pos_payment_ids:
                            print("pos order invoice")
                            for pos_payment in record.pos_payment_ids:
                                pos_payment_type=pos_payment.payment_method_id.name
                                if pos_payment_type =='Cash':
                                    type=1
                                elif pos_payment_type =='Card':
                                    type=2
                                else:
                                    type=0

                                invoice_data["payment"].append({
                                    "amount": pos_payment.amount,
                                    "paymentType": type
                                })
                                # vms_payment_type=pos_payment.vms_payment_type
                                # payment_type = 0
                                # if vms_payment_type and vms_payment_type.payment_type:
                                #     payment_type = int(vms_payment_type.payment_type)

                                # invoice_data["payment"].append({
                                #     "amount": pos_payment.amount,
                                #     "paymentType": payment_type
                                # })
                        else:
                            print("Normal Invoice") 
                            if record.partner_id.charge_customer != True and record.move_type =='out_invoice':
                                if not record.matched_payment_ids: 
                                    raise ValidationError(_("Payment Not collected."))

                                for payment in record.matched_payment_ids:
                                    if payment.state == 'paid':
                                        vms_payment_type=payment.vms_payment_type
                                        payment_type = False
                                        if vms_payment_type and vms_payment_type.payment_type:
                                            payment_type = vms_payment_type.payment_type

                                        if payment_type:
                                            invoice_data["payment"].append({
                                                "amount": payment.amount,
                                                "paymentType": int(payment_type)
                                            })
                            else:
                                print("Charge Customer",record.amount_total)
                                invoice_data["payment"].append({
                                    "amount": record.amount_total,
                                    "paymentType": 0 #other type
                                })

                        print("*****Final Json request*****",invoice_data)   

                        url = "https://vsdc.sandbox.vms.frcs.org.fj/api/v3/invoices"
                        context = ssl.create_default_context()
                        context.load_cert_chain(certfile=cert_file, keyfile=key_file, password=password)  
                        http = PoolManager(ssl_context=context)

                        # Include PAC in headers
                        headers = {
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "PAC": pac_value  
                        }

                        response = http.request(
                           "POST", url,
                            body=json.dumps(invoice_data),
                            headers=headers
                        )
                        
                        _logger.info("FRCS response status: %s", response.status)  
                        
                        if response.status == 200 or response.status == 201:
                            record.post_response = response.data #storing response
                            record.is_post_status = True
                            if record.move_type == 'out_refund':
                                if record.reversed_entry_id:
                                    record.origin_doc_num =  record.reversed_entry_id.ref_doc_num
                                    record.origin_doc_date = record.reversed_entry_id.ref_doc_date
                            record.action_generate_qr()

                            # Add success log message in chatter
                            log_message = "✅ %s invoice request successfully sent by %s on %s." % (
                                transactionType,
                                self.env.user.name,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            )
                            record.message_post(body=log_message)
                            self._onchange_show_send_button()

                            # ✅ Success popup
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
                    raise ValidationError(("Please Upload PFX for Mapped System."))
            else:
                raise ValidationError(("Please configure the required Branch Certification."))

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

            if record.post_response:
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
            if record.system_id:
                if record.system_id.pfx_status == True:
                    copy_post_response = record.copy_post_response and json.loads(record.copy_post_response) or False
                    if record.is_copy_post_status and copy_post_response:
                        record.copy_ref_doc_num = copy_post_response.get('invoiceNumber', False)

                    print("record.move_type",record.move_type)
                    if record.move_type in ['out_invoice','out_refund']:
                        # Fetch values from system parameters
                        # path = record.system_id.pfx_file_path

                        # base_path = "C:/Program Files/Odoo 18.0.2025090/VSDC/"

                        # cert_file = os.path.join(base_path, "certificate.pem")
                        # key_file = os.path.join(base_path, "private_key.pem")

                        # print("cert_file",cert_file)
                        # print("key_file",key_file)

                        # password = record.system_id.pfx_password
                        # pac_value = record.system_id.pfx_pac
                        # pfx_expiry_date = record.system_id.pfx_expiry_date

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

                        if pfx_expiry_date < datetime.now():
                            raise ValidationError(_("Certification in Branch Systems is expired."))

                        invoiceType = "Copy"
                        if record.move_type =='out_invoice':
                            transactionType = "Sale"

                        if record.move_type =='out_refund':
                            transactionType = "Refund"

                        invoice_data = {
                            'dateAndTimeOfIssue':record.create_date.strftime('%Y-%m-%d %H:%M:%S') if record.create_date else None,
                            'cashier':record.invoice_user_id.vat,
                            'buyerId':record.partner_id.id,
                            'buyerCostCenterId':record.buyer_cost_centerid or None,
                            'invoiceType':invoiceType,
                            'transactionType':transactionType, 
                            'payment': [],              
                            'invoiceNumber': record.name,
                            'referentDocumentNumber':record.ref_doc_num, 
                            'referentDocumentDT': record.ref_doc_date.strftime('%Y-%m-%d %H:%M:%S') if record.ref_doc_date else '',
                            'items': []
                        }

                        # Add line items (invoice lines)
                        for line in record.invoice_line_ids:
                            if  line.product_id.is_charging != True: 
                                tax_labels =[]
                                # Add tax lines (tax_ids)
                                for tax in line.tax_ids:
                                    tax_labels.append(tax.invoice_label)
                                # print("tax_labels",tax_labels)    

                                invoice_data["items"].append({
                                    # "gtin":"XX",
                                    "name": line.product_id.name,
                                    "quantity": line.quantity,
                                    "discount": line.discount,
                                    "unitPrice": line.price_unit,
                                    "totalAmount": line.price_total,
                                    "labels":tax_labels
                                })

                        # Add payment (matched_payment_ids) for Normal Sale & POS
                        if record.pos_order_ids:
                            print("copy pos order invoice")
                            for pos_payment in record.pos_order_ids.payment_ids:
                                pos_payment_type=pos_payment.payment_method_id.name
                                if pos_payment_type =='Cash':
                                    type=1
                                elif pos_payment_type =='Card':
                                    type=2
                                else:
                                    type=0

                                invoice_data["payment"].append({
                                    "amount": pos_payment.amount,
                                    "paymentType": type
                                })
                                # vms_payment_type=pos_payment.vms_payment_type
                                # payment_type = 0
                                # if vms_payment_type and vms_payment_type.payment_type:
                                #     payment_type = int(vms_payment_type.payment_type)
                              
                                # invoice_data["payment"].append({
                                #     "amount": pos_payment.amount,
                                #     "paymentType": payment_type
                                # })
                        else:
                            if record.move_type =='out_invoice':
                                if not record.matched_payment_ids: 
                                    raise ValidationError(_("Payment Not collected."))

                                for payment in record.matched_payment_ids:
                                    if payment.state == 'paid':
                                        vms_payment_type=payment.vms_payment_type
                                        payment_type = False
                                        if vms_payment_type and vms_payment_type.payment_type:
                                            payment_type = vms_payment_type.payment_type

                                        if payment_type:
                                            invoice_data["payment"].append({
                                                "amount": payment.amount,
                                                "paymentType": int(payment_type)
                                            })
                            else:
                                print("Charge Customer",record.amount_total)
                                invoice_data["payment"].append({
                                    "amount": record.amount_total,
                                    "paymentType": 0 #other type
                                })

                        print("*****Copy Final Json request*****",invoice_data)

                        url = "https://vsdc.sandbox.vms.frcs.org.fj/api/v3/invoices"
                        context = ssl.create_default_context()
                        context.load_cert_chain(certfile=cert_file, keyfile=key_file, password=password)  
                        http = PoolManager(ssl_context=context)

                        # Include PAC in headers
                        headers = {
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "PAC": pac_value  
                        }

                        response = http.request(
                           "POST", url,
                            body=json.dumps(invoice_data),
                            headers=headers
                        )

                        if response.status == 200 or response.status == 201:
                            record.copy_post_response = response.data #storing response
                            record.is_copy_post_status = True
                            record.action_generate_copy_qr()

                            # Add success log message in chatter
                            log_message = "✅ %s invoice copy request successfully sent by %s on %s." % (
                                transactionType,
                                self.env.user.name,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            )
                            record.message_post(body=log_message)

                            # Calling PDF report
                            return self.env.ref('enovasions_account.action_print_copy_report').report_action(self)
                        else:
                            raise ValidationError(("Request failed with status code. Data: %s") % response.data.decode("utf-8"))
                else:
                    raise ValidationError(_("Please Upload PFX for Mapped System."))
            else:
                raise ValidationError(_("Please configure the required Branch Certification."))

    # ********* END of  Copy Sale And Refund process **************
    def _post(self, soft=True):
        """Override the _post method to POS Order."""
        for move in self:
            if move.move_type == 'out_invoice':  # Ensure it runs only for customer invoices
                if move.pos_order_ids:
                    system_id=move.pos_order_ids.config_id.system_id
                    if system_id:
                        move.write({"system_id": system_id.id})
                    else: 
                        raise ValueError("Add System in Point of Sale config")   

                if move.pos_order_ids: # trigger only in pos while posting invoice
                    move.action_send_request()
        # Call the original _post method
        return super(AccountMoveInherit, self)._post(soft)

    def action_print_frcs_report(self):
        return self.env.ref('enovasions_account.action_report_frcs_move').report_action(self)


class AccountMoveLineInherit(models.Model):
    _inherit = 'account.move.line'
   
    @api.constrains('tax_ids')
    def _check_only_one_tax(self):
        for line in self:
            if len(line.tax_ids) > 1:
                raise ValidationError("You can select only one tax.")
