{
    'name': 'VAT Monitoring System Integration',
    'category': 'Accounting/Localizations/EDI',
    'version': '1.0',
    'depends': ['base','mail'],
    'summary': 'VAT Monitoring System Integration and certification for Fiji Tax Portal ',
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/mail_template.xml',
        'views/branch_system_view.xml',       
    ],
    'author': 'Enovasions Limited',
    'website':'https://enovasions.com',
    'icon': '/enovasions_vms_integration/static/description/icon.jpeg',
    'auto_install': True,
    'license': 'OEEL-1',
}

