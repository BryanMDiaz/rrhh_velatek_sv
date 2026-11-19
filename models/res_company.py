from odoo import models, fields

class ResCompany(models.Model):
    _inherit = 'res.company'

    velatek_legal_representative_id = fields.Many2one('res.partner', string='Representante Legal')
