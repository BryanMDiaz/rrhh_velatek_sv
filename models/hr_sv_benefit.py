from calendar import monthrange
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_is_zero

from .operational_security import MANAGER, require_group


EXIT_REASONS = [
    ('indefinite_dismissal', 'Despido sin causa, contrato indefinido'),
    ('fixed_dismissal', 'Despido sin causa antes del vencimiento'),
    ('resignation', 'Renuncia voluntaria'),
    ('expiration', 'Vencimiento del plazo'),
    ('justified_dismissal', 'Despido justificado'),
    ('agreement', 'Mutuo acuerdo u otra causa'),
]


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sv_benefit_payment_revision = fields.Integer(copy=False, groups='base.group_no_one')

    def _serialize_benefit_payments(self):
        # Update the common employee row so concurrent changes retry with a fresh snapshot.
        if self:
            self.env.cr.execute("""
                UPDATE hr_employee
                   SET sv_benefit_payment_revision = COALESCE(sv_benefit_payment_revision, 0) + 1
                 WHERE id IN %s
            """, (tuple(self.ids),))


class HrSvVacationCycle(models.Model):
    _name = 'hr.sv.vacation.cycle'
    _description = 'Saldo de vacaciones por año de servicio'
    _order = 'cycle_start desc'

    contract_id = fields.Many2one('hr.contract', required=True, ondelete='restrict')
    company_id = fields.Many2one(related='contract_id.company_id', store=True)
    cycle_start = fields.Date(string='Inicio del año de servicio', required=True)
    cycle_end = fields.Date(string='Fin del año de servicio', compute='_compute_balance', store=True)
    worked_days = fields.Integer(string='Días trabajados acreditados', required=True)
    taken_days = fields.Float(string='Días gozados', required=True, default=0)
    settled_days = fields.Float(string='Días ya liquidados', required=True, default=0)
    payment_allocation_ids = fields.One2many('hr.sv.vacation.payment.allocation', 'cycle_id', string='Pagos confirmados')
    confirmed_days = fields.Float(string='Días aplicados en Odoo', compute='_compute_balance', store=True)
    balance_days = fields.Float(string='Saldo devengado', compute='_compute_balance', store=True)
    evidence = fields.Char(string='Referencia de respaldo', required=True)

    _sql_constraints = [
        ('contract_cycle_unique', 'unique(contract_id, cycle_start)', 'El año de servicio ya está registrado.'),
    ]

    @api.depends('cycle_start', 'worked_days', 'taken_days', 'settled_days', 'payment_allocation_ids.days')
    def _compute_balance(self):
        for cycle in self:
            cycle.cycle_end = cycle.cycle_start + relativedelta(years=1) - timedelta(days=1) if cycle.cycle_start else False
            cycle.confirmed_days = sum(cycle.payment_allocation_ids.mapped('days'))
            used = cycle.taken_days + cycle.settled_days + cycle.confirmed_days
            cycle.balance_days = max(0, 15 - used) if cycle.worked_days >= 200 else 0

    _computed_balance_fields = {'cycle_end', 'balance_days', 'confirmed_days', 'payment_allocation_ids'}

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        if (any(self._computed_balance_fields.intersection(vals) for vals in vals_list)
                or any(f'default_{field}' in self.env.context for field in self._computed_balance_fields)):
            raise ValidationError(_('Los saldos y los pagos aplicados se calculan automáticamente.'))
        defaults = self.default_get(['contract_id'])
        contract_ids = {vals.get('contract_id', defaults.get('contract_id')) for vals in vals_list} - {None, False}
        contracts = self.env['hr.contract'].browse(contract_ids).exists()
        contracts.check_access('read')
        contracts.employee_id._serialize_benefit_payments()
        return super().create(vals_list)

    def write(self, vals):
        self.check_access('write')
        if self._computed_balance_fields.intersection(vals):
            raise ValidationError(_('Los saldos y los pagos aplicados se calculan automáticamente.'))
        employees = self.contract_id.employee_id
        if vals.get('contract_id'):
            contract = self.env['hr.contract'].browse(vals['contract_id'])
            contract.check_access('read')
            employees |= contract.employee_id
        employees._serialize_benefit_payments()
        if {'contract_id', 'cycle_start'} & vals.keys() and self.filtered('payment_allocation_ids'):
            raise ValidationError(_('El contrato y el ciclo de un saldo con pagos confirmados quedan fijados.'))
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        self.contract_id.employee_id._serialize_benefit_payments()
        return super().unlink()

    @api.constrains('cycle_start', 'cycle_end', 'contract_id', 'worked_days', 'taken_days', 'settled_days', 'evidence')
    def _check_cycle(self):
        for row in self:
            start = row.contract_id.date_start
            if (not start or row.cycle_start < start or min(row.worked_days, row.taken_days, row.settled_days) < 0
                    or row.worked_days > (row.cycle_end - row.cycle_start).days + 1
                    or float_compare(row.taken_days + row.settled_days + row.confirmed_days,
                                     15 if row.worked_days >= 200 else 0, precision_digits=6) > 0
                    or not (row.evidence or '').strip()):
                raise ValidationError(_('Revise el año de servicio, los días acreditados, el saldo y el respaldo.'))
            if self.search_count([
                ('id', '!=', row.id), ('company_id', '=', row.company_id.id),
                ('contract_id.employee_id', '=', row.contract_id.employee_id.id),
                ('cycle_start', '<=', row.cycle_end), ('cycle_end', '>=', row.cycle_start),
            ], limit=1):
                raise ValidationError(_('Los años de servicio para vacaciones no pueden superponerse.'))


class HrSvBenefit(models.Model):
    _name = 'hr.sv.benefit'
    _description = 'Cálculo de prestaciones de El Salvador'
    _order = 'benefit_date desc, id desc'

    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, ondelete='restrict')
    employee_id = fields.Many2one(related='contract_id.employee_id', store=True)
    company_id = fields.Many2one(related='contract_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    kind = fields.Selection([
        ('vacation', 'Vacaciones'), ('bonus', 'Aguinaldo'), ('settlement', 'Liquidación'), ('q25', 'Quincena 25'),
    ], required=True, string='Prestación')
    benefit_date = fields.Date(string='Fecha de referencia o pago', required=True, default=fields.Date.context_today)
    service_start = fields.Date(string='Inicio de servicio continuo verificado')
    exit_reason = fields.Selection(EXIT_REASONS, string='Causa de salida')
    first_payroll_day = fields.Date(
        string='Inicio del último período de pago',
        help='Indique el inicio del período completo según la frecuencia del contrato.',
    )
    last_payroll_day = fields.Date(
        string='Fin del último período de pago',
        help='Indique el fin del período completo. El cálculo toma del contrato el último día pagable.',
    )
    salary_already_paid = fields.Monetary(string='Salario base ya liquidado en el último período')
    agreed_payment = fields.Monetary(string='Importe adicional pactado y verificado')
    fixed_term_due = fields.Date(string='Vencimiento pactado antes del despido')
    resignation_eligible = fields.Boolean(string='Renuncia permanente con requisitos acreditados')
    proportional_entitlement = fields.Boolean(string='Proporcionales verificados por la causa de salida')
    verified_daily_base = fields.Monetary(string='Base diaria verificada (cuando corresponda)')
    verified_ordinary_monthly = fields.Monetary(string='Salario básico mensual verificado para Quincena 25')
    bonus_already_paid = fields.Monetary(
        string='Aguinaldo previo fuera de esta función',
        help='Incluya pagos del mismo ciclo que no fueron confirmados en una ficha de prestaciones.',
    )
    q25_verified_days = fields.Integer(string='Días computables verificados ante licencia sin goce')
    q25_already_paid = fields.Monetary(
        string='Otras sumas de Quincena 25 ya pagadas',
        help='Incluya pagos del ciclo que se liquida hechos fuera de esta función de Odoo.',
    )
    vacation_days_requested = fields.Float(string='Días de vacaciones a gozar', default=15)
    eligibility_confirmed = fields.Boolean(string='Causa, antigüedad, saldos y requisitos verificados')
    evidence = fields.Char(string='Referencia de respaldo', required=True)

    payment_date = fields.Date(string='Fecha del pago', copy=False)
    payment_reference = fields.Char(string='Referencia del comprobante o planilla', copy=False)
    payment_amount = fields.Monetary(string='Importe bruto aplicado al pago', copy=False)
    paid_by_id = fields.Many2one('res.users', string='Confirmado por', readonly=True, copy=False)
    confirmed_event_key = fields.Char(readonly=True, copy=False)
    confirmed_reference_key = fields.Char(readonly=True, copy=False)
    bonus_cycle_end = fields.Date(string='Corte del aguinaldo', readonly=True, copy=False)
    vacation_allocation_ids = fields.One2many('hr.sv.vacation.payment.allocation', 'benefit_id', string='Aplicación de vacaciones')

    calculated = fields.Boolean(readonly=True, copy=False)
    years = fields.Integer(string='Años completos', readonly=True)
    days_remaining = fields.Integer(string='Días desde el último aniversario', readonly=True)
    last_payable_days = fields.Integer(string='Días del último salario', readonly=True)
    wage_snapshot = fields.Monetary(string='Salario mensual de referencia', readonly=True)
    last_salary_amount = fields.Monetary(string='Salario base pendiente del último período', readonly=True)
    vacation_balance_days = fields.Float(string='Saldo de vacaciones acreditado', readonly=True)
    vacation_proportional_days = fields.Float(string='Días proporcionales de vacaciones', readonly=True)
    vacation_amount = fields.Monetary(string='Vacaciones y prima', readonly=True)
    bonus_amount = fields.Monetary(string='Aguinaldo pendiente', readonly=True)
    severance_amount = fields.Monetary(string='Indemnización o prestación por renuncia', readonly=True)
    q25_january_cycle = fields.Integer(string='Ejercicio de enero conciliado', readonly=True, copy=False)
    q25_january_amount = fields.Monetary(string='Pago de enero incluido en la salida', readonly=True, copy=False)
    q25_january_paid = fields.Monetary(string='Pago de enero conciliado en planilla', readonly=True, copy=False)
    q25_amount = fields.Monetary(string='Quincena 25 no gravada', readonly=True)
    total_amount = fields.Monetary(string='Total de la propuesta', readonly=True)
    payroll_id = fields.Many2one('hr.payroll', string='Planilla generada', readonly=True, ondelete='restrict', copy=False)
    q25_key = fields.Char(compute='_compute_q25_key', store=True)

    _sql_constraints = [
        ('unique_confirmed_event', 'unique(confirmed_event_key)', 'Ya hay un pago confirmado para este período o salida.'),
        ('unique_payment_reference', 'unique(confirmed_reference_key)', 'Esta referencia ya fue aplicada a esta prestación de la persona.'),
        ('unique_q25_benefit', 'unique(q25_key)', 'Ya existe una ficha de Quincena 25 para esta persona, compañía y año.'),
    ]

    RESULT_FIELDS = {
        'calculated', 'years', 'days_remaining', 'last_payable_days', 'wage_snapshot', 'last_salary_amount',
        'vacation_balance_days', 'vacation_proportional_days', 'vacation_amount', 'bonus_amount',
        'severance_amount', 'q25_amount', 'total_amount', 'payroll_id', 'q25_key', 'bonus_cycle_end',
        'paid_by_id', 'confirmed_event_key', 'confirmed_reference_key', 'vacation_allocation_ids',
        'q25_january_cycle', 'q25_january_amount', 'q25_january_paid',
    }

    @api.depends('kind', 'benefit_date', 'employee_id', 'company_id')
    def _compute_q25_key(self):
        for item in self:
            item.q25_key = (f'{item.company_id.id}/{item.employee_id.id}/{item.benefit_date.year}'
                            if item.kind == 'q25' and item.benefit_date and item.employee_id else False)

    @api.model_create_multi
    def create(self, vals_list):
        if (any(self.RESULT_FIELDS.intersection(values) for values in vals_list)
                or any(f'default_{field}' in self.env.context for field in self.RESULT_FIELDS)):
            raise ValidationError(_('Los resultados se registran al ejecutar el cálculo.'))
        return super().create(vals_list)

    def write(self, vals):
        if self.filtered(lambda item: item.payroll_id or item.paid_by_id) or self.RESULT_FIELDS.intersection(vals):
            raise ValidationError(_('Use las acciones de prestaciones. Las fichas pagadas o con planilla vinculada quedan fijadas.'))
        payment_fields = {'payment_date', 'payment_reference', 'payment_amount'}
        return super().write(dict(vals, calculated=False) if vals.keys() - payment_fields else vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_unless_paid(self):
        if self.filtered(lambda item: item.payroll_id or item.paid_by_id):
            raise ValidationError(_('No puede eliminar una ficha pagada o vinculada a una planilla.'))

    @staticmethod
    def _service_years(start, end):
        completed = relativedelta(end, start).years
        anniversary = start + relativedelta(years=completed)
        next_anniversary = start + relativedelta(years=completed + 1)
        return completed, (end - anniversary).days, (next_anniversary - anniversary).days

    @staticmethod
    def _bonus_days(years):
        return 21 if years >= 10 else 19 if years >= 3 else 15

    @staticmethod
    def _bonus_cutoff(year):
        return date(year, 10, 20) if year == 2025 else date(year, 12, 12)

    @classmethod
    def _bonus_payment_start(cls, year):
        if year >= 2026:
            return date(year, 10, 1)
        if year == 2025:
            return date(year, 10, 23)
        return cls._bonus_cutoff(year)

    def _bonus_for_date(self, start, cutoff, base, proportional):
        years, _days, _length = self._service_years(start, cutoff)
        days = self._bonus_days(years)
        next_cutoff = self._bonus_cutoff(cutoff.year)
        if cutoff > next_cutoff:
            next_cutoff = self._bonus_cutoff(cutoff.year + 1)
        if years < 1 or proportional:
            previous_cutoff = self._bonus_cutoff(next_cutoff.year - 1)
            cycle_start = previous_cutoff + timedelta(days=1)
            fraction = ((cutoff - max(start, cycle_start)).days + 1) / (next_cutoff - previous_cutoff).days
            days *= max(0.0, min(1.0, fraction))
        confirmed = self.search([
            ('company_id', '=', self.company_id.id), ('employee_id', '=', self.employee_id.id),
            ('bonus_cycle_end', '=', next_cutoff), ('paid_by_id', '!=', False), ('bonus_amount', '>', 0),
        ])
        paid = self.bonus_already_paid + sum(confirmed.mapped('bonus_amount'))
        return max(0, self.currency_id.round(days * base) - paid), next_cutoff

    def _severance(self, end, daily, years, fraction):
        if self.exit_reason == 'agreement':
            return self.agreed_payment
        if self.exit_reason not in ('indefinite_dismissal', 'fixed_dismissal', 'resignation'):
            return 0
        if self.exit_reason == 'resignation':
            if years < 2 or not self.resignation_eligible:
                return 0
            cap = 2
        else:
            cap = 4
        sector = self.contract_id._minimum_wage_sector()
        rate = self.env['hr.sv.minimum.wage']._rates_for_period(sector, end, end)
        limited_base = min(daily, rate.daily_amount * cap)
        if self.exit_reason == 'resignation':
            return limited_base * 15 * years
        indefinite = limited_base * max(15, 30 * (years + fraction))
        if self.exit_reason == 'fixed_dismissal':
            if not self.fixed_term_due or self.fixed_term_due <= end:
                raise ValidationError(_('Indique el vencimiento pactado posterior al despido.'))
            return min(indefinite, daily * (self.fixed_term_due - end).days)
        return indefinite

    @api.constrains('kind', 'contract_id', 'first_payroll_day', 'last_payroll_day')
    def _check_last_payroll_period(self):
        for item in self.filtered(lambda row: row.kind == 'settlement' and row.first_payroll_day and row.last_payroll_day):
            first, last = item.first_payroll_day, item.last_payroll_day
            frequency = item.contract_id.velatek_payment_frequency
            if frequency in ('weekly', 'fourteen_days'):
                period_days = 7 if frequency == 'weekly' else 14
                if last != first + timedelta(days=period_days - 1):
                    raise ValidationError(_('El último período de pago debe abarcar exactamente %s días.', period_days))
            elif frequency in ('biweekly', 'monthly'):
                month_end = first.replace(day=monthrange(first.year, first.month)[1])
                if frequency == 'monthly':
                    if first.day != 1 or last != month_end:
                        raise ValidationError(_('Indique el mes completo, del día 1 al último día del mes.'))
                elif (first.day, last) not in ((1, first.replace(day=15)), (16, month_end)):
                    raise ValidationError(_('Indique la quincena completa del 1 al 15 o del 16 al último día del mismo mes.'))
            else:
                raise ValidationError(_('Configure la frecuencia del último período de pago.'))

    def _vacation_cycles(self):
        self.ensure_one()
        return self.env['hr.sv.vacation.cycle'].search([
            ('company_id', '=', self.company_id.id), ('contract_id.employee_id', '=', self.employee_id.id),
            ('cycle_start', '>=', self.service_start or self.contract_id.date_start),
            ('cycle_end', '<=', self.benefit_date),
        ], order='cycle_start, id')

    @api.model
    def _q25_has_unpaid_leave(self, contract, payment, service_start):
        return bool(self.env['hr.employee.incident'].search_count([
            ('company_id', '=', contract.company_id.id), ('employee_id', '=', contract.employee_id.id),
            ('incident_type', '=', 'unpaid_leave'), ('state', 'in', ('approved', 'processed')),
            ('date_from', '<=', payment), ('date_to', '>=', max(service_start, date(payment.year - 1, 1, 15))),
        ], limit=1))

    def _release_cancelled_q25_payroll(self):
        self.ensure_one()
        if self.kind != 'q25' or self.payroll_id.state != 'cancelled':
            raise ValidationError(_('Solo se puede liberar una planilla cancelada de Quincena 25.'))
        super(HrSvBenefit, self).write({'payroll_id': False})

    def _q25_january_case(self, year):
        self.ensure_one()
        return self.search([
            ('q25_key', '=', f'{self.company_id.id}/{self.employee_id.id}/{year}'),
        ], limit=1)

    def _q25_settlement_due(self, end, start, nominal):
        self.ensure_one()
        year = end.year
        cutoff = date(year, 1, 14)
        january = self._q25_january_case(year)
        payroll = january.payroll_id
        paid = january.q25_amount if payroll.payment_verified_by_id else 0
        prior_start = date(year - 1, 1, 15)
        prior_days = max(0, (min(end, cutoff) - max(start, prior_start)).days + 1)
        new_days = max(0, (end - max(start, date(year, 1, 15))).days + 1)
        # After January, the new cycle is due. A missed January payment remains visible if unpaid.
        if payroll and payroll.state != 'cancelled':
            prior_gross = january.q25_amount
        elif end.month == 1:
            prior_gross = self.currency_id.round(nominal * 0.5 * min(1, prior_days / 365))
        else:
            january_wage = self.contract_id._velatek_terms_on(date(year, 1, 15))[0]
            prior_gross = (self.currency_id.round(january_wage * 0.5 * min(1, prior_days / 365))
                           if self.contract_id.velatek_remuneration_mode == 'fixed' and 0 < january_wage <= 1500 else 0)
        prior_due = max(0, prior_gross - paid)
        prior = max(0, prior_due - self.q25_already_paid)
        current = self.currency_id.round(nominal * 0.5 * min(1, new_days / 365)) if nominal <= 1500 else 0
        current = max(0, current - max(0, self.q25_already_paid - prior_due))
        return prior + current, prior, paid

    def action_calculate(self):
        require_group(self, MANAGER)
        for item in self:
            if item.payroll_id or item.paid_by_id:
                raise ValidationError(_('La ficha ya está pagada o vinculada a una planilla.'))
            contract, end = item.contract_id, item.benefit_date
            professional = self.env.ref('rrhh_velatek_sv.contract_type_professional_services')
            if (contract.state not in ('open', 'close') or contract.contract_type_id == professional or item.currency_id != self.env.ref('base.USD')
                    or not item.eligibility_confirmed or not (item.evidence or '').strip()):
                raise ValidationError(_('Confirme y documente los requisitos del contrato laboral en dólares.'))
            start = item.service_start or contract.date_start
            if not start or not contract.date_start or start > end or start > contract.date_start:
                raise ValidationError(_('Indique el inicio verificable del servicio continuo.'))
            if any(value < 0 for value in (item.salary_already_paid, item.agreed_payment,
                                          item.verified_daily_base, item.verified_ordinary_monthly, item.bonus_already_paid, item.q25_already_paid)):
                raise ValidationError(_('Los importes verificados no pueden ser negativos.'))
            if item.kind != 'q25' and contract.date_end and end > contract.date_end and item.kind != 'bonus':
                raise ValidationError(_('La fecha de referencia es posterior al fin del contrato.'))

            bonus_cutoff = item._bonus_cutoff(end.year)
            seniority_date = bonus_cutoff if item.kind == 'bonus' else end
            if seniority_date < start or (item.kind == 'bonus' and (
                    not item._bonus_payment_start(end.year) <= end <= date(end.year, 12, 20)
                    or contract.date_start > end or (contract.date_end and contract.date_end < end))):
                raise ValidationError(_('Use la ventana de pago legal del aguinaldo ordinario. Registre las salidas en su propia ficha.'))
            years, rest, year_length = item._service_years(start, seniority_date)
            wage = contract._velatek_terms_on(min(end, contract.date_end or end))[0]
            daily = item.verified_daily_base or wage / 30
            if item.kind != 'q25' and (daily <= 0 or (contract.velatek_remuneration_mode != 'fixed' and not item.verified_daily_base)):
                raise ValidationError(_('Registre la base diaria verificada de la prestación.'))

            vacation_balance = sum(item._vacation_cycles().mapped('balance_days')) if item.kind in ('vacation', 'settlement') else 0
            bonus_cycle_end = False
            january_year = january_amount = january_paid = 0
            vacation = bonus = severance = q25 = salary = proportional_days = paid_days = 0
            if item.kind == 'vacation':
                if item.vacation_days_requested <= 0 or float_compare(
                        item.vacation_days_requested, vacation_balance, precision_digits=6) > 0:
                    raise ValidationError(_('Los días solicitados superan el saldo acreditado.'))
                vacation = item.vacation_days_requested * daily * 1.30
            elif item.kind == 'bonus':
                bonus, bonus_cycle_end = item._bonus_for_date(start, seniority_date, daily, False)
            elif item.kind == 'settlement':
                first, last = item.first_payroll_day, item.last_payroll_day
                if not (item.exit_reason and first and last and contract.date_end == end and first <= end <= last):
                    raise ValidationError(_('Registre la causa, el fin del contrato y las fechas del último período de pago.'))
                item._check_last_payroll_period()
                paid_days = (end - max(first, contract.date_start)).days + 1
                _base, salary = contract._velatek_wage_for_period(first, last, contract.velatek_payment_frequency)
                if item.salary_already_paid > salary:
                    raise ValidationError(_('El salario ya liquidado supera el salario base del período.'))
                salary -= item.salary_already_paid
                has_proportional = (
                    item.exit_reason in ('indefinite_dismissal', 'fixed_dismissal', 'resignation')
                    or item.proportional_entitlement
                )
                if has_proportional:
                    proportional_days = 15 * rest / year_length
                    bonus, bonus_cycle_end = item._bonus_for_date(start, end, daily, True)
                    if bonus_cutoff <= end <= date(end.year, 12, 20):
                        bonus, bonus_cycle_end = item._bonus_for_date(start, bonus_cutoff, daily, False)
                vacation = (vacation_balance + proportional_days) * daily * 1.30
                severance = item._severance(end, daily, years, rest / year_length)
                if end >= date(2027, 1, 1):
                    nominal = wage if contract.velatek_remuneration_mode == 'fixed' else item.verified_ordinary_monthly
                    if not nominal:
                        raise ValidationError(_('Registre el salario nominal mensual para verificar la Quincena 25 de salida.'))
                    january_year = end.year
                    q25, january_amount, january_paid = item._q25_settlement_due(end, start, nominal)
            else:
                cutoff = date(end.year, 1, 14)
                if end.year < 2027 or end.month != 1 or not 15 <= end.day <= 25 or contract.date_start > end:
                    raise ValidationError(_('La Quincena 25 ordinaria se registra del 15 al 25 de enero, desde 2027.'))
                if contract.date_end and contract.date_end < end:
                    raise ValidationError(_('Registre la Quincena 25 de salida en la ficha de liquidación.'))
                nominal = wage if contract.velatek_remuneration_mode == 'fixed' else item.verified_ordinary_monthly
                if not 0 < nominal <= 1500:
                    raise ValidationError(_('El salario básico mensual debe ser positivo y de hasta $1,500.'))
                paid_at_exit = self.search_count([
                    ('kind', '=', 'settlement'), ('company_id', '=', item.company_id.id),
                    ('employee_id', '=', item.employee_id.id), ('q25_january_cycle', '=', end.year),
                    ('q25_january_amount', '>', 0), ('paid_by_id', '!=', False),
                ], limit=1)
                if paid_at_exit:
                    raise ValidationError(_('Revise la Quincena 25 ya confirmada en una liquidación de este ciclo.'))
                days = max(0, (cutoff - start).days + 1)
                if not days:
                    raise ValidationError(_('El contrato no ha devengado Quincena 25 para este corte.'))
                if item._q25_has_unpaid_leave(contract, end, start):
                    if not 0 < item.q25_verified_days <= 365:
                        raise ValidationError(_('Verifique los días de servicio computables por licencia sin goce.'))
                    days = min(days, item.q25_verified_days)
                wage = nominal
                q25 = nominal * 0.5 * min(1, days / 365)

            amounts = {
                'last_salary_amount': salary, 'vacation_amount': vacation, 'bonus_amount': bonus,
                'severance_amount': severance, 'q25_amount': q25,
            }
            rounded = {name: item.currency_id.round(value) for name, value in amounts.items()}
            super(HrSvBenefit, item).write(dict(
                rounded, calculated=True, years=years, days_remaining=rest, last_payable_days=paid_days,
                bonus_cycle_end=bonus_cycle_end, q25_january_cycle=january_year,
                q25_january_amount=january_amount, q25_january_paid=january_paid,
                wage_snapshot=wage, vacation_balance_days=vacation_balance, vacation_proportional_days=proportional_days,
                total_amount=sum(rounded.values()),
            ))

    def action_confirm_payment(self):
        self.ensure_one()
        require_group(self, MANAGER)
        self.check_access('write')
        if self.kind == 'q25' or self.payroll_id or self.paid_by_id or not self.calculated:
            raise ValidationError(_('Confirme el pago una sola vez en una ficha de prestación calculada.'))
        reference = ' '.join((self.payment_reference or '').split())
        if (not reference or not self.payment_date or self.payment_date > fields.Date.context_today(self)
                or self.payment_amount <= 0 or (self.kind == 'settlement' and self.payment_date < self.benefit_date)):
            raise ValidationError(_('Indique la fecha real, la referencia y el importe bruto aplicado al pago.'))

        self.employee_id._serialize_benefit_payments()
        amounts = ('last_salary_amount', 'vacation_amount', 'bonus_amount', 'severance_amount', 'q25_amount')
        reviewed = tuple(self[field] for field in amounts)
        self.action_calculate()
        if (reviewed != tuple(self[field] for field in amounts)
                or self.currency_id.compare_amounts(self.payment_amount, self.total_amount)):
            raise ValidationError(_('El importe cambió o no coincide con la propuesta. Vuelva a calcular y revise el pago.'))

        if self.kind == 'settlement' and self.q25_january_amount > 0:
            january_payroll = self._q25_january_case(self.q25_january_cycle).payroll_id
            if january_payroll and january_payroll.state != 'cancelled':
                raise ValidationError(_('Concilie o cancele la planilla de enero antes de confirmar la Quincena 25 en la liquidación.'))

        prefix = f'{self.company_id.id}/{self.employee_id.id}/{self.kind}'
        event = self.bonus_cycle_end if self.kind == 'bonus' else self.benefit_date
        days = self.vacation_days_requested if self.kind == 'vacation' else self.vacation_balance_days
        allocation_values = []
        for cycle in self._vacation_cycles() if days else ():
            applied = min(days, cycle.balance_days)
            if applied > 0:
                allocation_values.append({'benefit_id': self.id, 'cycle_id': cycle.id, 'days': applied})
                days -= applied
            if float_is_zero(days, precision_digits=6):
                break
        if not float_is_zero(days, precision_digits=6):
            raise ValidationError(_('El saldo de vacaciones cambió. Vuelva a calcular la prestación.'))
        if allocation_values:
            self.env['hr.sv.vacation.payment.allocation'].sudo().create(allocation_values)
        super(HrSvBenefit, self).write({
            'paid_by_id': self.env.uid, 'payment_reference': reference,
            'confirmed_event_key': f'{prefix}/{event}',
            'confirmed_reference_key': f'{prefix}/{reference.casefold()}',
        })

    def action_generate_q25_payroll(self):
        self.ensure_one()
        require_group(self, MANAGER)
        if self.kind != 'q25' or not self.calculated or self.q25_amount <= 0 or self.payroll_id:
            raise ValidationError(_('Calcule la Quincena 25 elegible antes de generar la planilla.'))
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM hr_sv_benefit WHERE id = %s FOR UPDATE', (self.id,))
        self.invalidate_recordset(['payroll_id'])
        self.employee_id._serialize_benefit_payments()
        payroll = self._create_q25_payroll(_('Quincena 25 - %s', self.employee_id.display_name))
        return {'type': 'ir.actions.act_window', 'res_model': 'hr.payroll', 'view_mode': 'form', 'res_id': payroll.id}

    def _create_q25_payroll(self, name):
        if not self or self.filtered(lambda case: case.kind != 'q25' or case.payroll_id) or len(self.company_id) != 1:
            raise ValidationError(_('Seleccione fichas de Quincena 25 de la misma compañía y sin planilla.'))
        payment_date = self[0].benefit_date
        if any(case.benefit_date != payment_date for case in self):
            raise ValidationError(_('Las fichas del lote deben tener la misma fecha de pago.'))
        self.action_calculate()
        payroll = self.env['hr.payroll'].create({
            'name': name, 'company_id': self.company_id.id, 'payroll_type': 'q25', 'payment_frequency': 'monthly',
            'date_start': payment_date, 'date_end': payment_date,
            'line_ids': [(0, 0, {
                'employee_id': case.employee_id.id, 'contract_id': case.contract_id.id,
                'base_wage': case.wage_snapshot, 'bonus_amount': case.q25_amount,
            }) for case in self],
        })
        super(HrSvBenefit, self).write({'payroll_id': payroll.id})
        return payroll


class HrSvVacationPaymentAllocation(models.Model):
    _name = 'hr.sv.vacation.payment.allocation'
    _description = 'Aplicación del pago al saldo anual de vacaciones'

    benefit_id = fields.Many2one('hr.sv.benefit', required=True, ondelete='restrict', index=True)
    cycle_id = fields.Many2one('hr.sv.vacation.cycle', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='benefit_id.company_id', store=True)
    cycle_start = fields.Date(related='cycle_id.cycle_start', string='Inicio del ciclo')
    cycle_end = fields.Date(related='cycle_id.cycle_end', string='Fin del ciclo')
    days = fields.Float(string='Días aplicados', required=True)

    _sql_constraints = [
        ('unique_cycle_allocation', 'unique(benefit_id, cycle_id)', 'El ciclo ya fue aplicado a este pago.'),
        ('positive_days', 'check(days > 0)', 'Los días aplicados deben ser positivos.'),
    ]
