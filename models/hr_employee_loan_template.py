from odoo import models, fields

class HrEmployeeLoanTemplate(models.Model):
    _name = 'hr.employee.loan.template'
    _description = 'Plantilla de Préstamo'
    _order = 'name'

    name = fields.Char(string='Nombre de la Plantilla', required=True)
    active = fields.Boolean(string='Activo', default=True)
    loan_type_id = fields.Many2one('hr.employee.loan.type', string='Tipo de Préstamo')
    is_default = fields.Boolean(string='Por defecto para el tipo de préstamo')
    version = fields.Integer(string='Versión', default=1)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    notes = fields.Text(string='Descripción y Notas')
    body_html = fields.Html(
        string='Contenido', 
        sanitize=False, 
        help="Puede usar variables como: {{empresa_nombre}}, {{empleado_nombre}}, {{empleado_documento}}, {{prestamo_referencia}}, {{prestamo_monto}}, {{prestamo_intereses}}, {{prestamo_total}}, {{numero_cuotas}}, {{cuota_inicial}}, {{frecuencia_descuento}}, {{fecha_primer_descuento}}, {{tabla_amortizacion}}, {{representante_empresa}}, etc."
    )

    def action_preview_template(self):
        self.ensure_one()
        # Buscar el préstamo más reciente o el primero disponible para precargarlo
        loan = self.env['hr.employee.loan'].search([
            ('company_id', '=', self.company_id.id)
        ], order='id desc', limit=1)
        
        emp = loan.employee_id if loan else self.env['hr.employee'].search([
            ('company_id', '=', self.company_id.id),
            ('contract_ids.state', '=', 'open')
        ], limit=1)
        
        contract = loan.contract_id if loan else (emp.contract_id if emp else False)

        wizard = self.env['hr.loan.template.preview.wizard'].create({
            'template_id': self.id,
            'employee_id': emp.id if emp else False,
            'contract_id': contract.id if contract else False,
            'loan_id': loan.id if loan else False,
        })
        wizard._render_preview()

        return {
            'name': f'Vista Previa: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.loan.template.preview.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }
