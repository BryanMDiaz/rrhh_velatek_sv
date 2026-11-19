"""Period salary advances: an early net payment, not a second taxable earning."""
import calendar

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class HrEmployeeSalaryAdvance(models.Model):
    _inherit = 'hr.employee.loan'

    is_salary_advance = fields.Boolean(compute='_compute_is_salary_advance')
    advance_stage = fields.Selection([
        ('draft', 'Borrador'), ('submitted', 'Por aprobar'), ('approved', 'Autorizado'),
        ('active', 'Entregado'), ('paid', 'Liquidado'), ('cancelled', 'Cancelado'),
    ], string='Estado del anticipo', compute='_compute_advance_stage')
    advance_prepayroll_id = fields.Many2one(
        'hr.prepayroll', string='Quincena a anticipar', copy=False, check_company=True, ondelete='restrict',
        help='Prepare y actualice la preplanilla del período antes de calcular el anticipo.',
    )
    advance_date_start = fields.Date(string='Inicio de quincena anticipada', readonly=True, copy=False, index=True)
    advance_date_end = fields.Date(string='Fin de quincena anticipada', readonly=True, copy=False, index=True)
    advance_gross = fields.Monetary(string='Devengado del período', readonly=True, copy=False)
    advance_isss = fields.Monetary(string='ISSS estimado', readonly=True, copy=False)
    advance_afp = fields.Monetary(string='AFP estimado', readonly=True, copy=False)
    advance_isr = fields.Monetary(string='ISR estimado', readonly=True, copy=False)
    advance_other_deductions = fields.Monetary(string='Otras deducciones del período', readonly=True, copy=False)
    advance_net = fields.Monetary(string='Neto a anticipar', readonly=True, copy=False)
    advance_calculated_at = fields.Datetime(string='Calculado el', readonly=True, copy=False)
    advance_payment_reference = fields.Char(string='Referencia de entrega / transferencia', copy=False)
    advance_settled_payroll_id = fields.Many2one('hr.payroll', string='Liquidado en planilla', readonly=True, copy=False)

    _ADVANCE_PROTECTED_FIELDS = {
        'employee_id', 'contract_id', 'company_id', 'loan_type_id', 'advance_prepayroll_id',
        'advance_date_start', 'advance_date_end', 'principal_amount', 'interest_amount', 'estimated_installments', 'first_payment_date',
        'payment_frequency', 'installment_ids', 'advance_gross', 'advance_isss', 'advance_afp',
        'advance_isr', 'advance_other_deductions', 'advance_net', 'advance_calculated_at',
    }

    @api.depends('loan_type_id.code')
    def _compute_is_salary_advance(self):
        for loan in self:
            loan.is_salary_advance = loan.loan_type_id.code == 'ANTI'

    @api.depends('state')
    def _compute_advance_stage(self):
        for loan in self:
            loan.advance_stage = loan.state

    @api.onchange('advance_prepayroll_id')
    def _onchange_advance_period(self):
        if self.advance_prepayroll_id:
            self.advance_date_start = self.advance_prepayroll_id.date_start
            self.advance_date_end = self.advance_prepayroll_id.date_end
            self.first_payment_date = self.advance_prepayroll_id.date_end
            self.payment_frequency = 'biweekly'
            self.estimated_installments = 1
            self.interest_amount = 0

    @api.model_create_multi
    def create(self, values_list):
        records = super().create(values_list)
        if records.filtered(lambda loan: loan.is_salary_advance and loan.state != 'draft'):
            raise ValidationError(_('Cree el anticipo en borrador y siga el flujo de aprobación y entrega.'))
        return records

    def _require_advance_manager(self):
        if not self.env.su and not self.env.user.has_group('hr.group_hr_manager'):
            raise AccessError(_('Solo un responsable de RRHH con permisos de administrador puede aprobar o confirmar la entrega del anticipo.'))

    def _lock_advance_employee(self):
        self.ensure_one()
        # Touch the row as well as locking it: concurrent REPEATABLE READ
        # transactions must retry with a fresh snapshot before checking duplicates.
        self.env.cr.execute('UPDATE hr_employee SET write_date = write_date WHERE id = %s', [self.employee_id.id])

    def _check_advance_source(self):
        self.ensure_one()
        source = self.advance_prepayroll_id
        if not self.is_salary_advance or not source:
            raise ValidationError(_('Seleccione la preplanilla de la quincena que desea anticipar.'))
        if source.company_id != self.company_id or source.prepayroll_type != 'regular' or source.state == 'cancelled':
            raise ValidationError(_('El anticipo necesita una preplanilla ordinaria vigente de la misma compañía.'))
        start, end = source.date_start, source.date_end
        if source.payment_frequency != 'biweekly' or not start or not end or (
            start.year != end.year or start.month != end.month
            or (start.day, end.day) not in ((1, 15), (16, calendar.monthrange(start.year, start.month)[1]))
        ):
            raise ValidationError(_('Seleccione una quincena completa: del 1 al 15 o del 16 al último día del mes.'))
        lines = source.line_ids.filtered(lambda line: line.employee_id == self.employee_id and line.contract_id == self.contract_id)
        if len(lines) != 1:
            raise ValidationError(_('Cargue al empleado y su contrato en la preplanilla seleccionada antes de calcular el anticipo.'))
        if self.contract_id.velatek_payment_frequency != 'biweekly':
            raise ValidationError(_('Este flujo de anticipo requiere un contrato con pago quincenal.'))
        return lines

    def _check_advance_duplicates(self):
        self.ensure_one()
        self._lock_advance_employee()
        start = self.advance_prepayroll_id.date_start
        end = self.advance_prepayroll_id.date_end
        duplicate = self.search([
            ('id', '!=', self.id), ('employee_id', '=', self.employee_id.id),
            ('company_id', '=', self.company_id.id), ('state', 'not in', ['draft', 'cancelled']),
            ('advance_date_start', '<=', end),
            ('advance_date_end', '>=', start),
        ], limit=1)
        if duplicate:
            raise ValidationError(_('Ya existe el anticipo %s para este empleado y período. No se puede anticipar dos veces la misma quincena.') % duplicate.name)
        payroll = self.env['hr.payroll'].search([
            ('company_id', '=', self.company_id.id), ('state', 'in', ['validated', 'closed']),
            ('payroll_type', '=', 'regular'), ('date_start', '<=', end),
            ('date_end', '>=', start), ('line_ids.employee_id', '=', self.employee_id.id),
        ], limit=1)
        if payroll:
            raise ValidationError(_('La planilla %s ya está validada o cerrada. No puede entregar un anticipo para ese período.') % payroll.name)

    def _get_advance_calculation(self):
        """Use exactly the payroll inputs, allocation snapshots and legal engine."""
        source_line = self._check_advance_source()
        source = source_line.prepayroll_id
        payroll = self.env['hr.payroll'].new({
            'name': _('Simulación de anticipo'), 'company_id': self.company_id.id,
            'date_start': source.date_start, 'date_end': source.date_end,
            'payment_frequency': source.payment_frequency, 'payroll_type': 'regular',
        })
        values = source_line._get_payroll_line_values()
        # Query current loans rather than using a stale prepayroll loan amount.
        installments = self.env['hr.employee.loan.installment'].search([
            ('loan_id.employee_id', '=', self.employee_id.id), ('loan_id.contract_id', '=', self.contract_id.id),
            ('loan_id.company_id', '=', self.company_id.id), ('loan_id.state', '=', 'active'),
            ('loan_id.advance_prepayroll_id', '=', False), ('state', 'in', ['pending', 'scheduled']),
            ('scheduled_date', '>=', source.date_start), ('scheduled_date', '<=', source.date_end),
        ])
        values.update({
            'payroll_id': payroll, 'loan_deduction_amount': sum(installments.mapped('remaining_amount')),
            'loan_installment_ids': [(6, 0, installments.ids)], 'salary_advance_amount': 0,
        })
        line = self.env['hr.payroll.line'].new(values)
        line._compute_preliminary_totals()
        # The legacy engine has fallback parameters. Never accept expired or
        # foreign-company fallbacks for a new advance.
        for parameter in (line.isss_parameter_id, line.afp_parameter_id, line.isr_table_id):
            if not parameter or not parameter.active or parameter.date_from > source.date_end or (
                parameter.date_to and parameter.date_to < source.date_end
            ) or (parameter.company_id and parameter.company_id != self.company_id):
                raise ValidationError(_('Configure parámetros vigentes de ISSS, AFP e ISR para la compañía y la quincena antes de anticipar el salario.'))
        if line.isr_table_id.payment_frequency != source.payment_frequency or not line.isr_table_id.bracket_ids:
            raise ValidationError(_('Configure la tabla de ISR quincenal vigente y sus tramos.'))
        currency = self.currency_id
        return {
            'advance_gross': currency.round(line.total_earned),
            'advance_isss': currency.round(line.isss_employee_amount),
            'advance_afp': currency.round(line.afp_employee_amount),
            'advance_isr': currency.round(line.income_tax_amount),
            'advance_other_deductions': currency.round(line.total_deductions - line.isss_employee_amount - line.afp_employee_amount - line.income_tax_amount),
            'advance_net': currency.round(line.payment_amount),
        }

    def action_calculate_salary_advance(self):
        self.ensure_one()
        if self.state != 'draft':
            raise ValidationError(_('Solo puede calcular el anticipo en borrador.'))
        self._check_advance_source()
        self._check_advance_duplicates()
        amounts = self._get_advance_calculation()
        if self.currency_id.compare_amounts(amounts['advance_net'], 0) <= 0:
            raise ValidationError(_('El neto de la quincena debe ser mayor a cero para entregar un anticipo.'))
        self.write(dict(amounts, advance_calculated_at=fields.Datetime.now(),
                        principal_amount=amounts['advance_net'], interest_amount=0,
                        advance_date_start=self.advance_prepayroll_id.date_start,
                        advance_date_end=self.advance_prepayroll_id.date_end,
                        estimated_installments=1, first_payment_date=self.advance_prepayroll_id.date_end,
                        payment_frequency='biweekly'))
        self.action_generate_calendar()

    def _check_calculated_advance(self):
        self.ensure_one()
        self._check_advance_source()
        if not self.advance_calculated_at:
            raise ValidationError(_('Pulse Calcular anticipo neto antes de continuar.'))
        if self.advance_date_start != self.advance_prepayroll_id.date_start or self.advance_date_end != self.advance_prepayroll_id.date_end:
            raise ValidationError(_('Cambió el período de la preplanilla. Calcule nuevamente el anticipo.'))
        amounts = self._get_advance_calculation()
        if any(self.currency_id.compare_amounts(self[key], value) for key, value in amounts.items()):
            raise ValidationError(_('Cambió el cálculo de la quincena. Cancele esta solicitud y calcule nuevamente el anticipo con los datos actualizados.'))
        if self.interest_amount or self.estimated_installments != 1 or self.first_payment_date != self.advance_date_end or self.payment_frequency != 'biweekly' or self.currency_id.compare_amounts(self.principal_amount, self.advance_net):
            raise ValidationError(_('El anticipo debe coincidir con el neto calculado, sin interés y con una sola liquidación.'))
        if len(self.installment_ids) != 1 or self.currency_id.compare_amounts(self.installment_ids.amount, self.advance_net) or self.installment_ids.scheduled_date != self.advance_date_end:
            raise ValidationError(_('Regenere el calendario del anticipo: debe contener una única liquidación al final de la quincena.'))

    def action_print_salary_advance(self):
        self.ensure_one()
        if not self.is_salary_advance or not self.advance_calculated_at:
            raise ValidationError(_('Calcule primero el anticipo neto de la quincena.'))
        return self.env.ref('rrhh_velatek_sv.action_report_salary_advance').report_action(self)

    @api.constrains('advance_prepayroll_id', 'loan_type_id', 'state')
    def _check_advance_kind(self):
        for loan in self:
            if loan.advance_prepayroll_id and not loan.is_salary_advance:
                raise ValidationError(_('La quincena anticipada solo se permite para el tipo Anticipo de Salario.'))
            if loan.is_salary_advance and loan.state not in ('draft', 'cancelled', 'paid') and not loan.advance_prepayroll_id:
                raise ValidationError(_('El anticipo de salario debe estar vinculado a su quincena.'))

    def action_submit(self):
        for loan in self.filtered('is_salary_advance'):
            loan._check_calculated_advance()
            loan._check_advance_duplicates()
        return super().action_submit()

    def action_approve(self):
        if self.is_salary_advance:
            self._require_advance_manager()
            if self.state != 'submitted':
                raise ValidationError(_('El anticipo debe estar por aprobar.'))
            self._check_calculated_advance()
            self._check_advance_duplicates()
        return super().action_approve()

    def action_activate(self):
        if self.is_salary_advance:
            self._require_advance_manager()
            if self.state != 'approved':
                raise ValidationError(_('Apruebe el anticipo antes de registrar su entrega.'))
            self._check_calculated_advance()
            self._check_advance_duplicates()
            if not self.advance_payment_reference or not self.advance_payment_reference.strip():
                raise ValidationError(_('Registre la referencia del pago realmente entregado al empleado.'))
            if not self.disbursement_date:
                self.disbursement_date = fields.Date.context_today(self)
        return super().action_activate()

    def action_cancel(self):
        if self.filtered(lambda loan: loan.advance_prepayroll_id and loan.state in ('active', 'paid')):
            raise ValidationError(_('El anticipo ya fue entregado. No se puede cancelar sin conciliar la devolución o el ajuste de pago.'))
        return super().action_cancel()

    def write(self, values):
        for loan in self:
            if loan.advance_prepayroll_id and values.get('state') and values['state'] != loan.state:
                transitions = {'draft': ('submitted', 'cancelled'), 'submitted': ('approved', 'cancelled'), 'approved': ('active', 'cancelled'), 'active': ('paid',), 'paid': (), 'cancelled': ()}
                if values['state'] not in transitions.get(loan.state, ()):
                    raise ValidationError(_('Siga el flujo de aprobación y entrega del anticipo.'))
            if loan.advance_prepayroll_id and loan.state != 'draft' and self._ADVANCE_PROTECTED_FIELDS.intersection(values):
                raise ValidationError(_('El cálculo del anticipo enviado o entregado no puede modificarse.'))
            if loan.advance_prepayroll_id and loan.state in ('active', 'paid') and {'advance_payment_reference', 'disbursement_date', 'signed_contract_document', 'signed_contract_filename'}.intersection(values):
                raise ValidationError(_('Conserve la evidencia y la referencia originales del anticipo entregado.'))
            if loan.advance_prepayroll_id and values.get('state') in ('submitted', 'approved', 'active'):
                loan._check_calculated_advance()
                loan._check_advance_duplicates()
                if values['state'] in ('approved', 'active'):
                    loan._require_advance_manager()
                if not loan.signed_contract_document:
                    raise ValidationError(_('Adjunte la solicitud/autorización firmada del anticipo.'))
                if values['state'] == 'active' and not (values.get('advance_payment_reference') or loan.advance_payment_reference):
                    raise ValidationError(_('Registre la referencia de entrega del anticipo.'))
            if loan.advance_prepayroll_id and loan.state in ('active', 'paid') and values.get('state') in ('draft', 'submitted', 'approved', 'cancelled'):
                raise ValidationError(_('No puede revertir un anticipo entregado.'))
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda loan: loan.advance_prepayroll_id and loan.state not in ('draft', 'cancelled')):
            raise ValidationError(_('No puede eliminar un anticipo aprobado, entregado o liquidado.'))
        return super().unlink()


class HrPayrollSalaryAdvance(models.Model):
    _inherit = 'hr.payroll'

    def _sync_salary_advances(self):
        for payroll in self:
            for line in payroll.line_ids:
                advances = self.env['hr.employee.loan'].search([
                    ('advance_prepayroll_id', '!=', False), ('company_id', '=', payroll.company_id.id),
                    ('employee_id', '=', line.employee_id.id), ('contract_id', '=', line.contract_id.id),
                    ('state', 'in', ['active', 'paid']),
                    ('advance_date_start', '<=', payroll.date_end),
                    ('advance_date_end', '>=', payroll.date_start),
                ]) if payroll.payroll_type == 'regular' else self.env['hr.employee.loan']
                for advance in advances:
                    if advance.advance_date_start != payroll.date_start or advance.advance_date_end != payroll.date_end:
                        raise ValidationError(_('El anticipo %s requiere una planilla de su misma quincena; no se puede aplicar a otro período.') % advance.name)
                    if advance.advance_settled_payroll_id and advance.advance_settled_payroll_id != payroll:
                        raise ValidationError(_('El anticipo %s ya fue liquidado en otra planilla.') % advance.name)
                if advances or line.loan_installment_ids:
                    # Loan payments may have changed since the prepayroll was
                    # prepared. Reconcile them before determining the final net.
                    installments = self.env['hr.employee.loan.installment'].search([
                        ('loan_id.employee_id', '=', line.employee_id.id),
                        ('loan_id.contract_id', '=', line.contract_id.id),
                        ('loan_id.company_id', '=', payroll.company_id.id),
                        ('loan_id.state', '=', 'active'), ('loan_id.advance_prepayroll_id', '=', False),
                        ('state', 'in', ['pending', 'scheduled']),
                        ('scheduled_date', '>=', payroll.date_start), ('scheduled_date', '<=', payroll.date_end),
                    ])
                    if set(line.loan_installment_ids.ids) != set(installments.ids):
                        line.loan_installment_ids = [(6, 0, installments.ids)]
                    deduction = sum(installments.mapped('remaining_amount'))
                    if line.currency_id.compare_amounts(line.loan_deduction_amount, deduction):
                        line.loan_deduction_amount = deduction
                amount = sum(advances.mapped('principal_amount'))
                if line.currency_id.compare_amounts(line.salary_advance_amount, amount):
                    line.salary_advance_amount = amount
            if payroll.state in ('draft', 'computed'):
                payroll.line_ids._compute_preliminary_totals()

    def action_load_employees(self):
        result = super().action_load_employees()
        self._sync_salary_advances()
        return result

    def action_load_from_prepayroll(self):
        result = super().action_load_from_prepayroll()
        self._sync_salary_advances()
        return result

    def action_compute(self):
        result = super().action_compute()
        self._sync_salary_advances()
        return result

    def action_validate(self):
        for employee in self.line_ids.employee_id.sorted('id'):
            self.env.cr.execute('UPDATE hr_employee SET write_date = write_date WHERE id = %s', [employee.id])
        self._sync_salary_advances()
        return super().action_validate()

    def action_close(self):
        # Same employee lock as advance delivery, so a close and a delivery
        # cannot race or consume an advance in two different payrolls.
        employees = self.line_ids.employee_id.sorted('id')
        for employee in employees:
            self.env.cr.execute('UPDATE hr_employee SET write_date = write_date WHERE id = %s', [employee.id])
        self.env['hr.employee.loan'].invalidate_model()
        self._sync_salary_advances()
        for payroll in self:
            if payroll.line_ids.filtered('salary_advance_amount') and not self.env.su and not self.env.user.has_group('hr.group_hr_manager'):
                raise AccessError(_('Solo un administrador de RRHH puede liquidar un anticipo salarial.'))
            for line in payroll.line_ids:
                if line.salary_advance_amount and line.currency_id.compare_amounts(line.payment_amount, 0) < 0:
                    raise ValidationError(_('El anticipo de %s supera el neto definitivo. Concilie la diferencia antes de cerrar; no se descontará automáticamente de otra quincena.') % line.employee_id.name)
        result = super().action_close()
        for payroll in self:
            advances = self.env['hr.employee.loan'].search([
                ('advance_prepayroll_id', '!=', False), ('company_id', '=', payroll.company_id.id),
                ('employee_id', 'in', payroll.line_ids.employee_id.ids), ('state', '=', 'active'),
                ('advance_date_start', '=', payroll.date_start),
                ('advance_date_end', '=', payroll.date_end),
            ]) if payroll.payroll_type == 'regular' else self.env['hr.employee.loan']
            for advance in advances:
                line = payroll.line_ids.filtered(lambda line: line.employee_id == advance.employee_id and line.contract_id == advance.contract_id)
                if len(line) != 1:
                    raise ValidationError(_('Falta la línea de planilla del anticipo %s.') % advance.name)
                advance.installment_ids.write({
                    'paid_amount': advance.principal_amount, 'state': 'paid',
                    'payment_date': payroll.date_end, 'payroll_id': payroll.id, 'payroll_line_id': line.id,
                })
                advance._compute_balances()
                advance.advance_settled_payroll_id = payroll
        return result


class HrPrepayrollLinePayrollValues(models.Model):
    _inherit = 'hr.prepayroll.line'

    def _get_payroll_line_values(self, sequence=1):
        self.ensure_one()
        return {
            'sequence': sequence,
            'employee_id': self.employee_id.id,
            'contract_id': self.contract_id.id,
            'department_id': self.department_id.id if self.department_id else self.employee_id.department_id.id,
            'job_id': self.job_id.id,
            'remuneration_mode': self.remuneration_mode,
            'base_wage': self.base_wage,
            'days_to_pay': self.net_days_to_pay,
            'worked_days': self.net_days_to_pay,
            'period_wage': self.period_wage,
            'production_quantity': self.production_quantity,
            'production_unit': self.production_unit,
            'production_rate': self.production_rate,
            'production_amount': self.production_amount,
            'overtime_hours': self.overtime_hours,
            'overtime_amount': self.overtime_amount,
            'night_overtime_hours': self.night_overtime_hours,
            'night_overtime_amount': self.night_overtime_amount,
            'night_work_hours': self.night_work_hours,
            'night_work_amount': self.night_work_amount,
            'holiday_work_hours': self.holiday_work_hours,
            'holiday_work_amount': self.holiday_work_amount,
            'holiday_amount': getattr(self, 'holiday_amount', self.holiday_work_amount),
            'weekly_rest_work_hours': self.weekly_rest_work_hours,
            'weekly_rest_work_amount': self.weekly_rest_work_amount,
            'absence_days': self.absence_days,
            'absence_hours': self.absence_hours,
            'absence_amount': self.absence_amount,
            'paid_leave_days': self.paid_leave_days,
            'paid_leave_hours': self.paid_leave_hours,
            'sick_leave_days': self.sick_leave_days,
            'sick_leave_hours': self.sick_leave_hours,
            'sick_ordinary_amount': self.sick_ordinary_amount,
            'sick_employer_amount': self.sick_employer_amount,
            'sick_isss_amount': self.sick_isss_amount,
            'suspension_days': self.suspension_days,
            'suspension_hours': self.suspension_hours,
            'bonus_amount': self.bonus_amount,
            'commission_amount': self.commission_amount,
            'viaticos_amount': self.viaticos_amount,
            'other_income': self.other_income,
            'deduction_amount': self.deduction_amount,
            'loan_deduction_amount': self.loan_deduction_amount,
            'loan_installment_ids': [(6, 0, self.loan_installment_ids.ids)],
            'source_type': 'prepayroll',
            'prepayroll_line_id': self.id,
        }


class HrPrepayrollAdvancePeriod(models.Model):
    _inherit = 'hr.prepayroll'

    def write(self, values):
        protected = {'date_start', 'date_end', 'company_id', 'payment_frequency', 'prepayroll_type'}
        if protected.intersection(values) or values.get('state') == 'cancelled':
            if self.env['hr.employee.loan'].search_count([
                ('advance_prepayroll_id', 'in', self.ids), ('state', 'in', ['active', 'paid']),
            ]):
                raise ValidationError(_('La quincena tiene un anticipo entregado. Conserve su período y compañía; no se puede cancelar ni trasladar el pago.'))
        return super().write(values)
