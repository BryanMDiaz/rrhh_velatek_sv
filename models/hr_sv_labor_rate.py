from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .payroll_parameter_utils import check_effective_periods, find_effective_parameter

class HrSvLaborRate(models.Model):
    _name = 'hr.sv.labor.rate'
    _description = 'Tasa o Multiplicador Laboral (El Salvador)'
    _order = 'code, date_from desc, id desc'

    name = fields.Char(string='Nombre de la tasa', required=True)
    code = fields.Selection([
        ('overtime_day', 'Horas extra diurnas'),
        ('overtime_night', 'Horas extra nocturnas'),
        ('night_work', 'Recargo nocturno'),
        ('holiday_work', 'Trabajo en día de asueto'),
        ('weekly_rest_work', 'Trabajo en día de descanso'),
    ], string='Código', required=True)
    concept_type = fields.Selection([
        ('overtime', 'Horas extra'),
        ('night_work', 'Trabajo nocturno'),
        ('holiday_work', 'Día de asueto'),
        ('weekly_rest_work', 'Descanso semanal'),
    ], string='Tipo de concepto', compute='_compute_concept_type', store=True, readonly=False)

    date_from = fields.Date(string='Vigente desde', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Vigente hasta')

    rate_type = fields.Selection([
        ('multiplier', 'Multiplicador'),
        ('percentage', 'Porcentaje'),
    ], string='Tipo de tasa', default='multiplier', required=True)

    value = fields.Float(string='Valor de tasa / Porcentaje', required=True)

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=False,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(string='Activo', default=True)

    legal_reference = fields.Char(string='Base legal / Referencia')
    notes = fields.Text(string='Notas')

    @api.depends('code')
    def _compute_concept_type(self):
        mapping = {
            'overtime_day': 'overtime',
            'overtime_night': 'overtime',
            'night_work': 'night_work',
            'holiday_work': 'holiday_work',
            'weekly_rest_work': 'weekly_rest_work',
        }
        for record in self:
            if record.code and record.code in mapping:
                record.concept_type = mapping[record.code]

    def get_factor(self):
        self.ensure_one()
        if self.rate_type == 'percentage':
            return self.value / 100.0 if self.value > 1.0 else self.value
        return self.value

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rate in self:
            if rate.date_from and rate.date_to and rate.date_to < rate.date_from:
                raise ValidationError(_("La fecha hasta no puede ser menor a la fecha desde."))

    @api.constrains('code', 'company_id', 'date_from', 'date_to', 'active')
    def _check_overlap(self):
        check_effective_periods(self, ('code', 'company_id'))

    @api.model
    def get_active_rate(self, code, date, company=None):
        date_val = fields.Date.to_date(date) if date else fields.Date.today()
        company_id = company.id if hasattr(company, 'id') and company else (company if isinstance(company, int) and company else self.env.company.id)
        
        base_domain = [
            ('code', '=', code),
            ('active', '=', True),
            ('date_from', '<=', date_val),
            '|', ('date_to', '=', False), ('date_to', '>=', date_val),
        ]

        if company_id:
            rate = find_effective_parameter(self, base_domain + [('company_id', '=', company_id)])
            if rate:
                return rate

        rate = find_effective_parameter(self, base_domain + [('company_id', '=', False)])
        if rate:
            return rate

        raise ValidationError(_("No hay tasa laboral vigente para %s, compañía y fecha %s. Configure la tasa antes de calcular la incidencia.") % (code, date_val))
