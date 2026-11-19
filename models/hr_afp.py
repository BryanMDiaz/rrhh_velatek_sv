from odoo import models, fields

class HrAfp(models.Model):
    _name = 'hr.afp'
    _description = 'Administradora de Fondos de Pensiones'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código')
    active = fields.Boolean(string='Activa', default=True)
