from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .payroll_parameter_utils import check_effective_periods


SECTORS = [
    ('commerce_services', 'Comercio y servicios'),
    ('textile', 'Maquila textil y confección'),
    ('industry', 'Industria'),
    ('sugar_mill', 'Ingenios azucareros'),
    ('agroindustry', 'Agroindustria'),
    ('coffee_processing', 'Beneficios de café'),
    ('agriculture', 'Agropecuario, pesca y otras actividades agrícolas'),
    ('sugar_harvest', 'Recolección de caña de azúcar'),
    ('coffee_harvest', 'Recolección de café'),
]

FREQUENCY_FACTORS = {
    'monthly': 1.0,
    'biweekly': 0.5,
    'weekly': 7.0 / 30.0,
    'fourteen_days': 14.0 / 30.0,
}


class HrSvMinimumWage(models.Model):
    _name = 'hr.sv.minimum.wage'
    _description = 'Salario mínimo de El Salvador'
    _order = 'sector, date_from desc'

    name = fields.Char(required=True)
    sector = fields.Selection(SECTORS, string='Sector económico', required=True)
    date_from = fields.Date(string='Vigente desde', required=True)
    date_to = fields.Date(string='Vigente hasta')
    active = fields.Boolean(default=True)
    monthly_amount = fields.Float(string='Salario mensual USD', digits=(12, 2), required=True)
    daily_amount = fields.Float(string='Salario por jornada USD', digits=(12, 3), required=True)
    hourly_amount = fields.Float(string='Salario por hora USD', digits=(12, 3), required=True)
    legal_reference = fields.Char(string='Referencia legal', required=True)

    @api.constrains('sector', 'date_from', 'date_to', 'active', 'monthly_amount', 'daily_amount', 'hourly_amount')
    def _check_rates(self):
        check_effective_periods(self, ('sector',))
        for rate in self:
            if min(rate.monthly_amount, rate.daily_amount, rate.hourly_amount) <= 0:
                raise ValidationError(_('Las tarifas mensual, diaria y por hora deben ser mayores que cero.'))

    @api.model
    def _rates_for_period(self, sector, start, end):
        rates = self.search([
            ('sector', '=', sector), ('date_from', '<=', end),
            '|', ('date_to', '=', False), ('date_to', '>=', start),
        ])
        covered_days = sum((min(end, rate.date_to or end) - max(start, rate.date_from)).days + 1 for rate in rates)
        if covered_days != (end - start).days + 1:
            raise ValidationError(_('Falta una tarifa de salario mínimo vigente para todo el intervalo del sector seleccionado.'))
        return rates


class HrSvMinimumWageReview(models.AbstractModel):
    _name = 'hr.sv.minimum.wage.review'
    _description = 'Revisión del salario mínimo por período'

    currency_id = fields.Many2one('res.currency', readonly=True)
    minimum_wage_reviewed = fields.Boolean(string='Mínimo del período verificado', copy=False)
    minimum_wage_review_amount = fields.Monetary(
        string='Mínimo verificado del período', currency_field='currency_id', copy=False,
    )
    minimum_wage_review_full_days = fields.Integer(
        string='Jornadas con mínimo diario', copy=False,
        help='Incluya las jornadas de más de cinco horas y las jornadas cortas que completan la semana laboral.',
    )
    minimum_wage_review_partial_hours = fields.Float(
        string='Horas ordinarias con mínimo proporcional', copy=False,
        help='Registre las horas de las jornadas que se remuneran proporcionalmente. Excluya las jornadas registradas con mínimo diario.',
    )
    minimum_wage_review_reference = fields.Char(
        string='Referencia del cálculo del mínimo', copy=False,
        help='Identifique la hoja de cálculo o expediente que respalda jornadas, descansos y unidades de obra.',
    )

    def _validate_period_minimum_wages(self, period):
        if not period.company_id.velatek_minimum_wage_from:
            return
        errors = []
        for line in self:
            try:
                line._validate_period_minimum_wage(period)
            except ValidationError as error:
                errors.append(f'{line.employee_id.display_name}: {error}')
        if errors:
            raise ValidationError(_('Revise el salario mínimo del período:\n%s', '\n'.join(errors)))

    def _validate_period_minimum_wage(self, period):
        self.ensure_one()
        contract = self.contract_id
        period_type = period.prepayroll_type if period._name == 'hr.prepayroll' else period.payroll_type
        if not contract or period_type != 'regular':
            return
        contract._validate_minimum_wage_amount(period, self)
