from odoo import models, fields, api

class HrEmployeeLoanType(models.Model):
    _name = 'hr.employee.loan.type'
    _description = 'Tipo de Préstamo de Empleado'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)

    default_interest_type = fields.Selection([
        ('none', 'Sin interés'),
        ('fixed', 'Interés fijo'),
        ('simple', 'Interés simple'),
    ], string='Tipo de Interés por Defecto', default='none', required=True)
    default_interest_rate = fields.Float(string='Tasa/Monto de Interés por Defecto')

    max_installments = fields.Integer(string='Máximo de Cuotas')
    max_capacity_percentage = fields.Float(string='Porcentaje Máximo de Capacidad (%)', default=30.0,
        help="Límite interno recomendado para este tipo de préstamo.")

    requires_attachment = fields.Boolean(string='Requiere Documento Adjunto', default=False)
    requires_authorization = fields.Boolean(string='Requiere Autorización', default=True)

    notes = fields.Text(string='Notas Internas')
