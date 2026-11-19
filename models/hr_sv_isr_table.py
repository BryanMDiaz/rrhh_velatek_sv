from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .payroll_parameter_utils import check_effective_periods, find_effective_parameter

class HrSvIsrTable(models.Model):
    _name = 'hr.sv.isr.table'
    _description = 'Tabla de Retención ISR'
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código', default='isr_table', required=True)
    payment_frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro')
    ], string='Frecuencia de Pago', required=True)
    
    date_from = fields.Date(string='Vigencia Desde', required=True)
    date_to = fields.Date(string='Vigencia Hasta')
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=False)
    active = fields.Boolean(string='Activo', default=True)
    legal_reference = fields.Char(string='Referencia Legal')
    notes = fields.Text(string='Notas')
    
    bracket_ids = fields.One2many('hr.sv.isr.bracket', 'table_id', string='Tramos ISR')

    @api.constrains('date_from', 'date_to', 'payment_frequency', 'company_id', 'active')
    def _check_overlap(self):
        check_effective_periods(self, ('payment_frequency', 'company_id'))

    @api.model
    def get_active_table(self, date, company=None, payment_frequency='monthly'):
        date_val = fields.Date.to_date(date) if date else fields.Date.today()
        company_id = company.id if hasattr(company, 'id') and company else (company if isinstance(company, int) and company else self.env.company.id)
        
        base_domain = [
            ('payment_frequency', '=', payment_frequency),
            ('active', '=', True),
            ('date_from', '<=', date_val),
            '|', ('date_to', '=', False), ('date_to', '>=', date_val),
        ]

        if company_id:
            table = find_effective_parameter(self, base_domain + [('company_id', '=', company_id)])
            if table:
                return table

        table = find_effective_parameter(self, base_domain + [('company_id', '=', False)])
        if table:
            return table

        raise ValidationError(_("No hay tabla ISR vigente para la compañía, frecuencia %s y fecha %s. Configure la tabla antes de calcular la planilla.") % (payment_frequency, date_val))

    def calculate_isr(self, taxable_base):
        self.ensure_one()
        sorted_brackets = self.bracket_ids.sorted(key=lambda b: (b.sequence, b.from_amount))
        if not sorted_brackets:
            raise ValidationError(_('La tabla ISR %s no tiene tramos. Configure los tramos antes de calcular la planilla.') % self.name)
        if taxable_base <= 0:
            return 0.0, False

        matched_bracket = False
        for bracket in sorted_brackets:
            is_first = (bracket.id == sorted_brackets[0].id)
            lower_bound_ok = (taxable_base >= bracket.from_amount) if is_first else (taxable_base > bracket.from_amount)
            upper_bound_ok = (taxable_base <= bracket.to_amount) if (bracket.to_amount and bracket.to_amount > 0) else True
            
            if lower_bound_ok and upper_bound_ok:
                matched_bracket = bracket
                break

        if not matched_bracket:
            raise ValidationError(_("La base imponible (%s) no se encuentra dentro de ningún tramo de la tabla ISR '%s'.", taxable_base, self.name))

        excess = taxable_base - matched_bracket.excess_over
        tax = matched_bracket.fixed_fee + (excess * (matched_bracket.rate / 100.0))
        return round(tax, 2), matched_bracket


class HrSvIsrBracket(models.Model):
    _name = 'hr.sv.isr.bracket'
    _description = 'Tramo de Tabla ISR'
    _order = 'sequence, from_amount'

    table_id = fields.Many2one('hr.sv.isr.table', string='Tabla ISR', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Secuencia', default=10)
    name = fields.Char(string='Descripción / Tramo')
    
    from_amount = fields.Float(string='Desde ($)', required=True)
    to_amount = fields.Float(string='Hasta ($)', help="0.0 para tramo sin límite superior")
    excess_over = fields.Float(string='Sobre el Exceso de ($)', required=True)
    fixed_fee = fields.Float(string='Cuota Fija ($)', default=0.0)
    rate = fields.Float(string='Porcentaje Exceso (%)', required=True)
