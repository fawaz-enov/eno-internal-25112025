# -*- coding: utf-8 -*-
from odoo import api, exceptions, fields, models, _
from odoo.exceptions import ValidationError



class PosConfigInherit(models.Model):
    _inherit = 'pos.config'

    system_id = fields.Many2one("branch.systems",string="System",required=False)


   