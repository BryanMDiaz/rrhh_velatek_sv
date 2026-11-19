from odoo import models, fields, api
from odoo.exceptions import ValidationError
import re

LEGACY_FILE_FIELDS = (
    'velatek_cv_file',
    'velatek_dui_file',
    'velatek_isss_afp_file',
    'velatek_contract_file',
    'velatek_warning_letter_file',
    'velatek_resignation_file',
)

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # DATOS PERSONALES
    velatek_codigo = fields.Char(string='Codigo', default=lambda self: self.env['ir.sequence'].next_by_code('hr.employee.velatek.codigo'), copy=False)
    
    _sql_constraints = [
        ('velatek_codigo_unique', 'unique(velatek_codigo)', '¡El código de empleado debe ser único e irrepetible!')
    ]
    
    certificate = fields.Selection(
        selection_add=[
            ('basico', 'Nivel básico'),
            ('bachiller', 'Bachiller'),
            ('tecnico', 'Técnico'),
            ('profesorado', 'Profesorado'),
            ('egresado', 'Egresado'),
            ('ingenieria', 'Ingeniería'),
            ('arquitectura', 'Arquitectura / Arquitecto'),
            ('medicina', 'Medicina / Médico'),
            ('en_curso', 'Estudios en curso'),
        ],
        ondelete={
            'basico': 'set null',
            'bachiller': 'set null',
            'tecnico': 'set null',
            'profesorado': 'set null',
            'egresado': 'set null',
            'ingenieria': 'set null',
            'arquitectura': 'set null',
            'medicina': 'set null',
            'en_curso': 'set null',
        }
    )
    velatek_nombre_pila = fields.Char(string='Nombre')
    velatek_primer_apellido = fields.Char(string='Primer Apellido')
    velatek_segundo_apellido = fields.Char(string='Segundo Apellido')
    velatek_apellido_casada = fields.Char(string='Apellido de casada')
    
    velatek_short_name = fields.Char(compute="_compute_velatek_short_name", string="Nombre Corto")

    @api.depends('velatek_nombre_pila', 'velatek_primer_apellido')
    def _compute_velatek_short_name(self):
        for rec in self:
            first_name = rec.velatek_nombre_pila.split(' ')[0] if rec.velatek_nombre_pila else ''
            first_surname = rec.velatek_primer_apellido.strip() if rec.velatek_primer_apellido else ''
            rec.velatek_short_name = f"{first_name} {first_surname}".strip()
    velatek_profesion = fields.Char(string='Profesion/Officio')
    velatek_nombre_afp = fields.Char(string='Nombre de la AFP (Legacy)', help='Campo deprecado. Usar el catálogo velatek_afp_id en su lugar.')
    velatek_cuenta_bancaria = fields.Char(string='Cuenta Bancaria (Legacy)', help='Campo deprecado. Usar el campo estándar bank_account_id (res.partner.bank) en su lugar.')

    # DATOS DE IDENTIFICACIÓN
    velatek_dui = fields.Char(string='DUI')
    velatek_is_homologated = fields.Boolean(string='Homologado con NIT', default=True)
    velatek_lugar_expedicion_dui = fields.Char(string='Lugar de expedición del DUI')
    velatek_fecha_expedicion_dui = fields.Date(string='Fecha de expedición del DUI')
    velatek_fecha_vencimiento_dui = fields.Date(string='Fecha de vencimiento del DUI')
    velatek_nit = fields.Char(string='NIT')
    velatek_nup = fields.Char(string='NUP')
    velatek_isss = fields.Char(string='ISSS')

    # INFORMACIÓN PREVISIONAL
    velatek_afp_id = fields.Many2one('hr.afp', string='AFP')
    velatek_previsional_state = fields.Selection([
        ('normal', 'Cotizante normal'),
        ('pensioner', 'Pensionado'),
        ('not_applicable', 'No aplica'),
        ('other', 'Otro')
    ], string='Estado previsional')

    # INFORMACIÓN BANCARIA
    velatek_payment_method = fields.Selection([
        ('transfer', 'Transferencia bancaria'),
        ('check', 'Cheque'),
        ('cash', 'Efectivo')
    ], string='Forma de pago')
    velatek_bank_id = fields.Many2one('res.bank', string='Nombre del Banco')
    velatek_bank_account_type = fields.Selection([
        ('savings', 'Ahorro'),
        ('checking', 'Corriente'),
        ('other', 'Otro')
    ], string='Tipo de cuenta bancaria')

    # INFORMACIÓN ADMINISTRATIVA
    velatek_company_entry_date = fields.Date(string='Fecha de ingreso a la empresa')
    velatek_employment_type = fields.Selection([
        ('permanent', 'Permanente'),
        ('temporary', 'Temporal'),
        ('eventual', 'Eventual'),
        ('intern', 'Pasante'),
        ('professional_services', 'Servicios Profesionales'),
        ('other', 'Otro')
    ], string='Tipo de empleado')
    velatek_analytic_account_id = fields.Many2one('account.analytic.account', string='Centro de costo')
    velatek_employee_file_status = fields.Selection([
        ('incomplete', 'Incompleto'),
        ('pending_documents', 'Pendiente de documentos'),
        ('complete', 'Completo'),
        ('validated', 'Validado')
    ], string='Estado del expediente', default='incomplete', readonly=True, copy=False)

    # CONTACTO DE EMERGENCIA
    velatek_emergency_relation = fields.Selection([
        ('father', 'Padre'),
        ('mother', 'Madre'),
        ('spouse', 'Cónyuge'),
        ('sibling', 'Hermano/a'),
        ('child', 'Hijo/a'),
        ('other', 'Otro')
    ], string='Parentesco de emergencia')

    # BENEFICIARIOS
    velatek_beneficiary_ids = fields.One2many(
        'hr.employee.beneficiary', 'employee_id', string='Beneficiarios'
    )

    # DOCUMENTACIÓN LEGAL
    velatek_document_ids = fields.One2many(
        'hr.employee.document', 'employee_id', string='Documentos'
    )
    # Binary fields kept for compatibility
    velatek_cv_file = fields.Binary(string='Currículum Vitae (Archivo)', attachment=True)
    velatek_cv_filename = fields.Char(string='Nombre del archivo CV')
    
    velatek_dui_file = fields.Binary(string='Copia de DUI (Archivo)', attachment=True)
    velatek_dui_filename = fields.Char(string='Nombre del archivo DUI')
    
    velatek_isss_afp_file = fields.Binary(string='Copia de AFP/ISSS (Archivo)', attachment=True)
    velatek_isss_afp_filename = fields.Char(string='Nombre del archivo AFP/ISSS')
    
    velatek_contract_file = fields.Binary(string='Contrato Firmado (Archivo)', attachment=True)
    velatek_contract_filename = fields.Char(string='Nombre del archivo Contrato')
    
    velatek_resignation_file = fields.Binary(string='Carta de Renuncia/Despido (Archivo)', attachment=True)
    velatek_resignation_filename = fields.Char(string='Nombre del archivo Renuncia/Despido')
    
    velatek_warning_letter_file = fields.Binary(string='Cartas de Amonestación (Archivo)', attachment=True)
    velatek_warning_letter_filename = fields.Char(string='Nombre del archivo Amonestación')

    @api.onchange('velatek_dui', 'velatek_is_homologated')
    def _onchange_velatek_dui(self):
        """Homologación del NIT con el DUI y autoformato."""
        for record in self:
            if record.velatek_dui:
                digits = re.sub(r'\D', '', record.velatek_dui)
                if len(digits) > 9:
                    digits = digits[:9]
                if len(digits) == 9:
                    record.velatek_dui = f"{digits[:8]}-{digits[8]}"
                elif len(digits) > 8:
                    record.velatek_dui = f"{digits[:8]}-{digits[8:]}"
                else:
                    record.velatek_dui = digits
                
            if record.velatek_is_homologated and record.velatek_dui:
                record.velatek_nit = record.velatek_dui

    @api.onchange('velatek_nit', 'velatek_is_homologated')
    def _onchange_velatek_nit(self):
        """Autoformato para NIT tradicional (no homologado)."""
        for record in self:
            if record.velatek_nit and not record.velatek_is_homologated:
                digits = re.sub(r'\D', '', record.velatek_nit)
                if len(digits) > 14:
                    digits = digits[:14]
                
                if len(digits) == 14:
                    record.velatek_nit = f"{digits[:4]}-{digits[4:10]}-{digits[10:13]}-{digits[13]}"
                elif len(digits) > 13:
                    record.velatek_nit = f"{digits[:4]}-{digits[4:10]}-{digits[10:13]}-{digits[13:]}"
                elif len(digits) > 10:
                    record.velatek_nit = f"{digits[:4]}-{digits[4:10]}-{digits[10:]}"
                elif len(digits) > 4:
                    record.velatek_nit = f"{digits[:4]}-{digits[4:]}"
                else:
                    record.velatek_nit = digits

    @api.constrains('velatek_dui')
    def _check_dui_format(self):
        for record in self:
            if record.velatek_dui:
                if not re.match(r'^\d{8}-\d$', record.velatek_dui):
                    raise ValidationError("El DUI debe tener el formato 00000000-0")

    @api.constrains('velatek_nit', 'velatek_is_homologated')
    def _check_nit_format(self):
        for record in self:
            if record.velatek_nit:
                if record.velatek_is_homologated:
                    if not re.match(r'^\d{8}-\d$', record.velatek_nit):
                        raise ValidationError("El NIT homologado debe tener el mismo formato que el DUI: 00000000-0")
                else:
                    if not re.match(r'^\d{4}-\d{6}-\d{3}-\d$', record.velatek_nit):
                        raise ValidationError("El NIT tradicional debe tener el formato 0000-000000-000-0")

    @api.constrains('velatek_dui', 'velatek_nit', 'velatek_nup', 'velatek_isss')
    def _check_unique_identifiers(self):
        for record in self:
            domain = [('id', '!=', record.id)]
            if record.velatek_dui and self.search_count(domain + [('velatek_dui', '=', record.velatek_dui)]) > 0:
                raise ValidationError("El DUI ingresado ya está registrado en otro empleado.")
            if record.velatek_nit and self.search_count(domain + [('velatek_nit', '=', record.velatek_nit)]) > 0:
                raise ValidationError("El NIT ingresado ya está registrado en otro empleado.")
            if record.velatek_nup and self.search_count(domain + [('velatek_nup', '=', record.velatek_nup)]) > 0:
                raise ValidationError("El NUP ingresado ya está registrado en otro empleado.")
            if record.velatek_isss and self.search_count(domain + [('velatek_isss', '=', record.velatek_isss)]) > 0:
                raise ValidationError("El ISSS ingresado ya está registrado en otro empleado.")

    @api.constrains('birthday')
    def _check_birthday(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.birthday and record.birthday > today:
                raise ValidationError("La fecha de nacimiento no puede ser futura.")

    @api.onchange('velatek_nombre_pila', 'velatek_primer_apellido', 'velatek_segundo_apellido', 'velatek_apellido_casada')
    def _onchange_name_parts(self):
        """Sincroniza los campos personalizados de nombre con el campo estándar 'name' de Odoo."""
        for record in self:
            parts = []
            if record.velatek_nombre_pila:
                parts.append(record.velatek_nombre_pila.strip())
            if record.velatek_primer_apellido:
                parts.append(record.velatek_primer_apellido.strip())
            if record.velatek_segundo_apellido:
                parts.append(record.velatek_segundo_apellido.strip())
            if record.velatek_apellido_casada:
                parts.append(f"de {record.velatek_apellido_casada.strip()}")
            
            if parts:
                record.name = " ".join(parts)

    @api.onchange('name')
    def _onchange_full_name(self):
        """Intenta separar el nombre completo en partes cuando el usuario escribe en el campo principal."""
        for record in self:
            if not record.name:
                continue
            
            # Evitar ciclo infinito: comprobar si el nombre coincide con las partes actuales
            parts = []
            if record.velatek_nombre_pila: parts.append(record.velatek_nombre_pila.strip())
            if record.velatek_primer_apellido: parts.append(record.velatek_primer_apellido.strip())
            if record.velatek_segundo_apellido: parts.append(record.velatek_segundo_apellido.strip())
            if record.velatek_apellido_casada: parts.append(f"de {record.velatek_apellido_casada.strip()}")
            current_computed = " ".join(parts)
            
            if record.name.strip() == current_computed:
                continue

            name = record.name.strip()
            de_match = re.search(r'\s+de\s+', name, flags=re.IGNORECASE)
            
            if de_match:
                before_de = name[:de_match.start()].strip()
                after_de = name[de_match.end():].strip()
                
                before_parts = before_de.split()
                if len(before_parts) > 1:
                    record.velatek_primer_apellido = before_parts[-1]
                    record.velatek_nombre_pila = " ".join(before_parts[:-1])
                else:
                    record.velatek_primer_apellido = before_de
                    record.velatek_nombre_pila = ""
                    
                record.velatek_segundo_apellido = False
                record.velatek_apellido_casada = after_de
            else:
                record.velatek_apellido_casada = False
                words = name.split()
                if len(words) == 1:
                    record.velatek_nombre_pila = words[0]
                    record.velatek_primer_apellido = False
                    record.velatek_segundo_apellido = False
                elif len(words) == 2:
                    record.velatek_nombre_pila = words[0]
                    record.velatek_primer_apellido = words[1]
                    record.velatek_segundo_apellido = False
                elif len(words) == 3:
                    record.velatek_nombre_pila = words[0]
                    record.velatek_primer_apellido = words[1]
                    record.velatek_segundo_apellido = words[2]
                else:
                    # 4 o más palabras: los últimos dos son apellidos, el resto es nombre
                    record.velatek_segundo_apellido = words[-1]
                    record.velatek_primer_apellido = words[-2]
                    record.velatek_nombre_pila = " ".join(words[:-2])

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals, velatek_employee_file_status='incomplete') for vals in vals_list]
        employees = super().create(vals_list)
        employees._sync_employee_file_status()
        for emp in employees:
            if emp.name and emp.work_contact_id and emp.work_contact_id.name != emp.name:
                emp.work_contact_id.name = emp.name
        return employees

    def write(self, vals):
        if 'velatek_employee_file_status' in vals:
            raise ValidationError('El estado se actualiza con los archivos. Usa «Validar expediente» para aprobarlo.')
        res = super().write(vals)
        if vals.keys() & set(LEGACY_FILE_FIELDS):
            self._sync_employee_file_status(keep_validation=False)
        if 'name' in vals or any(f in vals for f in ['velatek_nombre_pila', 'velatek_primer_apellido', 'velatek_segundo_apellido', 'velatek_apellido_casada']):
            for emp in self:
                if emp.name and emp.work_contact_id and emp.work_contact_id.name != emp.name:
                    emp.work_contact_id.name = emp.name
        return res

    def _get_legacy_file_status(self, uploaded):
        if uploaded == len(LEGACY_FILE_FIELDS):
            return 'complete'
        return 'pending_documents' if uploaded else 'incomplete'

    def _get_legacy_upload_counts(self):
        return dict(self.env['ir.attachment'].sudo()._read_group(
            [
                ('res_model', '=', self._name),
                ('res_id', 'in', self.ids),
                ('res_field', 'in', LEGACY_FILE_FIELDS),
                ('file_size', '>', 0),
            ],
            ['res_id'],
            ['res_field:count_distinct'],
        ))

    @api.onchange(*LEGACY_FILE_FIELDS)
    def _onchange_legacy_file_status(self):
        uploaded = sum(bool(self[field_name]) for field_name in LEGACY_FILE_FIELDS)
        self.velatek_employee_file_status = self._get_legacy_file_status(uploaded)

    def _sync_employee_file_status(self, keep_validation=True):
        counts = self._get_legacy_upload_counts()
        by_status = self.grouped(lambda employee: employee._get_legacy_file_status(counts.get(employee.id, 0)))
        for status, employees in by_status.items():
            employees = employees.filtered(
                lambda employee: employee.velatek_employee_file_status != status
                and not (keep_validation and status == 'complete' and employee.velatek_employee_file_status == 'validated')
            )
            if employees:
                super(HrEmployee, employees).write({'velatek_employee_file_status': status})

    def action_validate_employee_file(self):
        self.ensure_one()
        if self._get_legacy_upload_counts().get(self.id, 0) != len(LEGACY_FILE_FIELDS):
            raise ValidationError('Debes adjuntar los seis archivos para validar el expediente.')
        return super().write({'velatek_employee_file_status': 'validated'})
