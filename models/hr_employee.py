from odoo import models, fields

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # DATOS PERSONALES
    velatek_codigo = fields.Char(string='Codigo')
    velatek_nombre_pila = fields.Char(string='Nombre')
    velatek_primer_apellido = fields.Char(string='Primer Apellido')
    velatek_segundo_apellido = fields.Char(string='Segundo Apellido')
    velatek_apellido_casada = fields.Char(string='Apellido de casada')
    velatek_profesion = fields.Char(string='Profesion/Officio')
    velatek_nombre_afp = fields.Char(string='Nombre de la AFP')
    velatek_cuenta_bancaria = fields.Char(string='Cuenta Bancaria')

    # DATOS DE IDENTIFICACIÓN
    velatek_dui = fields.Char(string='DUI')
    velatek_lugar_expedicion_dui = fields.Char(string='Lugar de expedición del DUI')
    velatek_fecha_expedicion_dui = fields.Date(string='Fecha de expedición del DUI')
    velatek_nit = fields.Char(string='NIT')
    velatek_nup = fields.Char(string='NUP')
    velatek_isss = fields.Char(string='ISSS')

    # BENEFICIARIOS
    velatek_beneficiary_ids = fields.One2many(
        'hr.employee.beneficiary', 'employee_id', string='Beneficiarios'
    )
