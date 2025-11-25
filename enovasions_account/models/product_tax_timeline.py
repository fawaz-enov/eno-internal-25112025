from odoo import models, fields, api
from datetime import date 
from datetime import datetime
from odoo.exceptions import ValidationError



class ProductTimelineTax(models.Model):
    _name = "product.timeline.tax"
    _description = "Product Timeline Tax"
    _rec_name = "display_name"

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)
    tax_ids = fields.Many2many("account.tax", string="Taxes",required=True)
    product_ids = fields.Many2many("product.product", string="Products",required=True)
    display_name = fields.Char(string="Display Name", compute="_compute_display_name", store=True)



    @api.onchange('start_date')
    def _onchange_start_date(self):
        for record in self:
            if record.start_date:
                year = record.start_date.year
                record.end_date = datetime(year, 12, 31).date()

    @api.depends("start_date", "end_date")
    def _compute_display_name(self):
        """Generate a meaningful name for the record."""
        for rec in self:
            start = rec.start_date.strftime("%Y-%m-%d") if rec.start_date else "N/A"
            end = rec.end_date.strftime("%Y-%m-%d") if rec.end_date else "N/A"
            rec.display_name = f"Tax Timeline ({start} - {end})"


    @api.constrains("start_date", "end_date", "tax_ids")
    def _check_date_overlap(self):
        today = date.today()

        for record in self:
            if record.start_date < today:
                raise ValidationError("Start Date cannot be in the past.")        

            if record.start_date > record.end_date:
                raise ValidationError("Start Date cannot be greater than End Date.")



            for tax in record.tax_ids:
                overlapping_records = self.env["product.timeline.tax"].search([
                    ("id", "!=", record.id),  # Exclude the current record
                    ("tax_ids", "in", tax.id),
                    ("start_date", "<=", record.end_date),
                    ("end_date", ">=", record.start_date),
                ])

                if overlapping_records:
                    raise ValidationError(
                        f"Date range overlaps for Tax: {tax.name}. Please select a different date range."
                    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('start_date'):
                vals['start_date'] = fields.Date.today()
        record = super().create(vals_list)
        record.update_sales_taxes()  
        return record


    def write(self, vals):
        print("vals",vals)

        if 'start_date' in vals or 'end_date' in vals:
            raise ValidationError("Start Date and End Date cannot be modified after creation.")
        result = super().write(vals)
        self.update_sales_taxes()  
        return result           



    def unlink(self):
        """Prevent deletion if the record's start_date or end_date is in the past."""
        today = date.today()
        for record in self:
            if record.start_date <= today or record.end_date <= today:
                raise ValidationError(
                    f"Cannot delete record with past dates: {record.start_date} - {record.end_date}. "
                    "Only future records can be deleted."
                )
        return super(ProductTimelineTax, self).unlink() 
        

    @api.model
    def update_sales_taxes(self):
        today = date.today()
        tax_records = self.search([("start_date", "<=", today), ("end_date", ">=", today)])
        
        if not tax_records:
            return "No tax records found for today."

        updated_products = set()
        for tax_record in tax_records:
            for product in tax_record.product_ids:
                if product.product_tmpl_id:
                    product.product_tmpl_id.sudo().write({"taxes_id": [(6, 0, tax_record.tax_ids.ids)]})
                    updated_products.add(product.id)

        return f"Updated sales taxes for {len(updated_products)} unique products."
