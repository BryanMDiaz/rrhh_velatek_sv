from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .payroll_parameter_utils import check_effective_periods, find_effective_parameter

class HrSvPensionParameter(models.Model):
    _name = 'hr.sv.pension.parameter'
    _description = 'Parámetro de Fondo de Pensiones (AFP)'
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', default='afp_general', required=True)
    date_from = fields.Date(string='Vigencia Desde', required=True)
    date_to = fields.Date(string='Vigencia Hasta')
    
    employee_rate = fields.Float(string='Tasa Empleado (%)', required=True, default=7.25, help="Porcentaje aportado por el empleado, ej. 7.25 para 7.25%")
    employer_rate = fields.Float(string='Tasa Patrono (%)', required=True, default=8.75, help="Porcentaje aportado por el patrono, ej. 8.75 para 8.75%")
    
    min_base = fields.Float(string='Base Mínima Mensual', default=0.0)
    max_base = fields.Float(string='Base Máxima Mensual', default=0.0, help='Techo histórico: solo se aplica a períodos anteriores a enero de 2023. Cero significa sin techo.')
    
    afp_id = fields.Many2one('hr.afp', string='Administradora (AFP)')
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=False)
    active = fields.Boolean(string='Activo', default=True)
    legal_reference = fields.Char(string='Referencia Legal')
    notes = fields.Text(string='Notas')

    @api.constrains('date_from', 'date_to', 'code', 'company_id', 'afp_id', 'active')
    def _check_overlap(self):
        check_effective_periods(self, ('code', 'company_id', 'afp_id'))

    @api.model
    def get_active_parameter(self, date, company=None, code='afp_general', afp_id=None):
        date_val = fields.Date.to_date(date) if date else fields.Date.today()
        company_id = company.id if hasattr(company, 'id') and company else (company if isinstance(company, int) and company else self.env.company.id)
        afp_val = afp_id.id if hasattr(afp_id, 'id') and afp_id else (afp_id if isinstance(afp_id, int) else None)
        
        base_domain = [
            ('code', '=', code),
            ('active', '=', True),
            ('date_from', '<=', date_val),
            '|', ('date_to', '=', False), ('date_to', '>=', date_val),
        ]

        if afp_val:
            if company_id:
                param = find_effective_parameter(self, base_domain + [('company_id', '=', company_id), ('afp_id', '=', afp_val)])
                if param:
                    return param
            param = find_effective_parameter(self, base_domain + [('company_id', '=', False), ('afp_id', '=', afp_val)])
            if param:
                return param

        if company_id:
            param = find_effective_parameter(self, base_domain + [('company_id', '=', company_id), ('afp_id', '=', False)])
            if param:
                return param

        param = find_effective_parameter(self, base_domain + [('company_id', '=', False), ('afp_id', '=', False)])
        if param:
            return param

        raise ValidationError(_("No hay parámetros vigentes de AFP para la compañía y fecha %s. Configure la vigencia antes de calcular la planilla.") % date_val)

    def calculate_afp(self, earnings, payment_frequency, ref_date=None):
        self.ensure_one()
        if earnings <= 0:
            return 0.0, 0.0, 0.0

        freq_factors = {
            'monthly': 1.0,
            'biweekly': 0.5,
            'weekly': 0.25,
            'fourteen_days': 14.0 / 30.0,
        }
        factor = freq_factors.get(payment_frequency, 1.0)
        
        ref_date = fields.Date.to_date(ref_date) if ref_date else fields.Date.context_today(self)
        historical_cap = self.max_base if ref_date < fields.Date.to_date('2023-01-01') else 0.0
        adjusted_max_base = historical_cap * factor if historical_cap > 0 else earnings
        adjusted_min_base = self.min_base * factor if self.min_base > 0 else 0.0
        
        subject_base = min(earnings, adjusted_max_base)
        if subject_base < adjusted_min_base:
            subject_base = adjusted_min_base

        emp_amount = round(subject_base * (self.employee_rate / 100.0), 2)
        empr_amount = round(subject_base * (self.employer_rate / 100.0), 2)
        return emp_amount, empr_amount, subject_base
