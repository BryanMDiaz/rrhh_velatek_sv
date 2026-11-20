import base64
import csv
import io
import re
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..models.operational_security import MANAGER, require_group


RECALCULATION_BRACKETS = {
    6: ((3300, 5371.44, 106.20, 0.10), (5371.44, 12228.60, 360, 0.20), (12228.60, None, 1731.42, 0.30)),
    12: ((6600, 10742.86, 212.12, 0.10), (10742.86, 24457.14, 720, 0.20), (24457.14, None, 3462.86, 0.30)),
}


class HrPayrollPaymentFiscalWizard(models.TransientModel):
    _name = 'hr.payroll.payment.fiscal.wizard'
    _description = 'Registro de pago y fecha fiscal de planilla'

    payroll_id = fields.Many2one('hr.payroll', required=True)
    currency_id = fields.Many2one(related='payroll_id.currency_id')
    total_due = fields.Monetary(related='payroll_id.payment_due_total')
    fiscal_date = fields.Date(string='Fecha fiscal de pago o acreditación')
    register_payment = fields.Boolean(string='Registrar un movimiento de pago')
    payment_result = fields.Selection([('paid', 'Aplicado'), ('rejected', 'Rechazado')],
                                      string='Resultado', default='paid', required=True)
    bank_line_id = fields.Many2one('account.bank.statement.line', string='Transacción bancaria')
    rejection_reason = fields.Char(string='Motivo del rechazo')
    company_id = fields.Many2one(related='payroll_id.company_id')
    pending_amount = fields.Monetary(related='payroll_id.payment_pending_amount')
    payment_date = fields.Date(string='Fecha del pago')
    amount = fields.Monetary(string='Importe del movimiento')
    reference = fields.Char(string='Referencia de la transacción')
    proof = fields.Binary(string='Comprobante de pago')
    proof_name = fields.Char()

    @api.onchange('bank_line_id')
    def _onchange_bank_line_id(self):
        if self.bank_line_id:
            self.payment_date = self.bank_line_id.date
            self.amount = -self.bank_line_id.amount

    def action_register(self):
        self.ensure_one()
        payroll = self.payroll_id
        require_group(payroll, MANAGER)
        payroll.check_access('write')
        today = fields.Date.context_today(self)
        if payroll.is_test or payroll.state != 'closed' or (self.fiscal_date and self.fiscal_date > today):
            raise ValidationError(_('Registre la fecha fiscal real en una planilla oficial cerrada.'))
        self.env.cr.execute('SELECT id FROM hr_payroll WHERE id = %s FOR UPDATE', (payroll.id,))
        payroll.invalidate_recordset(['payment_verified_amount', 'payment_verified_by_id', 'fiscal_recognition_date'])
        values = {}
        if not self.register_payment or self.payment_result == 'paid':
            if not self.fiscal_date:
                raise ValidationError(_('Indique la fecha fiscal de pago o acreditación.'))
            if payroll.fiscal_recognition_date and payroll.fiscal_recognition_date != self.fiscal_date:
                raise ValidationError(_('La fecha fiscal ya está registrada. Revise la corrección con Contabilidad.'))
            values['fiscal_recognition_date'] = self.fiscal_date
        if self.register_payment:
            values.update(self._register_payment_event(payroll, today))
        payroll._write_operational_values(values, from_workflow=True)
        return {'type': 'ir.actions.act_window_close'}

    def _register_payment_event(self, payroll, today):
        reference = ' '.join((self.reference or '').split())
        if not self.payment_date or self.payment_date > today or not reference or not self.proof or self.amount <= 0:
            raise ValidationError(_('Indique la fecha real, el importe, la referencia y el comprobante.'))
        rejected = self.payment_result == 'rejected'
        line = self.bank_line_id
        if rejected:
            if line or not (self.rejection_reason or '').strip():
                raise ValidationError(_('Documente el motivo. Una orden rechazada no se aplica como salida de banco.'))
        else:
            if payroll.payment_verified_by_id or self.payment_date < self.fiscal_date:
                raise ValidationError(_('Revise la fecha fiscal y el saldo de esta planilla.'))
            if not line:
                raise ValidationError(_('Seleccione la transacción de egreso de Contabilidad.'))
            line.check_access('read')
            currency = line.journal_id.currency_id or line.company_id.currency_id
            if (line.company_id != payroll.company_id or line.move_id.state != 'posted' or line.date != self.payment_date
                    or currency != payroll.currency_id or line.amount >= 0
                    or payroll.currency_id.compare_amounts(-line.amount, self.amount)):
                raise ValidationError(_('El movimiento contabilizado debe coincidir en compañía, moneda, fecha e importe del egreso.'))
            if not payroll.line_ids or payroll.currency_id.compare_amounts(self.amount, payroll.payment_pending_amount) > 0:
                raise ValidationError(_('El pago supera el saldo pendiente de la planilla.'))

        self.env['hr.payroll.payment.event'].sudo().create({
            'payroll_id': payroll.id, 'payment_date': self.payment_date, 'state': self.payment_result,
            'amount': self.amount, 'reference': reference, 'normalized_reference': reference.casefold(),
            'bank_line_id': line.id, 'rejection_reason': self.rejection_reason if rejected else False,
            'proof': self.proof, 'proof_name': self.proof_name, 'recorded_by': self.env.uid,
        })
        if rejected:
            return {}
        total = payroll.currency_id.round(payroll.payment_verified_amount + self.amount)
        return {
            'payment_verified_amount': total, 'payment_date': self.payment_date, 'payment_reference': reference,
            'payment_proof': self.proof, 'payment_proof_name': self.proof_name,
            'payment_verified_by_id': self.env.uid if not payroll.currency_id.compare_amounts(total, payroll.payment_due_total) else False,
        }


class HrPayrollFilingWizard(models.TransientModel):
    _name = 'hr.payroll.filing.wizard'
    _description = 'Exportaciones y control fiscal de planilla'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    report_type = fields.Selection([
        ('spu', 'Planilla Única ordinaria (CSV)'),
        ('f14', 'F14 mensual (papel de trabajo)'),
        ('f910', 'F910 anual (papel de trabajo)'),
        ('f14_csv', 'F14 — archivo de anexo de nómina'),
        ('f910_csv', 'F910 — archivo anual de nómina'),
        ('recalc', 'Recálculo ISR junio o diciembre (control)'),
    ], required=True, default='spu', string='Salida')
    period = fields.Date(required=True, default=fields.Date.context_today, string='Mes y año')
    employee_id = fields.Many2one('hr.employee', string='Empleado para recálculo individual')
    prior_employer_base = fields.Monetary(string='Base gravada de otro patrono')
    prior_employer_withholding = fields.Monetary(string='Retención acreditada de otro patrono')
    prior_reference = fields.Char(string='Referencia de la constancia del otro patrono')
    currency_id = fields.Many2one(related='company_id.currency_id')
    download_file = fields.Binary(readonly=True)
    fiscal_domestic_confirmed = fields.Boolean(string='Domicilio y origen salvadoreños verificados para todo el archivo')
    f14_classification = fields.Char(
        string='Códigos F14 de operación, clasificación, sector y costo',
        help='Ingrese los cuatro códigos de las columnas S a V en orden, o 9999 si corresponde según Hacienda.',
    )
    special_isr_ids = fields.One2many('hr.payroll.isr.review.line', 'wizard_id', string='Componentes especiales verificados')

    def _official_payrolls(self):
        return self.env['hr.payroll'].search([
            ('company_id', '=', self.company_id.id), ('state', '=', 'closed'), ('is_test', '=', False),
        ])

    def _fiscal_payrolls(self, start, end):
        payrolls = self._official_payrolls()
        pending = payrolls.filtered(lambda p: not p.fiscal_recognition_date and start <= p.date_end <= end)
        if pending:
            raise ValidationError(_(
                'Registre la fecha fiscal de pago o acreditación antes de preparar el reporte: %s.',
                ', '.join(pending.mapped('name')),
            ))
        return payrolls.filtered(lambda p: p.fiscal_recognition_date and start <= p.fiscal_recognition_date <= end)

    @staticmethod
    def _amount(value):
        return f'{value:.2f}'

    @staticmethod
    def _digits(value, sizes, label):
        stripped = re.sub(r'[\s-]', '', value or '')
        if not stripped.isascii() or not stripped.isdigit() or len(stripped) not in sizes:
            raise ValidationError(_('%s: la identificación falta o tiene un formato inválido.', label))
        return stripped

    def _spu_rows(self, start, end):
        payrolls = self._official_payrolls().filtered(lambda p: p.date_start <= end and p.date_end >= start
            and p.payroll_type not in ('professional_services', 'q25'))
        if not payrolls or any(p.payroll_type != 'regular' or p.payment_frequency not in ('monthly', 'biweekly')
                               or p.date_start < start or p.date_end > end for p in payrolls):
            raise ValidationError(_('El CSV automático cubre planillas ordinarias mensuales o quincenales del mismo mes.'))
        employer = self._digits(self.company_id.vat, (9, 14), 'NIT/DUI patronal')
        employer_number = self._digits(self.company_id.sv_spu_employer_number, (9,), 'Número patronal ISSS')
        rows = []
        all_lines = payrolls.line_ids
        allocations = self.env['hr.prepayroll.incident.allocation'].search([
            ('prepayroll_line_id', 'in', all_lines.prepayroll_line_id.ids),
            ('incident_id.incident_type', '=', 'sick_leave'),
        ]).grouped('prepayroll_line_id')
        for employee, employee_lines in all_lines.grouped('employee_id').items():
            lines = employee_lines.sorted(lambda l: l.payroll_id.date_start)
            contract = lines.contract_id
            periods = [line.payroll_id for line in lines]
            if len(contract) != 1 or not contract.date_start:
                raise ValidationError(_('%s tiene más de un contrato o falta su fecha de inicio. Revise el archivo SPU.', employee.display_name))
            first = max(start, contract.date_start)
            last = min(end, contract.date_end or end)
            if (len(periods) != len(lines) or periods[0].date_start > first or periods[-1].date_end < last
                    or first > last or any(prev.date_end + timedelta(days=1) != current.date_start
                                        for prev, current in zip(periods, periods[1:]))):
                raise ValidationError(_('%s tiene períodos incompletos dentro de la vigencia del contrato.', employee.display_name))
            if (contract.velatek_remuneration_mode != 'fixed' or contract.velatek_work_schedule_type != 'full_time'
                    or contract.velatek_rotating_shift or contract.velatek_terms_ids.filtered(lambda t: first < t.effective_from <= last)
                    or any(line.absence_days or line.absence_hours or line.vacation_amount
                           or line.paid_leave_days or line.paid_leave_hours for line in lines)):
                raise ValidationError(_('%s tiene una combinación que requiere revisión específica en SPU.', employee.display_name))
            center = contract.velatek_work_location_id or employee.work_location_id
            afp = employee.velatek_afp_id
            code = afp.sv_spu_code or {'CONFIA': 'COF', 'CRECER': 'MAX'}.get(afp.code)
            names = (employee.velatek_nombre_pila or '').split(maxsplit=1)
            surname = (employee.velatek_primer_apellido or '').strip()
            if not names or not surname or code not in ('COF', 'MAX'):
                raise ValidationError(_('%s requiere nombres, apellido e institución previsional compatibles con SPU.', employee.display_name))
            name_fields = [names[0], names[1] if len(names) > 1 else '', surname,
                           employee.velatek_segundo_apellido or '', employee.velatek_apellido_casada or '']
            if any(len(name) > 40 or '\n' in name or '\r' in name for name in name_fields):
                raise ValidationError(_('%s tiene un nombre o apellido con formato inválido para SPU.', employee.display_name))
            worker_days = (last - first).days + 1
            sick_days = sum(lines.mapped('sick_leave_days'))
            subsidy = sum(lines.mapped('sick_isss_amount'))
            subsidy_days = 0
            if sick_days or subsidy or any(lines.mapped('sick_leave_hours')):
                detail = self.env['hr.prepayroll.incident.allocation']
                for preline in lines.prepayroll_line_id:
                    detail |= allocations.get(preline, detail.browse())
                if (not detail or any(lines.mapped('sick_leave_hours')) or sick_days != int(sick_days)
                        or sum(detail.mapped('allocated_days')) != sick_days
                        or any(row.allocated_days != (row.date_to - row.date_from).days + 1 for row in detail)
                        or any(lines.mapped('sick_employer_amount'))):
                    raise ValidationError(_('%s requiere revisar tramos, horas o complementos patronales de incapacidad.', employee.display_name))
                subsidy_days = int(sum(detail.filtered(
                    lambda row: row.incident_id.sick_isss_daily > 0
                ).mapped('allocated_days')))
                if bool(subsidy) != bool(subsidy_days):
                    raise ValidationError(_('%s requiere conciliar los días y el importe de subsidio del ISSS.', employee.display_name))

            wage = sum(lines.mapped('period_wage')) - sum(lines.mapped('sick_ordinary_amount'))
            isss_base = sum(lines.mapped('isss_taxable_base'))
            afp_base = sum(lines.mapped('afp_taxable_base'))
            extra = self.currency_id.round(isss_base - wage)
            days = worker_days - int(sick_days)
            # A full-month subsidy can be reported alone when the employer reports no remuneration.
            if (extra < 0 or self.currency_id.compare_amounts(isss_base, afp_base)
                    or (days > 0 and wage <= 0) or (days <= 0 and (subsidy_days != worker_days or wage or extra))
                    or sick_days < 0 or (subsidy and subsidy_days != sick_days)):
                raise ValidationError(_('%s tiene bases o días de cotización que requieren revisión específica.', employee.display_name))
            hours = contract._velatek_terms_on(first)[2]
            if not 0 < hours <= 8 or hours != int(hours):
                raise ValidationError(_('%s requiere revisar las horas ordinarias para SPU.', employee.display_name))
            observations = []
            if contract.date_end and start <= contract.date_end <= end:
                observations.append('07')
            if start <= contract.date_start <= end:
                observations.append('08')
            if sick_days:
                observations.append('06')
            if extra:
                observations.append('02')
            if len(observations) > 2 or (not days and any(code in observations for code in ('07', '08'))):
                raise ValidationError(_('%s combina más novedades de las que admite una línea SPU; revise su declaración.', employee.display_name))
            identity = [
                employer, employer_number, start.strftime('%m%Y'),
                self._digits(center.sv_spu_work_center, (3,), 'Centro de trabajo'),
                self._digits(employee.velatek_dui, (9,), employee.display_name), '01',
                self._digits(employee.velatek_isss, (9,), employee.display_name), code, *name_fields,
            ]
            if days > 0:
                rows.append(identity + [
                    self._amount(wage), self._amount(extra), '0.00', str(days), str(int(hours)), '0',
                    *(observations + ['00'] * (2 - len(observations))),
                ])
            if subsidy_days:
                rows.append(identity + [self._amount(subsidy), '0.00', '0.00', str(subsidy_days), str(int(hours)), '0', '12', '00'])
        if not rows:
            raise ValidationError(_('No hay empleados aptos para generar el CSV.'))
        if len({(row[4], '12' in row[19:]) for row in rows}) != len(rows):
            raise ValidationError(_('Hay documentos de trabajador duplicados en el archivo.'))
        return rows

    def _fiscal_workpaper(self, start, end):
        totals = defaultdict(lambda: [0.0, 0.0, 0.0])
        kinds = {'professional_services': 'honorarios', 'q25': 'Quincena 25 — ingreso no gravado'}
        for p in self._fiscal_payrolls(start, end):
            kind = kinds.get(p.payroll_type, 'remuneración en planilla')
            for line in p.line_ids:
                key = (line.employee_id, kind)
                values = totals[key]
                values[0] += line.total_earned
                values[1] += self._line_net_tax_base(line)
                values[2] += line.income_tax_amount
        rows = [['DUI/NIT', 'Empleado', 'Concepto de origen', 'Devengado Odoo', 'Base ISR neta Odoo', 'Retención acumulada Odoo']]
        for (employee, kind), values in totals.items():
            rows.append([employee.velatek_nit or employee.velatek_dui or '', employee.name, kind,
                         *(self._amount(value) for value in values)])
        return rows

    def _hacienda_rows(self, start, end):
        annual = self.report_type == 'f910_csv'
        if not self.fiscal_domestic_confirmed or end.year < 2025:
            raise ValidationError(_('Confirme el domicilio y origen de los perceptores para el archivo de nómina desde 2025.'))
        classification = (self.f14_classification or '').strip()
        if not annual and classification != '9999' and not re.fullmatch(r'[1-4][12][1-4][1-8]', classification):
            raise ValidationError(_('Verifique los cuatro códigos F14 de las columnas S, T, U y V.'))
        buckets = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        for payroll in self._fiscal_payrolls(start, end):
            for line in payroll.line_ids:
                employee = line.employee_id
                fees = payroll.payroll_type == 'professional_services'
                ordinary = payroll.payroll_type in ('regular', 'vacation')
                if (not (ordinary or fees) or line.bonus_amount
                        or (fees and line.contract_id.sv_honorarium_isr_profile != 'domestic_individual')
                        or (ordinary and (not line.isss_employee_amount or not line.afp_employee_amount))):
                    raise ValidationError(_('%(employee)s en %(payroll)s requiere una clasificación fiscal especial. Revise el papel de trabajo y el anexo aplicable.',
                                            employee=employee.display_name, payroll=payroll.display_name))
                fields_by_month = buckets[(employee, fees)][payroll.fiscal_recognition_date.month]
                for idx, amount in enumerate((line.isr_taxable_base, line.income_tax_amount,
                                              line.afp_employee_amount, line.isss_employee_amount)):
                    fields_by_month[idx] += amount

        rows = []
        identifiers = set()
        for (employee, fees), months in buckets.items():
            name = ' '.join(filter(None, (employee.velatek_primer_apellido, employee.velatek_segundo_apellido,
                                         employee.velatek_apellido_casada, employee.velatek_nombre_pila))).upper()
            name = ' '.join(name.split())
            if (not employee.velatek_nombre_pila or not employee.velatek_primer_apellido or len(name) > (40 if annual else 100)
                    or any(character in name for character in ',;"') or name[0] in '=+-@'):
                raise ValidationError(_('%s requiere apellidos y nombres con el formato de Hacienda.', employee.display_name))
            if annual:
                tax_id = employee.velatek_nit or (employee.velatek_dui if employee.velatek_is_homologated else False)
                nit = self._digits(tax_id, (9, 14) if employee.velatek_is_homologated else (14,), employee.display_name)
                dui = ''
            else:
                dui = self._digits(employee.velatek_dui, (9,), employee.display_name) if employee.velatek_dui else ''
                nit = self._digits(employee.velatek_nit, (14,), employee.display_name) if not dui else ''
            totals = [self.currency_id.round(sum(month[idx] for month in months.values())) for idx in range(4)]
            gross, withheld, afp, isss = totals
            code = '11' if fees else '01' if withheld else '60'
            key = (nit or dui, code)
            if key in identifiers or gross <= 0 or min(totals) < 0:
                raise ValidationError(_('%s tiene identificación duplicada o importes fiscales incompatibles.', employee.display_name))
            identifiers.add(key)
            if annual:
                def cents(amount):
                    value = str(int(round(amount * 100)))
                    if not value.isdecimal() or len(value) > 15:
                        raise ValidationError(_('Revise los importes anuales y mensuales admitidos por F910.'))
                    return value

                monthly = [cents(value) for month in range(1, 13) for value in months.get(month, [0.0] * 4)[:3]]
                rows.append([name, nit, code, cents(gross), '0', cents(withheld), '0', '0', cents(isss), cents(afp),
                             '0', '0', '0', '0', *monthly, str(end.year)])
            else:
                money = [self._amount(value) for value in (gross, 0, withheld, 0, 0, afp, isss)]
                if any(len(value) > width for value, width in zip(money, (12, 12, 12, 6, 12, 6, 5))):
                    raise ValidationError(_('%s supera la longitud de los importes admitidos por F14.', employee.display_name))
                rows.append(['1', '9300', name, nit, dui, code, *money, *(['0'] * 5), *classification, end.strftime('%m%Y')])
        return rows

    @staticmethod
    def _line_net_tax_base(line):
        return max(0.0, line.isr_taxable_base - line.isss_employee_amount - line.afp_employee_amount)

    def _recalc_rows(self, start, end):
        brackets = RECALCULATION_BRACKETS.get(end.month)
        if not brackets or end.year < 2025:
            raise ValidationError(_('Seleccione junio o diciembre desde el ejercicio 2025.'))
        if (self.prior_employer_base or self.prior_employer_withholding) and (
            not self.employee_id or not self.prior_reference or min(self.prior_employer_base, self.prior_employer_withholding) < 0
        ):
            raise ValidationError(_('Para acumular otro patrono, indique empleado y constancia con importes no negativos.'))
        totals = defaultdict(lambda: [0.0, 0.0, 0.0])
        reviews = {review.payroll_line_id.id: review for review in self.special_isr_ids}
        ordinary = ('regular', 'adjustment', 'retroactive', 'complementary', 'vacation')
        for payroll in self._fiscal_payrolls(start, end).filtered(lambda p: p.payroll_type not in ('professional_services', 'q25')):
            for line in payroll.line_ids:
                if self.employee_id and line.employee_id != self.employee_id:
                    continue
                review = reviews.pop(line.id, False)
                if payroll.payroll_type not in ordinary and not review:
                    raise ValidationError(_('%(employee)s en %(payroll)s requiere una base y retención verificadas para el recálculo.',
                                            employee=line.employee_id.display_name, payroll=payroll.display_name))
                values = totals[line.employee_id]
                values[0] += review.taxable_net if review else self._line_net_tax_base(line)
                period_index = 2 if payroll.fiscal_recognition_date.month == end.month else 1
                values[period_index] += review.withholding if review else line.income_tax_amount
        if reviews:
            raise ValidationError(_('Hay componentes verificados que no pertenecen al período o al empleado del recálculo.'))
        if self.employee_id and self.employee_id not in totals:
            raise ValidationError(_('El empleado no tiene remuneraciones registradas para el recálculo.'))
        rows = [['Empleado', 'Base acumulada', 'ISR acumulado según tabla', 'ISR meses anteriores',
                 'ISR que corresponde al mes', 'ISR registrado en el mes', 'Saldo pendiente de retener', 'Constancia anterior']]
        for employee, (base, previous, current) in totals.items():
            base = self.currency_id.round(base + self.prior_employer_base)
            previous += self.prior_employer_withholding
            tax = next((self.currency_id.round(fee + (base - excess) * rate)
                        for excess, maximum, fee, rate in brackets if base > excess and (not maximum or base <= maximum)), 0.0)
            due = max(0.0, tax - previous)
            rows.append([employee.name, *(self._amount(value) for value in (base, tax, previous, due, current, max(0.0, due - current))),
                         self.prior_reference or ''])
        return rows

    def action_download(self):
        self.ensure_one()
        require_group(self, MANAGER)
        if self.company_id not in self.env.companies or self.currency_id != self.env.ref('base.USD'):
            raise ValidationError(_('Seleccione una compañía habilitada con nómina en dólares estadounidenses.'))
        start = self.period.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if self.report_type == 'spu':
            rows = self._spu_rows(start, end)
        elif self.report_type in ('f14_csv', 'f910_csv'):
            if self.report_type == 'f910_csv':
                start, end = date(start.year, 1, 1), date(start.year, 12, 31)
            rows = self._hacienda_rows(start, end)
        elif self.report_type == 'recalc':
            rows = self._recalc_rows(date(end.year, 1, 1), end)
        else:
            if self.report_type == 'f910':
                start, end = date(start.year, 1, 1), date(start.year, 12, 31)
            rows = self._fiscal_workpaper(start, end)
        headerless = self.report_type in ('spu', 'f14_csv', 'f910_csv')
        if len(rows) <= (0 if headerless else 1):
            raise ValidationError(_('No hay información fiscal para el período seleccionado.'))
        stream = io.StringIO(newline='')
        csv.writer(stream).writerows(rows)
        encoding = 'utf-8' if headerless else 'utf-8-sig'
        self.download_file = base64.b64encode(stream.getvalue().encode(encoding))
        prefix = {'spu': 'SPU', 'f14_csv': 'F14-nomina', 'f910_csv': 'F910-nomina'}.get(
            self.report_type, f'control-{self.report_type}',
        )
        filename = f'{prefix}-{end:%Y-%m}.csv'
        return {
            'type': 'ir.actions.act_url', 'target': 'download',
            'url': f'/web/content/{self._name}/{self.id}/download_file/{quote(filename)}?download=1',
        }


class HrPayrollIsrReviewLine(models.TransientModel):
    _name = 'hr.payroll.isr.review.line'
    _description = 'Base y retención verificadas para el recálculo de ISR'

    wizard_id = fields.Many2one('hr.payroll.filing.wizard', required=True, ondelete='cascade')
    payroll_line_id = fields.Many2one('hr.payroll.line', required=True, string='Línea de planilla')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    taxable_net = fields.Monetary(string='Base gravada neta', required=True, default=0)
    withholding = fields.Monetary(string='ISR retenido aplicable', required=True, default=0)
    reference = fields.Char(string='Respaldo fiscal', required=True)

    @api.constrains('reference', 'taxable_net', 'withholding')
    def _check_review(self):
        for review in self:
            if not (review.reference or '').strip() or review.withholding > review.taxable_net:
                raise ValidationError(_('Documente la base fiscal y verifique que la retención no la exceda.'))

    _sql_constraints = [
        ('unique_review', 'unique(wizard_id, payroll_line_id)', 'La línea ya tiene una revisión fiscal.'),
        ('nonnegative_amounts', 'check(taxable_net >= 0 and withholding >= 0)', 'Los importes no pueden ser negativos.'),
    ]
