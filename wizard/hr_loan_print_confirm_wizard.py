from odoo import fields, models


class HrLoanPrintConfirmWizard(models.TransientModel):
    _name = 'hr.loan.print.confirm.wizard'
    _description = 'Confirmación de impresión de contrato de préstamo'

    loan_id = fields.Many2one('hr.employee.loan', required=True, readonly=True)
    message = fields.Text(readonly=True)

    def action_confirm(self):
        self.ensure_one()
        return self.loan_id._print_contract_report()
