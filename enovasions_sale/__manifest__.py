{
    'name': 'VAT Monitoring System For Sales',
    'category': 'Sales/Localizations/EDI',
    'version': '1.0',
    'depends': ['sale','account','enovasions_vms_integration','enovasions_account'],
    'summary': 'VAT Monitoring System for sales customisation ',
    'data': [
        'security/ir.model.access.csv',
        'report/proforma_sale_report.xml',
        'report/proforma_refund_report.xml',
        'views/sale_order_views.xml',        
    ],
    'author': 'Enovasions Limited',
    'website':'https://enovasions.com',
    'icon': '/enovasions_sale/static/description/icon.jpeg',
    'auto_install': True,
    'license': 'OEEL-1',
}

