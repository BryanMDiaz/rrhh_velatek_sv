from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .operational_security import MANAGER, require_group


PAYMENT_FIELDS = {
    'payment_date', 'payment_reference', 'payment_verified_amount', 'payment_proof',
    'payment_proof_name', 'payment_verified_by_id', 'fiscal_recognition_date', 'payment_event_ids',
}


class HrFiscalContract(models.Model):
    _inherit = 'hr.contract'

    sv_honorarium_isr_profile = fields.Selection([
        ('domestic_individual', 'Persona natural domiciliada, art. 156 (10 %)'),
        ('verified', 'Otro perfil fiscal con tasa verificada'),
    ], string='Perfil ISR de honorarios')
    sv_honorarium_isr_rate = fields.Float(string='Tasa de retención verificada (%)')
    sv_honorarium_isr_reference = fields.Char(string='Fundamento del perfil fiscal')

    @api.constrains('sv_honorarium_isr_profile', 'sv_honorarium_isr_rate', 'sv_honorarium_isr_reference')
    def _check_honorarium_tax_profile(self):
        for contract in self.filtered(lambda row: row.sv_honorarium_isr_profile == 'verified'):
            if not 0 <= contract.sv_honorarium_isr_rate <= 100 or not (contract.sv_honorarium_isr_reference or '').strip():
                raise ValidationError(_('Indique una tasa entre 0 y 100 y el fundamento fiscal aplicable.'))

    def _honorarium_tax_rate(self):
        self.ensure_one()
        if self.sv_honorarium_isr_profile == 'domestic_individual':
            return 10.0
        return self.sv_honorarium_isr_rate if self.sv_honorarium_isr_profile == 'verified' else 0.0


class HrPayrollFiscal(models.Model):
    _inherit = 'hr.payroll'

    payment_date = fields.Date(string='Fecha del pago conciliado', copy=False, readonly=True)
    payment_reference = fields.Char(string='Referencia de pago', copy=False, readonly=True)
    payment_verified_amount = fields.Monetary(string='Monto pagado verificado', copy=False, readonly=True)
    payment_proof = fields.Binary(string='Comprobante de pago', attachment=True, copy=False, readonly=True)
    payment_proof_name = fields.Char(copy=False, readonly=True)
    payment_verified_by_id = fields.Many2one('res.users', string='Conciliado por', copy=False, readonly=True)
    fiscal_recognition_date = fields.Date(string='Fecha fiscal de pago o acreditación', copy=False, readonly=True)
    payment_due_total = fields.Monetary(string='Total a conciliar', compute='_compute_payment_due_total')

    payment_event_ids = fields.One2many('hr.payroll.payment.event', 'payroll_id', readonly=True)
    payment_pending_amount = fields.Monetary(string='Saldo pendiente', compute='_compute_payment_pending')
    payment_tracking = fields.Selection([
        ('pending', 'Pendiente'), ('partial', 'Pago parcial'), ('rejected', 'Rechazado'), ('paid', 'Pagado'),
    ], string='Estado del pago', compute='_compute_payment_pending')

    @api.depends('payment_due_total', 'payment_verified_amount', 'payment_verified_by_id', 'payment_event_ids.state')
    def _compute_payment_pending(self):
        for payroll in self:
            balance = max(0, payroll.currency_id.round(payroll.payment_due_total - payroll.payment_verified_amount))
            payroll.payment_pending_amount = balance
            if payroll.payment_verified_by_id and not balance:
                payroll.payment_tracking = 'paid'
            elif payroll.payment_verified_amount:
                payroll.payment_tracking = 'partial'
            elif 'rejected' in payroll.payment_event_ids.mapped('state'):
                payroll.payment_tracking = 'rejected'
            else:
                payroll.payment_tracking = 'pending'

    @api.depends('line_ids.total_to_settle')
    def _compute_payment_due_total(self):
        for payroll in self:
            payroll.payment_due_total = sum(payroll.line_ids.mapped('total_to_settle'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if PAYMENT_FIELDS.intersection(vals) or any(
                f'default_{field}' in self.env.context for field in PAYMENT_FIELDS
            ):
                raise ValidationError(_('Registre los datos de pago mediante su acción en la planilla cerrada.'))
        return super().create(vals_list)

    def _operational_metadata_fields(self):
        return super()._operational_metadata_fields() | PAYMENT_FIELDS

    def _write_operational_values(self, vals, *, from_workflow=False):
        if PAYMENT_FIELDS.intersection(vals):
            require_group(self, MANAGER)
            if not from_workflow:
                raise ValidationError(_('Use la acción Registrar pago y fecha fiscal.'))
        return super()._write_operational_values(vals, from_workflow=from_workflow)

    @api.constrains('state')
    def _check_honorarium_payroll_tax_profile(self):
        for payroll in self.filtered(lambda row: not row.is_test and row.payroll_type == 'professional_services'
                                     and row.state in ('computed', 'validated', 'closed')):
            for line in payroll.line_ids:
                if not line.contract_id.sv_honorarium_isr_profile:
                    raise ValidationError(_('%s: configure el perfil ISR de honorarios en su contrato.', line.employee_id.display_name))
                if line.isr_rate_snapshot != line.contract_id._honorarium_tax_rate():
                    raise ValidationError(_('%s: el perfil fiscal cambió. Recalcule la planilla.', line.employee_id.display_name))

    def action_open_payment_fiscal(self):
        self.ensure_one()
        require_group(self, MANAGER)
        return {
            'type': 'ir.actions.act_window', 'name': _('Registrar pago y fecha fiscal'),
            'res_model': 'hr.payroll.payment.fiscal.wizard', 'view_mode': 'form', 'target': 'new',
            'context': dict(self.env.context, default_payroll_id=self.id, default_fiscal_date=self.fiscal_recognition_date),
        }


class HrSpuCompany(models.Model):
    _inherit = 'res.company'

    sv_spu_employer_number = fields.Char(string='Número patronal ISSS (SPU)')


class HrSpuWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    sv_spu_work_center = fields.Char(string='Correlativo del centro en SPU')


class HrSpuAfp(models.Model):
    _inherit = 'hr.afp'

    sv_spu_code = fields.Selection([
        ('COF', 'AFP Confía'), ('MAX', 'AFP Crecer'),
        ('ISP', 'Instituto Salvadoreño de Pensiones'), ('ISS', 'Unidad de Pensiones del ISSS'),
    ], string='Institución previsional SPU')


class HrPayrollPaymentEvent(models.Model):
    _name = 'hr.payroll.payment.event'
    _description = 'Movimiento de pago de planilla'
    _order = 'payment_date desc, id desc'

    payroll_id = fields.Many2one('hr.payroll', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='payroll_id.company_id', store=True)
    currency_id = fields.Many2one(related='payroll_id.currency_id')
    payment_date = fields.Date(string='Fecha del movimiento', required=True)
    state = fields.Selection([('paid', 'Aplicado'), ('rejected', 'Rechazado')], required=True, string='Resultado')
    amount = fields.Monetary(string='Importe', required=True)
    reference = fields.Char(string='Referencia', required=True)
    normalized_reference = fields.Char(required=True)
    bank_line_id = fields.Many2one('account.bank.statement.line', string='Transacción bancaria', ondelete='restrict')
    move_id = fields.Many2one(related='bank_line_id.move_id', string='Asiento contable', groups='account.group_account_readonly')
    proof = fields.Binary(string='Comprobante', attachment=True, required=True)
    proof_name = fields.Char()
    rejection_reason = fields.Char(string='Motivo del rechazo')
    recorded_by = fields.Many2one('res.users', string='Registrado por', required=True)

    _sql_constraints = [
        ('unique_bank_line', 'unique(bank_line_id)', 'Esta transacción bancaria ya fue aplicada a una planilla.'),
        ('unique_reference', 'unique(payroll_id, state, normalized_reference)', 'Ya se registró este movimiento en la planilla.'),
        ('positive_amount', 'check(amount > 0)', 'El importe registrado debe ser positivo.'),
    ]
