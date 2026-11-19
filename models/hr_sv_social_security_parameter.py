from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .payroll_parameter_utils import check_effective_periods, find_effective_parameter

class HrSvSocialSecurityParameter(models.Model):
    _name = 'hr.sv.social.security.parameter'
    _description = 'Parámetro de Seguridad Social (ISSS)'
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', default='isss_general', required=True)
    date_from = fields.Date(string='Vigencia Desde', required=True)
    date_to = fields.Date(string='Vigencia Hasta')
    
    employee_rate = fields.Float(string='Tasa Empleado (%)', required=True, default=3.0, help="Porcentaje aportado por el empleado, ej. 3.0 para 3%")
    employer_rate = fields.Float(string='Tasa Patrono (%)', required=True, default=7.5, help="Porcentaje aportado por el patrono, ej. 7.5 para 7.5%")
    
    min_base = fields.Float(string='Base Mínima Mensual', default=0.0)
    max_base = fields.Float(string='Base Máxima Mensual', default=1000.0)
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=False)
    active = fields.Boolean(string='Activo', default=True)
    legal_reference = fields.Char(string='Referencia Legal')
    notes = fields.Text(string='Notas')

    @api.constrains('date_from', 'date_to', 'code', 'company_id', 'active')
    def _check_overlap(self):
        check_effective_periods(self, ('code', 'company_id'))

    @api.model
    def get_active_parameter(self, date, company=None, code='isss_general'):
        date_val = fields.Date.to_date(date) if date else fields.Date.today()
        company_id = company.id if hasattr(company, 'id') and company else (company if isinstance(company, int) and company else self.env.company.id)
        
        base_domain = [
            ('code', '=', code),
            ('active', '=', True),
            ('date_from', '<=', date_val),
            '|', ('date_to', '=', False), ('date_to', '>=', date_val),
        ]
        
        # 1. Company specific
        if company_id:
            param = find_effective_parameter(self, base_domain + [('company_id', '=', company_id)])
            if param:
                return param
        
        # 2. Global (company_id = False)
        param = find_effective_parameter(self, base_domain + [('company_id', '=', False)])
        if param:
            return param

        raise ValidationError(_("No hay parámetros vigentes de ISSS para la compañía y fecha %s. Configure la vigencia antes de calcular la planilla.") % date_val)

    def calculate_isss(self, earnings, payment_frequency):
        self.ensure_one()
        if earnings <= 0:
            return 0.0, 0.0, 0.0

        freq_factors = {
            'monthly': 1.0,
            'biweekly': 0.5,
            'weekly': 250.0 / 1000.0,
            'fourteen_days': 466.67 / 1000.0,
        }
        factor = freq_factors.get(payment_frequency, 1.0)
        
        adjusted_max_base = self.max_base * factor if self.max_base > 0 else earnings
        adjusted_min_base = self.min_base * factor if self.min_base > 0 else 0.0
        
        subject_base = min(earnings, adjusted_max_base)
        if subject_base < adjusted_min_base:
            subject_base = adjusted_min_base

        emp_amount = round(subject_base * (self.employee_rate / 100.0), 2)
        empr_amount = round(subject_base * (self.employer_rate / 100.0), 2)
        return emp_amount, empr_amount, subject_base
