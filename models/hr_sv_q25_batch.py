from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .operational_security import MANAGER, require_group


class HrSvQ25Batch(models.Model):
    _name = 'hr.sv.q25.batch'
    _description = 'Lote de Quincena 25'
    _order = 'payment_date desc, company_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    payment_date = fields.Date(string='Fecha prevista de pago', required=True)
    year = fields.Integer(string='Ejercicio', compute='_compute_year', store=True)
    line_ids = fields.One2many('hr.sv.q25.batch.line', 'batch_id', string='Personas evaluadas')
    payroll_id = fields.Many2one('hr.payroll', string='Planilla del lote', readonly=True, ondelete='restrict', copy=False)
    payroll_state = fields.Selection(related='payroll_id.state')

    _sql_constraints = [
        ('unique_company_year', 'unique(company_id, year)', 'Ya existe un lote por compañía y ejercicio.'),
    ]

    @api.depends('payment_date')
    def _compute_year(self):
        for batch in self:
            batch.year = batch.payment_date.year if batch.payment_date else 0

    @api.constrains('payment_date', 'company_id')
    def _check_batch(self):
        for batch in self:
            day = batch.payment_date
            if day.year < 2027 or day.month != 1 or not 15 <= day.day <= 25 or batch.currency_id != self.env.ref('base.USD'):
                raise ValidationError(_('Use una compañía en dólares y una fecha del 15 al 25 de enero desde 2027.'))

    def write(self, vals):
        if 'payroll_id' in vals or ('company_id' in vals and self.filtered('line_ids')):
            raise ValidationError(_('La compañía queda fijada al evaluar el lote.'))
        if 'payment_date' in vals and self.filtered(lambda batch: batch.payroll_id and batch.payroll_id.state != 'cancelled'):
            raise ValidationError(_('La fecha queda fijada mientras exista una planilla activa.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('default_payroll_id') or any('payroll_id' in vals for vals in vals_list):
            raise ValidationError(_('Genere la planilla con la acción del lote.'))
        return super().create(vals_list)

    def _contracts(self):
        self.ensure_one()
        return self.env['hr.contract'].with_context(active_test=False).search([
            ('company_id', '=', self.company_id.id), ('employee_id', '!=', False),
            ('state', 'in', ('open', 'close')),
            ('date_start', '<=', self.payment_date),
            '|', ('date_end', '=', False), ('date_end', '>=', date(self.year - 1, 1, 15)),
        ], order='date_start desc, id desc')

    def _assessment(self, contracts, line, january, settlements):
        self.ensure_one()
        payment = self.payment_date
        cutoff = date(payment.year, 1, 14)
        professional = self.env.ref('rrhh_velatek_sv.contract_type_professional_services')
        active = contracts.filtered(lambda c: not c.date_end or c.date_end >= payment)
        contract = (active or contracts)[0]
        start = contract.date_start
        wage = expected = 0
        prior_paid = settlements.filtered(lambda case: case.paid_by_id and case.q25_january_amount > 0)
        linked = january.payroll_id and january.payroll_id.state != 'cancelled'
        if linked or prior_paid:
            status, reason = 'eligible', _('Existe un pago de enero en planilla o en liquidación.')
            if linked:
                contract, start, wage, expected = january.contract_id, january.service_start or january.contract_id.date_start, january.wage_snapshot, january.q25_amount
            else:
                expected = sum(prior_paid.mapped('q25_january_amount'))
        elif not active:
            if contract.contract_type_id == professional:
                status, reason = 'excluded', _('Servicios profesionales independientes.')
            else:
                status, reason = 'exit', _('Contrato terminado antes del pago. Revise el proporcional en liquidación.')
        elif len(active) > 1:
            status, reason = 'review', _('Hay más de un contrato vigente. Aclare el vínculo y el salario nominal.')
        elif contract.contract_type_id == professional:
            status, reason = 'excluded', _('Contrato registrado como servicios profesionales independientes.')
        else:
            # A verified date may document continuity beyond the available contracts.
            history = self.env['hr.contract'].with_context(active_test=False).search([
                ('company_id', '=', self.company_id.id), ('employee_id', '=', contract.employee_id.id),
                ('state', 'in', ('open', 'close')), ('date_start', '<', start), ('date_end', '!=', False),
                ('contract_type_id', '!=', professional.id),
            ], order='date_start desc, id desc')
            for prior in history:
                if prior.date_end >= start - timedelta(days=1):
                    start = prior.date_start
                else:
                    break
            if line.reviewed_service_start and line.review_reference:
                start = line.reviewed_service_start
            wage = contract._velatek_terms_on(payment)[0] if contract.velatek_remuneration_mode == 'fixed' else line.verified_nominal
            unpaid_leave = self.env['hr.sv.benefit']._q25_has_unpaid_leave(contract, payment, start)
            if start > cutoff:
                status, reason = 'excluded', _('Sin servicio devengado al 14 de enero.')
            elif wage > 1500:
                status, reason = 'excluded', _('Salario nominal superior a $1,500.')
            elif unpaid_leave and line.leave_resolution == 'excluded' and line.review_reference:
                status, reason = 'excluded', _('Licencia sin goce revisada. Respaldo %s', line.review_reference)
            elif unpaid_leave and not (line.leave_resolution == 'eligible' and line.review_reference and line.verified_days):
                status, reason = 'review', _('Hay licencia sin goce. Documente la decisión y los días computables.')
            elif wage <= 0 or (contract.velatek_remuneration_mode != 'fixed' and not line.review_reference):
                status, reason = 'review', _('Falta verificar el salario básico o nominal mensual.')
            else:
                days = min(365, (cutoff - start).days + 1)
                if unpaid_leave:
                    days = min(days, line.verified_days)
                expected = self.currency_id.round(wage * 0.5 * days / 365)
                status, reason = ('eligible', _('Cumple el corte de servicio y el límite salarial.')
                                  ) if expected else ('excluded', _('Sin importe devengado para el período.'))
        return {
            'contract_id': contract.id, 'service_start': start, 'nominal': wage,
            'expected_amount': expected, 'assessment': status, 'reason': reason,
        }

    def _refresh_lines(self):
        self.ensure_one()
        contracts = self._contracts()
        Lines = self.env['hr.sv.q25.batch.line'].sudo()
        old = {line.employee_id.id: line for line in self.line_ids}
        Benefits = self.env['hr.sv.benefit']
        latest = date(self.year + 1, 1, 14)
        cases = Benefits.search([
            ('company_id', '=', self.company_id.id), ('employee_id', 'in', contracts.employee_id.ids),
            ('kind', 'in', ('q25', 'settlement')),
            ('benefit_date', '>=', date(self.year, 1, 1)), ('benefit_date', '<=', latest),
        ])
        for employee_id, employee_contracts in contracts.grouped('employee_id').items():
            row = old.pop(employee_id.id, Lines.browse())
            own = cases.filtered(lambda case: case.employee_id == employee_id)
            january = own.filtered(lambda case: case.kind == 'q25' and case.benefit_date.year == self.year)
            settlements = own.filtered(lambda case: case.kind == 'settlement' and case.q25_january_cycle == self.year)
            values = self._assessment(employee_contracts, row, january, settlements)
            values.update(january_case_id=january.id)
            if row:
                row.sudo().write(values)
            else:
                Lines.create(dict(values, batch_id=self.id, employee_id=employee_id.id))
        Lines.browse([row.id for row in old.values()]).unlink()
        self.invalidate_recordset(['line_ids'])
        self.line_ids.invalidate_recordset(['payment_status', 'january_paid', 'settlement_paid', 'settlement_pending', 'settlement_id'])

    def action_assess(self):
        self.ensure_one()
        require_group(self, MANAGER)
        self.check_access('write')
        self._refresh_lines()

    def action_generate(self):
        self.ensure_one()
        require_group(self, MANAGER)
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM hr_sv_q25_batch WHERE id = %s FOR UPDATE', (self.id,))
        self.invalidate_recordset(['payroll_id'])
        if self.payroll_id and self.payroll_id.state != 'cancelled':
            raise ValidationError(_('El lote ya tiene una planilla.'))
        self._contracts().employee_id._serialize_benefit_payments()
        self._refresh_lines()
        if self.line_ids.filtered(lambda row: row.assessment == 'review'):
            raise ValidationError(_('Resuelva las personas pendientes de revisión antes de generar el lote.'))
        payable = self.line_ids.filtered(lambda row: row.assessment == 'eligible' and row.payment_status == 'pending')
        if not payable:
            raise ValidationError(_('El lote no tiene personas elegibles pendientes de generar.'))

        Benefits = self.env['hr.sv.benefit']
        cases = Benefits.browse()
        for row in payable:
            values = {
                'contract_id': row.contract_id.id, 'benefit_date': self.payment_date, 'service_start': row.service_start,
                'verified_ordinary_monthly': row.nominal, 'eligibility_confirmed': True,
                'q25_verified_days': row.verified_days if row.leave_resolution == 'eligible' and row.review_reference else 0,
                'evidence': row.review_reference or _('Evaluación del lote de Quincena 25 %s', self.year),
            }
            qcase = row.january_case_id
            if qcase.payroll_id.state == 'cancelled':
                qcase._release_cancelled_q25_payroll()
            if qcase:
                qcase.write(values)
            else:
                qcase = Benefits.create(dict(values, kind='q25'))
            row.sudo().write({'january_case_id': qcase.id})
            cases |= qcase
        payroll = cases._create_q25_payroll(_('Quincena 25 de %s', self.year))
        super(HrSvQ25Batch, self).write({'payroll_id': payroll.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'hr.payroll', 'view_mode': 'form', 'res_id': payroll.id}


class HrSvQ25BatchLine(models.Model):
    _name = 'hr.sv.q25.batch.line'
    _description = 'Evaluación individual de Quincena 25'
    _order = 'employee_id'

    batch_id = fields.Many2one('hr.sv.q25.batch', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='batch_id.company_id', store=True)
    currency_id = fields.Many2one(related='batch_id.currency_id')
    employee_id = fields.Many2one('hr.employee', required=True, readonly=True)
    contract_id = fields.Many2one('hr.contract', readonly=True)
    service_start = fields.Date(string='Inicio de servicio aplicado', readonly=True)
    nominal = fields.Monetary(string='Base nominal', readonly=True)
    expected_amount = fields.Monetary(string='Devengado de enero', readonly=True)
    assessment = fields.Selection([
        ('eligible', 'Elegible'), ('excluded', 'Excluido'), ('review', 'Por revisar'), ('exit', 'Salida laboral'),
    ], readonly=True, string='Elegibilidad')
    reason = fields.Char(string='Motivo', readonly=True)
    january_case_id = fields.Many2one('hr.sv.benefit', string='Ficha de enero', readonly=True)
    verified_nominal = fields.Monetary(string='Base nominal verificada')
    reviewed_service_start = fields.Date(string='Inicio de servicio verificado')
    verified_days = fields.Integer(string='Días computables verificados')
    leave_resolution = fields.Selection([('eligible', 'Computar días verificados'), ('excluded', 'Exclusión documentada')], string='Decisión sobre licencia')
    review_reference = fields.Char(string='Respaldo de revisión')
    payment_status = fields.Selection([
        ('pending', 'Pendiente de generar'), ('payroll', 'Planilla pendiente de pago'),
        ('paid', 'Pagado en enero'), ('settled', 'Pagado en liquidación'),
        ('exit', 'Revisar liquidación'), ('na', 'Sin pago programado'),
    ], compute='_compute_tracking', string='Seguimiento')
    january_paid = fields.Monetary(string='Pagado en planilla', compute='_compute_tracking')
    settlement_paid = fields.Monetary(string='Quincena 25 pagada en salidas', compute='_compute_tracking')
    settlement_pending = fields.Monetary(string='Última salida pendiente', compute='_compute_tracking')
    settlement_id = fields.Many2one('hr.sv.benefit', string='Última liquidación', compute='_compute_tracking')

    _sql_constraints = [
        ('unique_employee', 'unique(batch_id, employee_id)', 'La persona ya está evaluada en este lote.'),
        ('review_values', 'check(verified_nominal >= 0 and verified_days between 0 and 365)', 'Revise el salario y los días verificados.'),
    ]

    def write(self, vals):
        if not self.env.su:
            require_group(self, MANAGER)
            allowed = {'verified_nominal', 'reviewed_service_start', 'verified_days', 'leave_resolution', 'review_reference'}
            if vals.keys() - allowed:
                raise ValidationError(_('Use Evaluar personal para actualizar los resultados.'))
        return super().write(vals)

    @api.constrains('reviewed_service_start', 'contract_id')
    def _check_reviewed_start(self):
        if self.filtered(lambda row: row.reviewed_service_start and row.reviewed_service_start > row.contract_id.date_start):
            raise ValidationError(_('El inicio continuo verificado debe ser igual o anterior al inicio del contrato.'))

    @api.depends('assessment', 'january_case_id.payroll_id.state', 'january_case_id.payroll_id.payment_verified_by_id')
    def _compute_tracking(self):
        Benefits = self.env['hr.sv.benefit']
        for row in self:
            batch = row.batch_id
            payroll = row.january_case_id.payroll_id
            exits = Benefits.search([
                ('company_id', '=', row.company_id.id), ('employee_id', '=', row.employee_id.id), ('kind', '=', 'settlement'),
                ('benefit_date', '>=', date(batch.year - 1, 1, 15)),
                ('benefit_date', '<=', date(batch.year, 12, 31)),
            ], order='benefit_date desc, paid_by_id desc, id desc')
            confirmed = exits.filtered('paid_by_id')
            latest = exits[:1]
            row.january_paid = row.january_case_id.q25_amount if payroll.payment_verified_by_id else 0
            row.settlement_id = latest
            row.settlement_paid = sum(confirmed.mapped('q25_amount'))
            row.settlement_pending = latest.q25_amount if not latest.paid_by_id and not confirmed.filtered(
                lambda case: case.benefit_date == latest.benefit_date) else 0
            if row.january_paid:
                row.payment_status = 'paid'
            elif payroll and payroll.state != 'cancelled':
                row.payment_status = 'payroll'
            elif confirmed.filtered(lambda case: case.q25_january_cycle == batch.year and case.q25_january_amount > 0):
                row.payment_status = 'settled'
            elif row.assessment == 'eligible':
                row.payment_status = 'pending'
            elif row.assessment == 'exit':
                row.payment_status = 'settled' if confirmed.filtered(lambda case: case.q25_amount > 0) else 'exit'
            else:
                row.payment_status = 'na'
