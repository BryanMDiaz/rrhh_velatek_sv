import base64

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalaryAdvance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': 'Anticipo quincenal'})
        cls.contract = cls.env['hr.contract'].create({
            'name': 'Salario 408.50', 'employee_id': cls.employee.id,
            'date_start': '2026-01-01', 'wage': 408.50,
            'velatek_payment_frequency': 'biweekly', 'velatek_remuneration_mode': 'fixed',
        })
        cls.prepayroll = cls.env['hr.prepayroll'].create({
            'name': 'Primera quincena noviembre', 'date_start': '2026-11-01',
            'date_end': '2026-11-15', 'payment_frequency': 'biweekly',
        })
        cls.source_line = cls.env['hr.prepayroll.line'].create({
            'prepayroll_id': cls.prepayroll.id, 'employee_id': cls.employee.id,
            'contract_id': cls.contract.id, 'remuneration_mode': 'fixed',
            'base_wage': 408.50, 'period_wage': 204.25, 'days_to_pay': 15,
        })
        cls.loan_type = cls.env.ref('rrhh_velatek_sv.loan_type_anticipo')

    def _advance(self):
        return self.env['hr.employee.loan'].create({
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'loan_type_id': self.loan_type.id, 'principal_amount': 0,
            'advance_prepayroll_id': self.prepayroll.id, 'first_payment_date': '2026-11-15',
        })

    def _deliver(self, advance):
        advance.action_calculate_salary_advance()
        advance.signed_contract_document = base64.b64encode(b'%PDF-1.4 signed request')
        action = advance.action_submit()
        if isinstance(action, dict):
            wizard = self.env['hr.loan.additional.wizard'].with_context(action['context']).create({})
            wizard.action_confirm()
        advance.action_approve()
        advance.advance_payment_reference = 'TRANSFER-TEST-1'
        advance.action_activate()

    def _payroll(self, period_wage=204.25):
        values = self.source_line._get_payroll_line_values()
        values['period_wage'] = period_wage
        return self.env['hr.payroll'].create({
            'name': 'Planilla definitiva', 'date_start': '2026-11-01', 'date_end': '2026-11-15',
            'payment_frequency': 'biweekly', 'line_ids': [Command.create(values)],
        })

    def test_full_flow_legal_bases_unchanged_and_no_second_payment(self):
        advance = self._advance()
        self._deliver(advance)
        self.assertAlmostEqual(advance.advance_gross, 204.25)
        self.assertAlmostEqual(advance.advance_isss, 6.13)
        self.assertAlmostEqual(advance.advance_afp, 14.81)
        self.assertAlmostEqual(advance.advance_isr, 0)
        self.assertAlmostEqual(advance.advance_net, 183.31)
        payroll = self._payroll()
        payroll.action_compute()
        line = payroll.line_ids
        self.assertAlmostEqual(line.salary_advance_amount, 183.31)
        self.assertAlmostEqual(line.payment_amount, 0)
        self.assertAlmostEqual(line.isss_taxable_base, 204.25)
        self.assertAlmostEqual(line.total_deductions, 20.94)
        payroll.action_validate()
        payroll.action_close()
        self.assertEqual(advance.state, 'paid')
        self.assertTrue(advance.currency_id.is_zero(advance.remaining_balance))
        self.assertEqual(advance.advance_settled_payroll_id, payroll)
        self.assertEqual(advance.installment_ids.payroll_id, payroll)

    def test_partial_period_is_rejected(self):
        self.prepayroll.date_start = '2026-11-02'
        with self.assertRaises(ValidationError):
            self._advance().action_calculate_salary_advance()

    def test_draft_or_approved_request_is_not_paid_money(self):
        advance = self._advance()
        advance.action_calculate_salary_advance()
        payroll = self._payroll()
        payroll.action_compute()
        self.assertEqual(payroll.line_ids.salary_advance_amount, 0)
        self.assertAlmostEqual(payroll.line_ids.payment_amount, 183.31)

    def test_duplicate_period_after_delivery_is_rejected(self):
        self._deliver(self._advance())
        with self.assertRaises(ValidationError):
            self._advance().action_calculate_salary_advance()

    def test_source_changes_require_a_new_calculation(self):
        advance = self._advance()
        advance.action_calculate_salary_advance()
        advance.signed_contract_document = base64.b64encode(b'%PDF-1.4')
        self.source_line.period_wage = 200
        with self.assertRaises(ValidationError):
            advance.action_submit()

    def test_delivery_requires_payment_reference(self):
        advance = self._advance()
        advance.action_calculate_salary_advance()
        advance.signed_contract_document = base64.b64encode(b'%PDF-1.4')
        action = advance.action_submit()
        if isinstance(action, dict):
            wizard = self.env['hr.loan.additional.wizard'].with_context(action['context']).create({})
            wizard.action_confirm()
        advance.action_approve()
        with self.assertRaises(ValidationError):
            advance.action_activate()

    def test_overpayment_is_not_carried_to_another_period(self):
        advance = self._advance()
        self._deliver(advance)
        payroll = self._payroll(period_wage=150)
        payroll.action_compute()
        payroll.action_validate()
        with self.assertRaises(ValidationError):
            payroll.action_close()
        self.assertEqual(advance.state, 'active')
        self.assertEqual(advance.installment_ids.paid_amount, 0)

    def test_delivered_advance_cannot_be_changed_or_cancelled(self):
        advance = self._advance()
        self._deliver(advance)
        with self.assertRaises(ValidationError):
            advance.principal_amount = 1
        with self.assertRaises(ValidationError):
            advance.action_cancel()
        with self.assertRaises(ValidationError):
            advance.unlink()

    def test_different_quincena_is_not_reduced(self):
        self._deliver(self._advance())
        payroll = self.env['hr.payroll'].create({
            'name': 'Quincena dos', 'date_start': '2026-11-16', 'date_end': '2026-11-30',
            'payment_frequency': 'biweekly', 'line_ids': [Command.create(self.source_line._get_payroll_line_values())],
        })
        payroll.action_compute()
        self.assertEqual(payroll.line_ids.salary_advance_amount, 0)
        self.assertAlmostEqual(payroll.line_ids.payment_amount, 183.31)

    def test_same_advance_cannot_settle_in_two_payrolls(self):
        self._deliver(self._advance())
        payroll = self._payroll()
        payroll.action_compute()
        payroll.action_validate()
        payroll.action_close()
        second = self._payroll()
        with self.assertRaises(ValidationError):
            second.action_compute()

    def test_existing_loan_is_included_in_net_but_advance_is_not_a_loan_deduction(self):
        self.env['hr.employee.loan'].create({
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'loan_type_id': self.env.ref('rrhh_velatek_sv.loan_type_personal').id,
            'principal_amount': 10, 'first_payment_date': '2026-11-15', 'state': 'active',
            'installment_ids': [Command.create({'sequence': 1, 'scheduled_date': '2026-11-15', 'amount': 10})],
        })
        advance = self._advance()
        self._deliver(advance)
        self.assertAlmostEqual(advance.advance_net, 173.31)
        self.assertAlmostEqual(advance.advance_other_deductions, 10)
        payroll = self._payroll()
        payroll.action_compute()
        self.assertAlmostEqual(payroll.line_ids.loan_deduction_amount, 10)
        self.assertAlmostEqual(payroll.line_ids.salary_advance_amount, 173.31)
        self.assertAlmostEqual(payroll.line_ids.payment_amount, 0)

    def test_positive_difference_remains_payable(self):
        self._deliver(self._advance())
        payroll = self._payroll(period_wage=224.25)
        payroll.action_compute()
        self.assertGreater(payroll.line_ids.payment_amount, 0)
        self.assertAlmostEqual(payroll.line_ids.salary_advance_amount, 183.31)
        self.assertAlmostEqual(payroll.line_ids.payment_amount,
                               payroll.line_ids.net_before_advance - 183.31)

    def test_authorization_report_renders_period_and_net(self):
        advance = self._advance()
        advance.action_calculate_salary_advance()
        content, _ = self.env['ir.actions.report']._render_qweb_html(
            'rrhh_velatek_sv.action_report_salary_advance', [advance.id],
        )
        self.assertIn(b'183.31', content)
        self.assertIn(advance.name.encode(), content)

    def test_period_snapshot_does_not_move_after_delivery(self):
        advance = self._advance()
        self._deliver(advance)
        with self.assertRaises(ValidationError):
            self.prepayroll.date_end = '2026-11-14'
        self.assertEqual(str(advance.advance_date_end), '2026-11-15')


    def test_real_prepayroll_loading_recognizes_delivered_advance(self):
        self._deliver(self._advance())
        for state in ('loaded', 'validated', 'closed'):
            self.prepayroll.state = state
        payroll = self.env['hr.payroll'].create({
            'name': 'Desde preplanilla', 'prepayroll_id': self.prepayroll.id,
            'date_start': '2026-11-01', 'date_end': '2026-11-15', 'payment_frequency': 'biweekly',
        })
        payroll.action_load_from_prepayroll()
        self.assertAlmostEqual(payroll.line_ids.salary_advance_amount, 183.31)
        self.assertAlmostEqual(payroll.line_ids.payment_amount, 0)
        self.assertEqual(payroll.line_ids.loan_deduction_amount, 0)
