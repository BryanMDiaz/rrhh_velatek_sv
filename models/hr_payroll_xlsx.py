import json
from io import BytesIO

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.misc import file_open, xlsxwriter


GROUP_COLORS = {
    'base': ('#E0E7FF', '#EEF2FF', '#0F172A'),
    'devengos': ('#D1FAE5', '#ECFDF5', '#065F46'),
    'descuentos': ('#FFE4E6', '#FFF1F2', '#9F1239'),
    'pago': ('#FEF3C7', '#FFFBEB', '#92400E'),
}
DAY_COLUMNS = {'worked_days', 'extra_days', 'holiday_days'}
RESULT_COLORS = {
    'total_earned': ('#ECFDF5', '#166534'),
    'total_deductions': ('#FFF1F2', '#BE123C'),
    'total_to_settle': ('#FDE68A', '#78350F'),
}


class HrPayroll(models.Model):
    _inherit = 'hr.payroll'

    @api.model
    def get_spreadsheet_layout(self):
        with file_open('rrhh_velatek_sv/data/payroll_spreadsheet_layout.json', filter_ext=('.json',)) as layout:
            return json.load(layout)

    def action_export_xlsx(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_url',
            'url': f'/rrhh_velatek_sv/payroll/{self.id}/xlsx',
            'target': 'self',
        }

    def _export_xlsx(self):
        self.ensure_one()
        self.check_access('read')
        lines = self.env['hr.payroll.line'].search([('payroll_id', '=', self.id)], order='department_id, sequence, id')
        if not lines:
            raise UserError(_('La planilla no tiene líneas para exportar.'))
        if not xlsxwriter:
            raise UserError(_('XlsxWriter es necesario para exportar la planilla.'))

        layout = self.get_spreadsheet_layout()
        groups = layout['columns'].get(self.payroll_type, layout['columns']['regular'])
        labels = {**layout['labels'], **layout['overrides'].get(self.payroll_type, {})}
        columns = [(group, field) for group, fields in groups.items() for field in fields]
        last_col = len(columns) + 1
        with BytesIO() as buffer:
            with xlsxwriter.Workbook(buffer, {
                'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
            }) as workbook:
                sheet = workbook.add_worksheet('Planilla')
                self._write_xlsx_sheet(workbook, sheet, lines, groups, columns, labels, last_col)
            content = buffer.getvalue()
        return content, f'Planilla - {self.period_name or self.name}.xlsx'

    def _write_xlsx_sheet(self, workbook, sheet, lines, groups, columns, labels, last_col):
        def cell_format(**options):
            return workbook.add_format({
                'font_name': 'Arial', 'font_size': 10, 'font_color': '#0F172A', 'valign': 'vcenter',
                **options,
            })

        border = {'border': 1, 'border_color': '#CBD5E1'}
        title = cell_format(align='center', bold=True, font_size=16)
        subtitle = cell_format(align='center', bold=True, font_size=11, font_color='#475569')
        identity_header = cell_format(**border, align='center', bold=True, bg_color='#E0F2FE')
        identity_name = cell_format(**border, align='left', bold=True, bg_color='#F0F9FF')
        department_header = cell_format(**border, bold=True, bg_color='#FFEDD5', indent=1)
        subtotal_label = cell_format(**border, bold=True, bg_color='#E2E8F0', indent=1)
        total_label = cell_format(**border, bold=True, bg_color='#0F172A', font_color='#FFFFFF', indent=1)
        sequence = cell_format(**border, align='center', bg_color='#F8FAFC')
        employee_name = cell_format(**border, bg_color='#F8FAFC', indent=1)
        footer_label = cell_format(align='center', bold=True)
        footer_name = cell_format(align='center', top=1, top_color='#475569')

        sheet.hide_gridlines(2)
        sheet.set_zoom(85)
        sheet.freeze_panes(5, 2)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.repeat_rows(0, 4)
        sheet.set_column(0, 0, 5)
        sheet.set_column(1, 1, 38)
        sheet.set_row(0, 25)
        sheet.set_row(1, 20)
        sheet.merge_range(0, 0, 0, last_col, self.company_id.name.upper(), title)
        sheet.merge_range(1, 0, 1, last_col, self.period_title or 'PLANILLA DE SUELDOS', subtitle)
        sheet.merge_range(3, 0, 3, 1, 'EMPLEADO', identity_header)
        sheet.write_string(4, 0, 'No.', identity_header)
        sheet.write_string(4, 1, 'Nombre Empleado', identity_name)
        sheet.set_row(4, 30)

        col = 2
        for group, fields in groups.items():
            if not fields:
                continue
            group_color, header_color, _total_color = GROUP_COLORS[group]
            heading = cell_format(**border, align='center', bold=True, bg_color=group_color)
            if len(fields) == 1:
                sheet.write_string(3, col, labels[f'{group}_group'], heading)
            else:
                sheet.merge_range(3, col, 3, col + len(fields) - 1, labels[f'{group}_group'], heading)
            header = cell_format(**border, align='center', bold=True, text_wrap=True, bg_color=header_color)
            for field in fields:
                sheet.set_column(col, col, max(12, min(len(labels[field]) + 2, 21)))
                sheet.write_string(4, col, labels[field], header)
                col += 1

        formats = {}
        for group, field in columns:
            number = '0.##' if field in DAY_COLUMNS else '"$" #,##0.00'
            align = 'center' if field in DAY_COLUMNS else 'right'
            background, foreground = RESULT_COLORS.get(field, ('#FFFFFF', '#0F172A'))
            formats[field] = (
                cell_format(**border, align=align, num_format=number, bg_color=background, font_color=foreground, bold=field in RESULT_COLORS),
                cell_format(**border, align=align, num_format=number, bold=True, bg_color='#F1F5F9', font_color=foreground),
                cell_format(**border, align=align, num_format=number, bold=True, bg_color=GROUP_COLORS[group][2], font_color='#FFFFFF'),
            )

        row, index = 5, 0
        subtotal_rows = []
        grand = dict.fromkeys(formats, 0)
        departments = lines.grouped(lambda line: line.department_id.display_name or 'SIN DEPARTAMENTO')
        for department, employee_lines in departments.items():
            sheet.merge_range(row, 0, row, last_col, department.upper(), department_header)
            row += 1
            start_row = row
            subtotal = dict.fromkeys(formats, 0)
            for line in employee_lines:
                index += 1
                sheet.write_number(row, 0, index, sequence)
                sheet.write_string(row, 1, line.employee_id.display_name or '', employee_name)
                for col, (_group, field) in enumerate(columns, 2):
                    value = line.deduction_amount + line.absence_amount if field == 'other_deductions' else (line[field] or 0)
                    sheet.write_number(row, col, value, formats[field][0])
                    subtotal[field] += value
                    grand[field] += value
                row += 1
            sheet.merge_range(row, 0, row, 1, f'SUBTOTAL {department}', subtotal_label)
            for col, (_group, field) in enumerate(columns, 2):
                if field == 'daily_salary':
                    sheet.write_string(row, col, '-', formats[field][1])
                else:
                    span = xlsxwriter.utility.xl_range(start_row, col, row - 1, col)
                    sheet.write_formula(row, col, f'=SUM({span})', formats[field][1], subtotal[field])
            subtotal_rows.append(row)
            row += 1

        sheet.merge_range(row, 0, row, 1, 'TOTALES GENERALES', total_label)
        for col, (_group, field) in enumerate(columns, 2):
            if field == 'daily_salary':
                sheet.write_string(row, col, '-', formats[field][2])
            else:
                references = ','.join(xlsxwriter.utility.xl_rowcol_to_cell(subtotal_row, col) for subtotal_row in subtotal_rows)
                sheet.write_formula(row, col, f'=SUM({references})', formats[field][2], grand[field])

        for index, (label, user) in enumerate((
            ('ELABORADA POR', self.prepared_employee_id or self.prepared_by_id),
            ('REVISADA POR', self.reviewed_employee_id or self.reviewed_by_id),
            ('AUTORIZADA POR', self.approved_employee_id or self.approved_by_id),
        )):
            left = index * (last_col + 1) // 3
            right = (index + 1) * (last_col + 1) // 3 - 1
            sheet.merge_range(row + 3, left, row + 3, right, label, footer_label)
            sheet.merge_range(row + 5, left, row + 5, right, user.display_name or '___________', footer_name)
        sheet.print_area(0, 0, row + 5, last_col)
