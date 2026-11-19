import math
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api

class HrLoanSimulateWizard(models.TransientModel):
    _name = 'hr.loan.simulate.wizard'
    _description = 'Simulador de Préstamo'

    principal_amount = fields.Monetary(string='Monto Original', required=True)
    interest_amount = fields.Monetary(string='Monto de Interés', default=0.0)
    total_financed = fields.Monetary(string='Total Financiado', compute='_compute_totals')
    
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    desired_installment = fields.Monetary(string='Cuota Deseada', required=True)
    
    payment_frequency = fields.Selection([
        ('biweekly', 'Cada quincena'),
        ('monthly', 'Mensual'),
        ('alternate_biweekly', 'Una quincena sí / una no'),
        ('custom_interval', 'Cada N períodos')
    ], string='Frecuencia de Cobro', required=True, default='biweekly')
    
    first_payment_date = fields.Date(string='Fecha Primer Cobro', required=True, default=fields.Date.context_today)
    
    estimated_installments = fields.Integer(string='Cuotas Estimadas', compute='_compute_totals')
    estimated_end_date = fields.Date(string='Fecha Estimada Final', compute='_compute_totals')
    
    @api.depends('principal_amount', 'interest_amount', 'desired_installment', 'payment_frequency', 'first_payment_date')
    def _compute_totals(self):
        for wiz in self:
            wiz.total_financed = wiz.principal_amount + wiz.interest_amount
            
            if wiz.desired_installment > 0 and wiz.total_financed > 0:
                wiz.estimated_installments = math.ceil(wiz.total_financed / wiz.desired_installment)
            else:
                wiz.estimated_installments = 0
                
            if not wiz.first_payment_date or not wiz.estimated_installments:
                wiz.estimated_end_date = False
                continue
                
            months_to_add = 0
            if wiz.payment_frequency == 'monthly':
                months_to_add = wiz.estimated_installments
            elif wiz.payment_frequency == 'biweekly':
                months_to_add = wiz.estimated_installments / 2.0
            elif wiz.payment_frequency == 'alternate_biweekly':
                months_to_add = wiz.estimated_installments
            elif wiz.payment_frequency == 'custom_interval':
                months_to_add = wiz.estimated_installments / 2.0
                
            wiz.estimated_end_date = wiz.first_payment_date + relativedelta(months=math.ceil(months_to_add))
