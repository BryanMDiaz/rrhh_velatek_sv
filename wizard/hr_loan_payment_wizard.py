from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class HrLoanPaymentWizard(models.TransientModel):
    _name = 'hr.loan.payment.wizard'
    _description = 'Wizard de Abono a Préstamo'

    loan_id = fields.Many2one('hr.employee.loan', string='Préstamo', required=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    amount = fields.Monetary(string='Monto', required=True)
    currency_id = fields.Many2one('res.currency', related='loan_id.currency_id')
    payment_method = fields.Char(string='Método de Pago')
    reference = fields.Char(string='Referencia')
    notes = fields.Text(string='Notas')
    
    is_partial_warning = fields.Boolean(default=False)
    suggested_amount = fields.Monetary(string='Cuota Sugerida', compute='_compute_suggested_amount')

    @api.depends('loan_id')
    def _compute_suggested_amount(self):
        for rec in self:
            pending = rec.loan_id.installment_ids.filtered(lambda i: i.state in ('pending', 'scheduled')).sorted('scheduled_date')
            rec.suggested_amount = pending[0].remaining_amount if pending else 0.0

    def action_register_payment(self):
        self.ensure_one()
        loan = self.loan_id
        if loan.state != 'active' or loan.advance_prepayroll_id:
            raise ValidationError(_('Solo puede registrar abonos de préstamos activos; los anticipos salariales se concilian en su planilla.'))
        # Share the employee lock with payroll validation/closing, then lock
        # this loan so cash payments cannot race payroll deductions.
        loan._lock_advance_employee()
        self.env.cr.execute('UPDATE hr_employee_loan SET write_date = write_date WHERE id = %s', [loan.id])
        loan.invalidate_recordset()
        loan.installment_ids.invalidate_recordset()
        currency = loan.currency_id
        amount = currency.round(self.amount)
        if currency.compare_amounts(amount, 0) <= 0:
            raise ValidationError(_('El monto del abono debe ser mayor a cero.'))
        if currency.compare_amounts(amount, loan.remaining_balance) > 0:
            raise ValidationError(_('No puede abonar más del saldo pendiente.'))

        reserved = self.env['hr.payroll.line'].search([
            ('loan_installment_ids', 'in', loan.installment_ids.filtered(lambda inst: inst.state in ('pending', 'scheduled')).ids),
            ('payroll_id.state', '=', 'validated'),
        ], limit=1)
        if reserved:
            raise ValidationError(_('La planilla %s ya está validada con cuotas de este préstamo. Vuelva a borrador antes de registrar el abono y recalcule el descuento.') % reserved.payroll_id.name)

        amount_to_distribute = amount
        pending_installments = self.loan_id.installment_ids.filtered(lambda i: i.state in ('pending', 'scheduled')).sorted('scheduled_date')
        
        if pending_installments and currency.compare_amounts(amount, pending_installments[0].remaining_amount) < 0 and not self.is_partial_warning:
            self.is_partial_warning = True
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'hr.loan.payment.wizard',
                'view_mode': 'form',
                'res_id': self.id,
                'target': 'new',
            }
        
        for inst in pending_installments:
            if amount_to_distribute <= 0:
                break
            
            payable = min(inst.remaining_amount, amount_to_distribute)
            if payable > 0:
                inst.paid_amount += payable
                amount_to_distribute = currency.round(amount_to_distribute - payable)
                inst.payment_date = self.date
                inst.notes = (inst.notes or '') + '\n[Abono directo %s del %s] Ref: %s; Método: %s; %s' % (payable, self.date, self.reference or '', self.payment_method or '', self.notes or '')

            if currency.is_zero(inst.remaining_amount):
                inst.state = 'paid'
                inst.payment_date = self.date
                inst.notes = (inst.notes or "") + f"\n[Pagado por adelantado] Ref: {self.reference or ''}"
                
        if not currency.is_zero(amount_to_distribute):
            raise ValidationError(_('El calendario no permite aplicar el abono completo. Revise las cuotas pendientes; no se registró el pago.'))
        loan._compute_balances()

        return {'type': 'ir.actions.act_window_close'}
