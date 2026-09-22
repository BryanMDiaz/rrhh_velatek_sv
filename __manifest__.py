{
    'name': 'HR Velatek SV',
    'version': '18.0.1.0.0',
    'summary': 'Customizations for HR Velatek SV',
    'description': """
        This module adds custom fields to the Employee form in Odoo for Velatek SV.
        - DATOS PERSONALES: Codigo, Nombre, Apellidos, Profesion, AFP, Cuenta Bancaria
        - DATOS DE IDENTIFICACIÓN: DUI, Lugar de expedición, Fecha de expedición, NIT, NUP, ISSS
    """,
    'category': 'Human Resources/Employees',
    'author': 'Velatek',
    'depends': ['hr'],
    'data': [
        'security/ir.model.access.csv',
        'views/hr_employee_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
