from base64 import b64encode
from io import BytesIO
import re
from urllib.parse import quote
from zipfile import ZipFile

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools.pdf import merge_pdf

class HrPayrollPayslipWizard(models.TransientModel):
    _name = 'hr.payroll.payslip.wizard'
    _description = 'Imprimir Boletas de Pago'

    payroll_id = fields.Many2one('hr.payroll', string='Planilla', required=True, default=lambda self: self.env.context.get('active_id'))
    print_mode = fields.Selection([
        ('all', 'Todos los Empleados'),
        ('employee', 'Un Empleado Específico')
    ], string='Imprimir', default='all', required=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado')
    eligible_employee_ids = fields.Many2many('hr.employee', compute='_compute_eligible_employee_ids')
    download_format = fields.Selection([
        ('pdf', 'Un solo PDF'),
        ('zip', 'ZIP con un PDF por empleado'),
    ], string='Formato', default='pdf', required=True)
    archive_file = fields.Binary(attachment=False, readonly=True)

    @api.depends('payroll_id.line_ids.employee_id')
    def _compute_eligible_employee_ids(self):
        for wizard in self:
            wizard.eligible_employee_ids = wizard.payroll_id.line_ids.employee_id

    def action_print(self):
        self.ensure_one()
        domain = [('payroll_id', '=', self.payroll_id.id)]
        if self.print_mode == 'employee':
            if not self.employee_id:
                raise ValidationError(_("Debe seleccionar un empleado."))
            domain.append(('employee_id', '=', self.employee_id.id))
        lines = self.env['hr.payroll.line'].search(domain)
        if not lines:
            if self.print_mode == 'employee':
                raise ValidationError(_("El empleado seleccionado no tiene registros en esta planilla."))
            raise ValidationError(_("No hay registros para imprimir."))

        report = self.env.ref('rrhh_velatek_sv.action_report_payslip')
        if self.print_mode == 'all' and self.download_format == 'zip':
            return self._download_zip(report, lines)
        pdf, _format = report.with_context(force_report_rendering=True)._render_qweb_pdf(report, res_ids=lines.ids)
        employee = self.employee_id if self.print_mode == 'employee' else False
        return self._download_file(pdf, self._boleta_filename(employee))

    def _boleta_filename(self, employee=False):
        period = (self.payroll_id.period_name or str(self.payroll_id.date_start)).replace(' - ', ' ')
        if employee:
            name = re.sub(r'[^\w -]+', ' ', employee.name or '')
            name = ' '.join(name.split())[:100] or 'Empleado'
            return f'Boleta de Pago - {name} - {period}.pdf'
        return f'Boletas de Pago General - {period}.pdf'

    def _download_zip(self, report, lines):
        report = report.with_context(force_report_rendering=True)
        streams, _format = report._pre_render_qweb_pdf(report, res_ids=lines.ids)
        pdf_by_line = {}
        for line_id, result in streams.items():
            if result['stream']:
                with result['stream'] as stream:
                    if line_id:
                        pdf_by_line[line_id] = stream.getvalue()

        used_names = set()
        with BytesIO() as buffer:
            with ZipFile(buffer, 'w') as archive:
                for employee, employee_lines in lines.grouped('employee_id').items():
                    filename = self._boleta_filename(employee)
                    if filename.casefold() in used_names:
                        filename = f'{filename[:-4]} - {employee.id}.pdf'
                    used_names.add(filename.casefold())
                    if all(line.id in pdf_by_line for line in employee_lines):
                        pages = [pdf_by_line[line.id] for line in employee_lines]
                        pdf = pages[0] if len(pages) == 1 else merge_pdf(pages)
                    else:
                        pdf, _format = report._render_qweb_pdf(report, res_ids=employee_lines.ids)
                    archive.writestr(filename, pdf)
            return self._download_file(buffer.getvalue(), self._boleta_filename().removesuffix('.pdf') + '.zip')

    def _download_file(self, content, filename):
        self.archive_file = b64encode(content)
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self._name}/{self.id}/archive_file/{quote(filename, safe="")}?download=1',
            'target': 'new',
            'close': True,
        }
