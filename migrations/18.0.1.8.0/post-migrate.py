from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    env['hr.employee'].search([])._sync_employee_file_status()
