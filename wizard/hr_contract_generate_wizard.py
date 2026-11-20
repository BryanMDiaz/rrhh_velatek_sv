from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.image import image_data_uri
from markupsafe import escape
import re

from ..models.velatek_contract_utils import calcular_edad, fecha_a_letras, monto_a_letras, numero_a_letras


CONTRACT_BLANK = '__________________'
CONTRACT_VARIABLE_PATTERN = r'\{\{\s*(.*?)\s*\}\}'


ORIGINAL_TEMPLATE_VARIABLES = {
    'contract.company_id.name': 'company.name',
    'contract.company_id.vat': 'company.vat',
    'contract.employee_id.country_id.name': 'employee.nationality',
    'contract.employee_id.department_id.name': 'department.name',
    'contract.employee_id.identification_id': 'employee.dui',
    'contract.employee_id.job_id.name': 'job.name',
    'contract.employee_id.name': 'employee.name',
    'contract.employee_id.work_location_id.name': 'contract.work_location_name',
    'contract.resource_calendar_id.name': 'work_schedule.name',
    'datos.banco': 'employee.bank',
    'datos.descanso_semanal': 'completar.descanso semanal',
    'datos.descripcion_funciones': 'completar.funciones del cargo',
    'datos.dias_laborables': 'completar.días laborables',
    'datos.empleado.codigo': 'employee.code',
    'datos.empleado.dependientes_economicos': 'completar.personas dependientes y sus datos, o ninguna',
    'datos.empleado.domicilio': 'employee.private_address',
    'datos.empleado.edad': 'employee.age',
    'datos.empleado.estado_civil': 'employee.marital',
    'datos.empleado.fecha_expedicion_dui': 'employee.dui_date',
    'datos.empleado.genero': 'employee.gender',
    'datos.empleado.lugar_expedicion_dui': 'employee.dui_place',
    'datos.empleado.profesion': 'employee.profession',
    'datos.empleado.residencia': 'completar.residencia de la persona trabajadora',
    'datos.empresa.domicilio': 'company.address',
    'datos.fecha_firma': 'signature_date_words',
    'datos.forma_pago': 'employee.payment_method',
    'datos.frecuencia_pago': 'contract.payment_frequency',
    'datos.herramientas_entregadas': 'completar.herramientas entregadas o referencia al inventario',
    'datos.horario_detallado': 'completar.horario detallado',
    'datos.lugar_firma': 'completar.ciudad de firma',
    'datos.lugar_pago': 'completar.lugar de pago',
    'datos.lugar_trabajo_direccion': 'completar.dirección del lugar de trabajo',
    'datos.modalidad_remuneracion': 'contract.remuneration_mode',
    'datos.representante.domicilio': 'legal_representative.address',
    'datos.representante.dui': 'legal_representative.dui',
    'datos.representante.edad': 'legal_representative.age',
    'datos.representante.estado_civil': 'completar.estado civil del representante',
    'datos.representante.fecha_expedicion': 'completar.fecha de expedición del DUI del representante',
    'datos.representante.genero': 'completar.sexo del representante',
    'datos.representante.lugar_expedicion': 'completar.lugar de expedición del DUI del representante',
    'datos.representante.nacionalidad': 'legal_representative.country',
    'datos.representante.nombre': 'legal_representative.name',
    'datos.representante.profesion': 'legal_representative.profession',
    'datos.representante.residencia': 'completar.residencia del representante',
    'datos.tipo_jornada': 'contract.work_schedule_type',
}

OPTIONAL_AUTOFILL = {
    'dirección del lugar de trabajo': 'contract.work_address',
    'días laborables': 'work_schedule.days',
    'funciones del cargo': 'job.description',
    'horario detallado': 'work_schedule.hours',
    'horario y dedicación': 'work_schedule.hours',
    'residencia de la persona trabajadora': 'employee.private_city',
    'salario pactado': 'contract.agreed_pay',
}


class HrContractGenerateConfirmWizard(models.TransientModel):
    _name = 'hr.contract.generate.confirm.wizard'
    _description = 'Confirmar Generación de Contrato'

    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True)

    def action_confirm(self):
        self.ensure_one()
        return self.contract_id.with_context(force_generate=True).action_generate_contract()

class HrContractGenerateWizard(models.TransientModel):
    _name = 'hr.contract.generate.wizard'
    _description = 'Wizard para generar contrato'

    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True)
    template_id = fields.Many2one('hr.contract.template', string='Plantilla', required=True, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    company_id = fields.Many2one('res.company', related='contract_id.company_id')
    signature_date = fields.Date(string='Fecha de Firma', default=fields.Date.context_today, required=True)
    effective_terms_id = fields.Many2one('hr.contract.terms', string='Cambio contractual vigente desde')
    preview_html = fields.Html(string='Vista Previa (Editable)', sanitize=False)
    generated_name = fields.Char(string='Nombre del documento')
    warning_message = fields.Text(string='Advertencias', readonly=True)

    @api.onchange('contract_id', 'template_id', 'signature_date', 'effective_terms_id')
    def _onchange_template(self):
        if self.effective_terms_id.contract_id and self.effective_terms_id.contract_id != self.contract_id:
            self.effective_terms_id = False
        self.generated_name = (
            f"Contrato - {self.contract_id.employee_id.name} - v{self.template_id.version}"
            if self.template_id and self.contract_id else False
        )
        self._render_preview()

    def _get_schedule_values(self, calendar, employee, date_start):
        def hour_text(hour):
            hours, minutes = divmod(round(hour * 60), 60)
            return f"{hours:02d}:{minutes:02d}"

        attendances = calendar.attendance_ids.filtered(
            lambda line: not line.display_type and line.day_period != 'lunch'
            and (not line.resource_id or line.resource_id == employee.resource_id)
            and (not line.date_from or (date_start and line.date_from <= date_start))
            and (not line.date_to or (date_start and line.date_to >= date_start))
        ).sorted(lambda line: (line.week_type or '', line.dayofweek, line.hour_from))
        days = dict(self.env['resource.calendar.attendance']._fields['dayofweek']._description_selection(self.env))
        intervals = {}
        for line in attendances:
            key = (line.week_type, line.dayofweek)
            intervals.setdefault(key, []).append(f"{hour_text(line.hour_from)}–{hour_text(line.hour_to)}")
        grouped_days = {}
        for (week, day), hours in intervals.items():
            grouped_days.setdefault((week, tuple(hours)), []).append(days[day])
        schedule = []
        for (week, hours), group in grouped_days.items():
            prefix = {'0': 'Primera semana, ', '1': 'Segunda semana, '}.get(week, '')
            schedule.append(f"{prefix}{', '.join(group)}: {', '.join(hours)}")
        workdays = sorted(set(attendances.mapped('dayofweek')))
        return {
            'work_schedule.hours': '; '.join(schedule),
            'work_schedule.days': ', '.join(days[day] for day in workdays),
        }

    def _get_safe_dict(self):
        self.ensure_one()
        contract = self.contract_id
        employee = contract.employee_id
        company = contract.company_id
        legal_rep = company.velatek_legal_representative_id
        work_location = contract.velatek_work_location_id or employee.work_location_id
        work_address = work_location.address_id or employee.address_id
        job = contract.job_id or employee.job_id
        effective_date = self.effective_terms_id.effective_from or self.signature_date
        wage, calendar, _hours = contract._velatek_terms_on(effective_date)
        calendar = calendar or employee.resource_calendar_id
        def format_address(*parts):
            components = {}
            for part in filter(None, parts):
                part = part.strip(' ,')
                if part:
                    components.setdefault(part.casefold(), part)
            return ', '.join(components.values())

        private_address = format_address(
            employee.private_street, employee.private_street2, employee.private_city,
            employee.private_state_id.name, employee.private_zip, employee.private_country_id.name,
        )

        def partner_address(partner):
            if not partner:
                return ''
            return format_address(
                partner.street, partner.street2, partner.city,
                partner.state_id.name, partner.zip, partner.country_id.name,
            )

        def selection_label(record, field_name):
            options = record._fields[field_name]._description_selection(self.env)
            return dict(options).get(record[field_name], '')

        currency = contract.currency_id.name
        fixed_pay = f"{currency} {wage:,.2f} mensuales" if wage > 0 else ''
        unit = selection_label(contract, 'velatek_production_unit')
        production_pay = (
            f"{currency} {contract.velatek_production_rate:,.2f} por {unit.lower()}"
            if contract.velatek_production_rate > 0 and unit else ''
        )
        agreed_pay = {
            'fixed': fixed_pay,
            'production': production_pay,
            'mixed': f"{fixed_pay}, más {production_pay}" if fixed_pay and production_pay else '',
        }.get(contract.velatek_remuneration_mode, '')

        # Empresa
        safe_dict = {
            'company.name': company.name or '',
            'company.vat': company.vat or '',
            'company.street': company.street or '',
            'company.street2': company.street2 or '',
            'company.city': company.city or '',
            'company.address': partner_address(company.partner_id),
            'company.state_name': company.state_id.name if company.state_id else '',
            'company.country_name': company.country_id.name if company.country_id else '',
            'company.phone': company.phone or '',
            'company.email': company.email or '',
        }

        # Representante Legal
        if legal_rep:
            edad_rep = calcular_edad(legal_rep.velatek_birth_date, self.signature_date)
            safe_dict.update({
                'legal_representative.name': legal_rep.name or '',
                'legal_representative.dui': legal_rep.velatek_dui or '',
                'legal_representative.profession': legal_rep.velatek_profession or '',
                'legal_representative.age': str(edad_rep) if legal_rep.velatek_birth_date else '',
                'legal_representative.age_words': numero_a_letras(edad_rep).lower() if legal_rep.velatek_birth_date else '',
                'legal_representative.address': partner_address(legal_rep),
                'legal_representative.country': legal_rep.country_id.name or '',
            })
        else:
            safe_dict.update({
                'legal_representative.name': '[FALTA REPRESENTANTE]',
                'legal_representative.dui': '[FALTA DUI REP]',
                'legal_representative.profession': '[FALTA PROFESION REP]',
                'legal_representative.age': '[FALTA EDAD REP]',
                'legal_representative.age_words': '[FALTA EDAD REP]',
                'legal_representative.address': '',
                'legal_representative.country': '',
            })

        # Empleado
        edad_emp = calcular_edad(employee.birthday, self.signature_date)
        safe_dict.update({
            'employee.name': employee.name or '',
            'employee.dui': employee.velatek_dui or employee.identification_id or '',
            'employee.nit': employee.velatek_nit or '',
            'employee.nup': employee.velatek_nup or '',
            'employee.isss': employee.velatek_isss or '',
            'employee.ssnid': employee.ssnid or '',
            'employee.profession': employee.velatek_profesion or employee.job_title or '',
            'employee.code': employee.velatek_codigo or '',
            'employee.gender': selection_label(employee, 'gender'),
            'employee.marital': selection_label(employee, 'marital'),
            'employee.nationality': employee.country_id.name or '',
            'employee.dui_place': employee.velatek_lugar_expedicion_dui or '',
            'employee.dui_date': employee.velatek_fecha_expedicion_dui.strftime('%d/%m/%Y') if employee.velatek_fecha_expedicion_dui else '',
            'employee.payment_method': selection_label(employee, 'velatek_payment_method'),
            'employee.bank': (employee.bank_account_id.bank_id or employee.velatek_bank_id).name or '',
            'employee.birth_date': employee.birthday.strftime('%d/%m/%Y') if employee.birthday else '',
            'employee.age': str(edad_emp) if employee.birthday else '',
            'employee.age_words': numero_a_letras(edad_emp).lower() if employee.birthday else '',
            'employee.private_address': private_address,
            'employee.private_city': employee.private_city or '',
            'employee.private_phone': employee.private_phone or '',
            'employee.private_email': employee.private_email or '',
        })

        # Contrato
        safe_dict.update({
            'contract.name': contract.name or '',
            'contract.date_start': contract.date_start.strftime('%d/%m/%Y') if contract.date_start else '',
            'contract.date_end': contract.date_end.strftime('%d/%m/%Y') if contract.date_end else 'indefinido',
            'contract.fixed_end_date': contract.date_end.strftime('%d/%m/%Y') if contract.date_end else '',
            'contract.contract_type': contract.contract_type_id.name if contract.contract_type_id else '',
            'contract.wage': f"{wage:,.2f}",
            'contract.wage_words': monto_a_letras(wage),
            'contract.terms_effective_from': self.effective_terms_id.effective_from.strftime('%d/%m/%Y') if self.effective_terms_id else '',
            'contract.agreed_pay': agreed_pay,
            'contract.payment_frequency': dict(contract._fields['velatek_payment_frequency'].selection).get(contract.velatek_payment_frequency, ''),
            'contract.remuneration_mode': dict(contract._fields['velatek_remuneration_mode'].selection).get(contract.velatek_remuneration_mode, ''),
            'contract.production_unit': dict(contract._fields['velatek_production_unit'].selection).get(contract.velatek_production_unit, '') if contract.velatek_production_unit else '',
            'contract.production_rate': f"{contract.velatek_production_rate:,.2f}",
            'contract.department_name': contract.department_id.name if contract.department_id else '',
            'contract.job_name': contract.job_id.name if contract.job_id else '',
            'contract.work_location_name': work_location.name or '',
            'contract.work_address': partner_address(work_address),
            'contract.work_schedule_type': selection_label(contract, 'velatek_work_schedule_type'),
            'job.name': job.name or '',
            'job.description': ' '.join(html2plaintext(job.description or '', include_references=False).split()),
            'department.name': contract.department_id.name if contract.department_id else (employee.department_id.name if employee.department_id else ''),
            'work_schedule.name': calendar.name or '',
        })

        # Fecha de firma
        safe_dict.update({
            'signature_date': self.signature_date.strftime('%d/%m/%Y') if self.signature_date else '',
            'signature_date_words': fecha_a_letras(self.signature_date),
        })
        safe_dict.update(self._get_schedule_values(calendar, employee, effective_date or contract.date_start))

        return safe_dict

    def _render_preview(self):
        if not self.template_id or not self.template_id.body_html:
            self.preview_html = False
            self.warning_message = False
            return

        company = self.contract_id.company_id or self.template_id.company_id or self.env.company
        logo = company.with_context(bin_size=False).logo_web if not company.uses_default_logo else False
        html_content = str(self.template_id.body_html)
        if logo:
            logo_image = (
                f'<img src="{escape(image_data_uri(logo))}" alt="{escape(company.name)}" '
                'style="display: block; width: auto; height: auto; '
                'max-width: 145px; max-height: 48px; margin: 0 0 6px;"/>'
            )
            company_marker = '<span data-contract-company="1">'
            if company_marker in html_content:
                company_label = (
                    '<span data-contract-company="1" style="display: block; padding-bottom: 4px; '
                    "font-family: Arial, sans-serif; font-size: 9pt; font-weight: 700; "
                    'line-height: 1.25; letter-spacing: 0.25px;">'
                )
                html_content = html_content.replace(company_marker, logo_image + company_label, 1)
            else:
                html_content = f'<div style="text-align: left; margin-bottom: 6px;">{logo_image}</div>{html_content}'

        if not self.contract_id:
            self.preview_html = re.sub(CONTRACT_VARIABLE_PATTERN, CONTRACT_BLANK, html_content)
            self.warning_message = 'Selecciona un contrato para rellenar la vista previa con sus datos.'
            return
        safe_dict = self._get_safe_dict()
        unknown_vars = []

        def replace_var(match):
            original_name = match.group(1).strip()
            var_name = ORIGINAL_TEMPLATE_VARIABLES.get(original_name, original_name)
            if var_name.startswith('completar.'):
                label = var_name.removeprefix('completar.')
                value = safe_dict.get(OPTIONAL_AUTOFILL.get(label))
                return str(escape(value)) if value else CONTRACT_BLANK
            if var_name in safe_dict:
                value = safe_dict[var_name]
                return str(escape(value)) if value and not value.startswith('[FALTA') else CONTRACT_BLANK
            unknown_vars.append(original_name)
            return CONTRACT_BLANK

        rendered_html = re.sub(CONTRACT_VARIABLE_PATTERN, replace_var, html_content)
        self.preview_html = rendered_html
        
        if unknown_vars:
            unique_unknown = list(dict.fromkeys(unknown_vars))
            self.warning_message = "Variables no reconocidas en la plantilla:\n" + "\n".join(unique_unknown)
        else:
            self.warning_message = False

    def action_generate(self):
        self.ensure_one()
        if self.env.context.get('contract_preview_only'):
            raise UserError("Usa «Generar contrato» para guardar el documento.")
        if self.effective_terms_id.contract_id and self.effective_terms_id.contract_id != self.contract_id:
            raise UserError('Seleccione una vigencia del mismo contrato.')
        if not self.preview_html:
            self._render_preview()
        
        doc = self.env['hr.contract.generated.document'].create({
            'name': self.generated_name or 'Contrato Generado',
            'contract_id': self.contract_id.id,
            'template_id': self.template_id.id,
            'template_version': self.template_id.version,
            'signature_date': self.signature_date,
            'mtps_document_type': 'amendment' if self.effective_terms_id else 'original',
            'content_snapshot': self.preview_html,
            'state': 'generated',
        })

        if not doc.pdf_attachment_id:
            report_action = self.env.ref('rrhh_velatek_sv.action_report_hr_contract_generated')
            pdf_content, _content_type = (
                self.env['ir.actions.report']
                .with_context(force_report_rendering=True)
                ._render_qweb_pdf(report_action, res_ids=doc.ids)
            )
            employee_name = re.sub(
                r'[^\w]+',
                '_',
                self.contract_id.employee_id.name or 'Empleado',
            ).strip('_')
            document_year = self.signature_date.year
            attachment = self.env['ir.attachment'].create({
                'name': f'Contrato_{employee_name}_{document_year}.pdf',
                'type': 'binary',
                'raw': pdf_content,
                'mimetype': 'application/pdf',
                'res_model': doc._name,
                'res_id': doc.id,
            })
            doc.pdf_attachment_id = attachment

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{doc.pdf_attachment_id.id}?download=1',
            'target': 'new',
            'close': True,
        }
