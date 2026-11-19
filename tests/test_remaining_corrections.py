from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRemainingCorrections(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': 'Saldos y fechas'})
        cls.contract = cls.env['hr.contract'].create({
            'name': 'Contrato de revisión', 'employee_id': cls.employee.id,
            'date_start': '2026-01-01', 'wage': 800,
            'velatek_payment_frequency': 'biweekly', 'velatek_remuneration_mode': 'fixed',
        })

    def _loan(self, amount=40, count=4, frequency='biweekly', start='2026-11-15', interval=1):
        loan = self.env['hr.employee.loan'].create({
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'loan_type_id': self.env.ref('rrhh_velatek_sv.loan_type_personal').id,
            'principal_amount': amount, 'estimated_installments': count,
            'first_payment_date': start, 'payment_frequency': frequency,
            'installment_interval': interval,
        })
        loan.action_generate_calendar()
        loan.action_approve()
        loan.action_activate()
        return loan

    def _pay(self, loan, amount):
        wizard = self.env['hr.loan.payment.wizard'].create({
            'loan_id': loan.id, 'amount': amount, 'reference': 'ABONO-TEST',
        })
        action = wizard.action_register_payment()
        if action.get('type') == 'ir.actions.act_window':
            wizard.action_register_payment()

    def test_partial_payment_updates_general_balance_immediately(self):
        loan = self._loan()
        self._pay(loan, 5)
        self.assertEqual(loan.total_paid, 5)
        self.assertEqual(loan.remaining_balance, 35)
        self.assertEqual(loan.installment_ids[0].remaining_amount, 5)
        self.assertEqual(loan.installment_ids[0].state, 'pending')
        self.assertIn('ABONO-TEST', loan.installment_ids[0].notes)
        self._pay(loan, 35)
        self.assertEqual(loan.total_paid, 40)
        self.assertEqual(loan.remaining_balance, 0)
        self.assertEqual(loan.state, 'paid')
        self.assertEqual(len(loan.installment_ids), 4)

    def test_one_cent_remainder_is_not_written_off(self):
        loan = self._loan(10, 1)
        self._pay(loan, 9.99)
        self.assertAlmostEqual(loan.remaining_balance, .01)
        self.assertEqual(loan.installment_ids.state, 'pending')
        self.assertEqual(loan.state, 'active')
        self._pay(loan, .01)
        self.assertEqual(loan.state, 'paid')

    def test_overpayment_is_rejected_without_changing_balance(self):
        loan = self._loan()
        self._pay(loan, 5)
        with self.assertRaises(ValidationError):
            self._pay(loan, 36)
        self.assertEqual(loan.remaining_balance, 35)

    def test_estimated_end_matches_real_calendar_for_every_frequency(self):
        for frequency, interval in [('biweekly', 1), ('monthly', 1), ('alternate_biweekly', 1), ('custom_interval', 3)]:
            loan = self._loan(frequency=frequency, interval=interval)
            self.assertEqual(loan.estimated_end_date, loan.installment_ids[-1].scheduled_date)
        loan = self._loan()
        self.assertEqual(str(loan.estimated_end_date), '2026-12-31')
        single = self._loan(10, 1)
        self.assertEqual(single.estimated_end_date, single.first_payment_date)

    def test_forecast_before_calendar_uses_same_date_rules(self):
        loan = self.env['hr.employee.loan'].new({
            'principal_amount': 40, 'estimated_installments': 4,
            'payment_frequency': 'biweekly', 'first_payment_date': '2026-11-15',
        })
        self.assertEqual(str(loan.estimated_end_date), '2026-12-31')

    def test_overlapping_parameters_rejected_but_adjacent_periods_allowed(self):
        models = ('hr.sv.labor.rate', 'hr.sv.social.security.parameter', 'hr.sv.pension.parameter', 'hr.sv.isr.table')
        for name in models:
            model = self.env[name]
            model.search([]).write({'active': False})
            values = {'name': 'Primera vigencia', 'date_from': '2026-01-01', 'date_to': '2026-12-31'}
            if name == 'hr.sv.labor.rate':
                values.update(code='overtime_day', value=2)
            if name == 'hr.sv.isr.table':
                values['payment_frequency'] = 'biweekly'
            model.create(values)
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                model.create(dict(values, name='Solapada', date_from='2026-12-31', date_to=False))
            second = model.create(dict(values, name='Siguiente', date_from='2027-01-01', date_to=False))
            self.assertTrue(second)

    def test_partial_consumption_protects_incident_and_allocation(self):
        incident = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'incident_type': 'sick_leave', 'date_from': '2026-11-14', 'date_to': '2026-11-18',
            'quantity_days': 5,
        })
        incident.action_submit()
        incident.action_approve()
        prep = self.env['hr.prepayroll'].create({
            'name': 'Consumo parcial', 'date_start': '2026-11-01', 'date_end': '2026-11-15',
            'payment_frequency': 'biweekly',
        })
        allocation = self.env['hr.prepayroll.incident.allocation'].create({
            'prepayroll_id': prep.id, 'incident_id': incident.id,
            'date_from': '2026-11-14', 'date_to': '2026-11-15', 'allocated_days': 2,
        })
        for state in ('loaded', 'validated', 'closed'):
            prep.state = state
        with self.assertRaises(ValidationError):
            incident.notes = 'Modificar fuente utilizada'
        with self.assertRaises(ValidationError):
            incident.unlink()
        with self.assertRaises(ValidationError):
            allocation.unlink()
        with self.assertRaises(ValidationError):
            allocation.allocated_days = 1


    def test_upgrade_refreshes_old_partial_balances_and_end_dates(self):
        from pathlib import Path
        import runpy
        loan = self._loan()
        self._pay(loan, 5)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hr_employee_loan SET total_paid=0, remaining_balance=40, estimated_end_date='2027-01-15' WHERE id=%s",
            [loan.id],
        )
        loan.invalidate_recordset()
        migration = Path(__file__).resolve().parents[1] / 'migrations' / '18.0.1.3.0' / 'post-migration.py'
        runpy.run_path(str(migration))['migrate'](self.env.cr, '18.0.1.2.0')
        loan.invalidate_recordset()
        self.assertEqual(loan.total_paid, 5)
        self.assertEqual(loan.remaining_balance, 35)
        self.assertEqual(str(loan.estimated_end_date), '2026-12-31')


    def test_night_work_variable_pay_keeps_manual_determination(self):
        for mode in ('production', 'mixed'):
            self.contract.velatek_remuneration_mode = mode
            incident = self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id, 'contract_id': self.contract.id,
                'incident_type': 'night_work', 'date': '2026-11-10', 'quantity_hours': 4,
            })
            self.assertEqual(incident.amount, 0)
            self.assertGreater(incident.night_work_hours_per_day, 0)
            self.assertIn('requiere determinación en nómina', incident.notes)


    def _payroll_with_loan(self, loan):
        return self.env['hr.payroll'].create({
            'name': 'Planilla con abono directo', 'date_start': '2026-11-01',
            'date_end': '2026-11-15', 'payment_frequency': 'biweekly',
            'line_ids': [Command.create({
                'employee_id': self.employee.id, 'contract_id': self.contract.id,
                'remuneration_mode': 'fixed', 'period_wage': 400,
                'loan_installment_ids': [Command.set(loan.installment_ids[:1].ids)],
                'loan_deduction_amount': 10,
            })],
        })

    def test_cash_partial_payment_refreshes_existing_draft_payroll(self):
        loan = self._loan()
        payroll = self._payroll_with_loan(loan)
        self._pay(loan, 5)
        payroll.action_compute()
        self.assertEqual(payroll.line_ids.loan_deduction_amount, 5)
        payroll.action_validate()
        payroll.action_close()
        loan._compute_balances()
        self.assertEqual(loan.total_paid, 10)
        self.assertEqual(loan.remaining_balance, 30)

    def test_validated_payroll_must_be_reopened_before_cash_payment(self):
        loan = self._loan()
        payroll = self._payroll_with_loan(loan)
        payroll.action_compute()
        payroll.action_validate()
        with self.assertRaises(ValidationError):
            self._pay(loan, 5)
        self.assertEqual(loan.remaining_balance, 40)
        payroll.action_draft()
        self._pay(loan, 5)
        payroll.action_compute()
        self.assertEqual(payroll.line_ids.loan_deduction_amount, 5)
