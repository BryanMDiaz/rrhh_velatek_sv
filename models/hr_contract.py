from odoo import _, models, fields, api
from odoo.exceptions import ValidationError
import uuid

from .hr_sv_minimum_wage import FREQUENCY_FACTORS, SECTORS

class HrContract(models.Model):
    _inherit = 'hr.contract'

    @api.model
    def default_get(self, fields_list):
        res = super(HrContract, self).default_get(fields_list)
        if 'name' in fields_list and not res.get('name'):
            res['name'] = f"CON-{uuid.uuid4().hex[:6].upper()}"
        return res

    velatek_payment_frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro')
    ], string='Frecuencia de pago', default='monthly')

    velatek_work_schedule_type = fields.Selection([
        ('full_time', 'Tiempo completo'),
        ('part_time', 'Medio tiempo'),
        ('partial', 'Jornada parcial'),
        ('other', 'Otra')
    ], string='Tipo de jornada', default='full_time')


    velatek_minimum_wage_sector = fields.Selection(
        SECTORS, string='Sector económico del contrato',
        help='Si se deja vacío, se aplica el sector del centro de trabajo o de la compañía.',
    )
    velatek_rotating_shift = fields.Boolean(string='Horario rotativo')

    def _minimum_wage_sector(self):
        self.ensure_one()
        location = self.velatek_work_location_id or self.employee_id.work_location_id
        if location.company_id and location.company_id != self.company_id:
            raise ValidationError(_('El centro de trabajo seleccionado debe pertenecer a la compañía del contrato.'))
        sector = self.velatek_minimum_wage_sector or location.velatek_minimum_wage_sector or self.company_id.velatek_minimum_wage_sector
        if not sector:
            raise ValidationError(_('Configure el sector económico del contrato, del centro de trabajo o de la compañía.'))
        return sector

    def _check_minimum_wage_enabled(self, date):
        self.ensure_one()
        effective = self.company_id.velatek_minimum_wage_from
        professional = self.env.ref('rrhh_velatek_sv.contract_type_professional_services', raise_if_not_found=False)
        return effective and date >= effective and self.contract_type_id != professional

    def _minimum_wage_in_company_currency(self, amount, date):
        return self.env.ref('base.USD')._convert(amount, self.company_id.currency_id, self.company_id, date)

    def _minimum_wage_requires_review(self, frequency):
        return (self.velatek_work_schedule_type != 'full_time' or self.velatek_rotating_shift
                or self.velatek_remuneration_mode != 'fixed' or frequency not in FREQUENCY_FACTORS)

    @api.constrains(
        'state', 'wage', 'date_start', 'date_end', 'company_id', 'employee_id', 'contract_type_id',
        'velatek_minimum_wage_sector', 'velatek_work_location_id', 'velatek_work_schedule_type',
        'velatek_rotating_shift', 'velatek_remuneration_mode', 'velatek_payment_frequency',
    )
    def _check_contract_minimum_wage(self):
        for contract in self.filtered(lambda c: c.state == 'open' and c.date_start):
            date = max(contract.date_start, fields.Date.context_today(contract))
            if (contract.date_end and contract.date_end < date) or not contract._check_minimum_wage_enabled(date):
                continue
            sector = contract._minimum_wage_sector()
            rates = self.env['hr.sv.minimum.wage']._rates_for_period(sector, date, date)
            if not contract._minimum_wage_requires_review(contract.velatek_payment_frequency):
                minimum = contract._minimum_wage_in_company_currency(rates.monthly_amount, date)
                if contract.company_id.currency_id.compare_amounts(contract._velatek_terms_on(date)[0], minimum) < 0:
                    raise ValidationError(_('El salario mensual es inferior al mínimo vigente de %(minimum).2f.', minimum=minimum))

    def _validate_minimum_wage_amount(self, period, line):
        self.ensure_one()
        start, end, frequency = period.date_start, period.date_end, period.payment_frequency
        fixed = line.period_wage
        period_days = (end - start).days + 1
        end = min(end, self.date_end) if self.date_end else end
        if not self._check_minimum_wage_enabled(end):
            return
        first_paid = max(start, self.date_start) if self.date_start else start
        if end < first_paid:
            return
        first_checked = max(first_paid, self.company_id.velatek_minimum_wage_from)
        checked_days = (end - first_checked).days + 1
        paid_days = (end - first_paid).days + 1
        sector = self._minimum_wage_sector()
        rates = self.env['hr.sv.minimum.wage']._rates_for_period(sector, first_checked, end)
        currency = self.company_id.currency_id
        if self._minimum_wage_requires_review(frequency):
            remuneration = (fixed if self.velatek_remuneration_mode != 'production' else 0.0) + line.production_amount
            minimum = line.minimum_wage_review_amount
            days = line.minimum_wage_review_full_days
            hours = line.minimum_wage_review_partial_hours
            if (not line.minimum_wage_reviewed or not (line.minimum_wage_review_reference or '').strip()
                    or min(days, hours, minimum) < 0 or (remuneration > 0 and not minimum)):
                raise ValidationError(_(
                    'Verifique el mínimo del período y registre el importe y la referencia del cálculo '
                    'según la jornada, el sector y la modalidad salarial.'
                ))
            if days > checked_days or hours > 5 * (checked_days - days):
                raise ValidationError(_('Las jornadas verificadas superan los días del período revisado.'))
            if not (days or hours) and remuneration > 0 and sector not in ('coffee_harvest', 'sugar_harvest'):
                raise ValidationError(_('Registre las jornadas o las horas ordinarias verificadas para comprobar el mínimo.'))
            tariff_floor = self._minimum_wage_in_company_currency(
                days * min(rates.mapped('daily_amount')) + hours * min(rates.mapped('hourly_amount')), end,
            )
            if currency.compare_amounts(minimum, tariff_floor) < 0:
                raise ValidationError(_(
                    'El mínimo verificado es inferior a la referencia de las jornadas y horas declaradas (%(amount).2f).',
                    amount=tariff_floor,
                ))
        else:
            minimum = self._minimum_wage_in_company_currency(sum(
                rate.monthly_amount * ((min(end, rate.date_to or end) - max(first_checked, rate.date_from)).days + 1) for rate in rates
            ) * FREQUENCY_FACTORS[frequency] / period_days, end)
            remuneration = fixed * checked_days / paid_days
        if currency.compare_amounts(remuneration, minimum) < 0:
            raise ValidationError(_(
                'La remuneración ordinaria de %(actual).2f es inferior al mínimo de %(minimum).2f para este período.',
                actual=remuneration, minimum=minimum,
            ))

    velatek_analytic_account_id = fields.Many2one('account.analytic.account', string='Centro de costo')
    velatek_work_location_id = fields.Many2one('hr.work.location', string='Ubicación de trabajo')
    velatek_contract_template_id = fields.Many2one('hr.contract.template', string='Plantilla de contrato', domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    velatek_generated_document_count = fields.Integer(compute='_compute_velatek_generated_document_count', string='Documentos generados')

    @api.onchange('employee_id')
    def _onchange_employee_id_velatek(self):
        if self.employee_id:
            if self.employee_id.department_id:
                self.department_id = self.employee_id.department_id
            if self.employee_id.job_id:
                self.job_id = self.employee_id.job_id
            if self.employee_id.velatek_company_entry_date:
                self.date_start = self.employee_id.velatek_company_entry_date

            if self.employee_id.velatek_employment_type == 'professional_services':
                self.contract_type_id = self.env.ref('rrhh_velatek_sv.contract_type_professional_services')
                self._onchange_contract_type_id_template()
                return
            
            # Mapear Tipo de Empleado (velatek_employment_type) a Tipo de Contrato (hr.contract.type)
            if self.employee_id.velatek_employment_type:
                mapping = {
                    'permanent': 'Permanente',
                    'temporary': 'Temporal',
                    'eventual': 'Eventual',
                    'intern': 'Pasante',
                }
                type_name = mapping.get(self.employee_id.velatek_employment_type)
                if type_name:
                    contract_type = self.env['hr.contract.type'].search([('name', 'ilike', type_name)], limit=1)
                    if contract_type:
                        self.contract_type_id = contract_type.id
                        self._onchange_contract_type_id_template()

    @api.depends('employee_id')
    def _compute_velatek_generated_document_count(self):
        for contract in self:
            contract.velatek_generated_document_count = self.env['hr.contract.generated.document'].search_count([('contract_id', '=', contract.id)])

    velatek_remuneration_mode = fields.Selection([
        ('fixed', 'Salario fijo'),
        ('production', 'Por producción'),
        ('mixed', 'Mixto')
    ], string='Modalidad de remuneración', default='fixed')

    @api.onchange('contract_type_id')
    def _onchange_contract_type_id_template(self):
        self.velatek_contract_template_id = self._get_default_contract_template()

    def _get_default_contract_template(self):
        self.ensure_one()
        templates = self.env['hr.contract.template']
        if not self.contract_type_id:
            return templates
        domain = [
            ('contract_type_id', '=', self.contract_type_id.id),
            ('is_default', '=', True),
        ]
        if self.company_id:
            company_template = templates.search(domain + [('company_id', '=', self.company_id.id)], limit=1)
            if company_template:
                return company_template
        return templates.search(domain + [('company_id', '=', False)], limit=1)

    @api.onchange('velatek_contract_template_id', 'contract_type_id')
    def _onchange_template_contract_type_warning(self):
        if self.velatek_contract_template_id and self.contract_type_id:
            if self.velatek_contract_template_id.contract_type_id and self.velatek_contract_template_id.contract_type_id != self.contract_type_id:
                return {
                    'warning': {
                        'title': "Advertencia de Plantilla",
                        'message': "La plantilla seleccionada corresponde a un tipo de contrato diferente al seleccionado en el contrato."
                    }
                }

    velatek_production_unit = fields.Selection([
        ('sqm', 'Metro cuadrado (m²)'),
        ('linear_meter', 'Metro lineal'),
        ('unit', 'Unidad'),
        ('day', 'Día'),
        ('other', 'Otro')
    ], string='Unidad de producción')

    velatek_production_rate = fields.Monetary(
        string='Tarifa por unidad', 
        currency_field='currency_id',
        help="Tarifa de referencia por unidad de producción. La tarifa definitiva podrá variar según el proyecto o registro de producción."
    )

    @api.constrains('state', 'velatek_remuneration_mode', 'wage', 'velatek_production_unit', 'velatek_production_rate')
    def _check_contract_activation_requirements(self):
        for contract in self:
            if contract.wage < 0:
                raise ValidationError("El salario base no puede ser negativo.")
            
            if contract.state == 'open':
                if not contract.employee_id:
                    raise ValidationError("Debe seleccionar un empleado para activar el contrato.")
                if not contract.date_start:
                    raise ValidationError("Debe definir una fecha de inicio para activar el contrato.")
                if not contract.contract_type_id:
                    raise ValidationError("Debe seleccionar un tipo de contrato para activarlo.")
                if not contract.resource_calendar_id:
                    raise ValidationError("Debe seleccionar un horario de trabajo para activarlo.")
                if not contract.velatek_payment_frequency:
                    raise ValidationError("Debe seleccionar una frecuencia de pago para activar el contrato.")
                if not contract.velatek_work_schedule_type:
                    raise ValidationError("Debe seleccionar un tipo de jornada para activar el contrato.")
                
                # Reglas de Remuneración
                if contract.velatek_remuneration_mode in ['fixed', 'mixed']:
                    if contract.wage <= 0:
                        raise ValidationError("El salario base debe ser mayor que cero para contratos de salario fijo o mixto en curso.")
                
                if contract.velatek_remuneration_mode in ['production', 'mixed']:
                    if not contract.velatek_production_unit:
                        raise ValidationError("Debe definir una unidad de producción para la modalidad seleccionada.")
                    if contract.velatek_production_rate < 0:
                        raise ValidationError("La tarifa por unidad de producción no puede ser negativa.")

    @api.constrains('date_start', 'date_end')
    def _check_dates_velatek(self):
        for contract in self:
            if contract.date_start and contract.date_end and contract.date_end < contract.date_start:
                raise ValidationError("La fecha de finalización del contrato no puede ser anterior a la fecha de inicio.")

    def action_generate_contract(self):
        self.ensure_one()
        
        if not self.env.context.get('force_generate'):
            signed_docs = self.env['hr.contract.generated.document'].search_count([
                ('contract_id', '=', self.id),
                ('state', '=', 'signed')
            ])
            if signed_docs:
                return {
                    'name': 'Contrato ya firmado',
                    'type': 'ir.actions.act_window',
                    'view_mode': 'form',
                    'res_model': 'hr.contract.generate.confirm.wizard',
                    'target': 'new',
                    'context': {'default_contract_id': self.id}
                }
        
        return {
            'name': 'Generar Contrato',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'hr.contract.generate.wizard',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'default_template_id': (self.velatek_contract_template_id or self._get_default_contract_template()).id,
            }
        }

    def action_open_generated_documents(self):
        self.ensure_one()
        return {
            'name': 'Documentos Contractuales',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.contract.generated.document',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
