from odoo import models, fields, api

class HrEmployeeLoanInstallment(models.Model):
    _name = 'hr.employee.loan.installment'
    _description = 'Cuota de Préstamo'
    _order = 'scheduled_date, sequence'

    loan_id = fields.Many2one('hr.employee.loan', string='Préstamo', required=True, ondelete='cascade')
    sequence = fields.Integer(string='No.', required=True)

    scheduled_date = fields.Date(string='Fecha Programada', required=True)
    
    payroll_period_start = fields.Date(string='Inicio de Período')
    payroll_period_end = fields.Date(string='Fin de Período')

    amount = fields.Monetary(string='Monto de Cuota', required=True)
    paid_amount = fields.Monetary(string='Monto Pagado', default=0.0)
    remaining_amount = fields.Monetary(string='Saldo de Cuota', compute='_compute_remaining_amount', store=True)

    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('scheduled', 'En Preplanilla'),
        ('deducted', 'Deducido'),
        ('paid', 'Pagado'),
        ('skipped', 'Omitido'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='pending', required=True)

    payroll_id = fields.Many2one('hr.payroll', string='Planilla Relacionada', ondelete='set null')
    payroll_line_id = fields.Many2one('hr.payroll.line', string='Línea de Planilla', ondelete='set null')
    
    payment_origin = fields.Char(string='Planilla', compute='_compute_payment_origin')
    
    @api.depends('payroll_id', 'state', 'paid_amount', 'notes')
    def _compute_payment_origin(self):
        for rec in self:
            if rec.payroll_id:
                rec.payment_origin = rec.payroll_id.name
            elif rec.paid_amount > 0 and not rec.payroll_id:
                rec.payment_origin = "Abono Directo"
            else:
                rec.payment_origin = ""
    
    payment_date = fields.Date(string='Fecha de Pago')
    notes = fields.Text(string='Notas')

    currency_id = fields.Many2one('res.currency', related='loan_id.currency_id')

    @api.depends('amount', 'paid_amount')
    def _compute_remaining_amount(self):
        for inst in self:
            inst.remaining_amount = inst.amount - inst.paid_amount
