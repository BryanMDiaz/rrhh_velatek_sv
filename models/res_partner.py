from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    velatek_dui = fields.Char(string='DUI')
    velatek_profession = fields.Char(string='Profesión u Oficio')
    velatek_birth_date = fields.Date(string='Fecha de nacimiento')
