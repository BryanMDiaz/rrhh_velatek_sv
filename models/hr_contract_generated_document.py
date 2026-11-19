from odoo import models, fields
from odoo.exceptions import ValidationError

class HrContractGeneratedDocument(models.Model):
    _name = 'hr.contract.generated.document'
    _description = 'Documento de Contrato Generado'
    _order = 'generated_date desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='Empleado', related='contract_id.employee_id', store=True)
    template_id = fields.Many2one('hr.contract.template', string='Plantilla Utilizada')
    template_version = fields.Integer(
        string='Versión de Plantilla',
        readonly=True,
        copy=False,
    )
    generated_date = fields.Datetime(string='Fecha de Generación', default=fields.Datetime.now)
    generated_by_id = fields.Many2one('res.users', string='Generado por', default=lambda self: self.env.user)
    signature_date = fields.Date(string='Fecha de Firma')
    content_snapshot = fields.Html(string='Contenido Generado (Snapshot)', readonly=True, sanitize=False)
    pdf_attachment_id = fields.Many2one('ir.attachment', string='Archivo PDF')
    
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('generated', 'Generado'),
        ('pending_signature', 'Pendiente de firma'),
        ('signed', 'Firmado'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='generated')

    signed_attachment_ids = fields.Many2many('ir.attachment', string='Contrato Firmado (Escaneado)')
    notes = fields.Text(string='Notas')

    def action_mark_signed(self):
        for doc in self:
            if not doc.signed_attachment_ids:
                raise ValidationError(
                    "Debe subir el contrato firmado antes de marcarlo como firmado."
                )
            doc.state = 'signed'
            doc.contract_id.state = 'open'
            if doc.employee_id:
                # Find the document type for contracts, or just create it without type if not strictly required
                doc_type = self.env['hr.employee.document.type'].search([('name', '=', 'Contrato Firmado')], limit=1)
                if not doc_type:
                    doc_type = self.env['hr.employee.document.type'].create({'name': 'Contrato Firmado'})
                
                for attachment in doc.signed_attachment_ids:
                    self.env['hr.employee.document'].create({
                        'employee_id': doc.employee_id.id,
                        'document_type_id': doc_type.id,
                        'name': f"Contrato Firmado - {doc.contract_id.name}",
                        'attachment_id': attachment.id,
                    })
                
    def action_cancel(self):
        for doc in self:
            doc.state = 'cancelled'
