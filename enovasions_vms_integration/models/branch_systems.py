# -*- coding: utf-8 -*-
from odoo import api, exceptions, fields, models, _
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12
from odoo.exceptions import UserError
from odoo.tools import date_utils
from datetime import datetime, timedelta
import base64
import os
from pathlib import Path



class BranchSystem(models.Model):
    _name = 'branch.systems'
    _description = 'Branch Systems'
    _rec_name = 'system_name'
 
    system_name = fields.Char(string='System Name',required=True,copy=False)
    pfx_file = fields.Binary(string='PFX File', attachment=True,required=True,copy=False)
    pfx_filename = fields.Char(string='File Name',copy=False)
    pfx_password = fields.Char(string='PFX Password',required=True,copy=False)
    pfx_uid = fields.Char(string='PFX UID',required=True,copy=False)
    pfx_pac = fields.Char(string='PFX PAC',required=True,copy=False)   
    pfx_file_path = fields.Char("File Path",compute="_compute_save_binary_file",store=True,copy=False)
    pfx_expiry_date  = fields.Datetime(string="Expiry Date",required=True,copy=False)
    pfx_status = fields.Boolean(string="PFX Status", default=False,copy=False)
    certificate_pem = fields.Binary(string="Certificate PEM",attachment=True,copy=False)
    private_key_pem = fields.Binary(string="Private Key PEM",attachment=True,copy=False)
    branch_id = fields.Many2one('res.company',string='Branch',required=True,copy=False)



    @api.constrains('pfx_filename')
    def _check_file_extension(self):
        for record in self:
            if record.pfx_filename and not record.pfx_filename.lower().endswith('.pfx'):
                raise UserError(_("⚠️ Only .pfx files are allowed. Please upload a valid .pfx file."))

    
    @api.constrains('branch_id')
    def _check_unique_branch(self):
        for record in self:
            existing_record = self.search([('branch_id', '=', record.branch_id.id), ('id', '!=', record.id)], limit=1)
            if existing_record:
                raise UserError('Certificate is already added for this Branch.')

    @api.constrains('pfx_expiry_date')
    def _check_pfx_expiry_date(self):
        for record in self:
            if record.pfx_expiry_date < datetime.now():
                raise UserError("Expiry Date cannot be in the past!")     
                       

    @api.depends('pfx_file')
    def _compute_save_binary_file(self):
        for record in self:
            if record.pfx_file:
                pfx_file_data =  record.pfx_file
                pfx_filename =   record.pfx_filename
                system_name =  record.system_name

                try:
                    try:
                        file_data = base64.b64decode(pfx_file_data, validate=True)
                    except Exception:
                        raise UserError(_("❌ Invalid PFX File: Base64 decoding failed. Please upload a valid file."))

                    file_dir = '/home/odoo/' + system_name +'/'
                    print("file_dir",file_dir)
                    os.makedirs(file_dir, exist_ok=True)  # Ensure directory exists

                    folder = Path(file_dir)
                    folder.chmod(0o755)
                    os.chmod(file_dir, 0o755)

                    file_path = os.path.join(file_dir, pfx_filename)
                    with open(file_path, 'wb') as f:
                        f.write(file_data)

                    record.pfx_file_path = file_path
                    print(f"✅ PFX file successfully saved at: {file_path}")

                except UserError as e:
                    raise e 
                except Exception as e:
                    raise UserError(_("❌ File Save Failed: %s") % str(e))      

    
    # @api.model
    # def ffetch_and_simulate_file_path(self, pfx_file):
    #     attachment = self.env['ir.attachment'].browse(['name','=',self.pfx_file])
    #     print("pfx_file attachment",attachment)
    #     if attachment.exists():
    #             attachment_url = "/web/content/%d?download=true" % attachment.id# Simulate a file path (doesn't actually point to a physical location)
    #             simulated_file_path = "/home/odooerp/Workspace/odoo_18/pfx_file_path/%d/%s" % (attachment.id, attachment.name)
    #             return {
    #             'attachment_url': attachment_url,
    #             'simulated_file_path': simulated_file_path,
    #                         }
    #     else:
    #       return"Attachment not found."
 
    def upload_pfx(self):
        try:
            # self.ffetch_and_simulate_file_path(self.pfx_file)
            # er
            pfx_path =   self.pfx_file_path 
            if not pfx_path:
                raise UserError(_("❌ PFX file path not found. Please upload a valid .pfx file."))

            pfx_password = self.pfx_password  
            if not pfx_password:
                raise UserError(_("❌ PFX password is missing."))

            pfx_password = pfx_password.encode('utf-8')

            with open(pfx_path, "rb") as pfx_file:
                pfx_data = pfx_file.read()

            # Load the PFX file
            private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
                pfx_data, password=pfx_password, backend=default_backend()
            )

            # Convert the certificate to PEM format
            cert_pem = certificate.public_bytes(encoding=serialization.Encoding.PEM)

            # Convert the private key to PEM format
            private_key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )

            # Define file paths for the PEM files
            cert_pem_path = os.path.join(os.path.dirname(pfx_path), "certificate.pem")
            private_key_pem_path = os.path.join(os.path.dirname(pfx_path), "private_key.pem")

            # Save certificate.pem
            with open(cert_pem_path, "wb") as cert_file:
                cert_file.write(cert_pem)

            # Save private_key.pem
            with open(private_key_pem_path, "wb") as key_file:
                key_file.write(private_key_pem)

       

            # Store binary data in Odoo fields for attachment
            self.certificate_pem = base64.b64encode(cert_pem)
            self.private_key_pem = base64.b64encode(private_key_pem)
            self.pfx_status = True

            return {
                'effect': {
                    'fadeout': 'slow',
                    'message': _('✅ Successfully extracted and stored Certificate & Private Key!'),
                    'type': 'rainbow_man',
                }
            }

        except Exception as e:
            raise UserError(_("❌ Upload failed: %s") % str(e))

    @api.model
    def _cron_notify_pfx_expiry(self):
        today = fields.Date.today()
        notify_days = [30, 5, 4, 3, 2, 1, 0]
        for days in notify_days:
            target_date = today + timedelta(days=days)
            expiring_records = self.search([
                ('pfx_expiry_date', '>=', target_date.strftime('%Y-%m-%d 00:00:00')),
                ('pfx_expiry_date', '<=', target_date.strftime('%Y-%m-%d 23:59:59'))
            ])
            for record in expiring_records:
                if record.branch_id.email:
                    template = self.env.ref('enovasions_vms_integration.email_template_pfx_expiry_notification')
                    template.send_mail(record.id, force_send=True)        
            

                  

    
