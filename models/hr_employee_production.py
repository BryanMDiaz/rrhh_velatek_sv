from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .operational_security import (
    USER, RESPONSIBLE, MANAGER, require_group, check_create, check_write, check_edit,
)

class HrEmployeeProduction(models.Model):
    _name = 'hr.employee.production'
    _check_company_auto = True
    _description = 'Producción del Empleado'
    _order = 'date desc'

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, check_company=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, check_company=True)
    project_id = fields.Many2one('project.project', string='Proyecto', check_company=True)
    
    date = fields.Date(string='Fecha', default=fields.Date.context_today, required=True)
    
    quantity = fields.Float(string='Cantidad', required=True, default=1.0)
    unit = fields.Selection([
        ('sqm', 'Metro cuadrado (m²)'),
        ('linear_meter', 'Metro lineal'),
        ('unit', 'Unidad'),
        ('day', 'Día'),
        ('other', 'Otro')
    ], string='Unidad', required=True)
    
    rate = fields.Monetary(string='Tarifa', required=True)
    amount = fields.Monetary(string='Importe', compute='_compute_amount', store=True)
    
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('to_approve', 'Por aprobar'),
        ('approved', 'Aprobado'),
        ('processed', 'Procesado'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='draft', required=True)
    
    notes = fields.Text(string='Notas')
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    
    processed_prepayroll_id = fields.Many2one(
        'hr.prepayroll',
        string='Preplanilla procesada',
        copy=False,
        readonly=True,
        index=True,
        check_company=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        check_create(self, vals_list)
        for vals in vals_list:
            if vals.get('state') and vals['state'] != 'draft':
                raise ValidationError(_("Los nuevos registros de producción deben crearse en estado Borrador."))
        return super().create(vals_list)

    def write(self, vals):
        check_write(self, vals)
        if 'state' in vals:
            target_state = vals['state']
            for record in self:
                if record.state == target_state:
                    continue
                if record.state == 'processed':
                    raise ValidationError(_("El registro está Procesado y es inmutable. No permite cambios de estado."))
                if target_state == 'to_approve':
                    if record.state != 'draft':
                        raise ValidationError(_("Transición a 'Por Aprobar' solo permitida desde 'Borrador'."))
                elif target_state == 'approved':
                    if record.state != 'to_approve':
                        raise ValidationError(_("No se puede aprobar directamente un registro de producción desde el estado '%s'. Debe estar en 'Por Aprobar'." % record.state))
                elif target_state == 'processed':
                    if record.state != 'approved':
                        raise ValidationError(_("Solo se pueden procesar registros de producción en estado Aprobado."))
                    if not vals.get('processed_prepayroll_id') and not record.processed_prepayroll_id:
                        raise ValidationError(_("Un registro de producción solo se puede marcar como procesado mediante el cierre formal de preplanilla."))
                elif target_state == 'draft':
                    if record.state not in ('to_approve', 'approved', 'cancelled'):
                        raise ValidationError(_("Transición a 'Borrador' no permitida desde el estado '%s'." % record.state))
                elif target_state == 'cancelled':
                    if record.state not in ('draft', 'to_approve', 'approved'):
                        raise ValidationError(_("Transición a 'Cancelado' no permitida desde el estado '%s'." % record.state))
                else:
                    raise ValidationError(_("Estado '%s' no es válido." % target_state))
        return super().write(vals)

    def unlink(self):
        check_edit(self)
        return super().unlink()

    @api.depends('quantity', 'rate')
    def _compute_amount(self):
        for record in self:
            record.amount = record.quantity * record.rate

    @api.constrains('quantity')
    def _check_quantity(self):
        for record in self:
            if record.quantity <= 0:
                raise ValidationError("La cantidad producida debe ser mayor que cero.")

    @api.constrains('rate')
    def _check_rate(self):
        for record in self:
            if record.rate < 0:
                raise ValidationError("La tarifa no puede ser negativa.")

    @api.constrains('employee_id', 'contract_id')
    def _check_contract_employee(self):
        for record in self:
            if record.contract_id and record.employee_id:
                if record.contract_id.employee_id != record.employee_id:
                    raise ValidationError(
                        "El contrato seleccionado no pertenece al empleado '%s'." % record.employee_id.name
                    )

    @api.constrains('company_id', 'contract_id')
    def _check_contract_company(self):
        for record in self:
            if record.contract_id and record.company_id:
                if record.contract_id.company_id != record.company_id:
                    raise ValidationError(
                        "La compañía del contrato no coincide con la compañía del registro de producción."
                    )

    @api.constrains('date', 'contract_id')
    def _check_date_within_contract(self):
        for record in self:
            if record.date and record.contract_id:
                contract = record.contract_id
                if contract.date_start and record.date < contract.date_start:
                    raise ValidationError(
                        "La fecha de producción (%s) está fuera de la vigencia del contrato (inicio: %s)."
                        % (record.date, contract.date_start)
                    )
                if contract.date_end and record.date > contract.date_end:
                    raise ValidationError(
                        "La fecha de producción (%s) está fuera de la vigencia del contrato (fin: %s)."
                        % (record.date, contract.date_end)
                    )

    @api.constrains('contract_id', 'unit')
    def _check_production_mode(self):
        for record in self:
            if record.contract_id:
                mode = record.contract_id.velatek_remuneration_mode
                if mode not in ('production', 'mixed'):
                    raise ValidationError(
                        "El contrato del empleado '%s' no está configurado en modalidad de producción o mixta."
                        % record.employee_id.name
                    )
                if not record.unit:
                    raise ValidationError(
                        "Debe especificar la unidad de producción para el empleado '%s'."
                        % record.employee_id.name
                    )

    @api.onchange('employee_id', 'date')
    def _onchange_employee_id_date(self):
        if not self.employee_id:
            return

        ref_date = self.date or fields.Date.context_today(self)
        domain = [
            ('employee_id', '=', self.employee_id.id),
            ('company_id', '=', (self.company_id or self.env.company).id),
            ('state', '=', 'open'),
            ('date_start', '<=', ref_date),
            '|', ('date_end', '=', False), ('date_end', '>=', ref_date),
        ]
        contracts = self.env['hr.contract'].search(domain)

        if len(contracts) == 1:
            self.contract_id = contracts
        elif len(contracts) > 1:
            self.contract_id = False
            return {
                'warning': {
                    'title': 'Múltiples contratos válidos',
                    'message': (
                        'El empleado "%s" tiene más de un contrato vigente para la fecha seleccionada.\n'
                        'Por favor seleccione el contrato correcto manualmente.'
                        % self.employee_id.name
                    ),
                }
            }

    @api.onchange('contract_id')
    def _onchange_contract_id(self):
        if self.contract_id:
            self.rate = self.contract_id.velatek_production_rate
            self.unit = self.contract_id.velatek_production_unit

    def action_submit(self):
        require_group(self, USER)
        for record in self:
            if record.state != 'draft':
                raise ValidationError(_("Solo se pueden enviar a aprobación registros de producción en estado Borrador."))
        self.write({'state': 'to_approve'})
        
    def action_approve(self):
        require_group(self, RESPONSIBLE)
        for record in self:
            if record.state != 'to_approve':
                raise ValidationError(_("Solo se pueden aprobar registros de producción en estado Por Aprobar."))
            if not record.employee_id:
                raise ValidationError(_("No se puede aprobar: falta el empleado."))
            if not record.contract_id:
                raise ValidationError(_("No se puede aprobar: falta el contrato."))
            if not record.date:
                raise ValidationError(_("No se puede aprobar: falta la fecha."))
            if record.quantity <= 0:
                raise ValidationError(_("No se puede aprobar: la cantidad producida debe ser mayor que cero."))
        self.write({'state': 'approved'})
        
    def action_draft(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'processed':
                raise ValidationError(_("Un registro en estado Procesado es inmutable y no puede volver a Borrador."))
            if record.state not in ('to_approve', 'approved', 'cancelled'):
                raise ValidationError(_("Transición a Borrador no permitida desde el estado '%s'." % record.state))
        self.write({'state': 'draft'})
        
    def action_cancel(self):
        require_group(self, MANAGER)
        for record in self:
            if record.state == 'processed':
                raise ValidationError(_("Un registro en estado Procesado es inmutable y no puede cancelarse."))
            if record.state not in ('draft', 'to_approve', 'approved'):
                raise ValidationError(_("Transición a Cancelado no permitida desde el estado '%s'." % record.state))
        self.write({'state': 'cancelled'})


