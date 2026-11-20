from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrIncidentSickSettlement(models.Model):
    _inherit = 'hr.employee.incident'

    sick_assisted = fields.Boolean(string='Cálculo asistido de incapacidad', copy=False)
    sick_episode_start = fields.Date(string='Inicio del episodio certificado', copy=False)
    sick_isss_eligibility = fields.Selection([
        ('confirmed', 'Cobertura confirmada'),
        ('none', 'Sin cobertura para este tramo'),
    ], string='Cobertura del ISSS', copy=False)
    sick_isss_covered_from = fields.Date(string='Cobertura ISSS desde', copy=False)
    sick_isss_covered_to = fields.Date(string='Cobertura ISSS hasta', copy=False)
    sick_isss_daily_base = fields.Monetary(string='Base diaria confirmada por ISSS', copy=False)
    sick_employer_entitlement = fields.Selection([
        ('confirmed', 'Corresponde prestación patronal'),
        ('none', 'Sin prestación patronal para este tramo'),
    ], string='Prestación patronal verificada', copy=False)
    sick_employer_base_daily = fields.Monetary(string='Salario básico diario para la prestación', copy=False)
    sick_employer_entitlement_rate = fields.Float(
        string='Porcentaje patronal antes del ISSS', copy=False,
        help='Con base en la obligación legal o la condición más favorable aplicable. '
             'Para enfermedad común y maternidad, el cálculo asistido usa 75 % si el campo queda en cero.',
    )

    def _assisted_sick_amounts(self):
        self.ensure_one()
        if self.sick_leave_type not in ('common', 'occupational', 'maternity'):
            raise ValidationError(_('Seleccione enfermedad común, riesgo profesional o maternidad para el cálculo asistido.'))
        if not (self.date_from and self.date_to and self.sick_episode_start and self.sick_episode_start <= self.date_from <= self.date_to):
            raise ValidationError(_('Registre el inicio del episodio y las fechas del tramo certificado.'))
        if not self.sick_isss_eligibility or not self.sick_employer_entitlement:
            raise ValidationError(_('Confirme si el ISSS y el empleador cubren este tramo.'))

        isss = 0.0
        if self.sick_isss_eligibility == 'confirmed':
            first, last = self.sick_isss_covered_from, self.sick_isss_covered_to
            offsets = {'common': 3, 'occupational': 1, 'maternity': 0}
            earliest = self.sick_episode_start + timedelta(days=offsets[self.sick_leave_type])
            if not first or not last or first < earliest or last < first or self.sick_isss_daily_base <= 0:
                raise ValidationError(_('Verifique la base y las fechas de cobertura confirmadas por el ISSS.'))
            if first > self.date_from or last < self.date_to:
                raise ValidationError(_('Separe los días cubiertos por el ISSS de los días sin cobertura en tramos distintos.'))
            factor = 1.0 if self.sick_leave_type == 'maternity' else 0.75
            isss = self.currency_id.round(self.sick_isss_daily_base * factor)

        entitlement = 0.0
        if self.sick_employer_entitlement == 'confirmed':
            rate = self.sick_employer_entitlement_rate
            if self.sick_leave_type in ('common', 'maternity'):
                rate = rate or 75.0
                if rate < 75.0:
                    raise ValidationError(_('La prestación patronal indicada es inferior al 75 % aplicable a este caso.'))
            if not 0 < rate <= 100 or self.sick_employer_base_daily <= 0:
                raise ValidationError(_('Indique el salario básico y el porcentaje patronal verificados.'))
            entitlement = self.sick_employer_base_daily * rate / 100.0

        return {
            'sick_isss_daily': isss,
            'sick_employer_daily': self.currency_id.round(max(0.0, entitlement - isss)),
        }

    def action_calculate_sick_settlement(self):
        self.ensure_one()
        if self.state not in ('draft', 'to_approve') or self.incident_type != 'sick_leave' or not self.sick_assisted:
            raise ValidationError(_('Calcule la incapacidad asistida en borrador o por aprobar.'))
        self.write(dict(self._assisted_sick_amounts(), sick_settlement_reviewed=False))
        return True

    def _check_assisted_sick_amounts(self):
        for incident in self.filtered(lambda record: record.incident_type == 'sick_leave' and record.sick_assisted):
            if any(
                incident.currency_id.compare_amounts(incident[field], value)
                for field, value in incident._assisted_sick_amounts().items()
            ):
                raise ValidationError(_('Vuelva a calcular los importes asistidos y verifique la liquidación antes de aprobar.'))

    def _check_payroll_rules(self):
        result = super()._check_payroll_rules()
        self._check_assisted_sick_amounts()
        return result

    @api.constrains(
        'sick_assisted', 'sick_episode_start', 'sick_isss_eligibility', 'sick_isss_covered_from',
        'sick_isss_covered_to', 'sick_isss_daily_base', 'sick_employer_entitlement',
        'sick_employer_base_daily', 'sick_employer_entitlement_rate',
    )
    def _check_settlement_inputs(self):
        self.filtered(lambda incident: incident.state == 'approved')._check_assisted_sick_amounts()
