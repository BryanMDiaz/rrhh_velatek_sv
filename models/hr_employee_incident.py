from datetime import datetime, time, timedelta
from dateutil.easter import easter
import pytz
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .operational_security import (
    USER, RESPONSIBLE, MANAGER, require_group, check_create, check_write, check_edit,
)

class HrEmployeeIncident(models.Model):
    _name = 'hr.employee.incident'
    _check_company_auto = True
    _description = 'Incidencia del Empleado'
    _order = 'date desc'

    _MONTH_NAMES = (
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
    )

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, check_company=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, check_company=True)
    
    date = fields.Date(string='Fecha', default=fields.Date.context_today, required=True)
    date_from = fields.Date(string='Fecha desde', copy=False)
    date_to = fields.Date(string='Fecha hasta', copy=False)
    day_count_mode = fields.Selection([
        ('calendar', 'Días calendario'),
        ('work_schedule', 'Jornada / Horario laboral'),
    ], string='Modo de conteo de días', compute='_compute_day_count_mode', store=True, readonly=False)
    payment_frequency = fields.Selection(
        related='contract_id.velatek_payment_frequency',
        string='Frecuencia de pago', store=True, readonly=True,
    )
    period_scope = fields.Selection([
        ('monthly', 'Mes completo'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro'),
    ], string='Tipo de período', compute='_compute_period_scope', store=True, readonly=True)
    period_name = fields.Char(
        string='Período de incidencia', compute='_compute_period_name',
        store=True, readonly=True, index=True,
    )
    
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
    ], string='Tipo de Incidencia', required=True)

    overtime_type = fields.Selection([
        ('daytime', 'Diurna'),
        ('nighttime', 'Nocturna'),
    ], string='Modalidad de hora extra', copy=False)
    overtime_day_context = fields.Selection([
        ('ordinary', 'Día ordinario'),
        ('holiday_work', 'Asueto trabajado'),
        ('weekly_rest_work', 'Descanso semanal trabajado'),
    ], string='Día en que se trabajaron las horas extra', default='ordinary')
    remuneration_mode = fields.Selection(related='contract_id.velatek_remuneration_mode')
    verified_hourly_rate = fields.Monetary(
        string='Salario básico por hora verificado', copy=False,
        help='Para remuneración variable, consigne el salario básico por hora determinado según el artículo 142 y documente su cálculo en Notas.',
    )
    overtime_base_wage = fields.Monetary(
        string='Salario mensual utilizado', copy=False, readonly=True,
    )
    overtime_hours_per_day = fields.Float(
        string='Horas ordinarias por jornada', copy=False, readonly=True,
    )
    overtime_hourly_rate = fields.Monetary(
        string='Valor de hora', copy=False, readonly=True,
    )
    overtime_multiplier = fields.Float(
        string='Multiplicador', copy=False, readonly=True,
    )
    night_work_base_wage = fields.Monetary(
        string='Salario base (Trabajo nocturno)', copy=False, readonly=True,
    )
    night_work_hours_per_day = fields.Float(
        string='Horas de jornada diurna equivalente', copy=False, readonly=True,
    )
    night_work_hourly_rate = fields.Monetary(
        string='Valor hora base', copy=False, readonly=True,
    )
    night_work_percentage = fields.Float(
        string='Porcentaje recargo nocturno', default=25.0, copy=False, readonly=True,
    )
    holiday_work_base_wage = fields.Monetary(
        string='Salario base (Asueto trabajado)', copy=False, readonly=True,
    )
    holiday_work_hours_per_day = fields.Float(
        string='Horas ordinarias por jornada', copy=False, readonly=True,
    )
    holiday_work_daily_rate = fields.Monetary(
        string='Valor día (Asueto)', copy=False, readonly=True,
    )
    holiday_work_hourly_rate = fields.Monetary(
        string='Valor hora (Asueto)', copy=False, readonly=True,
    )
    compensatory_rest_date = fields.Date(
        string='Fecha de descanso compensatorio', copy=False,
    )
    compensatory_rest_granted = fields.Boolean(
        string='Descanso compensatorio gozado', copy=False, default=False,
    )
    weekly_rest_work_base_wage = fields.Monetary(
        string='Salario base (Descanso trabajado)', copy=False, readonly=True,
    )
    weekly_rest_work_hours_per_day = fields.Float(
        string='Horas ordinarias por jornada', copy=False, readonly=True,
    )
    weekly_rest_work_daily_rate = fields.Monetary(
        string='Valor día (Descanso)', copy=False, readonly=True,
    )
    weekly_rest_work_hourly_rate = fields.Monetary(
        string='Valor hora (Descanso)', copy=False, readonly=True,
    )
    sick_leave_type = fields.Selection([
        ('common', 'Enfermedad común'),
        ('maternity', 'Maternidad'),
        ('occupational', 'Riesgo profesional / Accidente laboral'),
        ('other', 'Otro'),
    ], string='Tipo de incapacidad', copy=False)
    sick_settlement_reviewed = fields.Boolean(string='Liquidación de incapacidad verificada', copy=False)
    sick_ordinary_daily = fields.Monetary(string='Salario ordinario diario a sustituir', copy=False)
    sick_employer_daily = fields.Monetary(string='Prestación diaria a cargo del empleador', copy=False)
    sick_isss_daily = fields.Monetary(string='Subsidio diario del ISSS (informativo)', copy=False)
    sick_employer_affects_isss = fields.Boolean(string='Prestación patronal incluida en base ISSS', copy=False)
    sick_employer_affects_afp = fields.Boolean(string='Prestación patronal incluida en base AFP', copy=False)
    sick_employer_affects_isr = fields.Boolean(string='Prestación patronal incluida en base ISR', copy=False)

    issuing_entity = fields.Char(string='Entidad emisora', copy=False)
    certificate_reference = fields.Char(string='No. de incapacidad / Referencia', copy=False)
    attachment_id = fields.Many2one('ir.attachment', string='Comprobante de incapacidad', copy=False)
    support_file = fields.Binary(string='Documento de respaldo', attachment=True, copy=False)
    support_filename = fields.Char(string='Nombre del archivo de respaldo', copy=False)
    reason_id = fields.Many2one(
        'hr.incident.reason', string='Motivo / Clasificación',
        check_company=True,
    )
    reason_legal_guidance = fields.Text(related='reason_id.legal_guidance', readonly=True)
    reason_code = fields.Char(related='reason_id.code', string='Código del motivo', readonly=True)

    @api.onchange('incident_type')
    def _onchange_incident_reason_type(self):
        if self.reason_id.incident_type and self.reason_id.incident_type != self.incident_type:
            self.reason_id = False

    @api.constrains('incident_type', 'reason_id')
    def _check_incident_reason_type(self):
        for incident in self:
            if incident.reason_id.incident_type and incident.reason_id.incident_type != incident.incident_type:
                raise ValidationError(_('El motivo seleccionado no corresponde al tipo de incidencia.'))
    concept_id = fields.Many2one(
        'hr.incident.concept', string='Concepto de incidencia',
        check_company=True,
    )
    deduction_concept_id = fields.Many2one(
        'hr.incident.concept', string='Concepto de deducción',
        check_company=True,
    )
    beneficiary_name = fields.Char(string='Nombre del beneficiario', copy=False)
    beneficiary_partner_id = fields.Many2one('res.partner', string='Beneficiario', copy=False, check_company=True)
    authorization_reference = fields.Char(string='Ref. de autorización / Acta', copy=False)
    authorization_date = fields.Date(string='Fecha de autorización', copy=False)
    authorized_by_id = fields.Many2one('res.users', string='Autorizado por', copy=False, check_company=True)
    absence_base_wage = fields.Monetary(
        string='Salario mensual utilizado', copy=False, readonly=True,
    )
    absence_hours_per_day = fields.Float(
        string='Horas ordinarias por jornada', copy=False, readonly=True,
    )
    absence_total_hours = fields.Float(
        string='Horas totales equivalentes', copy=False, readonly=True,
    )
    absence_daily_rate = fields.Monetary(
        string='Valor de día', copy=False, readonly=True,
    )
    absence_hourly_rate = fields.Monetary(
        string='Valor de hora', copy=False, readonly=True,
    )
    
    ordinary_night_hours = fields.Float(string='Horas nocturnas ordinarias', copy=False, readonly=True)
    night_overtime_hours = fields.Float(string='Horas extra nocturnas', copy=False, readonly=True)
    source_incident_id = fields.Many2one('hr.employee.incident', string='Incidencia origen', copy=False, readonly=True)
    generated_incident_ids = fields.One2many('hr.employee.incident', 'source_incident_id', string='Incidencias generadas', readonly=True)

    rate_parameter_id = fields.Many2one(
        'hr.sv.labor.rate', string='Parámetro de tasa laboral',
        copy=False, readonly=True, check_company=True,
    )
    rate_snapshot = fields.Float(
        string='Tasa / Multiplicador aplicado', copy=False, readonly=True,
    )
    quantity_days = fields.Float(string='Días')
    quantity_hours = fields.Float(string='Horas')
    amount = fields.Monetary(string='Descuento preliminar / Importe')
    
    viatic_treatment = fields.Selection([
        ('remunerative', 'Remunerativo'),
        ('non_remunerative', 'No remunerativo'),
    ], string='Tratamiento del Viático', copy=False, help="REMUNERATIVO: El viático forma parte de la remuneración del trabajador y puede integrar las bases legales configuradas.\nNO REMUNERATIVO: El viático corresponde a gasto, reembolso o asignación de trabajo no remunerativa y no debe integrar automáticamente las bases legales configuradas.")

    affects_isss_snapshot = fields.Boolean('Afecta ISSS (Snapshot)', copy=False)
    affects_afp_snapshot = fields.Boolean('Afecta AFP (Snapshot)', copy=False)
    affects_isr_snapshot = fields.Boolean('Afecta ISR (Snapshot)', copy=False)

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('to_approve', 'Por aprobar'),
        ('approved', 'Aprobado'),
        ('processed', 'Procesado'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='draft', required=True)
    
    notes = fields.Text(string='Notas')
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    processed_prepayroll_id = fields.Many2one(
        'hr.prepayroll',
        string='Preplanilla procesada',
        copy=False,
        readonly=True,
        index=True,
        check_company=True,
    )

    @api.depends('contract_id', 'contract_id.velatek_payment_frequency')
    def _compute_period_scope(self):
        for record in self:
            if record.contract_id and record.contract_id.velatek_payment_frequency:
                record.period_scope = record.contract_id.velatek_payment_frequency
            else:
                record.period_scope = 'monthly'

    @api.depends('date', 'date_from', 'period_scope')
    def _compute_period_name(self):
        for record in self:
            ref_date = record.date or record.date_from
            if not ref_date:
                record.period_name = False
                continue

            month_year = '%s %s' % (
                self._MONTH_NAMES[ref_date.month], ref_date.year,
            )
            if record.period_scope == 'biweekly':
                half = 1 if ref_date.day <= 15 else 2
                record.period_name = 'Quincena %s - %s' % (half, month_year)
            elif record.period_scope == 'weekly':
                week = ref_date.isocalendar()[1]
                record.period_name = 'Semana %s - %s' % (week, month_year)
            elif record.period_scope == 'fourteen_days':
                record.period_name = 'Catorcena - %s' % month_year
            else:
                record.period_name = month_year

    @api.model
    def _get_labor_rate(self, code, date, company):
        if not date or not code:
            raise ValidationError(_("Se requiere código y fecha para obtener la tasa laboral."))
        rate = self.env['hr.sv.labor.rate'].get_active_rate(code, date, company)
        minimum_factors = {
            'overtime_day': 2.0, 'overtime_night': 2.5, 'night_work': 0.25,
            'holiday_work': 2.0, 'weekly_rest_work': 1.5,
        }
        if rate.get_factor() < minimum_factors[code]:
            raise ValidationError(_('La tasa laboral «%s» es inferior al mínimo aplicable.', rate.name))
        return rate

    @api.model
    def _prepare_overtime_values(self, vals, record=None):
        incident_type = vals.get('incident_type', record.incident_type if record else False)
        if incident_type != 'overtime':
            return vals

        contract_id = vals.get('contract_id', record.contract_id.id if record and record.contract_id else False)
        overtime_type = vals.get('overtime_type', record.overtime_type if record else False)
        quantity_hours = vals.get('quantity_hours', record.quantity_hours if record else 0.0)
        contract = self.env['hr.contract'].browse(contract_id).exists()
        if not contract or not overtime_type or not quantity_hours:
            return vals

        date_ref = vals.get('date', record.date if record else False) or vals.get('date_from', record.date_from if record else False) or fields.Date.context_today(self)
        company = contract.company_id or self.env.company
        code = 'overtime_day' if overtime_type == 'daytime' else 'overtime_night'
        rate_rec = self._get_labor_rate(code, date_ref, company)
        multiplier = rate_rec.get_factor()
        day_context = vals.get('overtime_day_context', record.overtime_day_context if record else 'ordinary')
        if day_context in ('holiday_work', 'weekly_rest_work'):
            special_rate = self._get_labor_rate(day_context, date_ref, company)
            multiplier *= special_rate.get_factor()

        wage, _calendar, hours_per_day = contract._velatek_terms_on(date_ref)
        mode = contract.velatek_remuneration_mode or 'fixed'

        note_msg = "El valor de hora requiere determinación en nómina por remuneración variable."

        vals.update({
            'rate_parameter_id': rate_rec.id,
            'rate_snapshot': multiplier,
        })

        verified_hourly = vals.get('verified_hourly_rate', record.verified_hourly_rate if record else 0.0)
        if mode == 'fixed' or verified_hourly > 0:
            hourly_rate = wage / 30.0 / hours_per_day if mode == 'fixed' else verified_hourly
            vals.update({
                'overtime_base_wage': wage,
                'overtime_hours_per_day': hours_per_day,
                'overtime_hourly_rate': hourly_rate,
                'overtime_multiplier': multiplier,
                'amount': quantity_hours * hourly_rate * multiplier,
            })
        else:
            current_notes = vals.get('notes', record.notes if record else '') or ''
            if note_msg not in current_notes:
                new_notes = (current_notes + '\n' + note_msg).strip() if current_notes else note_msg
                vals['notes'] = new_notes

            vals.update({
                'overtime_base_wage': wage if mode == 'mixed' else 0.0,
                'overtime_hours_per_day': hours_per_day,
                'overtime_hourly_rate': 0.0,
                'overtime_multiplier': multiplier,
                'amount': 0.0,
            })
        return vals

    @api.model
    def _prepare_night_work_values(self, vals, record=None):
        incident_type = vals.get('incident_type', record.incident_type if record else False)
        if incident_type != 'night_work':
            return vals

        contract_id = vals.get('contract_id', record.contract_id.id if record and record.contract_id else False)
        quantity_hours = vals.get('quantity_hours', record.quantity_hours if record else 0.0)
        contract = self.env['hr.contract'].browse(contract_id).exists()
        if not contract or not quantity_hours:
            return vals

        date_ref = vals.get('date', record.date if record else False) or fields.Date.context_today(self)
        company = contract.company_id or self.env.company
        rate_rec = self._get_labor_rate('night_work', date_ref, company)
        rate_factor = rate_rec.get_factor()
        rate_percentage = rate_rec.value if rate_rec.rate_type == 'percentage' and rate_rec.value > 1.0 else (rate_factor * 100.0)

        wage, calendar, historical_hours = contract._velatek_terms_on(date_ref)
        hours_per_day = min(historical_hours, 7.0)
        
        ordinary_night_hours = min(quantity_hours, hours_per_day)
        night_overtime_hours = quantity_hours - ordinary_night_hours if quantity_hours > ordinary_night_hours else 0.0

        mode = contract.velatek_remuneration_mode or 'fixed'
        note_msg = "El valor de hora requiere determinación en nómina por remuneración variable."

        vals.update({
            'rate_parameter_id': rate_rec.id,
            'rate_snapshot': rate_factor,
            'ordinary_night_hours': ordinary_night_hours,
            'night_overtime_hours': night_overtime_hours,
        })

        # Art. 168: surcharge over equivalent daytime work. Art. 161
        # limits shift duration; it does not change the daytime wage divisor.
        daytime_hours = historical_hours
        if any(att.hour_from >= 19.0 or att.hour_to <= 6.0 or (att.hour_from < 6.0 and att.hour_to > 0) for att in calendar.attendance_ids):
            daytime_hours = 8.0
        verified_hourly = vals.get('verified_hourly_rate', record.verified_hourly_rate if record else 0.0)
        if mode == 'fixed' or verified_hourly > 0:
            daily_rate = wage / 30.0
            hourly_rate = daily_rate / daytime_hours if mode == 'fixed' else verified_hourly
            surcharge_amount = ordinary_night_hours * hourly_rate * rate_factor
            vals.update({
                'night_work_base_wage': wage,
                'night_work_hours_per_day': daytime_hours,
                'night_work_hourly_rate': hourly_rate,
                'night_work_percentage': rate_percentage,
                'amount': surcharge_amount,
            })
        else:
            current_notes = vals.get('notes', record.notes if record else '') or ''
            if note_msg not in current_notes:
                new_notes = (current_notes + '\n' + note_msg).strip() if current_notes else note_msg
                vals['notes'] = new_notes

            vals.update({
                'night_work_base_wage': wage if mode == 'mixed' else 0.0,
                'night_work_hours_per_day': daytime_hours,
                'night_work_hourly_rate': 0.0,
                'night_work_percentage': rate_percentage,
                'amount': 0.0,
            })
        return vals

    @api.model
    def _prepare_holiday_work_values(self, vals, record=None):
        incident_type = vals.get('incident_type', record.incident_type if record else False)
        if incident_type != 'holiday_work':
            return vals

        contract_id = vals.get('contract_id', record.contract_id.id if record and record.contract_id else False)
        quantity_days = vals.get('quantity_days', record.quantity_days if record else 0.0)
        quantity_hours = vals.get('quantity_hours', record.quantity_hours if record else 0.0)
        contract = self.env['hr.contract'].browse(contract_id).exists()
        if not contract or (not quantity_days and not quantity_hours):
            return vals

        date_ref = vals.get('date', record.date if record else False) or vals.get('date_from', record.date_from if record else False) or fields.Date.context_today(self)
        company = contract.company_id or self.env.company
        rate_rec = self._get_labor_rate('holiday_work', date_ref, company)
        multiplier = rate_rec.get_factor()

        wage, _calendar, hours_per_day = contract._velatek_terms_on(date_ref)
        mode = contract.velatek_remuneration_mode or 'fixed'
        note_msg = "El valor de hora requiere determinación en nómina por remuneración variable."

        vals.update({
            'rate_parameter_id': rate_rec.id,
            'rate_snapshot': multiplier,
        })

        verified_hourly = vals.get('verified_hourly_rate', record.verified_hourly_rate if record else 0.0)
        if mode == 'fixed' or verified_hourly > 0:
            daily_rate = wage / 30.0 if mode == 'fixed' else verified_hourly * hours_per_day
            hourly_rate = daily_rate / hours_per_day
            calculated_amount = (quantity_days * daily_rate * multiplier) + (quantity_hours * hourly_rate * multiplier)
            vals.update({
                'holiday_work_base_wage': wage,
                'holiday_work_hours_per_day': hours_per_day,
                'holiday_work_daily_rate': daily_rate,
                'holiday_work_hourly_rate': hourly_rate,
                'amount': calculated_amount,
            })
        else:
            current_notes = vals.get('notes', record.notes if record else '') or ''
            if note_msg not in current_notes:
                new_notes = (current_notes + '\n' + note_msg).strip() if current_notes else note_msg
                vals['notes'] = new_notes

            vals.update({
                'holiday_work_base_wage': wage if mode == 'mixed' else 0.0,
                'holiday_work_hours_per_day': hours_per_day,
                'holiday_work_daily_rate': 0.0,
                'holiday_work_hourly_rate': 0.0,
                'amount': 0.0,
            })
        return vals

    @api.model
    def _prepare_weekly_rest_work_values(self, vals, record=None):
        incident_type = vals.get('incident_type', record.incident_type if record else False)
        if incident_type != 'weekly_rest_work':
            return vals

        contract_id = vals.get('contract_id', record.contract_id.id if record and record.contract_id else False)
        quantity_days = vals.get('quantity_days', record.quantity_days if record else 0.0)
        quantity_hours = vals.get('quantity_hours', record.quantity_hours if record else 0.0)
        contract = self.env['hr.contract'].browse(contract_id).exists()
        if not contract or (not quantity_days and not quantity_hours):
            return vals

        date_ref = vals.get('date', record.date if record else False) or vals.get('date_from', record.date_from if record else False) or fields.Date.context_today(self)
        company = contract.company_id or self.env.company
        rate_rec = self._get_labor_rate('weekly_rest_work', date_ref, company)
        multiplier = rate_rec.get_factor()

        wage, _calendar, hours_per_day = contract._velatek_terms_on(date_ref)
        mode = contract.velatek_remuneration_mode or 'fixed'
        note_msg = "El valor de hora requiere determinación en nómina por remuneración variable."

        vals.update({
            'rate_parameter_id': rate_rec.id,
            'rate_snapshot': multiplier,
        })

        verified_hourly = vals.get('verified_hourly_rate', record.verified_hourly_rate if record else 0.0)
        if mode == 'fixed' or verified_hourly > 0:
            daily_rate = wage / 30.0 if mode == 'fixed' else verified_hourly * hours_per_day
            hourly_rate = daily_rate / hours_per_day
            calculated_amount = (quantity_days * daily_rate * multiplier) + (quantity_hours * hourly_rate * multiplier)
            vals.update({
                'weekly_rest_work_base_wage': wage,
                'weekly_rest_work_hours_per_day': hours_per_day,
                'weekly_rest_work_daily_rate': daily_rate,
                'weekly_rest_work_hourly_rate': hourly_rate,
                'amount': calculated_amount,
            })
        else:
            current_notes = vals.get('notes', record.notes if record else '') or ''
            if note_msg not in current_notes:
                new_notes = (current_notes + '\n' + note_msg).strip() if current_notes else note_msg
                vals['notes'] = new_notes

            vals.update({
                'weekly_rest_work_base_wage': wage if mode == 'mixed' else 0.0,
                'weekly_rest_work_hours_per_day': hours_per_day,
                'weekly_rest_work_daily_rate': 0.0,
                'weekly_rest_work_hourly_rate': 0.0,
                'amount': 0.0,
            })
        return vals

    @api.depends('concept_id', 'concept_id.day_count_mode', 'deduction_concept_id', 'deduction_concept_id.day_count_mode')
    def _compute_day_count_mode(self):
        for record in self:
            concept = record.concept_id or record.deduction_concept_id
            if concept and concept.day_count_mode:
                record.day_count_mode = concept.day_count_mode
            elif not record.day_count_mode:
                record.day_count_mode = 'calendar'

    @api.model
    def _get_absence_days(self, date_from, date_to, day_count_mode='calendar', contract=None, employee=None):
        if not date_from or not date_to or date_to < date_from:
            return 0.0
        if isinstance(date_from, str):
            date_from = fields.Date.from_string(date_from)
        if isinstance(date_to, str):
            date_to = fields.Date.from_string(date_to)

        if day_count_mode == 'work_schedule':
            calendar = False
            if contract and contract._velatek_terms_on(date_from)[1]:
                calendar = contract._velatek_terms_on(date_from)[1]
            elif employee and employee.resource_calendar_id:
                calendar = employee.resource_calendar_id
            elif self.env.company.resource_calendar_id:
                calendar = self.env.company.resource_calendar_id

            if calendar:
                tz_name = calendar.tz or self.env.user.tz or 'America/El_Salvador'
                try:
                    tz = pytz.timezone(tz_name)
                except Exception:
                    tz = pytz.UTC
                dt_start = tz.localize(datetime.combine(date_from, time.min))
                dt_end = tz.localize(datetime.combine(date_to, time.max))
                duration_data = calendar.get_work_duration_data(dt_start, dt_end)
                return float(duration_data.get('days', 0.0))

        return float((date_to - date_from).days + 1)

    @api.model
    def _prepare_absence_values(self, vals, record=None):
        incident_type = vals.get('incident_type', record.incident_type if record else False)
        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        if incident_type not in absence_types:
            return vals

        date_from = vals.get('date_from', record.date_from if record else False)
        date_to = vals.get('date_to', record.date_to if record else False)
        if isinstance(date_from, str):
            date_from = fields.Date.from_string(date_from)
        if isinstance(date_to, str):
            date_to = fields.Date.from_string(date_to)

        day_count_mode = vals.get('day_count_mode')
        if not day_count_mode and record:
            day_count_mode = record.day_count_mode
        if not day_count_mode:
            concept_id = vals.get('concept_id', record.concept_id.id if record and record.concept_id else False)
            if concept_id:
                concept = self.env['hr.incident.concept'].browse(concept_id).exists()
                if concept and concept.day_count_mode:
                    day_count_mode = concept.day_count_mode
        if not day_count_mode:
            day_count_mode = 'calendar'

        contract_id = vals.get('contract_id', record.contract_id.id if record and record.contract_id else False)
        contract = self.env['hr.contract'].browse(contract_id).exists() if contract_id else False
        employee = record.employee_id if record else False
        quantity_hours = vals.get('quantity_hours', record.quantity_hours if record else 0.0)

        if date_from and date_to and date_to >= date_from:
            vals['date'] = date_from
            dates_changed = not record or {'date_from', 'date_to', 'day_count_mode'} & vals.keys()
            if 'quantity_days' not in vals and not quantity_hours and dates_changed:
                vals['quantity_days'] = self._get_absence_days(
                    date_from, date_to, day_count_mode,
                    contract=contract, employee=employee,
                )

        quantity_days = vals.get('quantity_days', record.quantity_days if record else 0.0)
        if not contract:
            return vals

        if date_from and date_to and any(date_from < term.effective_from <= date_to for term in contract.velatek_terms_ids):
            raise ValidationError(_('Divida la incidencia en tramos cuando cambie el salario o la jornada.'))
        wage, _calendar, hours_per_day = contract._velatek_terms_on(date_from or vals.get('date') or (record.date if record else None))
        daily_rate = wage / 30.0
        hourly_rate = daily_rate / hours_per_day
        vals.update({
            'absence_base_wage': wage,
            'absence_hours_per_day': hours_per_day,
            'absence_total_hours': (quantity_days * hours_per_day) + quantity_hours,
            'absence_daily_rate': daily_rate,
            'absence_hourly_rate': hourly_rate,
        })
        if incident_type in ('unpaid_leave', 'unjustified_absence', 'suspension'):
            vals['amount'] = (quantity_days * daily_rate) + (quantity_hours * hourly_rate)
        return vals

    @api.onchange('date_from', 'date_to', 'incident_type', 'day_count_mode', 'concept_id', 'contract_id')
    def _onchange_absence_dates(self):
        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        for record in self:
            if record.incident_type in absence_types and record.date_from and record.date_to:
                if record.date_to >= record.date_from:
                    mode = record.day_count_mode or (record.concept_id.day_count_mode if record.concept_id else 'calendar')
                    if not record.quantity_hours:
                        record.quantity_days = self._get_absence_days(
                            record.date_from, record.date_to, mode,
                            contract=record.contract_id,
                            employee=record.employee_id,
                        )
                    record.date = record.date_from
                    record._onchange_absence_amount()

    @api.onchange('incident_type', 'contract_id', 'quantity_hours', 'overtime_type', 'date', 'overtime_day_context', 'verified_hourly_rate')
    def _onchange_overtime_amount(self):
        for record in self:
            if record.incident_type != 'overtime':
                continue
            values = record._prepare_overtime_values({}, record=record)
            for field_name, value in values.items():
                record[field_name] = value

    @api.onchange('incident_type', 'contract_id', 'quantity_hours', 'date', 'verified_hourly_rate')
    def _onchange_night_work_amount(self):
        for record in self:
            if record.incident_type != 'night_work':
                continue
            values = record._prepare_night_work_values({}, record=record)
            for field_name, value in values.items():
                record[field_name] = value

    @api.onchange('incident_type', 'contract_id', 'quantity_days', 'quantity_hours', 'date', 'verified_hourly_rate')
    def _onchange_holiday_work_amount(self):
        for record in self:
            if record.incident_type != 'holiday_work':
                continue
            values = record._prepare_holiday_work_values({}, record=record)
            for field_name, value in values.items():
                record[field_name] = value

    @api.onchange('incident_type', 'contract_id', 'quantity_days', 'quantity_hours', 'date', 'verified_hourly_rate')
    def _onchange_weekly_rest_work_amount(self):
        for record in self:
            if record.incident_type != 'weekly_rest_work':
                continue
            values = record._prepare_weekly_rest_work_values({}, record=record)
            for field_name, value in values.items():
                record[field_name] = value

    @api.onchange('quantity_hours')
    def _onchange_partial_day_hours(self):
        partial_day_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        for record in self:
            if (
                record.incident_type in partial_day_types and record.quantity_hours
                and record.date_from and record.date_from == record.date_to
            ):
                record.quantity_days = 0
                record._onchange_absence_amount()

    @api.onchange('incident_type', 'contract_id', 'quantity_days', 'quantity_hours')
    def _onchange_absence_amount(self):
        for record in self:
            if record.incident_type not in ('unpaid_leave', 'unjustified_absence', 'suspension'):
                continue
            values = record._prepare_absence_values({}, record=record)
            for field_name, value in values.items():
                record[field_name] = value

    @api.onchange('deduction_concept_id')
    def _onchange_deduction_concept_id(self):
        if self.deduction_concept_id:
            self.concept_id = self.deduction_concept_id

    @api.onchange('concept_id')
    def _onchange_concept_id(self):
        if self.incident_type == 'deduction' and self.concept_id:
            self.deduction_concept_id = self.concept_id

    @api.onchange('incident_type', 'viatic_treatment', 'concept_id', 'reason_id')
    def _onchange_viatic_treatment(self):
        for record in self:
            if record.incident_type != 'viaticos':
                record.viatic_treatment = False
            elif record.reason_code == 'sv_expense_reimbursement':
                record.viatic_treatment = 'non_remunerative'
            
            # Actualizar snapshots
            if record.incident_type == 'viaticos' and record.viatic_treatment == 'non_remunerative':
                record.affects_isss_snapshot = False
                record.affects_afp_snapshot = False
                record.affects_isr_snapshot = False
            elif record.concept_id:
                record.affects_isss_snapshot = record.concept_id.affects_isss
                record.affects_afp_snapshot = record.concept_id.affects_afp
                record.affects_isr_snapshot = record.concept_id.affects_isr
            else:
                # Default para remunerativo u otros tipos sin concepto específico, pero usualmente asumen True
                if record.incident_type == 'viaticos' and record.viatic_treatment == 'remunerative':
                    record.affects_isss_snapshot = True
                    record.affects_afp_snapshot = True
                    record.affects_isr_snapshot = True
                else:
                    record.affects_isss_snapshot = False
                    record.affects_afp_snapshot = False
                    record.affects_isr_snapshot = False

    @api.model_create_multi
    def create(self, vals_list):
        check_create(self, vals_list)
        prepared_vals = []
        for vals in vals_list:
            values = dict(vals)
            if values.get('state') and values['state'] != 'draft':
                raise ValidationError(_("Los nuevos registros de incidencias deben crearse en estado Borrador."))

            if values.get('deduction_concept_id') and not values.get('concept_id'):
                values['concept_id'] = values['deduction_concept_id']
            elif values.get('concept_id') and not values.get('deduction_concept_id'):
                values['deduction_concept_id'] = values['concept_id']

            self._prepare_contribution_snapshots(values)

            values = self._prepare_overtime_values(values)
            values = self._prepare_night_work_values(values)
            values = self._prepare_holiday_work_values(values)
            values = self._prepare_weekly_rest_work_values(values)
            values = self._prepare_absence_values(values)
            prepared_vals.append(values)
        return super().create(prepared_vals)

    def _prepare_contribution_snapshots(self, values, record=None):
        incident_type = values.get('incident_type', record.incident_type if record else False)
        if incident_type == 'viaticos':
            reason_id = values.get('reason_id', record.reason_id.id if record else False)
            reason = self.env['hr.incident.reason'].browse(reason_id)
            if reason.code == 'sv_expense_reimbursement':
                values['viatic_treatment'] = 'non_remunerative'
        treatment = values.get('viatic_treatment', record.viatic_treatment if record else False)
        concept_id = values.get('concept_id', record.concept_id.id if record else False)
        concept = self.env['hr.incident.concept'].browse(concept_id)
        if not concept and not (incident_type == 'viaticos' and treatment):
            return
        for scheme in ('isss', 'afp', 'isr'):
            if incident_type == 'viaticos' and treatment == 'non_remunerative':
                applies = False
            elif concept:
                applies = concept[f'affects_{scheme}']
            else:
                applies = incident_type == 'viaticos' and treatment == 'remunerative'
            values[f'affects_{scheme}_snapshot'] = applies

    def write(self, vals):
        check_write(self, vals)
        if 'state' in vals:
            target_state = vals['state']
            for record in self:
                if record.state == target_state:
                    continue
                if record.state == 'processed':
                    raise ValidationError(_("El registro está Procesado y es inmutable. No permite cambios de estado."))
                if target_state == 'to_approve':
                    if record.state != 'draft':
                        raise ValidationError(_("Transición a 'Por Aprobar' solo permitida desde 'Borrador'."))
                elif target_state == 'approved':
                    if record.state != 'to_approve':
                        raise ValidationError(_("No se puede aprobar directamente una incidencia desde el estado '%s'. Debe estar en 'Por Aprobar'." % record.state))
                elif target_state == 'processed':
                    if record.state != 'approved':
                        raise ValidationError(_("Solo se pueden procesar incidencias en estado Aprobado."))
                elif target_state == 'draft':
                    if record.state not in ('to_approve', 'approved', 'cancelled'):
                        raise ValidationError(_("Transición a 'Borrador' no permitida desde el estado '%s'." % record.state))
                elif target_state == 'cancelled':
                    if record.state not in ('draft', 'to_approve', 'approved'):
                        raise ValidationError(_("Transición a 'Cancelado' no permitida desde el estado '%s'." % record.state))

        if vals.get('deduction_concept_id') and not vals.get('concept_id'):
            vals['concept_id'] = vals['deduction_concept_id']
        elif vals.get('concept_id') and not vals.get('deduction_concept_id'):
            vals['deduction_concept_id'] = vals['concept_id']

        calculated_inputs = {
            'incident_type', 'contract_id', 'quantity_days', 'quantity_hours',
            'overtime_type', 'overtime_day_context', 'verified_hourly_rate',
            'date', 'date_from', 'date_to', 'day_count_mode',
        }
        snapshot_inputs = {'incident_type', 'viatic_treatment', 'concept_id', 'reason_id'}
        recalculate = calculated_inputs.intersection(vals)
        refresh_snapshots = snapshot_inputs.intersection(vals)
        if recalculate or refresh_snapshots:
            for record in self:
                prepared_vals = dict(vals)
                if refresh_snapshots:
                    self._prepare_contribution_snapshots(prepared_vals, record)
                if recalculate:
                    prepared_vals = self._prepare_overtime_values(prepared_vals, record=record)
                    prepared_vals = self._prepare_night_work_values(prepared_vals, record=record)
                    prepared_vals = self._prepare_holiday_work_values(prepared_vals, record=record)
                    prepared_vals = self._prepare_weekly_rest_work_values(prepared_vals, record=record)
                    prepared_vals = self._prepare_absence_values(prepared_vals, record=record)
                super(HrEmployeeIncident, record).write(prepared_vals)
            return True
        return super().write(vals)

    def unlink(self):
        check_edit(self)
        return super().unlink()

    # ─── Constraints generales de valores negativos ───────────────────────────

    @api.constrains('quantity_days')
    def _check_quantity_days_positive(self):
        for record in self:
            if record.quantity_days < 0:
                raise ValidationError("El número de días no puede ser negativo.")

    @api.constrains('quantity_hours')
    def _check_quantity_hours_positive(self):
        for record in self:
            if record.quantity_hours < 0:
                raise ValidationError("El número de horas no puede ser negativo.")

    @api.constrains('amount')
    def _check_amount_positive(self):
        for record in self:
            if record.amount < 0:
                raise ValidationError("El importe no puede ser negativo.")

    @api.constrains('incident_type', 'viatic_treatment')
    def _check_viatic_treatment(self):
        for record in self:
            if record.incident_type == 'viaticos' and not record.viatic_treatment:
                raise ValidationError("Debe indicar si el viático es remunerativo o no remunerativo.")

    @api.constrains('state', 'reason_id', 'attachment_id', 'support_file', 'notes')
    def _check_expense_reimbursement_support(self):
        for record in self:
            if record.state not in ('approved', 'processed') or record.reason_code != 'sv_expense_reimbursement':
                continue
            if not (record.support_file or record.attachment_id) or not (record.notes or '').strip():
                raise ValidationError(_(
                    "Para aprobar un reintegro, adjunte el comprobante del gasto "
                    "y describa en Notas para qué actividad de trabajo se realizó."
                ))
            
    # ─── Constraints por tipo de incidencia ──────────────────────────────────

    @api.constrains(
        'incident_type', 'overtime_type',
        'quantity_hours', 'quantity_days', 'amount',
    )
    def _check_values_by_type(self):
        types_require_hours = ('overtime', 'night_work')
        types_require_amount = (
            'overtime', 'night_work', 'holiday_work', 'weekly_rest_work', 'unpaid_leave', 'unjustified_absence', 'suspension',
            'bonus', 'commission', 'viaticos', 'other_income', 'deduction',
        )
        types_require_days_or_hours = ('unpaid_leave', 'unjustified_absence', 'paid_leave', 'sick_leave', 'suspension', 'holiday_work', 'weekly_rest_work')

        for record in self:
            t = record.incident_type

            if t in types_require_hours:
                if record.quantity_hours <= 0:
                    raise ValidationError(
                        "Para el tipo '%s' se requieren horas mayores que cero." % record.incident_type
                    )
                if t == 'overtime' and not record.overtime_type:
                    raise ValidationError(
                        "Debe indicar si las horas extra son diurnas o nocturnas."
                    )

            if t in types_require_amount:
                is_variable_rate = (
                    t in ('overtime', 'night_work', 'holiday_work', 'weekly_rest_work') and
                    record.contract_id and
                    record.contract_id.velatek_remuneration_mode in ('production', 'mixed')
                )
                if not is_variable_rate and record.amount <= 0:
                    raise ValidationError(
                        "Para el tipo '%s' el importe debe ser mayor que cero." % record.incident_type
                    )

            if t in types_require_days_or_hours:
                if record.quantity_days <= 0 and record.quantity_hours <= 0:
                    raise ValidationError(
                        "Para el tipo '%s' debe registrar al menos días u horas mayores que cero." % record.incident_type
                    )

    @api.constrains('state', 'incident_type', 'amount', 'verified_hourly_rate', 'notes', 'contract_id')
    def _check_variable_wage_basis(self):
        calculated_types = ('overtime', 'night_work', 'holiday_work', 'weekly_rest_work')
        for incident in self:
            if (incident.state in ('approved', 'processed')
                    and incident.incident_type in calculated_types
                    and incident.remuneration_mode in ('production', 'mixed')
                    and (incident.verified_hourly_rate <= 0 or incident.amount <= 0
                         or not (incident.notes or '').replace(
                             "El valor de hora requiere determinación en nómina por remuneración variable.", ""
                         ).strip())):
                raise ValidationError(_(
                    'Registre el salario básico por hora verificado y documente su cálculo en Notas antes de aprobar esta incidencia.'
                ))

    @api.constrains('state', 'incident_type', 'date', 'contract_id')
    def _check_holiday_and_weekly_rest_overlap(self):
        conflicting_types = {
            'holiday_work': 'weekly_rest_work',
            'weekly_rest_work': 'holiday_work',
        }
        for incident in self:
            other_type = conflicting_types.get(incident.incident_type)
            if not other_type or incident.state not in ('approved', 'processed'):
                continue
            if self.search_count([
                ('contract_id', '=', incident.contract_id.id),
                ('date', '=', incident.date),
                ('incident_type', '=', other_type),
                ('state', 'in', ('approved', 'processed')),
            ], limit=1):
                raise ValidationError(_(
                    'Si el asueto coincide con el descanso semanal, registre una sola incidencia de asueto '
                    'con el motivo de coincidencia y consigne el descanso compensatorio (art. 194).'
                ))

    @api.constrains(
        'state', 'incident_type', 'reason_id', 'date', 'date_from', 'date_to',
        'employee_id', 'company_id', 'compensatory_rest_date', 'compensatory_rest_granted',
        'authorization_reference', 'attachment_id', 'support_file', 'sick_leave_type',
        'sick_settlement_reviewed', 'sick_ordinary_daily', 'sick_employer_daily',
        'sick_isss_daily', 'notes', 'quantity_days', 'quantity_hours', 'day_count_mode',
        'sick_employer_affects_isss', 'sick_employer_affects_afp', 'sick_employer_affects_isr',
    )
    def _check_approved_payroll_rules(self):
        self.filtered(lambda incident: incident.state == 'approved')._check_payroll_rules()

    def _check_payroll_rules(self):
        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        national_holidays = {(1, 1), (5, 1), (8, 6), (9, 15), (11, 2), (12, 25)}
        for incident in self:
            date = incident.date
            has_support = incident.support_file or incident.attachment_id
            if incident.state == 'approved':
                expected = incident._dated_amount_values().get('amount', incident.amount)
                if incident.currency_id.compare_amounts(incident.amount, expected):
                    raise ValidationError(_(
                        'El importe ya no coincide con el salario u horario vigente en la fecha de la incidencia. '
                        'Vuelva a borrador, revise el cálculo y apruébela nuevamente.'
                    ))
            if incident.incident_type == 'holiday_work' and date:
                holy_week = (easter(date.year) - date).days in (1, 2, 3)
                national = (date.month, date.day) in national_holidays or holy_week
                if incident.reason_code == 'sv_national_holiday' and not national:
                    raise ValidationError(_('La fecha no corresponde a un asueto nacional del art. 190. '
                                            'Si existe un asueto local o especial, clasifíquelo y registre su respaldo.'))
                if not national and not (incident.reason_id and (incident.authorization_reference or has_support)):
                    raise ValidationError(_('Registre la clasificación y la referencia o comprobante del asueto local o especial.'))

            needs_rest = incident.incident_type == 'weekly_rest_work' or (
                incident.incident_type == 'holiday_work' and incident.reason_code == 'sv_holiday_weekly_rest'
            )
            if needs_rest and date:
                rest_date = incident.compensatory_rest_date
                week_start = int(incident.company_id.velatek_work_week_start or '0')
                day_in_week = (date.weekday() - week_start) % 7
                week_end = date + timedelta(days=13 - day_in_week)
                if not rest_date or not date < rest_date <= week_end:
                    raise ValidationError(_('Registre un descanso compensatorio posterior al día trabajado, '
                                            'dentro de la misma semana laboral o de la siguiente (arts. 176 y 194).'))
                if incident.compensatory_rest_granted and rest_date > fields.Date.context_today(incident):
                    raise ValidationError(_('La fecha del descanso compensatorio gozado todavía no ha llegado.'))

            if incident.incident_type in absence_types and incident.date_from and incident.date_to:
                if self.search_count([
                    ('id', '!=', incident.id),
                    ('employee_id', '=', incident.employee_id.id),
                    ('company_id', '=', incident.company_id.id),
                    ('incident_type', 'in', absence_types),
                    ('state', 'in', ('approved', 'processed')),
                    ('date_from', '<=', incident.date_to),
                    ('date_to', '>=', incident.date_from),
                ], limit=1):
                    raise ValidationError(_('Ya existe otra ausencia, permiso, incapacidad o suspensión '
                                            'aprobada o procesada en ese intervalo. Revise las fechas antes de liquidar.'))

            if incident.incident_type == 'sick_leave':
                if not (incident.sick_leave_type and has_support and incident.sick_settlement_reviewed
                        and (incident.notes or '').strip()):
                    raise ValidationError(_('Para liquidar la incapacidad, indique el tipo, adjunte el certificado '
                                            'y documente y confirme la liquidación en Notas.'))
                if min(incident.sick_ordinary_daily, incident.sick_employer_daily, incident.sick_isss_daily) < 0:
                    raise ValidationError(_('Los importes diarios de incapacidad deben ser iguales o mayores que cero.'))
                if not incident.date_from or not incident.date_to or incident.quantity_hours or incident.day_count_mode != 'calendar' or (
                    incident.quantity_days != (incident.date_to - incident.date_from).days + 1
                ):
                    raise ValidationError(_('Liquide la incapacidad por días calendario completos. Use tramos '
                                            'separados cuando cambie el pagador o el importe diario.'))

    @api.constrains('incident_type', 'concept_id', 'deduction_concept_id')
    def _check_deduction_concept(self):
        for record in self:
            if record.incident_type == 'deduction':
                if not record.concept_id and not record.deduction_concept_id:
                    raise ValidationError("Para una incidencia de tipo 'Deducción', debe especificar el concepto de deducción.")

    @api.constrains('incident_type', 'notes')
    def _check_other_notes(self):
        for record in self:
            if record.incident_type == 'other':
                if not record.notes or not record.notes.strip():
                    raise ValidationError("Para la incidencia de tipo 'Otro', se requieren notas explicativas detalladas.")

    # ─── Constraints de integridad contractual ───────────────────────────────

    @api.constrains('employee_id', 'contract_id')
    def _check_contract_employee(self):
        for record in self:
            if record.contract_id and record.employee_id:
                if record.contract_id.employee_id != record.employee_id:
                    raise ValidationError(
                        "El contrato seleccionado no pertenece al empleado '%s'." % record.employee_id.name
                    )

    @api.constrains('company_id', 'contract_id')
    def _check_contract_company(self):
        for record in self:
            if record.contract_id and record.company_id:
                if record.contract_id.company_id != record.company_id:
                    raise ValidationError(
                        "La compañía del contrato no coincide con la compañía de la incidencia."
                    )

    @api.constrains('date_from', 'date_to', 'date', 'contract_id', 'incident_type')
    def _check_absence_date_range(self):
        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        for record in self:
            if record.incident_type in absence_types:
                if not record.date_from or not record.date_to:
                    raise ValidationError(
                        "Para las incidencias de ausencia es obligatorio registrar 'Fecha desde' y 'Fecha hasta'."
                    )
                if record.date_to < record.date_from:
                    raise ValidationError(
                        "La 'Fecha hasta' (%s) no puede ser anterior a la 'Fecha desde' (%s)."
                        % (record.date_to, record.date_from)
                    )
                if record.contract_id:
                    contract = record.contract_id
                    if contract.date_start and record.date_to < contract.date_start:
                        raise ValidationError(
                            "El rango de la ausencia (%s - %s) finaliza antes del inicio del contrato (%s)."
                            % (record.date_from, record.date_to, contract.date_start)
                        )
                    if contract.date_end and record.date_from > contract.date_end:
                        raise ValidationError(
                            "El rango de la ausencia (%s - %s) inicia después del fin del contrato (%s)."
                            % (record.date_from, record.date_to, contract.date_end)
                        )

    @api.constrains('date', 'contract_id')
    def _check_date_within_contract(self):
        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')
        for record in self:
            if record.incident_type not in absence_types and record.date and record.contract_id:
                contract = record.contract_id
                if contract.date_start and record.date < contract.date_start:
                    raise ValidationError(
                        "La fecha de la incidencia (%s) es anterior al inicio del contrato (%s)."
                        % (record.date, contract.date_start)
                    )
                if contract.date_end and record.date > contract.date_end:
                    raise ValidationError(
                        "La fecha de la incidencia (%s) es posterior al fin del contrato (%s)."
                        % (record.date, contract.date_end)
                    )

    # ─── Onchange helpers ─────────────────────────────────────────────────────

    @api.onchange('employee_id', 'date')
    def _onchange_employee_id_date(self):
        if not self.employee_id:
            return

        ref_date = self.date or fields.Date.context_today(self)
        domain = [
            ('employee_id', '=', self.employee_id.id),
            ('company_id', '=', (self.company_id or self.env.company).id),
            ('state', '=', 'open'),
            ('date_start', '<=', ref_date),
            '|', ('date_end', '=', False), ('date_end', '>=', ref_date),
        ]
        contracts = self.env['hr.contract'].search(domain)

        if len(contracts) == 1:
            self.contract_id = contracts
        elif len(contracts) > 1:
            self.contract_id = False
            return {
                'warning': {
                    'title': 'Múltiples contratos válidos',
                    'message': (
                        'El empleado "%s" tiene más de un contrato vigente para la fecha seleccionada.\n'
                        'Por favor seleccione el contrato correcto manualmente.'
                        % self.employee_id.name
                    ),
                }
            }

    # ─── State transitions ────────────────────────────────────────────────────

    def _dated_amount_values(self):
        self.ensure_one()
        if not self.contract_id.velatek_terms_ids:
            return {}
        calculation = {
            'overtime': self._prepare_overtime_values,
            'night_work': self._prepare_night_work_values,
            'holiday_work': self._prepare_holiday_work_values,
            'weekly_rest_work': self._prepare_weekly_rest_work_values,
            'unpaid_leave': self._prepare_absence_values,
            'unjustified_absence': self._prepare_absence_values,
            'suspension': self._prepare_absence_values,
        }.get(self.incident_type)
        return calculation({}, record=self) if calculation else {}

    def action_submit(self):
        require_group(self, USER)
        for record in self:
            if record.state != 'draft':
                raise ValidationError(_("Solo se pueden enviar a aprobación incidencias en estado Borrador."))
            calculated = record._dated_amount_values()
            if calculated:
                record.write(calculated)
        self.write({'state': 'to_approve'})

    def action_approve(self):
        require_group(self, RESPONSIBLE)
        for record in self:
            if record.state != 'to_approve':
                raise ValidationError(_("Solo se pueden aprobar incidencias en estado Por Aprobar."))
            if not record.employee_id:
                raise ValidationError(_("No se puede aprobar: falta el empleado."))
            if not record.contract_id:
                raise ValidationError(_("No se puede aprobar: falta el contrato."))
            if not record.date:
                raise ValidationError(_("No se puede aprobar: falta la fecha."))
            if not record.incident_type:
                raise ValidationError(_("No se puede aprobar: falta el tipo de incidencia."))
            record._check_values_by_type()
        self.write({'state': 'approved'})

    def action_draft(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'processed':
                raise ValidationError(_("Un registro en estado Procesado es inmutable y no puede volver a Borrador."))
            if record.state not in ('to_approve', 'approved', 'cancelled'):
                raise ValidationError(_("Transición a Borrador no permitida desde el estado '%s'." % record.state))
        self.write({'state': 'draft'})

    def action_cancel(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'processed':
                raise ValidationError(_("Un registro en estado Procesado es inmutable y no puede cancelarse."))
            if record.state not in ('draft', 'to_approve', 'approved'):
                raise ValidationError(_("Transición a Cancelado no permitida desde el estado '%s'." % record.state))
        self.write({'state': 'cancelled'})

    def action_generate_night_overtime(self):
        for record in self:
            if record.incident_type == 'night_work' and record.night_overtime_hours > 0:
                existing = self.env['hr.employee.incident'].search([('source_incident_id', '=', record.id)])
                if existing:
                    raise ValidationError(_("La hora extra nocturna para esta incidencia ya fue generada previamente."))
                
                vals = {
                    'employee_id': record.employee_id.id,
                    'contract_id': record.contract_id.id,
                    'date': record.date,
                    'incident_type': 'overtime',
                    'overtime_type': 'nighttime',
                    'quantity_hours': record.night_overtime_hours,
                    'source_incident_id': record.id,
                    'notes': f"Generado automáticamente por exceso de jornada nocturna (Origen: {record.id})",
                }
                new_incident = self.env['hr.employee.incident'].create([vals])
                return {
                    'type': 'ir.actions.act_window',
                    'name': _('Hora extra nocturna generada'),
                    'res_model': 'hr.employee.incident',
                    'res_id': new_incident.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
        return True
