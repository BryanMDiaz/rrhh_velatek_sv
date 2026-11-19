from odoo import models, fields, api
from odoo.exceptions import ValidationError
import uuid

class HrContract(models.Model):
    _inherit = 'hr.contract'

    @api.model
    def default_get(self, fields_list):
        res = super(HrContract, self).default_get(fields_list)
        if 'name' in fields_list and not res.get('name'):
            res['name'] = f"CON-{uuid.uuid4().hex[:6].upper()}"
        return res

    velatek_payment_frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro')
    ], string='Frecuencia de pago', default='monthly')

    velatek_work_schedule_type = fields.Selection([
        ('full_time', 'Tiempo completo'),
        ('part_time', 'Medio tiempo'),
        ('partial', 'Jornada parcial'),
        ('other', 'Otra')
    ], string='Tipo de jornada', default='full_time')


    velatek_analytic_account_id = fields.Many2one('account.analytic.account', string='Centro de costo')
    velatek_work_location_id = fields.Many2one('hr.work.location', string='Ubicación de trabajo')
    velatek_contract_template_id = fields.Many2one('hr.contract.template', string='Plantilla de contrato', domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    velatek_generated_document_count = fields.Integer(compute='_compute_velatek_generated_document_count', string='Documentos generados')

    @api.onchange('employee_id')
    def _onchange_employee_id_velatek(self):
        if self.employee_id:
            if self.employee_id.department_id:
                self.department_id = self.employee_id.department_id
            if self.employee_id.job_id:
                self.job_id = self.employee_id.job_id
            if self.employee_id.velatek_company_entry_date:
                self.date_start = self.employee_id.velatek_company_entry_date

            if self.employee_id.velatek_employment_type == 'professional_services':
                self.contract_type_id = self.env.ref('rrhh_velatek_sv.contract_type_professional_services')
                self._onchange_contract_type_id_template()
                return
            
            # Mapear Tipo de Empleado (velatek_employment_type) a Tipo de Contrato (hr.contract.type)
            if self.employee_id.velatek_employment_type:
                mapping = {
                    'permanent': 'Permanente',
                    'temporary': 'Temporal',
                    'eventual': 'Eventual',
                    'intern': 'Pasante',
                }
                type_name = mapping.get(self.employee_id.velatek_employment_type)
                if type_name:
                    contract_type = self.env['hr.contract.type'].search([('name', 'ilike', type_name)], limit=1)
                    if contract_type:
                        self.contract_type_id = contract_type.id
                        self._onchange_contract_type_id_template()

    @api.depends('employee_id')
    def _compute_velatek_generated_document_count(self):
        for contract in self:
            contract.velatek_generated_document_count = self.env['hr.contract.generated.document'].search_count([('contract_id', '=', contract.id)])

    velatek_remuneration_mode = fields.Selection([
        ('fixed', 'Salario fijo'),
        ('production', 'Por producción'),
        ('mixed', 'Mixto')
    ], string='Modalidad de remuneración', default='fixed')

    @api.onchange('contract_type_id')
    def _onchange_contract_type_id_template(self):
        self.velatek_contract_template_id = self._get_default_contract_template()

    def _get_default_contract_template(self):
        self.ensure_one()
        templates = self.env['hr.contract.template']
        if not self.contract_type_id:
            return templates
        domain = [
            ('contract_type_id', '=', self.contract_type_id.id),
            ('is_default', '=', True),
        ]
        if self.company_id:
            company_template = templates.search(domain + [('company_id', '=', self.company_id.id)], limit=1)
            if company_template:
                return company_template
        return templates.search(domain + [('company_id', '=', False)], limit=1)

    @api.onchange('velatek_contract_template_id', 'contract_type_id')
    def _onchange_template_contract_type_warning(self):
        if self.velatek_contract_template_id and self.contract_type_id:
            if self.velatek_contract_template_id.contract_type_id and self.velatek_contract_template_id.contract_type_id != self.contract_type_id:
                return {
                    'warning': {
                        'title': "Advertencia de Plantilla",
                        'message': "La plantilla seleccionada corresponde a un tipo de contrato diferente al seleccionado en el contrato."
                    }
                }

    velatek_production_unit = fields.Selection([
        ('sqm', 'Metro cuadrado (m²)'),
        ('linear_meter', 'Metro lineal'),
        ('unit', 'Unidad'),
        ('day', 'Día'),
        ('other', 'Otro')
    ], string='Unidad de producción')

    velatek_production_rate = fields.Monetary(
        string='Tarifa por unidad', 
        currency_field='currency_id',
        help="Tarifa de referencia por unidad de producción. La tarifa definitiva podrá variar según el proyecto o registro de producción."
    )

    @api.constrains('state', 'velatek_remuneration_mode', 'wage', 'velatek_production_unit', 'velatek_production_rate')
    def _check_contract_activation_requirements(self):
        for contract in self:
            if contract.wage < 0:
                raise ValidationError("El salario base no puede ser negativo.")
            
            if contract.state == 'open':
                if not contract.employee_id:
                    raise ValidationError("Debe seleccionar un empleado para activar el contrato.")
                if not contract.date_start:
                    raise ValidationError("Debe definir una fecha de inicio para activar el contrato.")
                if not contract.contract_type_id:
                    raise ValidationError("Debe seleccionar un tipo de contrato para activarlo.")
                if not contract.resource_calendar_id:
                    raise ValidationError("Debe seleccionar un horario de trabajo para activarlo.")
                if not contract.velatek_payment_frequency:
                    raise ValidationError("Debe seleccionar una frecuencia de pago para activar el contrato.")
                if not contract.velatek_work_schedule_type:
                    raise ValidationError("Debe seleccionar un tipo de jornada para activar el contrato.")
                
                # Reglas de Remuneración
                if contract.velatek_remuneration_mode in ['fixed', 'mixed']:
                    if contract.wage <= 0:
                        raise ValidationError("El salario base debe ser mayor que cero para contratos de salario fijo o mixto en curso.")
                
                if contract.velatek_remuneration_mode in ['production', 'mixed']:
                    if not contract.velatek_production_unit:
                        raise ValidationError("Debe definir una unidad de producción para la modalidad seleccionada.")
                    if contract.velatek_production_rate < 0:
                        raise ValidationError("La tarifa por unidad de producción no puede ser negativa.")

    @api.constrains('date_start', 'date_end')
    def _check_dates_velatek(self):
        for contract in self:
            if contract.date_start and contract.date_end and contract.date_end < contract.date_start:
                raise ValidationError("La fecha de finalización del contrato no puede ser anterior a la fecha de inicio.")

    def action_generate_contract(self):
        self.ensure_one()
        
        if not self.env.context.get('force_generate'):
            signed_docs = self.env['hr.contract.generated.document'].search_count([
                ('contract_id', '=', self.id),
                ('state', '=', 'signed')
            ])
            if signed_docs:
                return {
                    'name': 'Contrato ya firmado',
                    'type': 'ir.actions.act_window',
                    'view_mode': 'form',
                    'res_model': 'hr.contract.generate.confirm.wizard',
                    'target': 'new',
                    'context': {'default_contract_id': self.id}
                }
        
        return {
            'name': 'Generar Contrato',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'hr.contract.generate.wizard',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'default_template_id': (self.velatek_contract_template_id or self._get_default_contract_template()).id,
            }
        }

    def action_open_generated_documents(self):
        self.ensure_one()
        return {
            'name': 'Documentos Contractuales',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.contract.generated.document',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
