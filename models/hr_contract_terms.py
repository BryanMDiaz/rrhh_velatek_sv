from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .hr_sv_minimum_wage import FREQUENCY_FACTORS


class HrContractTerms(models.Model):
    _name = 'hr.contract.terms'
    _description = 'Condiciones de contrato por vigencia'
    _order = 'effective_from desc, id desc'
    _check_company_auto = True

    contract_id = fields.Many2one('hr.contract', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='contract_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    effective_from = fields.Date(string='Vigente desde', required=True)
    wage = fields.Monetary(string='Salario mensual', required=True)
    calendar_id = fields.Many2one(
        'resource.calendar', string='Horario de trabajo', required=True, check_company=True,
        help='Seleccione un horario nuevo para cambios de jornada. Conserve el horario anterior para los períodos históricos.',
    )
    hours_per_day = fields.Float(string='Horas ordinarias por día', readonly=True)
    reason = fields.Char(string='Motivo del cambio', required=True)
    document_id = fields.Many2one('hr.contract.generated.document', string='Documento contractual', ondelete='restrict')

    _sql_constraints = [
        ('contract_effective_date_unique', 'unique(contract_id, effective_from)', 'Ya existe un cambio para esa fecha.'),
    ]

    def _check_unlocked(self):
        for terms in self:
            for model, path in (('hr.prepayroll', 'line_ids.contract_id'), ('hr.payroll', 'line_ids.contract_id')):
                if self.env[model].sudo().search_count([
                    (path, '=', terms.contract_id.id),
                    ('date_end', '>=', terms.effective_from),
                    ('is_test', '=', False),
                    ('state', 'in', ('validated', 'closed')),
                ], limit=1):
                    raise ValidationError(_('El cambio afectaría un período oficial validado o cerrado.'))

    @api.constrains('effective_from', 'contract_id', 'wage', 'calendar_id', 'document_id')
    def _check_contract_terms(self):
        for terms in self:
            contract = terms.contract_id
            if (not contract.date_start or terms.effective_from < contract.date_start
                    or (contract.date_end and terms.effective_from > contract.date_end)):
                raise ValidationError(_('La fecha de vigencia debe estar dentro del contrato.'))
            if terms.wage < 0 or (not terms.wage and contract.velatek_remuneration_mode in ('fixed', 'mixed')):
                raise ValidationError(_('El salario de la vigencia debe ser válido para la modalidad de remuneración.'))
            if terms.document_id and terms.document_id.contract_id != contract:
                raise ValidationError(_('El documento de respaldo debe pertenecer al mismo contrato.'))
            if terms.hours_per_day <= 0:
                raise ValidationError(_('El horario debe tener horas ordinarias por día.'))
            if (contract._check_minimum_wage_enabled(terms.effective_from)
                    and not contract._minimum_wage_requires_review(contract.velatek_payment_frequency)):
                rate = self.env['hr.sv.minimum.wage']._rates_for_period(
                    contract._minimum_wage_sector(), terms.effective_from, terms.effective_from,
                )
                amount = contract._minimum_wage_in_company_currency(rate.monthly_amount, terms.effective_from)
                if terms.currency_id.compare_amounts(terms.wage, amount) < 0:
                    raise ValidationError(_('El salario de la vigencia es inferior al mínimo aplicable.'))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            values['hours_per_day'] = self.env['resource.calendar'].browse(values.get('calendar_id') or self.env.context.get('default_calendar_id')).hours_per_day
        records = super().create(vals_list)
        records._check_unlocked()
        return records

    def write(self, vals):
        financial = {'contract_id', 'effective_from', 'wage', 'calendar_id', 'hours_per_day'}
        if financial.intersection(vals):
            self._check_unlocked()
            vals = dict(vals)
            vals.pop('hours_per_day', None)
            if 'calendar_id' in vals:
                vals['hours_per_day'] = self.env['resource.calendar'].browse(vals['calendar_id']).hours_per_day
            result = super().write(vals)
            self._check_unlocked()
            return result
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_locked(self):
        self._check_unlocked()


class HrContractDatedTerms(models.Model):
    _inherit = 'hr.contract'

    velatek_terms_ids = fields.One2many('hr.contract.terms', 'contract_id', string='Cambios de salario y jornada', copy=False)
    velatek_current_wage = fields.Monetary(string='Salario vigente', compute='_compute_velatek_current_terms')
    velatek_current_calendar_id = fields.Many2one('resource.calendar', string='Horario vigente', compute='_compute_velatek_current_terms')

    @api.depends('wage', 'resource_calendar_id', 'velatek_terms_ids.effective_from', 'velatek_terms_ids.wage', 'velatek_terms_ids.calendar_id')
    def _compute_velatek_current_terms(self):
        today = fields.Date.context_today(self)
        for contract in self:
            wage, calendar, _hours = contract._velatek_terms_on(today)
            contract.velatek_current_wage = wage
            contract.velatek_current_calendar_id = calendar

    def write(self, vals):
        for contract in self.filtered('velatek_terms_ids'):
            calendar = contract.resource_calendar_id
            if (('wage' in vals and vals['wage'] != contract.wage)
                    or ('resource_calendar_id' in vals and vals['resource_calendar_id'] != calendar.id)):
                raise ValidationError(_('Registre el nuevo salario u horario en Cambios de salario y jornada, con su fecha de vigencia.'))
        return super().write(vals)

    def _velatek_terms_on(self, date):
        self.ensure_one()
        date = fields.Date.to_date(date) or fields.Date.context_today(self)
        term = self.velatek_terms_ids.filtered(lambda item: item.effective_from <= date)[:1]
        if term:
            return term.wage, term.calendar_id, term.hours_per_day
        calendar = self.resource_calendar_id
        return self.wage, calendar, calendar.hours_per_day or 8.0

    def _velatek_term_ranges(self, start, end):
        self.ensure_one()
        boundaries = sorted(item.effective_from for item in self.velatek_terms_ids if start < item.effective_from <= end)
        return zip([start, *boundaries], [*(date - timedelta(days=1) for date in boundaries), end])

    def _velatek_wage_for_period(self, start, end, frequency):
        self.ensure_one()
        period_length = (end - start).days + 1
        first = max(start, self.date_start or start)
        last = min(end, self.date_end or end)
        wage = self._velatek_terms_on(last)[0]
        if first > last or period_length <= 0:
            return wage, 0.0
        weighted_wage = sum(
            self._velatek_terms_on(segment_start)[0] * ((segment_end - segment_start).days + 1)
            for segment_start, segment_end in self._velatek_term_ranges(first, last)
        )
        factor = FREQUENCY_FACTORS.get(frequency, period_length / 30.0)
        return wage, self.company_id.currency_id.round(weighted_wage * factor / period_length)
