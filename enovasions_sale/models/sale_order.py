# -*- coding: utf-8 -*-
from odoo import api, exceptions, fields, models, _
from odoo.exceptions import ValidationError
from urllib3 import PoolManager
from urllib3.contrib import pyopenssl
from datetime import datetime
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
import logging
_logger = logging.getLogger(__name__)


class SaleOrderInherit(models.Model):
    _inherit = 'sale.order'

    so_post_response = fields.Text("Post response",readonly=True,copy=False)
    so_post_response_json = fields.Json(string="Post Response JSON", compute="_compute_post_sale_json", store=False,copy=False)
    so_qr_code =fields.Image(string="QR Code",attachment=True,store=True,copy=False,readonly=True)
    so_refund_response = fields.Text("Refund response",readonly=True,copy=False)
    so_refund_response_json = fields.Json(string="Refund Response JSON", compute="_compute_post_refund_json", store=False,copy=False)
    so_refund_qr_code =fields.Image(string="Refund QR Code",attachment=True,store=True,copy=False,readonly=True)
    so_is_post_sale_status = fields.Boolean(default=False,string="Proforma Sale Status",copy=False)
    so_is_post_refund_status = fields.Boolean(default=False,string="Proforma Return Status",copy=False)
    so_system_id = fields.Many2one("branch.systems",string="System",copy=False)
    is_proforma = fields.Boolean(default=False,string="Proforma",copy=False)
    is_proforma_sale = fields.Boolean(default=False,string="Proforma Sale",copy=False)
    is_proforma_refund = fields.Boolean(default=False,string="Proforma Return",copy=False)
    ref_doc_num = fields.Text("Reference Doc No",copy=False,compute="_compute_ref_doc_fields", store=True)
    ref_doc_date = fields.Text("Reference Doc Date",copy=False,compute="_compute_ref_doc_fields", store=True)
    origin_doc_num = fields.Text("SDC Invoice No",copy=False,compute="_compute_original_doc_fields", store=True)
    origin_doc_date = fields.Text("SDC Time",copy=False,compute="_compute_original_doc_fields", store=True)
    is_refund = fields.Boolean(default=True,string="Refund",copy=False)
    is_button_visible = fields.Boolean(compute='_compute_button_visibility', store=False)
    so_buyer_cost_centerid = fields.Char(string="Buyer Cost CenterId")
    order_type = fields.Selection([
        ('quotation', 'Quotation'),
        ('proforma', 'Proforma'),
        ('training', 'Training'),
        ('advance', 'Advance')
    ], string="Order Type", default='quotation')
    is_advance = fields.Boolean(string="Advance", compute='_compute_advance', store=True)

    @api.onchange('order_type')
    def _onchange_show_send_button(self):
        for rec in self:
            if rec.order_type == 'advance':
                rec.is_advance = True
            else:
                rec.is_advance = False

    @api.depends('order_type')
    def _compute_advance(self):
        for rec in self:
            if rec.order_type == 'advance':
                rec.is_advance = True
            else:
                rec.is_advance = False

    @api.depends('so_post_response')
    def _compute_ref_doc_fields(self):
        for rec in self:
            rec.ref_doc_num = False
            rec.ref_doc_date = False

            if rec.so_post_response:
                try:
                    response_data = json.loads(rec.so_post_response)
                    sdc_datetime_str = response_data.get('sdcDateTime')

                    if sdc_datetime_str:
                        sdc_datetime_obj = parser.isoparse(sdc_datetime_str)
                        rec.ref_doc_date = sdc_datetime_obj.strftime('%Y-%m-%d %H:%M:%S')

                    rec.ref_doc_num = response_data.get('invoiceNumber', False)

                except Exception as e:
                    _logger.warning(f"Failed to parse ref doc datetime from post_response: {e}")

    @api.depends('so_refund_response')
    def _compute_original_doc_fields(self):
        for rec in self:
            rec.origin_doc_date = False
            rec.origin_doc_num = False

            if rec.so_refund_response:
                try:
                    response_data = json.loads(rec.so_refund_response)
                    sdc_datetime_str = response_data.get('sdcDateTime')

                    if sdc_datetime_str:
                        sdc_datetime_obj = parser.isoparse(sdc_datetime_str)
                        rec.origin_doc_date = sdc_datetime_obj.strftime('%Y-%m-%d %H:%M:%S')

                    rec.origin_doc_num = response_data.get('invoiceNumber', False)

                except Exception as e:
                    _logger.warning(f"Failed to parse Original ref doc datetime from post_response: {e}")  
 

    @api.depends('so_post_response','is_proforma')
    def _compute_button_visibility(self):
        for record in self:
            record.is_button_visible = (record.is_proforma and not record.so_is_post_sale_status)
  
 


    @api.depends('so_post_response')
    def _compute_post_sale_json(self):
        for record in self:
            try:
                record.so_post_response_json = json.loads(record.so_post_response) if record.so_post_response else {}               
            except (json.JSONDecodeError, TypeError):
                record.so_post_response_json = {}

    @api.depends('so_refund_response')
    def _compute_post_refund_json(self):
        for record in self:
            try:
                record.so_refund_response_json = json.loads(record.so_refund_response) if record.so_refund_response else {}
            except (json.JSONDecodeError, TypeError):
                record.so_refund_response_json = {}

    def _prepare_invoice(self):
        invoice_vals = super()._prepare_invoice()
        invoice_vals.update({
            'order_type': self.order_type,
            'system_id': self.so_system_id.id,
            'buyer_cost_centerid':self.so_buyer_cost_centerid,
        })
        return invoice_vals

    def action_confirm(self):
        for order in self:
            if order.order_type in ['training', 'proforma']:
                raise ValidationError(_("You cannot confirm a Sale Order with order type '%s'.") % order.order_type.capitalize())
        return super().action_confirm()            

    def action_refund_qr(self):
        for record in self:
            qr_code_base64 = None

            if record.so_refund_response:
                try:
                    response_data = json.loads(record.so_refund_response)
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

                    record.so_refund_qr_code = base64.b64encode(img_io.getvalue())
                except Exception as e:
                    record.so_refund_qr_code = False
                    raise ValueError(f"Error Decoding Base64: {str(e)}")
            else:
                record.so_refund_qr_code = False
                raise ValueError("No QR Code Found in Refund Response") 

    def action_generate_qr(self):
        for record in self:
            qr_code_base64 = None

            if record.so_post_response:
                try:
                    response_data = json.loads(record.so_post_response)
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

                    record.so_qr_code = base64.b64encode(img_io.getvalue())
                except Exception as e:
                    record.so_qr_code = False
                    raise ValueError(f"Error Decoding Base64: {str(e)}")
            else:
                record.so_qr_code = False
                raise ValueError("No QR Code Found in Response")            

    def action_proforma_sale_request(self):
        for record in self:
            if record.is_proforma == True:
                if record.so_system_id:
                    if record.so_system_id.pfx_status == True: 
                            path = record.so_system_id.pfx_file_path       
                            cert_file = os.path.join(os.path.dirname(path), "certificate.pem")
                            key_file = os.path.join(os.path.dirname(path), "private_key.pem")

                            password = record.so_system_id.pfx_password
                            pac_value = record.so_system_id.pfx_pac
                            pfx_expiry_date = record.so_system_id.pfx_expiry_date
                          
                            if not cert_file or not key_file or not password or not pac_value:
                                raise ValidationError(_("Please configure the required certification in Branch Systems."))

                            if pfx_expiry_date < datetime.now():
                                raise ValidationError(_("Certification in Branch Systems is expired."))                     
                               
                            proforma_data = {
                                'dateAndTimeOfIssue':record.create_date.strftime('%Y-%m-%d %H:%M:%S') if record.create_date else None,
                                'cashier':record.user_id.vat,
                                'buyerId':record.partner_id.id,
                                'buyerCostCenterId':record.so_buyer_cost_centerid or None,
                                'invoiceType': "Proforma",
                                'transactionType': "Sale", 
                                'payment': [],              
                                'invoiceNumber': record.name,
                                'referentDocumentNumber':'', #passig null
                                'referentDocumentDT':'', #hardcoded value
                                'items': [],
                                
                            }

                            # Add line items (invoice lines)
                            for line in record.order_line:
                                if  line.product_id.is_charging != True: 
                                    tax_labels =[]
                                    # Add tax lines (tax_ids)
                                    for tax in line.tax_id:
                                        tax_labels.append(tax.invoice_label)
                                    # print("tax_labels",tax_labels)    

                                    proforma_data["items"].append({
                                        "name": line.product_id.name,
                                        "quantity": line.product_uom_qty,
                                        "unitPrice": line.price_unit,
                                        "totalAmount": line.price_total,
                                        "labels":tax_labels
                                    })

                            # Add payment  Proforma Sale 
                            proforma_data["payment"].append({
                                        "amount": record.amount_total,
                                        "paymentType": 1 #cash
                                    })  
                             
                            print("*****Final Json Proforma Sales request*****",proforma_data)   


                            
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
                                        body=json.dumps(proforma_data),
                                        headers=headers
                                    )

                            
                            if response.status == 200 or response.status == 201:
                                print("******Request was successful")
                                record.so_post_response = response.data
                                record.so_is_post_sale_status = True
                                record.is_proforma_sale = True  
                                record.is_proforma_refund = False
                                record.is_refund = False                               
                                record.action_generate_qr()
                                
                                # Add success log message in chatter
                                record.message_post(
                                    body='✅ Proforma Sale request successfully sent by %s on %s' % (self.env.user.name,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                                )
                                
                                # ✅ Success popup
                                return {
                                    'type': 'ir.actions.client',
                                    'tag': 'display_notification',
                                    'params': {
                                        'title': 'Success',
                                        'message': 'Proforma Sale posted successfully and QR generated.',
                                        'type': 'success',
                                        'sticky': False,
                                        'next': {
                                            'type': 'ir.actions.client',
                                            'tag': 'reload',
                                        }
                                    }
                                }
                            else:
                                raise ValidationError(("Request failed with status. Data: %s") % response.data.decode("utf-8"))                        

                           
                    else:
                        raise ValidationError(_("Please Upload PFX for Mapped System."))
                else:
                    raise ValidationError(_("Please configure the required Branch Certification.")) 
            else:
                    raise ValidationError(_("Please add in Proforma,This is Normal Sale not Proforma.")) 
        

    def action_proforma_refund_request(self):
        for record in self:
            if record.is_proforma == True:
                if not record.ref_doc_num or not record.ref_doc_date:
                    raise ValidationError(_("Please provide both Reference Doc No and Reference Doc Date before canceling the Proforma."))

                if record.so_system_id:
                    if record.so_system_id.pfx_status == True:   
                            path = record.so_system_id.pfx_file_path       
                            cert_file = os.path.join(os.path.dirname(path), "certificate.pem")
                            key_file = os.path.join(os.path.dirname(path), "private_key.pem")

                            password = record.so_system_id.pfx_password
                            pac_value = record.so_system_id.pfx_pac
                            pfx_expiry_date = record.so_system_id.pfx_expiry_date
                          
                            if not cert_file or not key_file or not password or not pac_value:
                                raise ValidationError(_("Please configure the required certification in Branch Systems."))

                            if pfx_expiry_date < datetime.now():
                                raise ValidationError(_("Certification in Branch Systems is expired.")) 

                                                                              
                               
                            proforma_data = {
                                'dateAndTimeOfIssue':record.create_date.strftime('%Y-%m-%d %H:%M:%S') if record.create_date else None,
                                'cashier':record.user_id.vat,
                                'buyerId':record.partner_id.id,
                                'buyerCostCenterId':record.so_buyer_cost_centerid or None,
                                'invoiceType': "Proforma",
                                'transactionType': "Refund", 
                                'payment': [],              
                                'invoiceNumber': record.name,
                                'referentDocumentNumber':record.ref_doc_num, 
                                'referentDocumentDT': record.ref_doc_date if record.ref_doc_date else None, 
                                'items': [],
                                
                            }

                            # Add line items (invoice lines)
                            for line in record.order_line:
                                if  line.product_id.is_charging != True: 
                                    tax_labels =[]
                                    # Add tax lines (tax_ids)
                                    for tax in line.tax_id:
                                        tax_labels.append(tax.invoice_label)
                                    # print("tax_labels",tax_labels)    

                                    proforma_data["items"].append({
                                        "name": line.product_id.name,
                                        "quantity": line.product_uom_qty,
                                        "unitPrice": line.price_unit,
                                        "totalAmount": line.price_total,
                                        "labels":tax_labels
                                    })

                            # Add payment  Proforma Sale 
                            proforma_data["payment"].append({
                                       "amount": record.amount_total,
                                        "paymentType": 1 #cash
                                    })  
                            print("*****Final Json Proforma Sales request*****",proforma_data)   


                            
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
                                        body=json.dumps(proforma_data),
                                        headers=headers
                                    )

                                
                            if response.status == 200 or response.status == 201:
                                print("******Request was successful")

                                record.so_refund_response = response.data
                                record.so_is_post_refund_status = True
                                record.is_proforma_sale = False  
                                record.is_proforma_refund = True
                                record.is_refund = True                               
                                record.action_refund_qr()
                                
                                # Add success log message in chatter
                                log_message = ("✅ Proforma Refund request successfully sent by %s on %s") % (
                                    self.env.user.name,
                                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                )
                                record.message_post(body=log_message)
                                
                                # ✅ Success popup
                                return {
                                    'type': 'ir.actions.client',
                                    'tag': 'display_notification',
                                    'params': {
                                        'title': 'Success',
                                        'message': 'Proforma Refund posted successfully and QR generated.',
                                        'type': 'success',
                                        'sticky': False,
                                        'next': {
                                            'type': 'ir.actions.client',
                                            'tag': 'reload',
                                        }
                                    }
                                }


                            else:
                                raise ValidationError(("Request failed with status. Data: %s") % response.data.decode("utf-8"))      
                                                
                    else:
                        raise ValidationError(_("Please Upload PFX for Mapped System."))
                else:
                    raise ValidationError(_("Please configure the required Branch Certification."))   
            else:
                raise ValidationError(_("Please add in Proforma,This is Normal Sale not Proforma."))                               

    def unlink(self):
        for order in self:
            if order.so_is_post_sale_status or order.so_is_post_refund_status:
                raise ValidationError(_("You cannot delete a Proforma orders."))
        return super(SaleOrderInherit, self).unlink()  

    def action_print_ps_report(self):
        return self.env.ref('enovasions_sale.action_print_ps_report').report_action(self)
     
    def action_print_pr_report(self):
        return self.env.ref('enovasions_sale.action_print_pr_report').report_action(self)
   

class SaleOrderLineInherit(models.Model):
    _inherit = 'sale.order.line'


    @api.constrains('tax_id')
    def _check_only_one_tax(self):
        for line in self:
            if len(line.tax_id) > 1:
                raise ValidationError("You can select only one tax.")


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    is_advance = fields.Boolean(default=False, string="Advance")

    def create_invoices(self):
        if self.is_advance and self.advance_payment_method == 'delivered':
            raise ValidationError("You can not create regular invoice for advance order")
        return super(SaleAdvancePaymentInv, self).create_invoices()
