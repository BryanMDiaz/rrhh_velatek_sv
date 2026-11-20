from odoo import models, fields

from .hr_sv_minimum_wage import SECTORS

class ResCompany(models.Model):
    _inherit = 'res.company'

    velatek_legal_representative_id = fields.Many2one('res.partner', string='Representante Legal')

    velatek_work_week_start = fields.Selection([
        ('0', 'Lunes'),
        ('1', 'Martes'),
        ('2', 'Miércoles'),
        ('3', 'Jueves'),
        ('4', 'Viernes'),
        ('5', 'Sábado'),
        ('6', 'Domingo'),
    ], string='Inicio de la semana laboral', default='0', required=True,
        help='Se utiliza para validar el plazo del descanso compensatorio.')

    velatek_minimum_wage_sector = fields.Selection(SECTORS, string='Sector económico predeterminado')
    velatek_minimum_wage_from = fields.Date(
        string='Validar salario mínimo desde',
        help='Active el control desde una fecha luego de clasificar los sectores de la empresa y sus centros.',
    )


class HrWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    velatek_minimum_wage_sector = fields.Selection(
        SECTORS, string='Sector económico del centro',
        help='Si se deja vacío, se utiliza el sector predeterminado de la compañía.',
    )
