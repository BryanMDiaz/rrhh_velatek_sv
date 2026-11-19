from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import timedelta

class HrEmployeeDocumentType(models.Model):
    _name = 'hr.employee.document.type'
    _description = 'Tipo de Documento de Empleado'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Código')
    category = fields.Selection([
        ('personal', 'Personal'),
        ('labor', 'Laboral'),
        ('other', 'Otro')
    ], string='Categoría', default='other', required=True)
    requires_expiration = fields.Boolean(string='Requiere Fecha de Vencimiento (Legacy)', default=False, help='Campo deprecado. Use requires_expiration_date.')
    requires_number = fields.Boolean(string='Requiere número', default=False)
    requires_issue_date = fields.Boolean(string='Requiere fecha de emisión', default=False)
    requires_expiration_date = fields.Boolean(string='Requiere vencimiento', default=False)
    requires_attachment = fields.Boolean(string='Requiere archivo', default=False)
    is_required_for_employee_file = fields.Boolean(string='Obligatorio para expediente', default=False)
    active = fields.Boolean(string='Activo', default=True)

    def init(self):
        super().init()
        # Sincronizar datos entre legacy y canónico para no perder información
        self.env.cr.execute("""
            UPDATE hr_employee_document_type 
            SET requires_expiration_date = requires_expiration 
            WHERE requires_expiration = true AND (requires_expiration_date = false OR requires_expiration_date IS NULL)
        """)
        self.env.cr.execute("""
            UPDATE hr_employee_document_type 
            SET requires_expiration = requires_expiration_date 
            WHERE requires_expiration_date = true AND (requires_expiration = false OR requires_expiration IS NULL)
        """)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'requires_expiration' in vals and 'requires_expiration_date' not in vals:
                vals['requires_expiration_date'] = vals['requires_expiration']
            elif 'requires_expiration_date' in vals:
                vals['requires_expiration'] = vals['requires_expiration_date']
        return super().create(vals_list)

    def write(self, vals):
        if 'requires_expiration' in vals and 'requires_expiration_date' not in vals:
            vals['requires_expiration_date'] = vals['requires_expiration']
        elif 'requires_expiration_date' in vals:
            vals['requires_expiration'] = vals['requires_expiration_date']
        return super().write(vals)


class HrEmployeeDocument(models.Model):
    _name = 'hr.employee.document'
    _description = 'Documento de Empleado'

    name = fields.Char(string='Descripción', required=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, ondelete='cascade')
    document_type_id = fields.Many2one('hr.employee.document.type', string='Tipo de Documento', required=True)
    document_number = fields.Char(string='Número de documento')
    issuing_entity = fields.Char(string='Entidad emisora')
    attachment_file = fields.Binary(string='Archivo', attachment=True, required=False) # Will be validated if required
    attachment_filename = fields.Char(string='Nombre del Archivo')
    attachment_id = fields.Many2one('ir.attachment', string='Archivo Adjunto')
    issue_date = fields.Date(string='Fecha de Emisión')
    expiration_date = fields.Date(string='Fecha de Vencimiento')
    
    state = fields.Selection([
        ('no_expiration', 'Sin vencimiento'),
        ('valid', 'Vigente'),
        ('expiring', 'Próximo a vencer'),
        ('expired', 'Vencido')
    ], string='Estado', compute='_compute_state', store=False)
    
    verified = fields.Boolean(string='Verificado', default=False)
    verified_by_id = fields.Many2one('res.users', string='Verificado por', readonly=True, copy=False)
    verified_date = fields.Datetime(string='Fecha de verificación', readonly=True, copy=False)
    
    notes = fields.Text(string='Notas')
    active = fields.Boolean(string='Activo', default=True)

    @api.depends('expiration_date')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for doc in self:
            if not doc.expiration_date:
                doc.state = 'no_expiration'
            elif doc.expiration_date < today:
                doc.state = 'expired'
            elif doc.expiration_date <= today + timedelta(days=30):
                doc.state = 'expiring'
            else:
                doc.state = 'valid'

    @api.constrains('issue_date', 'expiration_date')
    def _check_dates(self):
        for doc in self:
            if doc.issue_date and doc.expiration_date and doc.expiration_date < doc.issue_date:
                raise ValidationError("La fecha de vencimiento no puede ser anterior a la fecha de emisión.")

    @api.constrains('document_type_id', 'document_number', 'issue_date', 'expiration_date', 'attachment_file', 'attachment_id')
    def _check_requirements(self):
        for doc in self:
            if not doc.document_type_id:
                continue
            if doc.document_type_id.requires_number and not doc.document_number:
                raise ValidationError(f"El tipo de documento '{doc.document_type_id.name}' requiere un Número de documento.")
            if doc.document_type_id.requires_issue_date and not doc.issue_date:
                raise ValidationError(f"El tipo de documento '{doc.document_type_id.name}' requiere una Fecha de Emisión.")
            if doc.document_type_id.requires_expiration_date and not doc.expiration_date:
                raise ValidationError(f"El tipo de documento '{doc.document_type_id.name}' requiere una Fecha de Vencimiento.")
            if doc.document_type_id.requires_attachment and not doc.attachment_file and not doc.attachment_id:
                raise ValidationError(f"El tipo de documento '{doc.document_type_id.name}' requiere un Archivo adjunto.")

    def write(self, vals):
        if 'verified' in vals:
            if vals['verified']:
                vals['verified_by_id'] = self.env.uid
                vals['verified_date'] = fields.Datetime.now()
            else:
                vals['verified_by_id'] = False
                vals['verified_date'] = False
        return super().write(vals)

    @api.onchange('verified')
    def _onchange_verified(self):
        if self.verified:
            self.verified_by_id = self.env.uid
            self.verified_date = fields.Datetime.now()
        else:
            self.verified_by_id = False
            self.verified_date = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('verified'):
                vals['verified_by_id'] = self.env.uid
                vals['verified_date'] = fields.Datetime.now()
        return super().create(vals_list)

