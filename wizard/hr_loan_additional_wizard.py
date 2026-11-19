from odoo import models, fields, api

class HrLoanAdditionalWizard(models.TransientModel):
    _name = 'hr.loan.additional.wizard'
    _description = 'Autorización de Préstamo Adicional'

    loan_id = fields.Many2one('hr.employee.loan', string='Préstamo Nuevo')
    active_loan_id = fields.Many2one('hr.employee.loan', string='Préstamo Activo Existente')

    def action_confirm(self):
        self.ensure_one()
        # Returns the action_submit method call but with a context flag
        return self.loan_id.with_context(force_submit_additional_loan=True).action_submit()

    def action_cancel_and_delete(self):
        self.ensure_one()
        # Delete the draft loan if it's still in draft
        if self.loan_id and self.loan_id.state == 'draft':
            self.loan_id.unlink()
            
        # Redirect to the tree view
        action = self.env.ref('rrhh_velatek_sv.action_hr_employee_loan').read()[0]
        action['target'] = 'main'
        return action

