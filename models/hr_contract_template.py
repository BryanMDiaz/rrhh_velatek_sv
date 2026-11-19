from odoo import models, fields

class HrContractTemplate(models.Model):
    _name = 'hr.contract.template'
    _description = 'Plantilla de Contrato'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True)
    contract_type_id = fields.Many2one('hr.contract.type', string='Tipo de contrato')
    version = fields.Integer(string='Versión', default=1)
    active = fields.Boolean(string='Activo', default=True)
    description = fields.Text(string='Descripción')
    body_html = fields.Html(string='Contenido', sanitize=False)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    is_default = fields.Boolean(string='Por defecto para el tipo de contrato')
    notes = fields.Text(string='Notas internas')

    _sql_constraints = [
        (
            'name_company_uniq',
            'unique(name, company_id, version)',
            'El nombre y la versión de la plantilla deben ser únicos por compañía.',
        )
    ]

    def action_preview_template(self):
        self.ensure_one()
        return {
            'name': 'Vista previa de la plantilla',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.contract.generate.wizard',
            'views': [(self.env.ref('rrhh_velatek_sv.hr_contract_generate_wizard_view_form').id, 'form')],
            'target': 'new',
            'context': {
                'contract_preview_only': True,
                'default_template_id': self.id,
                'preview_company_id': (self.company_id or self.env.company).id,
            },
        }
