from odoo import models, fields, api

class HrIncidentReason(models.Model):
    _name = 'hr.incident.reason'
    _description = 'Motivo de Incidencia'
    _order = 'name'

    name = fields.Char(string='Motivo', required=True)
    code = fields.Char(string='Código')
    legal_guidance = fields.Text(string='Referencia legal y registro')
    incident_type = fields.Selection([
        ('paid_leave', 'Permiso con goce'),
        ('unpaid_leave', 'Permiso sin goce'),
        ('unjustified_absence', 'Ausencia injustificada'),
        ('sick_leave', 'Incapacidad'),
        ('suspension', 'Suspensión disciplinaria sin goce'),
        ('overtime', 'Horas extra'),
        ('night_work', 'Trabajo nocturno ordinario'),
        ('holiday_work', 'Asueto trabajado'),
        ('weekly_rest_work', 'Descanso semanal trabajado'),
        ('bonus', 'Bono'),
        ('commission', 'Comisión'),
        ('viaticos', 'Viáticos'),
        ('other_income', 'Otro ingreso'),
        ('deduction', 'Deducción'),
        ('other', 'Otro')
    ], string='Tipo de Incidencia', default='paid_leave')
    active = fields.Boolean(string='Activo', default=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
