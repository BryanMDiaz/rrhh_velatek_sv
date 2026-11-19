from decimal import Decimal
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class HrEmployeeLoan(models.Model):
    _name = 'hr.employee.loan'
    _description = 'Préstamo de Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Referencia', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, tracking=True, domain="[('contract_ids.state', '=', 'open')]")
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, domain="[('employee_id', '=', employee_id), ('state', '=', 'open')]", tracking=True)
    company_id = fields.Many2one('res.company', string='Compañía', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id:
            # Find running contract
            contract = self.env['hr.contract'].search([
                ('employee_id', '=', self.employee_id.id),
                ('state', '=', 'open')
            ], limit=1)
            if contract:
                self.contract_id = contract.id
            else:
                self.contract_id = False
                return {
                    'warning': {
                        'title': _('Sin contrato activo'),
                        'message': _('El empleado seleccionado no tiene un contrato activo (vigente).')
                    }
                }

    loan_type_id = fields.Many2one('hr.employee.loan.type', string='Tipo de Préstamo', required=True, tracking=True)

    request_date = fields.Date(string='Fecha de Solicitud', default=fields.Date.context_today, required=True, tracking=True)
    approval_date = fields.Date(string='Fecha de Aprobación', readonly=True, tracking=True)
    disbursement_date = fields.Date(string='Fecha de Desembolso', tracking=True)

    principal_amount = fields.Monetary(string='Monto Original', required=True, tracking=True)
    interest_amount = fields.Monetary(string='Monto de Interés', default=0.0, tracking=True)
    total_financed = fields.Monetary(string='Total Financiado', compute='_compute_total_financed', store=True)

    requested_installment = fields.Monetary(string='Cuota Solicitada', tracking=True)
    approved_installment = fields.Monetary(string='Cuota Aprobada', compute='_compute_approved_installment', store=True, tracking=True)

    payment_frequency = fields.Selection([
        ('biweekly', 'Cada quincena'),
        ('monthly', 'Una vez al mes'),
        ('alternate_biweekly', 'Una quincena sí y la siguiente no'),
        ('custom_interval', 'Cada N períodos')
    ], string='Forma de Descuento', required=True, default='biweekly', tracking=True)
    
    loan_template_id = fields.Many2one('hr.employee.loan.template', string='Plantilla de Préstamo', tracking=True)
    contract_html = fields.Html(
        string='Contrato personalizado', sanitize=False, copy=False,
        help='Contenido editado manualmente para este préstamo; se utiliza al imprimir.',
    )
    signed_contract_document = fields.Binary(string='Contrato Firmado (PDF)', attachment=True)
    signed_contract_filename = fields.Char(string='Nombre de Archivo')

    monthly_payroll_position = fields.Selection([
        ('first', 'Primera quincena del mes'),
        ('second', 'Segunda quincena del mes'),
        ('monthly_payroll', 'Planilla mensual')
    ], string='Posición en Planilla Mensual', default='monthly_payroll')

    # Alert Fields
    active_loan_id = fields.Many2one('hr.employee.loan', string='Préstamo Activo Existente', compute='_compute_active_loan')
    has_active_loan = fields.Boolean(compute='_compute_active_loan')

    installment_interval = fields.Integer(string='Intervalo (N períodos)', default=1)

    first_payment_date = fields.Date(string='Fecha Primer Cobro', required=True, tracking=True)

    estimated_installments = fields.Integer(string='Cuotas Estimadas', default=1, required=True, tracking=True)
    last_installment_amount = fields.Monetary(string='Última Cuota', compute='_compute_last_installment_amount', store=True)
    estimated_end_date = fields.Date(string='Fecha Estimada Final', compute='_compute_estimated_end_date', store=True)

    installment_ids = fields.One2many('hr.employee.loan.installment', 'loan_id', string='Cuotas')

    total_paid = fields.Monetary(string='Total Abonado', compute='_compute_balances', store=True)
    remaining_balance = fields.Monetary(string='Saldo Pendiente', compute='_compute_balances', store=True)

    # Capacidad
    reference_income = fields.Monetary(string='Ingreso de Referencia', compute='_compute_capacity', store=True)
    existing_loan_commitments = fields.Monetary(string='Compromisos Actuales', compute='_compute_capacity', store=True)
    maximum_recommended_deduction = fields.Monetary(string='Máx. Deducción Recomendada', compute='_compute_capacity', store=True)
    available_payment_capacity = fields.Monetary(string='Capacidad Disponible', compute='_compute_capacity', store=True)
    capacity_percentage = fields.Float(string='% de Capacidad', compute='_compute_capacity_percentage')
    capacity_status = fields.Selection([
        ('pending', 'PENDIENTE DE EVALUACIÓN'),
        ('ok', 'DENTRO DE CAPACIDAD RECOMENDADA'),
        ('exceeded', 'EXCEDE CAPACIDAD RECOMENDADA')
    ], string='Estado de Capacidad', compute='_compute_capacity', store=True)

    @api.depends('loan_type_id.max_capacity_percentage')
    def _compute_capacity_percentage(self):
        for loan in self:
            loan.capacity_percentage = (loan.loan_type_id.max_capacity_percentage / 100.0) if loan.loan_type_id else 0.0

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('submitted', 'Por aprobar'),
        ('approved', 'Aprobado'),
        ('active', 'En cobro'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='draft', required=True, tracking=True)

    notes = fields.Text(string='Notas')
    created_by_id = fields.Many2one('res.users', string='Creado por', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', string='Aprobado por', readonly=True)

    @api.depends('principal_amount', 'interest_amount')
    def _compute_total_financed(self):
        for loan in self:
            loan.total_financed = loan.principal_amount + loan.interest_amount

    @api.depends('total_financed', 'estimated_installments')
    def _compute_approved_installment(self):
        for loan in self:
            if loan.estimated_installments > 0:
                loan.approved_installment = loan.total_financed / loan.estimated_installments
            else:
                loan.approved_installment = 0.0

    @api.depends('total_financed', 'estimated_installments', 'approved_installment', 'currency_id')
    def _compute_last_installment_amount(self):
        for loan in self:
            try:
                amounts = loan._get_calendar_amounts()
                loan.last_installment_amount = amounts[-1]
            except ValidationError:
                # Allow incomplete drafts; calendar generation validates inputs.
                loan.last_installment_amount = 0.0

    def _get_calendar_amounts(self):
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        unit = Decimal(str(currency.rounding))
        total_units = int(Decimal(str(currency.round(self.total_financed))) / unit)
        count = self.estimated_installments
        if count <= 0 or total_units <= 0:
            raise ValidationError(_('Defina un total financiado y una cantidad de cuotas mayores a cero.'))
        if count > total_units:
            raise ValidationError(_('El monto no permite esa cantidad de cuotas: cada cuota debe ser al menos la unidad mínima de la moneda.'))
        nominal_units = int(Decimal(str(currency.round(self.approved_installment))) / unit)
        if nominal_units <= 0:
            raise ValidationError(_('La cuota debe ser mayor a cero después del redondeo.'))
        remaining = total_units
        amounts = []
        for sequence in range(1, count + 1):
            # Reserve at least one currency unit for each remaining installment.
            units = min(nominal_units, remaining - (count - sequence)) if sequence < count else remaining
            amounts.append(float(unit * units))
            remaining -= units
        return amounts

    @api.depends('first_payment_date', 'estimated_installments', 'payment_frequency', 'installment_interval', 'installment_ids.scheduled_date')
    def _compute_estimated_end_date(self):
        for loan in self:
            dates = loan.installment_ids.mapped('scheduled_date')
            if dates and all(dates):
                loan.estimated_end_date = max(dates)
            elif loan.first_payment_date and loan.estimated_installments > 0 and loan.payment_frequency and (loan.payment_frequency != 'custom_interval' or loan.installment_interval > 0):
                current = loan.first_payment_date
                for _ in range(loan.estimated_installments - 1):
                    current = loan._next_installment_date(current)
                loan.estimated_end_date = current
            else:
                loan.estimated_end_date = False

    def _next_installment_date(self, current):
        self.ensure_one()
        if self.payment_frequency == 'monthly':
            return current + relativedelta(months=1)
        if self.payment_frequency == 'alternate_biweekly':
            if current.day <= 15:
                return (current + relativedelta(months=1)).replace(day=15)
            return current.replace(day=1) + relativedelta(months=2) - relativedelta(days=1)
        periods = self.installment_interval if self.payment_frequency == 'custom_interval' else 1
        if periods <= 0:
            raise ValidationError(_('El intervalo de descuento debe ser mayor a cero.'))
        for _ in range(periods):
            if current.day <= 15:
                current = current.replace(day=1) + relativedelta(months=1) - relativedelta(days=1)
            else:
                current = (current + relativedelta(months=1)).replace(day=15)
        return current

    @api.depends('installment_ids.state', 'installment_ids.paid_amount', 'total_financed')
    def _compute_balances(self):
        for loan in self:
            currency = loan.currency_id or self.env.company.currency_id
            total_paid = currency.round(sum(loan.installment_ids.mapped('paid_amount')))
            loan.total_paid = total_paid
            loan.remaining_balance = currency.round(loan.total_financed - total_paid)

            if loan.state == 'active' and currency.compare_amounts(loan.remaining_balance, 0) <= 0:
                loan.state = 'paid'

    @api.depends('employee_id')
    def _compute_active_loan(self):
        for loan in self:
            if loan.employee_id:
                domain = [
                    ('employee_id', '=', loan.employee_id.id),
                    ('state', 'in', ('approved', 'active')),
                ]
                if loan.ids:
                    domain.append(('id', '!=', loan.ids[0]))
                
                active_loan = self.env['hr.employee.loan'].search(domain, limit=1)
                if active_loan:
                    loan.active_loan_id = active_loan.id
                    loan.has_active_loan = True
                else:
                    loan.active_loan_id = False
                    loan.has_active_loan = False
            else:
                loan.active_loan_id = False
                loan.has_active_loan = False



    @api.depends('employee_id', 'contract_id', 'requested_installment', 'approved_installment', 'loan_type_id')
    def _compute_capacity(self):
        for loan in self:
            installment = loan.approved_installment or loan.requested_installment
            if not loan.employee_id or not loan.contract_id or not loan.loan_type_id or not installment:
                loan.reference_income = 0.0
                loan.existing_loan_commitments = 0.0
                loan.maximum_recommended_deduction = 0.0
                loan.available_payment_capacity = 0.0
                loan.capacity_status = 'pending'
                continue

            # Reference income fallback to contract wage. Better logic can fetch closed payrolls later.
            loan.reference_income = loan.contract_id.wage

            domain = [
                ('employee_id', '=', loan.employee_id.id),
                ('state', 'in', ('active', 'approved')),
            ]
            if loan.ids:
                domain.append(('id', '!=', loan.ids[0]))
            active_loans = self.search(domain)
            loan.existing_loan_commitments = sum(active_loans.mapped('approved_installment'))

            percentage = loan.loan_type_id.max_capacity_percentage / 100.0 if loan.loan_type_id.max_capacity_percentage else 0.30
            loan.maximum_recommended_deduction = loan.reference_income * percentage
            loan.available_payment_capacity = loan.maximum_recommended_deduction - loan.existing_loan_commitments

            if installment > loan.available_payment_capacity:
                loan.capacity_status = 'exceeded'
            else:
                loan.capacity_status = 'ok'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('hr.employee.loan') or _('Nuevo')
        return super().create(vals_list)

    def action_submit(self):
        for loan in self:
            if not loan.employee_id:
                raise ValidationError(_("Debe seleccionar un empleado."))
            if not loan.loan_type_id:
                raise ValidationError(_("Debe definir un tipo de préstamo."))
            if loan.principal_amount <= 0:
                raise ValidationError(_("El monto del préstamo debe ser mayor a cero."))
            if not loan.estimated_installments or loan.estimated_installments <= 0:
                raise ValidationError(_("Debe definir una cantidad de cuotas válida."))
            if not loan.payment_frequency:
                raise ValidationError(_("Debe definir una forma de descuento."))
            if not loan.first_payment_date:
                raise ValidationError(_("Debe definir la fecha del primer descuento."))
            if not loan.installment_ids:
                raise ValidationError(_("Debe generar el calendario de cuotas antes de enviar por aprobar."))
            if not loan.signed_contract_document:
                raise ValidationError(_("Debe subir el contrato firmado en PDF antes de enviar a aprobación."))
            if loan.has_active_loan and not self.env.context.get('force_submit_additional_loan'):
                action = self.env.ref('rrhh_velatek_sv.action_hr_loan_additional_wizard').read()[0]
                action['context'] = {
                    'default_loan_id': loan.id,
                    'default_active_loan_id': loan.active_loan_id.id,
                }
                return action
                
            loan.state = 'submitted'
            if not loan.approved_installment:
                loan.approved_installment = loan.requested_installment

    def action_approve(self):
        self.ensure_one()
        if self.principal_amount <= 0:
            raise ValidationError(_("El monto original debe ser mayor a cero."))
        if self.approved_installment <= 0:
            raise ValidationError(_("La cuota aprobada debe ser mayor a cero."))
        if not self.installment_ids:
            raise ValidationError(_("Debe generar el calendario de cuotas antes de aprobar."))

        self.state = 'approved'
        self.approval_date = fields.Date.context_today(self)
        self.approved_by_id = self.env.user

    def action_activate(self):
        self.ensure_one()
        self.state = 'active'
        if not self.disbursement_date:
            self.disbursement_date = fields.Date.context_today(self)

    def _get_rendered_contract_html(self):
        self.ensure_one()
        if self.contract_html:
            return self.contract_html
        if not self.loan_template_id or not self.loan_template_id.body_html:
            return ""
        wizard = self.env['hr.loan.template.preview.wizard'].new({
            'template_id': self.loan_template_id.id,
            'employee_id': self.employee_id.id,
            'contract_id': self.contract_id.id if self.contract_id else False,
            'loan_id': self.id,
        })
        wizard._render_preview()
        return wizard.preview_html or ""

    def action_preview_contract(self):
        self.ensure_one()
        if not self.loan_template_id:
            raise ValidationError(_("Debe seleccionar una Plantilla de Préstamo para ver la vista previa."))
        wizard = self.env['hr.loan.template.preview.wizard'].create({
            'template_id': self.loan_template_id.id,
            'employee_id': self.employee_id.id,
            'contract_id': self.contract_id.id if self.contract_id else False,
            'loan_id': self.id,
        })
        wizard._render_preview()
        return {
            'name': f'Vista Previa: {self.name} - {self.loan_template_id.name}',
            'context': dict(self.env.context, default_editing_loan_id=self.id),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.loan.template.preview.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }

    def action_print_contract(self):
        self.ensure_one()
        if not self.loan_template_id:
            raise ValidationError(_("Debe seleccionar una Plantilla de Préstamo para imprimir el contrato."))
        existing_loans = self.search([
            ('employee_id', '=', self.employee_id.id),
            ('company_id', '=', self.company_id.id),
        ]).with_context(bin_size=True).filtered(
            lambda loan: loan.currency_id.compare_amounts(loan.remaining_balance, 0.0) > 0
            and (loan.state in ('submitted', 'approved', 'active') or loan.signed_contract_document)
        )
        if existing_loans:
            states = dict(self._fields['state']._description_selection(self.env))
            references = []
            for loan in existing_loans:
                detail = states.get(loan.state, loan.state)
                if loan.signed_contract_document:
                    detail += _('; contrato subido')
                references.append('%s (%s)' % (loan.name, detail))
            wizard = self.env['hr.loan.print.confirm.wizard'].create({
                'loan_id': self.id,
                'message': _(
                    'El empleado tiene un préstamo con saldo pendiente y contrato subido o en proceso. '
                    'No se puede generar otro sin confirmar.\n\n'
                    'Referencias:\n%s\n\n'
                    '¿Desea continuar con la impresión del contrato %s?'
                ) % ('\n'.join(references), self.name),
            })
            return {
                'name': _('Confirmar impresión de contrato'),
                'type': 'ir.actions.act_window',
                'res_model': wizard._name,
                'res_id': wizard.id,
                'view_mode': 'form',
                'target': 'new',
            }
        return self._print_contract_report()

    def _print_contract_report(self):
        self.ensure_one()
        if not self.loan_template_id:
            raise ValidationError(_('Debe seleccionar una Plantilla de Préstamo para imprimir el contrato.'))
        return self.env.ref('rrhh_velatek_sv.action_report_hr_employee_loan_contract').report_action(self)

    def action_cancel(self):
        self.ensure_one()
        self.state = 'cancelled'

    def action_generate_calendar(self):
        self.ensure_one()
        if self.state not in ('draft', 'submitted'):
            raise ValidationError(_("Solo puede regenerar el calendario en estado borrador o por aprobar."))

        if not self.employee_id:
            raise ValidationError(_("Debe seleccionar un empleado antes de generar el calendario."))
        if self.principal_amount <= 0:
            raise ValidationError(_("El monto del préstamo debe ser mayor a cero."))
        
        if not self.estimated_installments or self.estimated_installments <= 0:
            raise ValidationError(_("Defina una cantidad de cuotas mayor a cero antes de generar el calendario."))
        amounts = self._get_calendar_amounts()
            
        if not self.payment_frequency:
            raise ValidationError(_("Defina una forma de descuento antes de generar el calendario."))
            
        if not self.first_payment_date:
            raise ValidationError(_("Defina la fecha del primer descuento antes de generar el calendario."))

        if self.payment_frequency == 'custom_interval' and self.installment_interval <= 0:
            raise ValidationError(_('El intervalo de descuento debe ser mayor a cero.'))

        self.installment_ids.unlink()
        current_date = self.first_payment_date

        installments = []
        for seq, amt in enumerate(amounts, 1):
            installments.append((0, 0, {
                'sequence': seq,
                'scheduled_date': current_date,
                'amount': amt,
                'state': 'pending',
            }))
            
            if seq < len(amounts):
                current_date = self._next_installment_date(current_date)

        self.write({'installment_ids': installments})
