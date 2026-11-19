from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPayrollCorrections(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': 'Correcciones de planilla'})
        cls.contract = cls.env['hr.contract'].create({
            'name': 'Contrato de correcciones', 'employee_id': cls.employee.id,
            'date_start': '2026-01-01', 'wage': 1000,
            'velatek_payment_frequency': 'weekly', 'velatek_remuneration_mode': 'fixed',
            'contract_type_id': cls.env['hr.contract.type'].create({'name': 'Tiempo completo'}).id,
            'resource_calendar_id': cls.env.company.resource_calendar_id.id,
            'velatek_work_schedule_type': 'full_time', 'state': 'open',
        })

    def _payroll(self):
        return self.env['hr.payroll'].create({
            'name': 'Planilla de correcciones', 'date_start': '2026-11-01',
            'date_end': '2026-11-07', 'payment_frequency': 'weekly',
        })

    def test_closed_payroll_and_its_lines_cannot_be_deleted_or_edited(self):
        payroll = self._payroll()
        payroll.action_load_employees()
        payroll.action_compute()
        payroll.action_validate()
        payroll.action_close()
        with self.assertRaises(ValidationError):
            payroll.unlink()
        with self.assertRaises(ValidationError):
            payroll.line_ids.unlink()
        with self.assertRaises(ValidationError):
            payroll.line_ids.period_wage = 1
        with self.assertRaises(ValidationError):
            payroll.date_end = '2026-11-08'
        with self.assertRaises(ValidationError):
            self.env['hr.payroll.line'].create({'payroll_id': payroll.id, 'employee_id': self.employee.id, 'contract_id': self.contract.id})

    def test_validated_prepayroll_cannot_be_deleted(self):
        prep = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla protegida', 'date_start': '2026-11-01',
            'date_end': '2026-11-07', 'payment_frequency': 'weekly',
        })
        prep.state = 'loaded'
        prep.state = 'validated'
        with self.assertRaises(ValidationError):
            prep.unlink()
        with self.assertRaises(ValidationError):
            prep.date_end = '2026-11-08'

    def test_direct_and_prepayroll_weekly_wages_match(self):
        payroll = self._payroll()
        payroll.action_load_employees()
        prep = self.env['hr.prepayroll'].create({
            'name': 'Misma semana', 'date_start': payroll.date_start,
            'date_end': payroll.date_end, 'payment_frequency': 'weekly',
        })
        prep.action_load_employees()
        self.assertAlmostEqual(payroll.line_ids.period_wage, round(1000 * 7 / 30, 2))
        self.assertAlmostEqual(payroll.line_ids.period_wage, prep.line_ids.period_wage)

    def test_missing_expired_or_other_company_parameters_are_rejected(self):
        models = ('hr.sv.social.security.parameter', 'hr.sv.pension.parameter', 'hr.sv.isr.table')
        other_company = self.env['res.company'].create({'name': 'Compañía ajena'})
        for model_name in models:
            model = self.env[model_name]
            model.search([]).write({'active': False})
            vals = {'name': 'No aplicable', 'company_id': other_company.id, 'date_from': '2026-01-01'}
            if model_name == 'hr.sv.isr.table':
                vals['payment_frequency'] = 'weekly'
            model.create(vals)
            before = model.search_count([])
            with self.assertRaises(ValidationError):
                if model_name == 'hr.sv.isr.table':
                    model.get_active_table('2026-11-07', self.env.company, 'weekly')
                else:
                    model.get_active_parameter('2026-11-07', self.env.company)
            self.assertEqual(model.search_count([]), before)
            model.create(dict(vals, company_id=self.env.company.id, date_to='2026-10-31'))
            with self.assertRaises(ValidationError):
                if model_name == 'hr.sv.isr.table':
                    model.get_active_table('2026-11-07', self.env.company, 'weekly')
                else:
                    model.get_active_parameter('2026-11-07', self.env.company)

    def test_global_valid_parameter_is_allowed(self):
        model = self.env['hr.sv.social.security.parameter']
        model.search([]).write({'active': False})
        param = model.create({'name': 'ISSS global', 'company_id': False, 'date_from': '2026-01-01'})
        self.assertEqual(model.get_active_parameter('2026-11-07', self.env.company), param)

    def test_empty_isr_table_is_not_zero_tax(self):
        table = self.env['hr.sv.isr.table'].create({
            'name': 'Tabla incompleta', 'date_from': '2026-01-01', 'payment_frequency': 'weekly', 'active': False,
        })
        with self.assertRaises(ValidationError):
            table.calculate_isr(1000)
        with self.assertRaises(ValidationError):
            table.calculate_isr(0)

    def test_afp_has_no_ceiling_from_2023_and_keeps_explicit_historical_cap(self):
        param = self.env['hr.sv.pension.parameter'].create({
            'name': 'AFP con techo heredado', 'date_from': '2000-01-01', 'max_base': 7045.06, 'active': False,
        })
        employee, employer, base = param.calculate_afp(10000, 'monthly', ref_date='2026-11-30')
        self.assertEqual(base, 10000)
        self.assertEqual(employee, 725)
        self.assertEqual(employer, 875)
        _, _, historical_base = param.calculate_afp(10000, 'monthly', ref_date='2022-12-31')
        self.assertEqual(historical_base, 7045.06)

    def _loan(self, amount, count):
        return self.env['hr.employee.loan'].create({
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'loan_type_id': self.env.ref('rrhh_velatek_sv.loan_type_personal').id,
            'principal_amount': amount, 'estimated_installments': count, 'first_payment_date': '2026-11-15',
        })

    def test_calendar_uses_exact_number_of_positive_installments(self):
        for total, count in [(1, 3), (100, 6), (1.5, 100)]:
            loan = self._loan(total, count)
            loan.action_generate_calendar()
            self.assertEqual(len(loan.installment_ids), count)
            self.assertTrue(all(inst.amount > 0 for inst in loan.installment_ids))
            self.assertTrue(loan.currency_id.is_zero(sum(loan.installment_ids.mapped('amount')) - total))
            self.assertEqual(loan.installment_ids[-1].amount, loan.last_installment_amount)
        self.assertEqual(self._loan(1, 3)._get_calendar_amounts(), [.33, .33, .34])

    def test_zero_rounded_installment_is_rejected_without_deleting_calendar(self):
        loan = self._loan(1, 1)
        loan.action_generate_calendar()
        loan.estimated_installments = 1000
        old_ids = loan.installment_ids.ids
        with self.assertRaises(ValidationError):
            loan.action_generate_calendar()
        self.assertEqual(loan.installment_ids.ids, old_ids)

    def test_payslip_total_does_not_count_viaticos_twice(self):
        payroll = self._payroll()
        payroll.action_load_employees()
        line = payroll.line_ids
        line.viaticos_amount = 50
        line.outside_payment_amount = 10
        payroll.action_compute()
        self.assertAlmostEqual(line.total_to_settle, line.payment_amount + 10)
        # Check the actual rendered total, not only the model amount.
        from lxml import html
        view = self.env.ref('rrhh_velatek_sv.report_payslip_document')
        rendered = self.env['ir.qweb']._render(view.id, {'o': line})
        root = html.fromstring(str(rendered))
        rows = root.xpath('//tr[td[contains(., "Total a recibir")]]')
        self.assertTrue(rows)
        self.assertIn('%.2f' % line.total_to_settle, rows[0].text_content())


    def test_missing_labor_rate_is_not_invented(self):
        rates = self.env['hr.sv.labor.rate']
        rates.search([('code', '=', 'overtime_day')]).write({'active': False})
        count = rates.search_count([])
        with self.assertRaises(ValidationError):
            rates.get_active_rate('overtime_day', '2026-11-07', self.env.company)
        self.assertEqual(rates.search_count([]), count)
