import re
from datetime import date
from lxml import html
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class HrLoanTemplatePreviewWizard(models.TransientModel):
    _name = 'hr.loan.template.preview.wizard'
    _description = 'Vista Previa de Plantilla de Préstamo'

    template_id = fields.Many2one('hr.employee.loan.template', string='Plantilla', required=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado de Muestra', domain="[('contract_ids.state', '=', 'open')]")
    contract_id = fields.Many2one('hr.contract', string='Contrato de Muestra', domain="[('employee_id', '=', employee_id), ('state', '=', 'open')]")
    loan_id = fields.Many2one('hr.employee.loan', string='Préstamo Real (Opcional)')
    editing_loan_id = fields.Many2one('hr.employee.loan', readonly=True)
    preview_html = fields.Html(string='Contenido del contrato', sanitize=False)

    def _prepare_editable_html(self, content):
        """Inline contract styles so Odoo opens the visual text editor."""
        root = html.fragment_fromstring(content or '<p><br/></p>', create_parent='div')
        for stylesheet in root.xpath('.//style'):
            css = re.sub(r'/\*.*?\*/', '', stylesheet.text or '', flags=re.S)
            for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
                for selector in selectors.split(','):
                    selector = selector.strip()
                    # Support contract tag/class/id descendant selectors using
                    # lxml's built-in XPath; no optional cssselect package needed.
                    parts = selector.split()
                    if not parts or any(not re.fullmatch(
                        r'(?:[a-zA-Z][a-zA-Z0-9_-]*)?(?:[.#][a-zA-Z_][a-zA-Z0-9_-]*)*',
                        part,
                    ) for part in parts):
                        continue
                    xpath = '.'
                    for part in parts:
                        tag = re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*', part)
                        xpath += '//' + (tag.group(0) if tag else '*')
                        for prefix, name in re.findall(r'([.#])([a-zA-Z_][a-zA-Z0-9_-]*)', part):
                            if prefix == '#':
                                xpath += "[@id='%s']" % name
                            else:
                                xpath += "[contains(concat(' ', normalize-space(@class), ' '), ' %s ')]" % name
                    for element in root.xpath(xpath):
                        existing = element.get('style', '')
                        element.set('style', declarations.strip() + ';' + existing)
            stylesheet.drop_tree()
        # Override inherited report/editor table borders without a style element.
        for element in root.xpath('.//table | .//thead | .//tbody | .//tfoot | .//tr | .//th | .//td'):
            style = element.get('style', '')
            element.set('style', style + ';border:0 !important;outline:0 !important;box-shadow:none !important;')
        return html.tostring(root, encoding='unicode')

    def action_save_contract(self):
        self.ensure_one()
        loan = self.editing_loan_id
        if not loan or self.loan_id != loan or self.employee_id != loan.employee_id or self.contract_id != loan.contract_id or self.template_id != loan.loan_template_id:
            raise ValidationError(_('Abra la vista previa desde el préstamo para guardar su contrato.'))
        if not self.preview_html:
            raise ValidationError(_('El contenido del contrato no puede estar vacío.'))
        loan.write({'contract_html': self.preview_html})
        return {'type': 'ir.actions.act_window_close'}

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id:
            contract = self.env['hr.contract'].search([
                ('employee_id', '=', self.employee_id.id),
                ('state', '=', 'open')
            ], limit=1)
            self.contract_id = contract.id if contract else False
            
            # Si tiene un préstamo asociado, lo sugerimos
            loan = self.env['hr.employee.loan'].search([
                ('employee_id', '=', self.employee_id.id)
            ], order='id desc', limit=1)
            self.loan_id = loan.id if loan else False
        else:
            self.contract_id = False
            self.loan_id = False

    @api.onchange('template_id', 'employee_id', 'contract_id', 'loan_id')
    def _onchange_render_preview(self):
        self._render_preview()

    def _render_preview(self):
        self.ensure_one()
        if not self.template_id or not self.template_id.body_html:
            self.preview_html = "<div class='alert alert-warning'>La plantilla no contiene texto o está vacía.</div>"
            return

        if self.loan_id and self.loan_id.contract_html:
            self.preview_html = self._prepare_editable_html(self.loan_id.contract_html)
            return

        company = self.template_id.company_id or self.env.company
        employee = self.employee_id or (self.loan_id.employee_id if self.loan_id else False)
        contract = self.contract_id or (self.loan_id.contract_id if self.loan_id else False)
        loan = self.loan_id

        # Valores base de fallback si no hay empleado o préstamo seleccionado
        c_name = company.name or "GRUPO 9, S.A. DE C.V."
        rep = company.velatek_legal_representative_id
        rep_name = rep.name if rep else (company.name or "REPRESENTANTE LEGAL")
        rep_dui = rep.velatek_dui if rep else "00000000-0"

        e_name = employee.name if employee else "NOMBRE DEL EMPLEADO (EJEMPLO)"
        e_dui = (employee.velatek_dui or employee.velatek_nit) if employee else "00000000-0"
        e_job = employee.job_id.name if (employee and employee.job_id) else (contract.job_id.name if (contract and contract.job_id) else "EMPLEADO")

        p_ref = loan.name if loan else "PRST-2026-00001"
        p_type = loan.loan_type_id.name if (loan and loan.loan_type_id) else (self.template_id.loan_type_id.name or "Préstamo Personal")
        p_monto = loan.principal_amount if loan else 100.00
        p_interes = loan.interest_amount if loan else 0.00
        p_total = loan.total_financed if loan else (p_monto + p_interes)
        p_cuotas = loan.estimated_installments if loan else 4
        p_cuota = loan.approved_installment if loan else (p_total / (p_cuotas or 1))

        freq_dict = {
            'biweekly': 'Cada quincena',
            'monthly': 'Una vez al mes',
            'alternate_biweekly': 'Una quincena sí y la siguiente no',
            'custom_interval': 'Cada N períodos'
        }
        p_freq = freq_dict.get(loan.payment_frequency, 'Cada quincena') if loan else 'Cada quincena'
        today_str = fields.Date.today().strftime('%d/%m/%Y')
        p_first_date = loan.first_payment_date.strftime('%d/%m/%Y') if (loan and loan.first_payment_date) else today_str
        p_end_date = loan.estimated_end_date.strftime('%d/%m/%Y') if (loan and loan.estimated_end_date) else today_str

        # Construir tabla de amortización con diseño ejecutivo de alto nivel
        table_html = """
        <div style="margin: 15px 0;">
            <table style="width: 100%; border-collapse: collapse; font-size: 11px; text-align: left;">
                <thead>
                    <tr style="background-color: #1e293b; color: #ffffff;">
                        <th style="padding: 8px 12px; font-weight: 600; text-align: center; border: 1px solid #1e293b; width: 8%;">#</th>
                        <th style="padding: 8px 12px; font-weight: 600; text-align: center; border: 1px solid #1e293b; width: 32%;">Fecha de Descuento</th>
                        <th style="padding: 8px 12px; font-weight: 600; text-align: right; border: 1px solid #1e293b; width: 30%;">Monto Cuota</th>
                        <th style="padding: 8px 12px; font-weight: 600; text-align: right; border: 1px solid #1e293b; width: 30%;">Saldo Restante</th>
                    </tr>
                </thead>
                <tbody>
        """
        if loan and loan.installment_ids:
            scheduled_balance = p_total
            installments = loan.installment_ids.sorted(key=lambda inst: (inst.scheduled_date or date.max, inst.sequence, inst.id))
            for idx, inst in enumerate(installments):
                # Saldo contractual después de la cuota, no saldo individual impago.
                scheduled_balance = loan.currency_id.round(max(0.0, scheduled_balance - inst.amount))
                bg = "#f8fafc" if idx % 2 == 1 else "#ffffff"
                table_html += f"""
                <tr style="background-color: {bg}; border-bottom: 1px solid #e2e8f0;">
                    <td style="padding: 6px 12px; text-align: center; color: #64748b; font-weight: 600; border: 1px solid #e2e8f0;">{inst.sequence}</td>
                    <td style="padding: 6px 12px; text-align: center; color: #334155; border: 1px solid #e2e8f0;">{inst.scheduled_date.strftime('%d/%m/%Y') if inst.scheduled_date else ''}</td>
                    <td style="padding: 6px 12px; text-align: right; font-weight: 600; color: #0f172a; border: 1px solid #e2e8f0;">${inst.amount:,.2f}</td>
                    <td style="padding: 6px 12px; text-align: right; color: #475569; border: 1px solid #e2e8f0;">${scheduled_balance:,.2f}</td>
                </tr>
                """
        else:
            saldo_sim = p_total
            for i in range(1, p_cuotas + 1):
                simulated_amount = min(p_cuota, saldo_sim) if i < p_cuotas else saldo_sim
                saldo_sim = round(max(0.0, saldo_sim - simulated_amount), 2)
                bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
                table_html += f"""
                <tr style="background-color: {bg}; border-bottom: 1px solid #e2e8f0;">
                    <td style="padding: 6px 12px; text-align: center; color: #64748b; font-weight: 600; border: 1px solid #e2e8f0;">{i}</td>
                    <td style="padding: 6px 12px; text-align: center; color: #334155; border: 1px solid #e2e8f0;">{p_first_date}</td>
                    <td style="padding: 6px 12px; text-align: right; font-weight: 600; color: #0f172a; border: 1px solid #e2e8f0;">${simulated_amount:,.2f}</td>
                    <td style="padding: 6px 12px; text-align: right; color: #475569; border: 1px solid #e2e8f0;">${saldo_sim:,.2f}</td>
                </tr>
                """
        table_html += "</tbody></table></div>"

        mapping = {
            # Empresa
            'empresa_nombre': c_name,
            'company_name': c_name,
            'company.name': c_name,
            'representante_empresa': rep_name,
            'legal_representative_name': rep_name,
            'legal_representative.name': rep_name,
            'legal_representative_dui': rep_dui,
            'legal_representative.dui': rep_dui,

            # Empleado y Contrato
            'empleado_nombre': e_name,
            'empleado': e_name,
            'employee.name': e_name,
            'empleado_documento': e_dui,
            'employee_dui': e_dui,
            'employee.dui': e_dui,
            'employee_cargo': e_job,
            'cargo': e_job,
            'contract_name': contract.name if contract else 'CONTRATO-001',
            'contract.name': contract.name if contract else 'CONTRATO-001',

            # Préstamo
            'prestamo_referencia': p_ref,
            'prestamo_tipo': p_type,
            'prestamo_monto': f"${p_monto:,.2f}",
            'monto': f"${p_monto:,.2f}",
            'prestamo_intereses': f"${p_interes:,.2f}",
            'prestamo_total': f"${p_total:,.2f}",
            'numero_cuotas': str(p_cuotas),
            'cuotas': str(p_cuotas),
            'cuota_inicial': f"${p_cuota:,.2f}",
            'cuota_aprobada': f"${p_cuota:,.2f}",
            'frecuencia_descuento': p_freq,
            'fecha_solicitud': (loan.request_date.strftime('%d/%m/%Y') if loan and loan.request_date else today_str),
            'fecha_primer_descuento': p_first_date,
            'fecha_finalizacion': p_end_date,
            'tabla_amortizacion': table_html,
            'lugar_firma': "San Salvador, El Salvador",
            'fecha_firma': today_str,
            'plantilla_version': f"v{self.template_id.version}",
        }

        def replace_var(match):
            key = match.group(1).strip()
            return mapping.get(key, match.group(0))

        rendered = re.sub(r'\{\{\s*([a-zA-Z0-9_\.]+)\s*\}\}', replace_var, self.template_id.body_html)
        self.preview_html = self._prepare_editable_html(f"""
        <style>
            .velatek-loan-contract table,
            .velatek-loan-contract thead,
            .velatek-loan-contract tbody,
            .velatek-loan-contract tfoot,
            .velatek-loan-contract tr,
            .velatek-loan-contract th,
            .velatek-loan-contract td {{
                border: 0 !important;
                outline: 0 !important;
                box-shadow: none !important;
            }}
            .velatek-loan-contract table {{
                width: 100% !important;
                border-collapse: collapse;
            }}
            .velatek-loan-contract td,
            .velatek-loan-contract th {{
                padding: 6px 10px !important;
                vertical-align: middle;
            }}
            .velatek-loan-contract .meta-box {{
                border: 0 !important;
                box-shadow: none !important;
            }}
        </style>
        <div class="velatek-loan-contract" style="background:#ffffff; max-width:850px; margin:0 auto; padding:35px 45px; font-family:'Helvetica Neue', Arial, sans-serif; font-size:12.5px; line-height:1.7; color:#1e293b;">
            {rendered}
        </div>
        """)
