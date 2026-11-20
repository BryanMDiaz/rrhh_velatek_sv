from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

class HrContractGeneratedDocument(models.Model):
    _name = 'hr.contract.generated.document'
    _description = 'Documento de Contrato Generado'
    _order = 'generated_date desc, id desc'

    name = fields.Char(string='Nombre', required=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, ondelete='cascade')
    contract_company_id = fields.Many2one(related='contract_id.company_id')
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

    mtps_document_type = fields.Selection([
        ('original', 'Contrato inicial'),
        ('amendment', 'Modificación'),
        ('extension', 'Prórroga'),
    ], string='Tipo de presentación', default='original', required=True)
    mtps_status = fields.Selection([
        ('not_required', 'No aplica'),
        ('waiting_signature', 'Pendiente de firma'),
        ('pending', 'Pendiente de presentar'),
        ('submitted', 'Presentado, pendiente de acuse'),
        ('acknowledged', 'Acuse registrado'),
    ], string='Estado MTPS', compute='_compute_mtps_status', store=True)
    mtps_due_date = fields.Date(string='Fecha de seguimiento', compute='_compute_mtps_status', store=True)
    mtps_submitted_date = fields.Date(string='Fecha de presentación', copy=False)
    mtps_responsible_id = fields.Many2one('res.users', string='Responsable de presentación', copy=False)
    mtps_reference = fields.Char(string='Folio o referencia de presentación', copy=False)
    mtps_ack_date = fields.Date(string='Fecha del acuse', copy=False)
    mtps_ack_file = fields.Binary(string='Acuse del MTPS', attachment=True, copy=False)
    mtps_ack_filename = fields.Char(string='Nombre del archivo de acuse', copy=False)

    def _mtps_filing_required(self):
        self.ensure_one()
        professional = self.env.ref('rrhh_velatek_sv.contract_type_professional_services', raise_if_not_found=False)
        return self.contract_id.contract_type_id != professional

    @api.depends(
        'state', 'signature_date', 'contract_id.contract_type_id',
        'mtps_submitted_date', 'mtps_ack_date', 'mtps_ack_file',
    )
    def _compute_mtps_status(self):
        for doc in self:
            doc.mtps_due_date = False
            if doc.state == 'cancelled' or not doc._mtps_filing_required():
                doc.mtps_status = 'not_required'
            elif doc.state != 'signed' or not doc.signature_date:
                doc.mtps_status = 'waiting_signature'
            else:
                doc.mtps_due_date = doc.signature_date + timedelta(days=8)
                if doc.mtps_ack_date and doc.mtps_ack_file:
                    doc.mtps_status = 'acknowledged'
                elif doc.mtps_submitted_date:
                    doc.mtps_status = 'submitted'
                else:
                    doc.mtps_status = 'pending'

    @api.constrains(
        'state', 'signature_date', 'mtps_submitted_date', 'mtps_responsible_id',
        'mtps_reference', 'mtps_ack_date', 'mtps_ack_file', 'contract_id',
    )
    def _check_mtps_filing(self):
        for doc in self:
            if not (doc.mtps_submitted_date or doc.mtps_ack_date or doc.mtps_ack_file):
                continue
            if not (doc.state == 'signed' and doc.signature_date and doc._mtps_filing_required()):
                raise ValidationError(_('La presentación al MTPS requiere un contrato laboral firmado y su fecha de firma.'))
            if (not doc.mtps_submitted_date or not doc.mtps_responsible_id
                    or not (doc.mtps_reference or '').strip()):
                raise ValidationError(_('Ingrese la fecha, el responsable y la referencia de presentación.'))
            if doc.contract_id.company_id not in doc.mtps_responsible_id.company_ids:
                raise ValidationError(_('El responsable debe tener acceso a la compañía del contrato.'))
            if doc.mtps_submitted_date < doc.signature_date or doc.mtps_submitted_date > fields.Date.context_today(doc):
                raise ValidationError(_('La fecha de presentación debe estar entre la firma y el día de hoy.'))
            if doc.mtps_ack_date or doc.mtps_ack_file:
                if not doc.mtps_ack_date or not doc.mtps_ack_file:
                    raise ValidationError(_('Registre la fecha del acuse y suba el archivo correspondiente.'))
                if doc.mtps_ack_date < doc.mtps_submitted_date or doc.mtps_ack_date > fields.Date.context_today(doc):
                    raise ValidationError(_('La fecha del acuse debe estar entre la presentación y el día de hoy.'))

    def action_mark_signed(self):
        for doc in self:
            if doc.state == 'signed':
                continue
            if doc.state == 'cancelled':
                raise ValidationError(_('El documento está cancelado.'))
            if doc._mtps_filing_required() and (not doc.signature_date or doc.signature_date > fields.Date.context_today(doc)):
                raise ValidationError(_('Registre la fecha real de firma antes de marcar el documento como firmado.'))
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
