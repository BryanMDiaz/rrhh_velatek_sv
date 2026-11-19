"""Model for Payroll (Planilla) and Payroll Line (Línea de Planilla)."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from .operational_security import (
    USER, RESPONSIBLE, MANAGER, check_edit, check_create, check_write, require_group,
)
import calendar


INCIDENT_EARNINGS_TYPES = {
    'overtime', 'night_work', 'holiday_work', 'weekly_rest_work',
    'bonus', 'commission', 'viaticos', 'other_income',
}


class HrPayroll(models.Model):
    _name = 'hr.payroll'
    _description = 'Planilla de Sueldos'
    _order = 'date_start desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        default=lambda self: self.env.company, required=True,
    )
    date_start = fields.Date(string='Fecha Inicio', required=True)
    date_end = fields.Date(string='Fecha Fin', required=True)
    payment_frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro')
    ], string='Frecuencia de pago', required=True, default='monthly')

    payroll_type = fields.Selection([
        ('regular', 'Planilla ordinaria'),
        ('professional_services', 'Servicios Profesionales'),
        ('complementary', 'Planilla complementaria'),
        ('adjustment', 'Ajuste'),
        ('retroactive', 'Retroactivo'),
        ('settlement', 'Liquidación'),
        ('bonus', 'Aguinaldo'),
        ('vacation', 'Vacación'),
        ('extraordinary', 'Extraordinaria'),
        ('other', 'Otra'),
    ], string='Tipo de planilla', required=True, default='regular')

    is_test = fields.Boolean(string='De prueba', copy=False)

    prepayroll_id = fields.Many2one(
        'hr.prepayroll', string='Preplanilla origen',
        required=False, check_company=True,
    )

    data_source = fields.Selection([
        ('prepayroll', 'Desde preplanilla'),
        ('manual', 'Carga directa'),
        ('mixed', 'Mixta'),
    ], string='Origen de datos', compute='_compute_data_source', store=True, readonly=True)

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('computed', 'Calculada'),
        ('validated', 'Validada'),
        ('closed', 'Cerrada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='draft', copy=False, required=True)

    line_ids = fields.One2many('hr.payroll.line', 'payroll_id', string='Líneas de planilla')
    notes = fields.Text(string='Notas')
    currency_id = fields.Many2one(related='company_id.currency_id')

    warning_message = fields.Char(compute='_compute_warning_message', store=False)

    _MONTH_NAMES = (
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
    )

    period_name = fields.Char(
        string='Período de planilla', compute='_compute_period_name',
        store=True, readonly=True, index=True,
    )

    period_title = fields.Char(string='Título del Período', compute='_compute_period_title', store=True)
    prepared_by_id = fields.Many2one('res.users', string='Elaborada por', default=lambda self: self.env.user)
    reviewed_by_id = fields.Many2one('res.users', string='Revisada por')
    approved_by_id = fields.Many2one('res.users', string='Autorizada por')
    prepared_employee_id = fields.Many2one('hr.employee', string='Elaborada por', copy=False)
    reviewed_employee_id = fields.Many2one('hr.employee', string='Revisada por', copy=False)
    approved_employee_id = fields.Many2one('hr.employee', string='Autorizada por', copy=False)

    @api.constrains('company_id', 'prepared_employee_id', 'reviewed_employee_id', 'approved_employee_id')
    def _check_signatory_company(self):
        for payroll in self:
            employees = payroll.prepared_employee_id | payroll.reviewed_employee_id | payroll.approved_employee_id
            if any(employee.company_id != payroll.company_id for employee in employees):
                raise ValidationError(_('Los empleados firmantes deben pertenecer a la compañía de la planilla.'))

    @api.depends('date_start', 'payment_frequency')
    def _compute_period_name(self):
        for record in self:
            ref_date = record.date_start
            if not ref_date:
                record.period_name = False
                continue

            month_year = '%s %s' % (
                self._MONTH_NAMES[ref_date.month], ref_date.year,
            )
            if record.payment_frequency == 'biweekly':
                half = 1 if ref_date.day <= 15 else 2
                record.period_name = 'Quincena %s - %s' % (half, month_year)
            elif record.payment_frequency == 'weekly':
                week = ref_date.isocalendar()[1]
                record.period_name = 'Semana %s - %s' % (week, month_year)
            elif record.payment_frequency == 'fourteen_days':
                record.period_name = 'Catorcena - %s' % month_year
            else:
                record.period_name = month_year

    _ALLOWED_TRANSITIONS = {
        'draft': {'computed', 'validated', 'cancelled'},
        'computed': {'draft', 'validated', 'cancelled'},
        'validated': {'draft', 'closed', 'cancelled'},
        'closed': set(),
        'cancelled': {'draft'},
    }

    @api.onchange('date_start', 'date_end', 'payment_frequency')
    def _onchange_check_dates_frequency(self):
        if not self.date_start or not self.date_end or not self.payment_frequency:
            return
            
        warning_msg = ""
        days = (self.date_end - self.date_start).days + 1
        
        if self.payment_frequency == 'weekly' and days != 7:
            warning_msg = f"Para frecuencia Semanal, el período seleccionado debe ser de exactamente 7 días. Actual: {days} días."
        elif self.payment_frequency == 'fourteen_days' and days != 14:
            warning_msg = f"Para frecuencia Catorcenal, el período seleccionado debe ser de exactamente 14 días. Actual: {days} días."
        elif self.payment_frequency == 'biweekly':
            last_day_of_month = calendar.monthrange(self.date_start.year, self.date_start.month)[1]
            if self.date_start.day == 1 and self.date_end.day == 15:
                pass
            elif self.date_start.day == 16 and self.date_end.day == last_day_of_month and self.date_start.month == self.date_end.month:
                pass
            else:
                warning_msg = "Para frecuencia Quincenal, las fechas válidas recomendadas son del 1 al 15 (Primera Quincena), o del 16 al último día del mes (Segunda Quincena)."
        elif self.payment_frequency == 'monthly':
            last_day_of_month = calendar.monthrange(self.date_start.year, self.date_start.month)[1]
            if self.date_start.day == 1 and self.date_end.day == last_day_of_month and self.date_start.month == self.date_end.month:
                pass
            else:
                warning_msg = "Para frecuencia Mensual, el período recomendado debe abarcar exactamente desde el día 1 hasta el último día del mes completo."
                
        if warning_msg:
            return {
                'warning': {
                    'title': 'Advertencia de Fechas y Frecuencia',
                    'message': warning_msg,
                }
            }

    @api.depends('date_start', 'date_end', 'payment_frequency')
    def _compute_period_title(self):
        months_es = {
            1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO',
            7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'
        }
        freq_es = {
            'monthly': 'MES COMPLETO',
            'biweekly': 'QUINCENA',
            'weekly': 'SEMANA',
            'fourteen_days': 'CATORCENA',
            'other': 'PERÍODO'
        }
        for record in self:
            if not record.date_start:
                record.period_title = ''
                continue
            m = months_es.get(record.date_start.month, '')
            y = record.date_start.year
            if record.payment_frequency == 'biweekly':
                q_num = 1 if record.date_start.day <= 15 else 2
                freq_str = f"QUINCENA {q_num}"
            else:
                freq_str = freq_es.get(record.payment_frequency, 'PERÍODO')
            record.period_title = f"PLANILLA CORRESPONDIENTE AL MES DE {m} {freq_str} - {y}"

    @api.depends('payroll_type', 'prepayroll_id')
    def _compute_warning_message(self):
        for record in self:
            if record.payroll_type == 'regular' and not record.prepayroll_id:
                record.warning_message = record.env._(
                    "Esta planilla ordinaria no tiene una preplanilla asociada. "
                    "La carga directa no importa incidencias ni producción. Para consolidarlas, utilice una preplanilla cerrada."
                )
            else:
                record.warning_message = False

    @api.depends('prepayroll_id', 'line_ids.source_type')
    def _compute_data_source(self):
        for record in self:
            if record.prepayroll_id:
                record.data_source = 'prepayroll'
            else:
                sources = set(record.line_ids.mapped('source_type'))
                if len(sources) > 1:
                    record.data_source = 'mixed'
                elif 'prepayroll' in sources:
                    record.data_source = 'prepayroll'
                else:
                    record.data_source = 'manual'

    @api.onchange('prepayroll_id')
    def _onchange_prepayroll_id(self):
        if self.prepayroll_id:
            self.company_id = self.prepayroll_id.company_id
            self.date_start = self.prepayroll_id.date_start
            self.date_end = self.prepayroll_id.date_end
            self.payment_frequency = self.prepayroll_id.payment_frequency

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for record in self:
            if record.date_start and record.date_end and record.date_end < record.date_start:
                raise ValidationError(_("La fecha fin no puede ser anterior a la fecha inicio."))

    @api.constrains('prepayroll_id', 'company_id', 'payment_frequency', 'date_start', 'date_end', 'is_test')
    def _check_prepayroll_compatibility(self):
        for record in self:
            if not record.prepayroll_id:
                continue
            prep = record.prepayroll_id
            if prep.is_test != record.is_test:
                raise ValidationError(_('La planilla y su preplanilla deben tener el mismo modo de prueba.'))
            if prep.state != 'closed':
                raise ValidationError(_("Solo se pueden asociar preplanillas en estado Cerrado."))
            if prep.company_id != record.company_id:
                raise ValidationError(_("La preplanilla debe pertenecer a la misma compañía (%s).", record.company_id.name))
            if prep.payment_frequency != record.payment_frequency:
                raise ValidationError(_("La frecuencia de la preplanilla (%s) no coincide con la frecuencia de la planilla (%s).", prep.payment_frequency, record.payment_frequency))
            if prep.date_start > record.date_end or prep.date_end < record.date_start:
                raise ValidationError(_("El período de la preplanilla no es compatible con el período de la planilla."))

    @api.constrains('prepayroll_id', 'payroll_type', 'state')
    def _check_unique_regular_prepayroll(self):
        for record in self:
            if record.prepayroll_id and record.payroll_type == 'regular' and record.state != 'cancelled':
                existing = self.search([
                    ('id', '!=', record.id),
                    ('prepayroll_id', '=', record.prepayroll_id.id),
                    ('payroll_type', '=', 'regular'),
                    ('state', '!=', 'cancelled'),
                ], limit=1)
                if existing:
                    raise ValidationError(_(
                        "La preplanilla '%(prep_name)s' ya está vinculada a la planilla ordinaria '%(payroll_name)s'.",
                        prep_name=record.prepayroll_id.name,
                        payroll_name=existing.name,
                    ))

    @api.model_create_multi
    def create(self, vals_list):
        check_create(self, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        if 'is_test' in vals and any(record.is_test != bool(vals['is_test']) for record in self):
            raise ValidationError(_('Seleccione el modo de prueba al crear la planilla.'))
        allowed = {'state', 'reviewed_by_id', 'approved_by_id', 'reviewed_employee_id', 'approved_employee_id'}
        if set(vals) - allowed and self.filtered(lambda payroll: payroll.state in ('validated', 'closed')):
            raise ValidationError(_('La planilla está validada o cerrada. No puede modificar sus datos de pago.'))
        if {'reviewed_employee_id', 'approved_employee_id'} & vals.keys() and self.filtered(lambda payroll: payroll.state == 'closed'):
            raise ValidationError(_('No se pueden cambiar los firmantes de una planilla cerrada.'))
        if 'state' in vals:
            target_state = vals['state']
            for payroll in self:
                if (
                    target_state != payroll.state
                    and target_state not in self._ALLOWED_TRANSITIONS.get(payroll.state, set())
                ):
                    raise ValidationError(_(
                        "No se permite cambiar la planilla de '%(current_state)s' a '%(target_state)s'.",
                        current_state=payroll.state,
                        target_state=target_state,
                    ))
        check_write(self, vals)
        return super().write(vals)

    def unlink(self):
        for record in self:
            if record.state in ('validated', 'closed'):
                raise ValidationError(_("No se pueden eliminar planillas validadas o cerradas."))
        check_edit(self)
        return super().unlink()

    # ─── Workflows ────────────────────────────────────────────────────────────

    def action_load_employees(self):
        require_group(self, USER)
        self.ensure_one()
        if self.state not in ('draft', 'computed'):
            raise ValidationError(_("Solo se pueden cargar empleados en estado Borrador o Calculada."))

        if self.prepayroll_id:
            return self.action_load_from_prepayroll()

        domain = [
            ('state', '=', 'open'),
            ('company_id', '=', self.company_id.id),
            '|', ('date_start', '<=', self.date_end), ('date_start', '=', False),
            '|', ('date_end', '>=', self.date_start), ('date_end', '=', False),
        ]

        if self.payroll_type == 'professional_services':
            domain.append(('employee_id.velatek_employment_type', '=', 'professional_services'))
        else:
            domain.append(('employee_id.velatek_employment_type', '!=', 'professional_services'))
        if self.payment_frequency:
            domain = ['&'] + domain + ['|', ('velatek_payment_frequency', '=', self.payment_frequency), ('velatek_payment_frequency', '=', False)]

        contracts = self.env['hr.contract'].search(domain)
        existing_contracts = self.line_ids.mapped('contract_id')
        new_contracts = contracts - existing_contracts

        loan_domain = [
            ('loan_id.employee_id', 'in', new_contracts.mapped('employee_id').ids),
            ('loan_id.contract_id', 'in', new_contracts.ids),
            ('state', 'in', ['pending', 'scheduled']),
            ('scheduled_date', '>=', self.date_start),
            ('scheduled_date', '<=', self.date_end),
            ('loan_id.state', '=', 'active'),
            ('loan_id.advance_prepayroll_id', '=', False),
        ]
        all_candidate_installments = self.env['hr.employee.loan.installment'].search(loan_domain)
        installments_by_key = {}
        for inst in all_candidate_installments:
            key = (inst.loan_id.employee_id.id, inst.loan_id.contract_id.id)
            if key not in installments_by_key:
                installments_by_key[key] = self.env['hr.employee.loan.installment']
            installments_by_key[key] |= inst

        lines_to_create = []
        for idx, contract in enumerate(new_contracts, len(existing_contracts) + 1):
            start_date = max(contract.date_start, self.date_start) if contract.date_start else self.date_start
            end_date = min(contract.date_end, self.date_end) if contract.date_end else self.date_end
            if end_date >= start_date:
                days_to_pay = (end_date - start_date).days + 1
            else:
                days_to_pay = 0

            period_length = (self.date_end - self.date_start).days + 1
            
            frequency = self.payment_frequency
            frequency_factors = {
                'monthly': 1.0,
                'biweekly': 0.5,
                'weekly': 7.0 / 30.0,
                'fourteen_days': 14.0 / 30.0,
            }
            frequency_factor = frequency_factors.get(frequency, period_length / 30.0)
            base_period_wage = contract.wage * frequency_factor
            
            period_wage = self.currency_id.round(base_period_wage * (days_to_pay / period_length)) if period_length > 0 else 0.0

            key = (contract.employee_id.id, contract.id)
            candidate_installments = installments_by_key.get(key, self.env['hr.employee.loan.installment'])
            loan_deduction_amount = sum(candidate_installments.mapped('remaining_amount'))

            lines_to_create.append((0, 0, {
                'sequence': idx,
                'employee_id': contract.employee_id.id,
                'contract_id': contract.id,
                'department_id': contract.department_id.id if contract.department_id else contract.employee_id.department_id.id,
                'job_id': contract.job_id.id,
                'remuneration_mode': contract.velatek_remuneration_mode or 'fixed',
                'base_wage': contract.wage,
                'days_to_pay': days_to_pay,
                'worked_days': days_to_pay,
                'period_wage': period_wage,
                'loan_deduction_amount': loan_deduction_amount,
                'loan_installment_ids': [(6, 0, candidate_installments.ids)],
                'source_type': 'manual',
                'prepayroll_line_id': False,
            }))

        if lines_to_create:
            self.write({'line_ids': lines_to_create})
            self.line_ids._compute_preliminary_totals()

        return True

    def action_load_from_prepayroll(self):
        require_group(self, USER)
        self.ensure_one()
        if not self.prepayroll_id:
            raise ValidationError(_("No hay una preplanilla asociada."))
        if self.prepayroll_id.state != 'closed':
            raise ValidationError(_("La preplanilla debe estar en estado Cerrado."))

        self.line_ids.unlink()

        valid_pre_lines = self.prepayroll_id.line_ids.filtered(lambda l: l.employee_id and l.contract_id)
        lines_to_create = []
        for idx, pre_line in enumerate(valid_pre_lines, 1):
            lines_to_create.append((0, 0, pre_line._get_payroll_line_values(idx)))

        if lines_to_create:
            self.write({'line_ids': lines_to_create})
            self.line_ids._compute_preliminary_totals()

    def action_compute(self):
        require_group(self, RESPONSIBLE)
        for record in self:
            if record.state not in ('draft', 'computed'):
                raise ValidationError(_("Solo se pueden calcular planillas en estado Borrador o Calculada."))
            if record.prepayroll_id and (not record.line_ids or any(line.source_type != 'prepayroll' for line in record.line_ids)):
                record.action_load_from_prepayroll()
            record.line_ids._compute_preliminary_totals()
            record.state = 'computed'

    def action_validate(self):
        require_group(self, RESPONSIBLE)
        for record in self:
            if record.state != 'computed':
                raise ValidationError(_("Solo se pueden validar planillas en estado Calculada."))
            if not record.reviewed_by_id:
                record.reviewed_by_id = self.env.user
            record.state = 'validated'

    def action_close(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state != 'validated':
                raise ValidationError(_("Solo se pueden cerrar planillas en estado Validada."))
            if not record.approved_by_id:
                record.approved_by_id = self.env.user
            record.state = 'closed'
            
            if record.is_test:
                continue

            # Marcar cuotas como pagadas
            for line in record.line_ids:
                deducted = line.loan_deduction_amount or 0.0
                for inst in line.loan_installment_ids.sorted('scheduled_date'):
                    if inst.state in ('pending', 'scheduled') and deducted > 0:
                        payable = min(inst.remaining_amount, deducted)
                        if payable > 0:
                            inst.paid_amount += payable
                            deducted -= payable
                            
                        if inst.currency_id.is_zero(inst.remaining_amount):
                            inst.write({
                                'state': 'paid',
                                'payment_date': record.date_end or fields.Date.context_today(self),
                                'payroll_id': record.id,
                                'payroll_line_id': line.id,
                            })

    def action_draft(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'closed':
                raise ValidationError(_("No se puede volver a borrador una planilla cerrada."))
            record.state = 'draft'

    def action_cancel(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'closed':
                raise ValidationError(_("No se puede cancelar una planilla cerrada."))
            record.state = 'cancelled'

    def action_view_prepayroll(self):
        self.ensure_one()
        if not self.prepayroll_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'name': 'Preplanilla origen',
            'res_model': 'hr.prepayroll',
            'res_id': self.prepayroll_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class HrPayrollLine(models.Model):
    _name = 'hr.payroll.line'
    _description = 'Línea de Planilla'
    _order = 'department_id, sequence, id'

    payroll_id = fields.Many2one('hr.payroll', string='Planilla', ondelete='cascade', required=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, check_company=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, check_company=True)
    department_id = fields.Many2one('hr.department', string='Departamento', related='employee_id.department_id', store=True, readonly=False)
    job_id = fields.Many2one('hr.job', string='Puesto')
    payroll_type = fields.Selection(related='payroll_id.payroll_type', string='Tipo de planilla', store=True)

    source_type = fields.Selection([
        ('prepayroll', 'Desde preplanilla'),
        ('manual', 'Carga manual'),
    ], string='Origen de línea', default='manual', required=True)

    prepayroll_line_id = fields.Many2one('hr.prepayroll.line', string='Línea de preplanilla origen', check_company=True)

    remuneration_mode = fields.Selection([
        ('fixed', 'Salario fijo'),
        ('production', 'Por producción'),
        ('mixed', 'Mixto')
    ], string='Modalidad')

    sequence = fields.Integer(string='No.', default=10)

    # Base salarial y días
    base_wage = fields.Monetary(string='Sueldo Mensual')
    monthly_wage = fields.Monetary(string='Sueldo Mensual (Ref)', related='base_wage', readonly=True)

    viaticos_amount = fields.Monetary(string='Viático')
    viatic_amount = fields.Monetary(string='Viático (Ref)', related='viaticos_amount', readonly=False)

    days_to_pay = fields.Float(string='Días a Pagar')
    worked_days = fields.Float(string='Días Trabajados', compute='_compute_worked_days', inverse='_inverse_worked_days', store=True)
    extra_days = fields.Float(string='Días Extras', default=0.0)
    holiday_days = fields.Float(string='Asueto (Días)', default=0.0)

    period_wage = fields.Monetary(string='Valor')
    period_salary_amount = fields.Monetary(string='Valor (Ref)', related='period_wage', readonly=False)
    daily_salary = fields.Monetary(string='Salario por Día', compute='_compute_daily_salary', store=True, readonly=False)

    # Producción
    production_quantity = fields.Float(string='Producción (Cant.)')
    production_unit = fields.Selection([
        ('sqm', 'Metro cuadrado (m²)'),
        ('linear_meter', 'Metro lineal'),
        ('unit', 'Unidad'),
        ('day', 'Día'),
        ('other', 'Otro')
    ], string='Unidad Prod.')
    production_rate = fields.Monetary(string='Tarifa Prod.')
    production_amount = fields.Monetary(string='Producción $')

    # Devengos adicionales
    holiday_amount = fields.Monetary(string='Asueto $')
    vacation_amount = fields.Monetary(string='Vacación $', default=0.0)

    overtime_hours = fields.Float(string='Horas Extra Diurnas')
    overtime_amount = fields.Monetary(string='Extra Diurna $')
    
    night_overtime_hours = fields.Float(string='Horas Extra Nocturnas')
    night_overtime_amount = fields.Monetary(string='Extra Nocturna $')

    night_work_hours = fields.Float(string='Horas Nocturnas Ord.')
    night_work_amount = fields.Monetary(string='Nocturnidad $')

    holiday_work_hours = fields.Float(string='Horas Asueto Trab.')
    holiday_work_amount = fields.Monetary(string='Importe Asueto Trab.')

    weekly_rest_work_hours = fields.Float(string='Horas Descanso Trab.')
    weekly_rest_work_amount = fields.Monetary(string='Descanso Trab. $')

    bonus_amount = fields.Monetary(string='Bonos')
    commission_amount = fields.Monetary(string='Comisiones')
    viaticos_amount = fields.Monetary(string='Viáticos')
    other_income = fields.Monetary(string='Otros Ingresos')

    # Total Devengado
    total_earned = fields.Monetary(string='Total Devengado', compute='_compute_preliminary_totals', store=True)
    preliminary_earnings_total = fields.Monetary(string='Devengos preliminares', related='total_earned', store=True)

    # Bases Legales
    isss_taxable_base = fields.Monetary(string='Base ISSS', compute='_compute_preliminary_totals', store=True, readonly=False)
    afp_taxable_base = fields.Monetary(string='Base AFP', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_taxable_base = fields.Monetary(string='Base ISR', compute='_compute_preliminary_totals', store=True, readonly=False)

    # Descuentos legales (estructurales) y Snapshots de Parámetros
    isss_employee_amount = fields.Monetary(string='ISSS', compute='_compute_preliminary_totals', store=True, readonly=False)
    isss_parameter_id = fields.Many2one('hr.sv.social.security.parameter', string='Parámetro ISSS', compute='_compute_preliminary_totals', store=True, readonly=False)
    isss_rate_snapshot = fields.Float(string='Snapshot Tasa ISSS Empleado (%)', compute='_compute_preliminary_totals', store=True, readonly=False)
    isss_employer_rate_snapshot = fields.Float(string='Snapshot Tasa ISSS Patrono (%)', compute='_compute_preliminary_totals', store=True, readonly=False)

    afp_employee_amount = fields.Monetary(string='AFP', compute='_compute_preliminary_totals', store=True, readonly=False)
    afp_parameter_id = fields.Many2one('hr.sv.pension.parameter', string='Parámetro AFP', compute='_compute_preliminary_totals', store=True, readonly=False)
    afp_rate_snapshot = fields.Float(string='Snapshot Tasa AFP Empleado (%)', compute='_compute_preliminary_totals', store=True, readonly=False)
    afp_employer_rate_snapshot = fields.Float(string='Snapshot Tasa AFP Patrono (%)', compute='_compute_preliminary_totals', store=True, readonly=False)

    income_tax_amount = fields.Monetary(string='Renta', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_table_id = fields.Many2one('hr.sv.isr.table', string='Tabla ISR', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_bracket_id = fields.Many2one('hr.sv.isr.bracket', string='Tramo ISR Aplicado', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_rate_snapshot = fields.Float(string='Snapshot Tasa ISR (%)', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_fixed_fee_snapshot = fields.Float(string='Snapshot Cuota Fija ISR', compute='_compute_preliminary_totals', store=True, readonly=False)
    isr_excess_over_snapshot = fields.Float(string='Snapshot Exceso ISR', compute='_compute_preliminary_totals', store=True, readonly=False)

    # Ausencias y deducciones
    absence_days = fields.Float(string='Días Ausente')
    absence_hours = fields.Float(string='Horas Ausente')
    absence_amount = fields.Monetary(string='Descuento Ausencias')

    paid_leave_days = fields.Float(string='Perm. c/goce (días)')
    paid_leave_hours = fields.Float(string='Perm. c/goce (horas)')
    sick_leave_days = fields.Float(string='Incapacidad (días)')
    sick_leave_hours = fields.Float(string='Incapacidad (horas)')
    sick_ordinary_amount = fields.Monetary(string='Salario ordinario sustituido por incapacidad', readonly=True)
    sick_employer_amount = fields.Monetary(string='Incapacidad a cargo del empleador', readonly=True)
    sick_isss_amount = fields.Monetary(string='Subsidio ISSS (informativo)', readonly=True)
    suspension_days = fields.Float(string='Suspensión (días)')
    suspension_hours = fields.Float(string='Suspensión (horas)')
    deduction_amount = fields.Monetary(string='Deducciones Generales')

    other_withholdings_amount = fields.Monetary(string='Otras Retenciones', default=0.0)
    other_deductions_amount = fields.Monetary(string='Otras Deducciones (Ref)', related='deduction_amount', readonly=False)

    loan_installment_ids = fields.Many2many(
        'hr.employee.loan.installment',
        'hr_payroll_line_loan_rel',
        'line_id',
        'installment_id',
        string='Cuotas de Préstamo'
    )
    loan_deduction_amount = fields.Monetary(string='Préstamos')

    # Total Descuentos
    total_deductions = fields.Monetary(string='Total Descuentos', compute='_compute_preliminary_totals', store=True)
    preliminary_deductions_total = fields.Monetary(string='Descuentos preliminares', related='total_deductions', store=True)

    # Pago y Total a Liquidar
    net_before_advance = fields.Monetary(string='Neto del período antes del anticipo', compute='_compute_preliminary_totals', store=True)
    salary_advance_amount = fields.Monetary(string='Salario ya anticipado', readonly=True, copy=False)
    payment_amount = fields.Monetary(string='Pago', compute='_compute_preliminary_totals', store=True)
    preliminary_balance = fields.Monetary(string='Balance preliminar', related='payment_amount', store=True)
    gross_total = fields.Monetary(string='Total preliminar', related='payment_amount', store=True)

    outside_payment_amount = fields.Monetary(string='Pago por Fuera', default=0.0)
    total_to_settle = fields.Monetary(string='Total a Liquidar', compute='_compute_preliminary_totals', store=True)

    notes = fields.Char(string='Notas')
    state = fields.Selection(related='payroll_id.state', string='Estado Planilla', store=True)

    company_id = fields.Many2one(related='payroll_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('days_to_pay')
    def _compute_worked_days(self):
        for line in self:
            line.worked_days = line.days_to_pay or 0.0

    def _inverse_worked_days(self):
        for line in self:
            line.days_to_pay = line.worked_days or 0.0

    @api.depends('base_wage', 'period_wage', 'worked_days')
    def _compute_daily_salary(self):
        for line in self:
            if line.base_wage:
                line.daily_salary = line.base_wage / 30.0
            elif line.period_wage and line.worked_days:
                line.daily_salary = line.period_wage / line.worked_days
            else:
                line.daily_salary = 0.0

    def _check_payroll_editable(self, payrolls=None):
        payrolls = payrolls if payrolls is not None else self.mapped('payroll_id')
        if payrolls.filtered(lambda payroll: payroll.state in ('validated', 'closed')):
            raise ValidationError(_('No puede agregar, modificar ni eliminar líneas de una planilla validada o cerrada.'))

    @api.model_create_multi
    def create(self, vals_list):
        require_group(self, USER)
        payroll_ids = {vals.get('payroll_id') or self.env.context.get('default_payroll_id') for vals in vals_list}
        self._check_payroll_editable(self.env['hr.payroll'].browse([pid for pid in payroll_ids if pid]))
        return super().create(vals_list)

    def write(self, vals):
        require_group(self, USER)
        self._check_payroll_editable()
        if vals.get('payroll_id'):
            self._check_payroll_editable(self.env['hr.payroll'].browse(vals['payroll_id']))
        return super().write(vals)

    def unlink(self):
        require_group(self, USER)
        self._check_payroll_editable()
        return super().unlink()

    def _calculate_isr_salvador(self, taxable_base, frequency, ref_date=None, company=None):
        if taxable_base <= 0:
            return 0.0
        ref_date = ref_date or fields.Date.today()
        company = company or self.env.company
        isr_table = self.env['hr.sv.isr.table'].get_active_table(ref_date, company, frequency)
        tax_amount, _ = isr_table.calculate_isr(taxable_base)
        return tax_amount

    @api.depends(
        'remuneration_mode', 'period_wage', 'production_amount', 'viaticos_amount',
        'overtime_amount', 'night_overtime_amount', 'night_work_amount', 'holiday_work_amount', 'holiday_amount',
        'vacation_amount', 'weekly_rest_work_amount', 'bonus_amount', 'commission_amount',
        'other_income', 'other_withholdings_amount', 'absence_amount', 'deduction_amount',
        'loan_deduction_amount', 'salary_advance_amount', 'sick_ordinary_amount', 'sick_employer_amount',
        'outside_payment_amount', 'payroll_id.date_start', 'payroll_id.date_end',
        'payroll_id.company_id', 'payroll_id.payment_frequency', 'contract_id', 'prepayroll_line_id',
    )
    def _compute_preliminary_totals(self):
        # Optimizar búsquedas en batch
        prepayroll_line_ids = self.mapped('prepayroll_line_id').ids
        allocations_by_pline = {}
        if prepayroll_line_ids:
            all_allocs = self.env['hr.prepayroll.incident.allocation'].search([
                ('prepayroll_line_id', 'in', prepayroll_line_ids)
            ])
            for alloc in all_allocs:
                allocations_by_pline.setdefault(alloc.prepayroll_line_id.id, []).append(alloc)

        for line in self:
            base_part = 0.0 if line.remuneration_mode == 'production' else (line.period_wage or 0.0)
            # Imported holiday_amount mirrors holiday_work_amount for display.
            native_holiday = 0.0 if line.prepayroll_line_id else line.holiday_amount
            earnings = (
                base_part - line.sick_ordinary_amount + line.sick_employer_amount +
                (line.production_amount or 0.0) +
                (line.viaticos_amount or 0.0) +
                (line.overtime_amount or 0.0) +
                (line.night_overtime_amount or 0.0) +
                (line.night_work_amount or 0.0) +
                (line.holiday_work_amount or 0.0) +
                native_holiday +
                (line.vacation_amount or 0.0) +
                (line.weekly_rest_work_amount or 0.0) +
                (line.bonus_amount or 0.0) +
                (line.commission_amount or 0.0) +
                (line.other_income or 0.0)
            )

            # --- CÁLCULO DE BASES LEGALES ---
            native_base = max(0.0, (
                base_part - line.sick_ordinary_amount +
                (line.production_amount or 0.0) +
                native_holiday +
                (line.vacation_amount or 0.0) -
                (line.absence_amount or 0.0)
            ))

            isss_incident_base = 0.0
            afp_incident_base = 0.0
            isr_incident_base = 0.0

            if line.prepayroll_line_id:
                allocs = allocations_by_pline.get(line.prepayroll_line_id.id, [])
                for alloc in allocs:
                    incident = alloc.incident_id
                    if incident.incident_type == 'sick_leave':
                        employer_amount = alloc.allocated_days * incident.sick_employer_daily
                        if incident.sick_employer_affects_isss:
                            isss_incident_base += employer_amount
                        if incident.sick_employer_affects_afp:
                            afp_incident_base += employer_amount
                        if incident.sick_employer_affects_isr:
                            isr_incident_base += employer_amount
                        continue
                    if incident.incident_type not in INCIDENT_EARNINGS_TYPES:
                        continue
                    
                    # Utilizar snapshots si es un viático clasificado o si tiene snapshot seteado recientemente
                    if incident.incident_type == 'viaticos' and incident.viatic_treatment:
                        if incident.affects_isss_snapshot:
                            isss_incident_base += alloc.allocated_amount
                        if incident.affects_afp_snapshot:
                            afp_incident_base += alloc.allocated_amount
                        if incident.affects_isr_snapshot:
                            isr_incident_base += alloc.allocated_amount
                    else:
                        # Fallback a concepto o default para incidencias antiguas u otros tipos
                        concept = incident.concept_id
                        if concept:
                            if concept.affects_isss:
                                isss_incident_base += alloc.allocated_amount
                            if concept.affects_afp:
                                afp_incident_base += alloc.allocated_amount
                            if concept.affects_isr:
                                isr_incident_base += alloc.allocated_amount
                        else:
                            isss_incident_base += alloc.allocated_amount
                            afp_incident_base += alloc.allocated_amount
                            isr_incident_base += alloc.allocated_amount
            else:
                # Líneas manuales: asumimos todo excepto viáticos como base
                manual_incident_total = (
                    (line.overtime_amount or 0.0) +
                    (line.night_overtime_amount or 0.0) +
                    (line.night_work_amount or 0.0) +
                    (line.holiday_work_amount or 0.0) +
                    (line.weekly_rest_work_amount or 0.0) +
                    (line.bonus_amount or 0.0) +
                    (line.commission_amount or 0.0) +
                    (line.other_income or 0.0)
                )
                isss_incident_base = manual_incident_total
                afp_incident_base = manual_incident_total
                isr_incident_base = manual_incident_total

            line.isss_taxable_base = line.currency_id.round(native_base + isss_incident_base) if line.currency_id else (native_base + isss_incident_base)
            line.afp_taxable_base = line.currency_id.round(native_base + afp_incident_base) if line.currency_id else (native_base + afp_incident_base)
            line.isr_taxable_base = line.currency_id.round(native_base + isr_incident_base) if line.currency_id else (native_base + isr_incident_base)

            line.total_earned = earnings

            ref_date = (line.payroll_id and (line.payroll_id.date_end or line.payroll_id.date_start)) or fields.Date.today()
            company = (line.payroll_id and line.payroll_id.company_id) or line.company_id or self.env.company
            freq = (line.payroll_id and line.payroll_id.payment_frequency) or 'monthly'

            # ISSS
            if line.payroll_id and line.payroll_id.payroll_type in ('professional_services', 'bonus'):
                line.isss_parameter_id = False
                line.isss_rate_snapshot = 0.0
                line.isss_employer_rate_snapshot = 0.0
                line.isss_employee_amount = 0.0
            else:
                isss_param = self.env['hr.sv.social.security.parameter'].get_active_parameter(ref_date, company)
                isss_calc, isss_empr, _ = isss_param.calculate_isss(line.isss_taxable_base, freq)
                line.isss_parameter_id = isss_param
                line.isss_rate_snapshot = isss_param.employee_rate
                line.isss_employer_rate_snapshot = isss_param.employer_rate
                line.isss_employee_amount = isss_calc

            # AFP
            if line.payroll_id and line.payroll_id.payroll_type in ('professional_services', 'bonus'):
                line.afp_parameter_id = False
                line.afp_rate_snapshot = 0.0
                line.afp_employer_rate_snapshot = 0.0
                line.afp_employee_amount = 0.0
            else:
                afp_id = getattr(line.contract_id, 'velatek_afp_id', None)
                afp_param = self.env['hr.sv.pension.parameter'].get_active_parameter(ref_date, company, afp_id=afp_id)
                afp_calc, afp_empr, _ = afp_param.calculate_afp(line.afp_taxable_base, freq, ref_date=ref_date)
                line.afp_parameter_id = afp_param
                line.afp_rate_snapshot = afp_param.employee_rate
                line.afp_employer_rate_snapshot = afp_param.employer_rate
                line.afp_employee_amount = afp_calc

            # ISR
            taxable_base_isr = max(0.0, line.isr_taxable_base - (line.isss_employee_amount or 0.0) - (line.afp_employee_amount or 0.0))
            if line.payroll_id and line.payroll_id.payroll_type == 'professional_services':
                line.isr_table_id = False
                line.isr_bracket_id = False
                # Servicios Profesionales has a fixed 10% withholding
                line.income_tax_amount = taxable_base_isr * 0.10
                line.isr_rate_snapshot = 10.0
                line.isr_fixed_fee_snapshot = 0.0
                line.isr_excess_over_snapshot = 0.0
            else:
                isr_table = self.env['hr.sv.isr.table'].get_active_table(ref_date, company, freq)
                isr_calc, isr_bracket = isr_table.calculate_isr(taxable_base_isr)
                line.isr_table_id = isr_table
                line.isr_bracket_id = isr_bracket
                line.income_tax_amount = isr_calc
                if isr_bracket:
                    line.isr_rate_snapshot = isr_bracket.rate
                    line.isr_fixed_fee_snapshot = isr_bracket.fixed_fee
                    line.isr_excess_over_snapshot = isr_bracket.excess_over
                else:
                    line.isr_rate_snapshot = 0.0
                    line.isr_fixed_fee_snapshot = 0.0
                    line.isr_excess_over_snapshot = 0.0

            deductions = (
                (line.isss_employee_amount or 0.0) +
                (line.afp_employee_amount or 0.0) +
                (line.income_tax_amount or 0.0) +
                (line.other_withholdings_amount or 0.0) +
                (line.absence_amount or 0.0) +
                (line.deduction_amount or 0.0) +
                (line.loan_deduction_amount or 0.0)
            )
            payment = earnings - deductions
            outside = line.outside_payment_amount or 0.0

            line.total_earned = earnings
            line.total_deductions = deductions
            line.net_before_advance = payment
            line.payment_amount = payment - line.salary_advance_amount
            line.total_to_settle = payment - line.salary_advance_amount + outside

    _sql_constraints = [
        ('unique_payroll_employee_contract', 'unique(payroll_id, employee_id, contract_id)', 'El empleado ya tiene una línea con el mismo contrato en esta planilla.')
    ]
