from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Refresh derived loan balances/dates, without recalculating payrolls."""
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    loans = env['hr.employee.loan'].search([])
    loans._compute_balances()
    loans._compute_estimated_end_date()
