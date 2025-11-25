# -*- coding: utf-8 -*-
{
    "name": "VAT Monitoring System For Point Of Sales",
    "category": "Sales/Point of Sale",
    "version": "1.0",
    "summary": "VAT Monitoring System for Point Of Sales customisation",
    "author": "Enovasions Limited",
    "website": "https://enovasions.com",
    "license": "OEEL-1",
    "icon": "/enovasions_pos/static/description/icon.png",
    "depends": ["point_of_sale", "pos_sale", "enovasions_vms_integration", "enovasions_account"],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_config_views.xml",
        # IMPORTANT: do not include views/assets.xml here if you use the 'assets' section below
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "enovasions_pos/static/src/js/probe.js",
            "enovasions_pos/static/src/js/frcs_invoice_print.js",
        ],
    },
    "auto_install": True,
    "application": False,
}
