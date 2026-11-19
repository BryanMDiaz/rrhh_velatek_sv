from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class HrPrepayrollIncidentAllocation(models.Model):
    _name = 'hr.prepayroll.incident.allocation'
    _description = 'Asignación Parcial de Incidencia a Preplanilla'
    _order = 'date_from, id'

    prepayroll_id = fields.Many2one(
        'hr.prepayroll', string='Preplanilla', ondelete='cascade',
        required=True, check_company=True, index=True,
    )
    prepayroll_line_id = fields.Many2one(
        'hr.prepayroll.line', string='Línea de Preplanilla', ondelete='cascade',
        check_company=True, index=True,
    )
    incident_id = fields.Many2one(
        'hr.employee.incident', string='Incidencia Origen', ondelete='cascade',
        required=True, check_company=True, index=True,
    )

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', related='incident_id.employee_id',
        store=True, readonly=True, index=True,
    )
    contract_id = fields.Many2one(
        'hr.contract', string='Contrato', related='incident_id.contract_id',
        store=True, readonly=True, index=True,
    )

    date_from = fields.Date(string='Fecha Desde', required=True)
    date_to = fields.Date(string='Fecha Hasta', required=True)

    allocated_days = fields.Float(string='Días Asignados', default=0.0)
    allocated_hours = fields.Float(string='Horas Asignadas', default=0.0)
    allocated_amount = fields.Monetary(string='Monto Asignado', default=0.0)

    company_id = fields.Many2one(
        'res.company', string='Compañía', related='prepayroll_id.company_id',
        store=True, readonly=True,
    )
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection(
        related='prepayroll_id.state', string='Estado Preplanilla',
        store=True, readonly=True,
    )

    def _check_editable(self, prepayrolls=None):
        prepayrolls = prepayrolls if prepayrolls is not None else self.mapped('prepayroll_id')
        if prepayrolls.filtered(lambda prep: prep.state in ('validated', 'closed')):
            raise ValidationError(_('No puede modificar ni eliminar asignaciones de una preplanilla validada o cerrada.'))

    @api.model_create_multi
    def create(self, values_list):
        ids = {values.get('prepayroll_id') or self.env.context.get('default_prepayroll_id') for values in values_list}
        self._check_editable(self.env['hr.prepayroll'].browse([pid for pid in ids if pid]))
        return super().create(values_list)

    def write(self, values):
        self._check_editable()
        if values.get('prepayroll_id'):
            self._check_editable(self.env['hr.prepayroll'].browse(values['prepayroll_id']))
        return super().write(values)

    def unlink(self):
        self._check_editable()
        return super().unlink()

    _sql_constraints = [
        ('unique_prepayroll_incident_alloc_range',
         'unique(prepayroll_id, incident_id, date_from, date_to)',
         'Ya existe una asignación para esta incidencia y período de intersección en la preplanilla.')
    ]
