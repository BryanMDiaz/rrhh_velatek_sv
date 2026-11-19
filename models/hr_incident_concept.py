from odoo import models, fields, api

class HrIncidentConcept(models.Model):
    _name = 'hr.incident.concept'
    _description = 'Concepto de Incidencia Monetaria'
    _order = 'name'

    name = fields.Char(string='Nombre del concepto', required=True)
    code = fields.Char(string='Código')
    incident_category = fields.Selection([
        ('bonus', 'Bono'),
        ('commission', 'Comisión'),
        ('other_income', 'Otro ingreso'),
        ('viaticos', 'Viáticos'),
        ('deduction', 'Deducción'),
        ('other', 'Otro')
    ], string='Categoría de incidencia', required=True, default='bonus')
    active = fields.Boolean(string='Activo', default=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)

    # Configuración de flags para nómina futura (sin aplicar cálculo aún)
    day_count_mode = fields.Selection([
        ('calendar', 'Días calendario'),
        ('work_schedule', 'Jornada / Horario laboral'),
    ], string='Modo de conteo de días', default='calendar', required=True)
    is_salary_component = fields.Boolean(string='Es componente salarial', default=True)
    is_recurring = fields.Boolean(string='Es recurrente', default=False)
    affects_isss = fields.Boolean(string='Afecta ISSS', default=True)
    affects_afp = fields.Boolean(string='Afecta AFP', default=True)
    affects_isr = fields.Boolean(string='Afecta ISR', default=True)
