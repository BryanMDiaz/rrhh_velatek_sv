from odoo import models, fields

class HrEmployeeBeneficiary(models.Model):
    _name = 'hr.employee.beneficiary'
    _description = 'Employee Beneficiary'

    name = fields.Char(string='Nombre', required=True)
    relationship = fields.Char(string='Parentezco', required=True)
    birthdate = fields.Date(string='Fecha de nacimiento')
    percentage = fields.Float(string='Porcentaje', default=100.0)
    employee_id = fields.Many2one('hr.employee', string='Empleado', ondelete='cascade')
