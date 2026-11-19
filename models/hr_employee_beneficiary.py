from odoo import models, fields, api
from odoo.exceptions import ValidationError

class HrEmployeeBeneficiary(models.Model):
    _name = 'hr.employee.beneficiary'
    _description = 'Employee Beneficiary'

    name = fields.Char(string='Nombre', required=True)
    relationship = fields.Char(string='Parentezco', required=True)
    birthdate = fields.Date(string='Fecha de nacimiento')
    percentage = fields.Float(string='Porcentaje', default=100.0)
    employee_id = fields.Many2one('hr.employee', string='Empleado', ondelete='cascade')

    @api.constrains('percentage', 'employee_id')
    def _check_percentage_sum(self):
        for record in self:
            if record.employee_id:
                total_percentage = sum(record.employee_id.velatek_beneficiary_ids.mapped('percentage'))
                if total_percentage > 100.0:
                    raise ValidationError('La suma de los porcentajes de todos los beneficiarios no puede superar el 100%.')
