import time
from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestOperationalAuthorization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.profiles = {
            # Contract access remains a separate, existing prerequisite. This
            # group does not grant the operational Responsible/Manager roles.
            name: new_test_user(
                cls.env, login='rrhh_auth_' + name,
                groups=group + (',hr_contract.group_hr_contract_manager' if name != 'internal' else ''),
            )
            for name, group in {
                'internal': 'base.group_user',
                'user': 'hr.group_hr_user',
                'responsible': 'rrhh_velatek_sv.group_hr_responsible',
                'manager': 'hr.group_hr_manager',
            }.items()
        }
        cls.employee = cls.env['hr.employee'].create({'name': 'Operational authorization'})
        cls.contract_type = cls.env['hr.contract.type'].create({'name': 'Full Time'})
        cls.calendar = cls.env['resource.calendar'].create({'name': 'Standard Calendar'})
        cls.contract = cls.env['hr.contract'].create({
            'name': 'Operational authorization',
            'employee_id': cls.employee.id,
            'date_start': '2026-01-01',
            'wage': 1000,
            'contract_type_id': cls.contract_type.id,
            'resource_calendar_id': cls.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'mixed',
            'velatek_production_unit': 'unit',
            'state': 'open',
        })

    def operational_record(self, model, state='draft'):
        vals = {
            'employee_id': self.employee.id, 'contract_id': self.contract.id,
            'date': '2026-09-01',
        }
        if model == 'hr.employee.production':
            vals.update(quantity=2, rate=10, unit='unit', state='draft')
            rec = self.env[model].create(vals)
        else:
            vals.update(incident_type='bonus', amount=20, state='draft')
            rec = self.env[model].create(vals)

        if state == 'to_approve':
            rec.action_submit()
        elif state in ('approved', 'processed'):
            rec.action_submit()
            rec.action_approve()
            if state == 'processed':
                payroll = self.env['hr.prepayroll'].search([('state', '=', 'closed')], limit=1)
                if not payroll:
                    payroll = self.env['hr.prepayroll'].create({
                        'name': 'Dummy Prepayroll Closed Helper',
                        'date_start': '2020-03-01',
                        'date_end': '2020-03-31',
                        'payment_frequency': 'monthly',
                        'state': 'closed',
                    })
                rec.sudo().write({
                    'state': 'processed',
                    'processed_prepayroll_id': payroll.id,
                })
        elif state == 'cancelled':
            rec.action_cancel()
        return rec

    def test_incident_period_can_be_monthly_or_biweekly(self):
        incident = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-10',
            'incident_type': 'bonus',
            'amount': 20,
        })
        self.assertEqual(incident.period_name, 'Septiembre 2026')

        self.contract.velatek_payment_frequency = 'biweekly'
        biweekly = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-10',
            'incident_type': 'bonus',
            'amount': 20,
        })
        self.assertEqual(biweekly.period_scope, 'biweekly')
        self.assertEqual(biweekly.period_name, 'Quincena 1 - Septiembre 2026')
        biweekly.date = '2026-09-29'
        self.assertEqual(biweekly.period_name, 'Quincena 2 - Septiembre 2026')
        self.contract.velatek_payment_frequency = 'monthly'

    def test_overtime_requires_modality_and_calculates_amount(self):
        self.contract.write({'velatek_remuneration_mode': 'fixed', 'wage': 1000})
        base_vals = {
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-01',
            'incident_type': 'overtime',
            'quantity_hours': 2,
        }
        with self.assertRaisesRegex(ValidationError, 'diurnas o nocturnas'):
            self.env['hr.employee.incident'].create({**base_vals, 'amount': 20})

        daytime = self.env['hr.employee.incident'].create({
            **base_vals,
            'overtime_type': 'daytime',
        })
        self.assertAlmostEqual(daytime.overtime_multiplier, 2.0)
        self.assertAlmostEqual(daytime.amount, 2 * (1000 / 30 / 8) * 2.0, places=2)

        incident = self.env['hr.employee.incident'].create({
            **base_vals,
            'overtime_type': 'nighttime',
        })
        self.assertEqual(incident.overtime_type, 'nighttime')
        self.assertAlmostEqual(incident.overtime_base_wage, 1000)
        self.assertAlmostEqual(incident.overtime_hours_per_day, 8)
        self.assertAlmostEqual(incident.overtime_hourly_rate, 1000 / 30 / 8, places=2)
        self.assertAlmostEqual(incident.overtime_multiplier, 2.5)
        self.assertAlmostEqual(incident.amount, 2 * (1000 / 30 / 8) * 2.5, places=2)

        # The incident stores a historical snapshot: changing the contract later
        # must not silently recalculate an already registered overtime amount.
        original_amount = incident.amount
        self.contract.wage = 1200
        self.assertAlmostEqual(incident.amount, original_amount)
        self.contract.wage = 1000

    def test_unpaid_leave_calculates_historical_discount(self):
        incident = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-02',
            'date_from': '2026-09-02',
            'date_to': '2026-09-02',
            'incident_type': 'unpaid_leave',
            'quantity_days': 1,
            'quantity_hours': 2,
        })
        daily_rate = 1000 / 30
        hourly_rate = daily_rate / 8
        self.assertAlmostEqual(incident.absence_base_wage, 1000)
        self.assertAlmostEqual(incident.absence_hours_per_day, 8)
        self.assertAlmostEqual(incident.absence_total_hours, 10)
        self.assertAlmostEqual(incident.absence_daily_rate, daily_rate, places=2)
        self.assertAlmostEqual(incident.absence_hourly_rate, hourly_rate, places=2)
        self.assertAlmostEqual(incident.amount, daily_rate + (2 * hourly_rate), places=2)

        original_amount = incident.amount
        self.contract.wage = 1200
        self.assertAlmostEqual(incident.amount, original_amount)
        self.contract.wage = 1000

    def test_unjustified_absence_calculates_historical_discount(self):
        incident = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-03',
            'date_from': '2026-09-03',
            'date_to': '2026-09-04',
            'incident_type': 'unjustified_absence',
            'quantity_days': 2,
            'quantity_hours': 1,
        })
        daily_rate = 1000 / 30
        hourly_rate = daily_rate / 8
        self.assertAlmostEqual(incident.absence_total_hours, 17)
        self.assertAlmostEqual(incident.amount, (2 * daily_rate) + hourly_rate, places=2)

        original_amount = incident.amount
        self.contract.wage = 1200
        self.assertAlmostEqual(incident.amount, original_amount)
        self.contract.wage = 1000

    def test_suspension_calculates_historical_discount(self):
        incident = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-04',
            'date_from': '2026-09-04',
            'date_to': '2026-09-04',
            'incident_type': 'suspension',
            'quantity_days': 1,
            'quantity_hours': 3,
        })
        daily_rate = 1000 / 30
        hourly_rate = daily_rate / 8
        self.assertAlmostEqual(incident.absence_total_hours, 11)
        self.assertAlmostEqual(incident.amount, daily_rate + (3 * hourly_rate), places=2)

        original_amount = incident.amount
        self.contract.wage = 1200
        self.assertAlmostEqual(incident.amount, original_amount)
        self.contract.wage = 1000

    def prepayroll(self, state='draft', with_line=False, date_start='2026-09-01', date_end='2026-09-30'):
        existing = self.env['hr.prepayroll'].search([
            ('date_start', '=', date_start),
            ('date_end', '=', date_end),
            ('payment_frequency', '=', 'monthly'),
        ])
        if existing:
            self.env.cr.execute("UPDATE hr_prepayroll SET state='cancelled' WHERE id IN %s", [tuple(existing.ids)])
            existing.invalidate_recordset(['state'])
            existing.sudo().unlink()
        vals = {
            'name': 'Authorization test %s' % fields.Datetime.now(), 'date_start': date_start,
            'date_end': date_end, 'payment_frequency': 'monthly', 'state': 'draft',
        }
        if with_line:
            vals['line_ids'] = [Command.create({
                'employee_id': self.employee.id, 'contract_id': self.contract.id,
                'remuneration_mode': 'mixed', 'production_unit': 'unit',
            })]
        payroll = self.env['hr.prepayroll'].create(vals)
        if state == 'loaded':
            payroll.action_load_employees()
        elif state == 'validated':
            payroll.action_load_employees()
            payroll.action_validate()
        elif state == 'closed':
            payroll.action_load_employees()
            payroll.action_validate()
            payroll.action_close()
        elif state != 'draft':
            payroll.write({'state': state})
        return payroll

    def test_period_wage_uses_monthly_contract_wage_and_payment_frequency(self):
        self.contract.velatek_payment_frequency = 'biweekly'
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Second half September',
            'date_start': '2026-09-15',
            'date_end': '2026-09-30',
            'payment_frequency': 'biweekly',
        })
        payroll.action_load_employees()
        line = payroll.line_ids.filtered(lambda item: item.contract_id == self.contract)
        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.base_wage, 1000)
        self.assertAlmostEqual(line.period_wage, 500)

        line.period_wage = 1000
        payroll.action_update_data()
        self.assertAlmostEqual(line.period_wage, 500)
        self.contract.velatek_payment_frequency = 'monthly'

    def test_commission_is_added_to_prepayroll_total(self):
        commission = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-08',
            'incident_type': 'commission',
            'amount': 125,
        })
        commission.action_submit()
        commission.action_approve()
        payroll = self.prepayroll('draft', with_line=True)
        payroll.action_update_data()
        line = payroll.line_ids

        self.assertIn(commission, line.incident_ids)
        self.assertAlmostEqual(line.commission_amount, 125)
        self.assertAlmostEqual(line.gross_total, line.period_wage + 125)

    def test_operational_actions_by_profile(self):
        for model in ('hr.employee.production', 'hr.employee.incident'):
            for profile, user in self.profiles.items():
                with self.subTest(model=model, profile=profile):
                    record = self.operational_record(model).with_user(user)
                    if profile == 'internal':
                        for action in ('action_submit', 'action_approve', 'action_cancel', 'action_draft'):
                            with self.assertRaises(AccessError):
                                getattr(record, action)()
                        continue
                    record.write({'notes': 'Draft edit'})
                    record.action_submit()
                    if profile == 'user':
                        with self.assertRaises(AccessError):
                            record.action_approve()
                    else:
                        record.action_approve()
                        self.assertEqual(record.state, 'approved')
                    if profile != 'manager':
                        for action in ('action_cancel', 'action_draft'):
                            with self.assertRaises(AccessError):
                                getattr(record, action)()
                    else:
                        record.action_cancel()
                        record.action_draft()
                        self.assertEqual(record.state, 'draft')

    def test_prepayroll_actions_by_profile(self):
        for profile, user in self.profiles.items():
            with self.subTest(profile=profile):
                record = self.prepayroll('loaded').with_user(user)
                if profile in ('internal', 'user'):
                    with self.assertRaises(AccessError):
                        record.action_validate()
                else:
                    record.action_validate()
                    self.assertEqual(record.state, 'validated')
                if profile != 'manager':
                    for action in ('action_close', 'action_cancel', 'action_draft'):
                        with self.assertRaises(AccessError):
                            getattr(record, action)()
                else:
                    record.action_close()
                    self.assertEqual(record.state, 'closed')
                    with self.assertRaises(ValidationError):
                        record.action_draft()

    def test_prepayroll_state_machine_via_official_actions(self):
        payroll = self.env['hr.prepayroll'].with_user(self.profiles['manager']).create({
            'name': 'Workflow by actions',
            'date_start': '2026-11-01',
            'date_end': '2026-11-30',
            'payment_frequency': 'monthly',
        })
        self.assertEqual(payroll.state, 'draft')

        payroll.action_load_employees()
        self.assertEqual(payroll.state, 'loaded')
        payroll.action_validate()
        self.assertEqual(payroll.state, 'validated')
        payroll.action_close()
        self.assertEqual(payroll.state, 'closed')

    def test_prepayroll_state_machine_on_direct_write(self):
        manager = self.profiles['manager']
        payroll = self.env['hr.prepayroll'].with_user(manager).create({
            'name': 'Workflow by direct write',
            'date_start': '2026-12-01',
            'date_end': '2026-12-31',
            'payment_frequency': 'biweekly',
        })

        payroll.write({'state': 'loaded'})
        self.assertEqual(payroll.state, 'loaded')
        payroll.write({'state': 'validated'})
        self.assertEqual(payroll.state, 'validated')
        payroll.write({'state': 'closed'})
        self.assertEqual(payroll.state, 'closed')

        for target in ('draft', 'loaded', 'validated', 'cancelled'):
            with self.subTest(source='closed', target=target):
                with self.assertRaisesRegex(ValidationError, 'No se permite cambiar'):
                    payroll.write({'state': target})
                self.assertEqual(payroll.state, 'closed')

        cancelled = self.env['hr.prepayroll'].with_user(manager).create({
            'name': 'Cancelled workflow',
            'date_start': '2027-01-01',
            'date_end': '2027-01-31',
            'payment_frequency': 'weekly',
        })
        cancelled.write({'state': 'cancelled'})
        with self.assertRaisesRegex(ValidationError, 'No se permite cambiar'):
            cancelled.write({'state': 'draft'})
        self.assertEqual(cancelled.state, 'cancelled')

    def test_prepayroll_state_machine_blocks_state_skips_for_manager(self):
        payroll = self.env['hr.prepayroll'].with_user(self.profiles['manager']).create({
            'name': 'Workflow skips',
            'date_start': '2027-02-01',
            'date_end': '2027-02-28',
            'payment_frequency': 'fourteen_days',
        })
        for target in ('validated', 'closed'):
            with self.subTest(source='draft', target=target):
                with self.assertRaisesRegex(ValidationError, 'No se permite cambiar'):
                    payroll.write({'state': target})
                self.assertEqual(payroll.state, 'draft')

        payroll.write({'state': 'loaded'})
        with self.assertRaisesRegex(ValidationError, 'No se permite cambiar'):
            payroll.write({'state': 'closed'})
        self.assertEqual(payroll.state, 'loaded')

    def test_only_manager_can_delete_cancelled_prepayroll(self):
        for profile in ('user', 'responsible'):
            with self.subTest(profile=profile):
                payroll = self.prepayroll('cancelled').with_user(self.profiles[profile])
                with self.assertRaises(AccessError):
                    payroll.unlink()
                self.assertTrue(payroll.exists())

        payroll = self.prepayroll('cancelled').with_user(self.profiles['manager'])
        payroll.unlink()
        self.assertFalse(payroll.exists())

    def test_cannot_delete_validated_or_closed_prepayroll(self):
        msg = "No se puede eliminar una preplanilla validada o cerrada. Debe utilizarse un proceso formal de reversión."
        for profile in ('user', 'responsible', 'manager'):
            user = self.profiles[profile]
            for state in ('validated', 'closed'):
                with self.subTest(profile=profile, state=state):
                    payroll = self.prepayroll('draft')
                    payroll.write({'state': 'loaded'})
                    payroll.write({'state': 'validated'})
                    if state == 'closed':
                        payroll.write({'state': 'closed'})
                    
                    with self.assertRaisesRegex(ValidationError, msg):
                        payroll.with_user(user).unlink()
                    self.assertTrue(payroll.exists())

    def test_draft_and_loaded_prepayroll_unlink(self):
        manager = self.profiles['manager']
        draft_payroll = self.prepayroll('draft').with_user(manager)
        draft_payroll.unlink()
        self.assertFalse(draft_payroll.exists())

        loaded_payroll = self.prepayroll('loaded').with_user(manager)
        loaded_payroll.unlink()
        self.assertFalse(loaded_payroll.exists())

    def test_direct_state_write_and_context_do_not_bypass_groups(self):
        for profile in ('user', 'responsible'):
            for model in ('hr.employee.production', 'hr.employee.incident', 'hr.prepayroll'):
                record = self.prepayroll() if model == 'hr.prepayroll' else self.operational_record(model)
                record = record.with_user(self.profiles[profile]).with_context(skip_security=True)
                targets = ['cancelled', 'closed'] if model == 'hr.prepayroll' else ['cancelled', 'processed']
                if profile == 'user':
                    targets.append('validated' if model == 'hr.prepayroll' else 'approved')
                for state in targets:
                    with self.subTest(profile=profile, model=model, state=state):
                        try:
                            record.write({'state': state})
                            self.fail("Expected AccessError or ValidationError")
                        except (AccessError, ValidationError):
                            pass
                record.sudo().write({'state': 'cancelled'})
                try:
                    record.write({'state': 'draft'})
                    self.fail("Expected AccessError or ValidationError")
                except (AccessError, ValidationError):
                    pass
                try:
                    record.write({'state': 'validated' if model == 'hr.prepayroll' else 'approved'})
                    self.fail("Expected AccessError or ValidationError")
                except (AccessError, ValidationError):
                    pass

    def test_create_only_draft_including_context_defaults(self):
        for model in ('hr.employee.production', 'hr.employee.incident', 'hr.prepayroll'):
            sample = self.prepayroll() if model == 'hr.prepayroll' else self.operational_record(model)
            vals = sample.copy_data()[0]
            vals['state'] = 'draft'
            for idx, profile in enumerate(('user', 'responsible', 'manager')):
                records = self.env[model].with_user(self.profiles[profile])
                cur_vals = dict(vals)
                if model == 'hr.prepayroll':
                    cur_vals['date_start'] = f'2026-11-{1+idx*2:02d}'
                    cur_vals['date_end'] = f'2026-11-{2+idx*2:02d}'
                created = records.create(cur_vals)
                self.assertEqual(created.state, 'draft')
                if profile == 'manager':
                    continue
                with self.assertRaises(AccessError):
                    records.create(dict(cur_vals, state='cancelled'))
                without_state = {k: v for k, v in cur_vals.items() if k != 'state'}
                with self.assertRaises(AccessError):
                    records.with_context(default_state='cancelled').create(without_state)

    def test_locked_records_and_lines(self):
        for profile in ('user', 'responsible'):
            user = self.profiles[profile]
            for model in ('hr.employee.production', 'hr.employee.incident'):
                record = self.operational_record(model, 'approved').with_user(user)
                with self.assertRaises(AccessError):
                    record.write({'notes': 'Unauthorized change'})
                with self.assertRaises(AccessError):
                    record.unlink()
                with self.assertRaises(AccessError):
                    record.write({'processed_prepayroll_id': False})
            payroll = self.prepayroll('validated', with_line=True).with_user(user)
            with self.assertRaises(AccessError):
                payroll.write({'notes': 'Unauthorized change'})
            with self.assertRaises(ValidationError):
                payroll.unlink()
            line = payroll.line_ids
            with self.assertRaises(ValidationError):
                line.write({'bonus_amount': 999})
            with self.assertRaises(ValidationError):
                line.unlink()
            draft = self.prepayroll('draft', with_line=True, date_start='2026-10-01', date_end='2026-10-31').with_user(user)
            draft.line_ids.write({'bonus_amount': 12})
            try:
                draft.line_ids.write({'prepayroll_id': payroll.id})
                self.fail("Expected AccessError or ValidationError")
            except (AccessError, ValidationError):
                pass
            try:
                self.env['hr.prepayroll.line'].with_user(user).create({
                    'prepayroll_id': payroll.id, 'employee_id': self.employee.id,
                    'contract_id': self.contract.id,
                })
                self.fail("Expected AccessError or ValidationError")
            except (AccessError, ValidationError):
                pass

    def test_manager_close_processes_approved_records(self):
        self.contract.state = 'open'
        production = self.operational_record('hr.employee.production', 'approved')
        incident = self.operational_record('hr.employee.incident', 'approved')
        payroll = self.prepayroll('loaded', with_line=True).with_user(self.profiles['manager'])
        payroll.action_update_data()
        payroll.action_validate()
        payroll.action_close()
        for record in (production, incident):
            self.assertEqual(record.state, 'processed')
            self.assertEqual(record.processed_prepayroll_id, payroll)

    def test_processed_production_and_incident_are_immutable_for_every_role(self):
        message = (
            'El registro ya fue procesado en una preplanilla cerrada '
            'y no puede modificarse.'
        )
        for model in ('hr.employee.production', 'hr.employee.incident'):
            for profile in ('user', 'responsible', 'manager'):
                with self.subTest(model=model, profile=profile, operation='write'):
                    record = self.operational_record(model, 'processed').with_user(
                        self.profiles[profile]
                    )
                    with self.assertRaisesRegex(ValidationError, message):
                        record.write({'notes': 'Intento de modificación'})
                    self.assertFalse(record.notes)

                with self.subTest(model=model, profile=profile, operation='unlink'):
                    record = self.operational_record(model, 'processed').with_user(
                        self.profiles[profile]
                    )
                    with self.assertRaisesRegex(ValidationError, message):
                        record.unlink()
                    self.assertTrue(record.exists())

    def test_production_explicit_state_machine(self):
        prod = self.env['hr.employee.production'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-01',
            'quantity': 5,
            'rate': 10,
            'unit': 'unit',
            'state': 'draft',
        })
        # draft -> approved directo = error
        with self.assertRaisesRegex(ValidationError, "Por Aprobar"):
            prod.action_approve()
        with self.assertRaisesRegex(ValidationError, "Por Aprobar"):
            prod.write({'state': 'approved'})

        # draft -> to_approve = correcto
        prod.action_submit()
        self.assertEqual(prod.state, 'to_approve')

        # to_approve -> approved = correcto
        prod.action_approve()
        self.assertEqual(prod.state, 'approved')

        # approved -> processed manual = error
        with self.assertRaisesRegex(ValidationError, "cierre formal de preplanilla"):
            prod.write({'state': 'processed'})

        # processed record setup via prepayroll close
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Test Prepayroll Processed State Machine',
            'date_start': '2020-02-01',
            'date_end': '2020-02-28',
            'payment_frequency': 'monthly',
            'state': 'closed',
        })
        prod.write({'state': 'processed', 'processed_prepayroll_id': payroll.id})

        # processed -> draft = error
        with self.assertRaises(ValidationError):
            prod.action_draft()
        with self.assertRaises(ValidationError):
            prod.write({'state': 'draft'})

        # processed -> cancelled = error
        with self.assertRaises(ValidationError):
            prod.action_cancel()
        with self.assertRaises(ValidationError):
            prod.write({'state': 'cancelled'})

    def test_prepayroll_action_validate_comprehensive_rules(self):
        other_employee = self.env['hr.employee'].create({'name': 'Employee Test Rules'})
        other_contract = self.env['hr.contract'].create({
            'name': 'Expired Contract Test',
            'employee_id': other_employee.id,
            'company_id': self.env.company.id,
            'contract_type_id': self.contract_type.id,
            'wage': 500.0,
            'date_start': '2020-01-01',
            'date_end': '2020-01-31',
            'velatek_payment_frequency': 'monthly',
            'velatek_remuneration_mode': 'production',
            'velatek_production_unit': 'unit',
            'state': 'open',
        })
        payroll_single = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Single Error',
            'date_start': '2020-05-01',
            'date_end': '2020-05-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
            'line_ids': [(0, 0, {
                'employee_id': other_employee.id,
                'contract_id': other_contract.id,
                'remuneration_mode': 'fixed',
            })]
        })
        with self.assertRaisesRegex(ValidationError, "No se puede validar la preplanilla"):
            payroll_single.action_validate()

        other_contract.write({
            'date_start': '2020-06-01',
            'date_end': '2020-06-30',
            'velatek_payment_frequency': 'biweekly',
        })
        prod_draft = self.env['hr.employee.production'].create({
            'employee_id': other_employee.id,
            'contract_id': other_contract.id,
            'date': '2020-06-10',
            'quantity': 5,
            'rate': 10,
            'unit': 'unit',
            'state': 'draft',
        })
        payroll_combined = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Combined Errors',
            'date_start': '2020-06-01',
            'date_end': '2020-06-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
            'line_ids': [(0, 0, {
                'employee_id': other_employee.id,
                'contract_id': other_contract.id,
                'remuneration_mode': 'production',
                'production_unit': 'unit',
                'production_ids': [(6, 0, [prod_draft.id])],
            })]
        })
        with self.assertRaisesRegex(ValidationError, "frecuencia contractual"):
            payroll_combined.action_validate()

    def test_draft_and_approved_keep_existing_permissions(self):
        for model in ('hr.employee.production', 'hr.employee.incident'):
            for profile in ('user', 'responsible', 'manager'):
                user = self.profiles[profile]
                with self.subTest(model=model, profile=profile, state='draft'):
                    draft = self.operational_record(model).with_user(user)
                    draft.write({'notes': 'Edición permitida'})
                    draft.unlink()
                    self.assertFalse(draft.exists())

                with self.subTest(model=model, profile=profile, state='approved'):
                    approved = self.operational_record(model, 'approved').with_user(user)
                    if profile == 'manager':
                        approved.write({'notes': 'Edición permitida al Manager'})
                        approved.unlink()
                        self.assertFalse(approved.exists())
                    else:
                        with self.assertRaises(AccessError):
                            approved.write({'notes': 'Edición denegada'})
                        with self.assertRaises(AccessError):
                            approved.unlink()

    def test_prepayroll_traceability_critical_case(self):
        self.contract.state = 'open'
        # 1. Producción A: $500 approved
        prod_a = self.env['hr.employee.production'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-05',
            'quantity': 50,
            'rate': 10,
            'unit': 'unit',
            'state': 'draft',
        })
        prod_a.action_submit()
        prod_a.action_approve()
        self.assertEqual(prod_a.amount, 500.0)

        # 2. Preplanilla + Cargar + Actualizar datos
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla Septiembre Traceability',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        
        # Actualizar datos 2 veces para verificar idempotencia
        payroll.action_update_data()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == self.employee)
        self.assertEqual(line.production_amount, 500.0)
        self.assertIn(prod_a, line.production_ids)
        self.assertEqual(len(line.production_ids), 1)

        # 3. Validar
        payroll.action_validate()
        self.assertEqual(payroll.state, 'validated')

        # Verificar que no se puede actualizar datos estando validada
        with self.assertRaises(Exception):
            payroll.action_update_data()

        # 4. Crear Producción B: $200 approved después de validar
        prod_b = self.env['hr.employee.production'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-10',
            'quantity': 20,
            'rate': 10,
            'unit': 'unit',
            'state': 'draft',
        })
        prod_b.action_submit()
        prod_b.action_approve()
        self.assertEqual(prod_b.amount, 200.0)

        # 5. Cerrar Preplanilla
        payroll.action_close()
        self.assertEqual(payroll.state, 'closed')

        # RESULTADO OBLIGATORIO:
        self.assertEqual(prod_a.state, 'processed')
        self.assertEqual(prod_a.processed_prepayroll_id, payroll)
        self.assertEqual(prod_b.state, 'approved')
        self.assertFalse(prod_b.processed_prepayroll_id)
        self.assertEqual(line.production_amount, 500.0)

    def test_prepayroll_freezes_incident_sources(self):
        self.contract.state = 'open'
        incident_a = self.operational_record('hr.employee.incident', 'approved')
        payroll = self.prepayroll('draft', with_line=True).with_user(self.profiles['manager'])
        payroll.action_load_employees()

        payroll.action_update_data()
        payroll.action_update_data()
        line = payroll.line_ids
        self.assertEqual(line.incident_ids, incident_a)

        payroll.action_validate()
        frozen_ids = line.incident_ids.ids
        incident_b = self.operational_record('hr.employee.incident', 'approved')
        self.assertEqual(line.incident_ids.ids, frozen_ids)

        payroll.action_close()
        self.assertEqual(incident_a.state, 'processed')
        self.assertEqual(incident_a.processed_prepayroll_id, payroll)
        self.assertEqual(incident_b.state, 'approved')
        self.assertFalse(incident_b.processed_prepayroll_id)

    def test_close_rejects_incompatible_frozen_source_state(self):
        production = self.operational_record('hr.employee.production', 'approved')
        payroll = self.prepayroll('loaded', with_line=True).with_user(self.profiles['manager'])
        payroll.action_update_data()
        payroll.action_validate()

        production.sudo().action_draft()
        production.sudo().action_submit()
        with self.assertRaisesRegex(ValidationError, 'ya no está aprobada'):
            payroll.action_close()
        self.assertEqual(payroll.state, 'validated')

    def test_close_rejects_source_processed_by_another_prepayroll(self):
        production = self.operational_record('hr.employee.production', 'approved')
        payroll = self.prepayroll('loaded', with_line=True).with_user(self.profiles['manager'])
        payroll.action_update_data()
        payroll.action_validate()
        other_payroll = self.env['hr.prepayroll'].create({
            'name': 'Other payroll',
            'date_start': '2026-10-01',
            'date_end': '2026-10-31',
            'payment_frequency': 'monthly',
            'state': 'cancelled',
        })

        production.sudo().write({'processed_prepayroll_id': other_payroll.id})
        with self.assertRaisesRegex(
            ValidationError,
            'Uno o más registros utilizados ya fueron procesados por otra preplanilla.',
        ):
            payroll.action_close()

    def test_processed_sources_are_not_consumed_again(self):
        production = self.operational_record('hr.employee.production', 'approved')
        first = self.prepayroll('draft', with_line=True).with_user(self.profiles['manager'])
        first.action_load_employees()
        first.action_update_data()
        first.action_validate()
        first.action_close()
        self.assertEqual(production.state, 'processed')

        with self.assertRaisesRegex(ValidationError, 'No se puede eliminar una preplanilla validada o cerrada'):
            first.unlink()

        second = self.env['hr.prepayroll'].create({
            'name': 'Second payroll test %s' % time.time(),
            'date_start': '2026-10-01',
            'date_end': '2026-10-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        second.action_load_employees()
        second.action_update_data()

        self.assertEqual(production.state, 'processed')
        self.assertEqual(production.processed_prepayroll_id, first)
        self.assertNotIn(production, second.line_ids.mapped('production_ids'))
        second.action_load_employees()
        second.action_update_data()
        self.assertNotIn(production, second.line_ids.production_ids)

    def test_preparation_actions_by_profile(self):
        for profile, user in self.profiles.items():
            payroll = self.prepayroll().with_user(user)
            if profile == 'internal':
                for action in ('action_load_employees', 'action_update_data'):
                    with self.assertRaises(AccessError):
                        getattr(payroll, action)()
                with self.assertRaises(AccessError):
                    payroll.write({'notes': 'Denied'})
            else:
                payroll.write({'notes': 'Preparation'})
                payroll.action_load_employees()
                self.assertEqual(payroll.state, 'loaded')
                payroll.action_update_data()

    def test_multi_record_write_is_atomic(self):
        for model in ('hr.employee.production', 'hr.employee.incident'):
            draft = self.operational_record(model)
            approved = self.operational_record(model, 'approved')
            records = (draft | approved).with_user(self.profiles['user'])
            with self.assertRaises(AccessError):
                records.write({'notes': 'Denied for mixed states'})
            self.assertFalse(draft.notes)

    def test_overtime_calculation_by_remuneration_mode(self):
        # 1. Fixed $800, 8h day, 3h daytime -> $20.00
        emp_fixed = self.env['hr.employee'].create({'name': 'Emp Fixed Overtime'})
        contract_fixed = self.env['hr.contract'].create({
            'name': 'Fixed Contract',
            'employee_id': emp_fixed.id,
            'date_start': '2026-01-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })
        inc_fixed_day = self.env['hr.employee.incident'].create({
            'employee_id': emp_fixed.id,
            'contract_id': contract_fixed.id,
            'date': '2026-09-05',
            'incident_type': 'overtime',
            'overtime_type': 'daytime',
            'quantity_hours': 3.0,
        })
        self.assertAlmostEqual(inc_fixed_day.amount, 20.00, places=2)

        # 2. Fixed $800, 8h day, 3h nighttime -> $25.00
        inc_fixed_night = self.env['hr.employee.incident'].create({
            'employee_id': emp_fixed.id,
            'contract_id': contract_fixed.id,
            'date': '2026-09-06',
            'incident_type': 'overtime',
            'overtime_type': 'nighttime',
            'quantity_hours': 3.0,
        })
        self.assertAlmostEqual(inc_fixed_night.amount, 25.00, places=2)

        # 3. Production -> $0.00 auto amount, preserve hours/type, note indicator
        emp_prod = self.env['hr.employee'].create({'name': 'Emp Prod Overtime'})
        contract_prod = self.env['hr.contract'].create({
            'name': 'Production Contract',
            'employee_id': emp_prod.id,
            'date_start': '2026-01-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'production',
            'velatek_production_unit': 'unit',
            'state': 'open',
        })
        inc_prod = self.env['hr.employee.incident'].create({
            'employee_id': emp_prod.id,
            'contract_id': contract_prod.id,
            'date': '2026-09-07',
            'incident_type': 'overtime',
            'overtime_type': 'daytime',
            'quantity_hours': 3.0,
        })
        self.assertEqual(inc_prod.amount, 0.0)
        self.assertEqual(inc_prod.quantity_hours, 3.0)
        self.assertEqual(inc_prod.overtime_type, 'daytime')
        self.assertIn('El valor de hora requiere determinación en nómina por remuneración variable.', inc_prod.notes)

        # 4. Mixed -> $0.00 auto amount from wage alone, preserve hours/type, note indicator
        emp_mixed = self.env['hr.employee'].create({'name': 'Emp Mixed Overtime'})
        contract_mixed = self.env['hr.contract'].create({
            'name': 'Mixed Contract',
            'employee_id': emp_mixed.id,
            'date_start': '2026-01-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'mixed',
            'velatek_production_unit': 'unit',
            'state': 'open',
        })
        inc_mixed = self.env['hr.employee.incident'].create({
            'employee_id': emp_mixed.id,
            'contract_id': contract_mixed.id,
            'date': '2026-09-08',
            'incident_type': 'overtime',
            'overtime_type': 'nighttime',
            'quantity_hours': 3.0,
        })
        self.assertEqual(inc_mixed.amount, 0.0)
        self.assertEqual(inc_mixed.quantity_hours, 3.0)
        self.assertEqual(inc_mixed.overtime_type, 'nighttime')
        self.assertIn('El valor de hora requiere determinación en nómina por remuneración variable.', inc_mixed.notes)

    def test_night_work_incident_and_prepayroll_consolidation(self):
        emp_nw = self.env['hr.employee'].create({'name': 'Emp Night Work'})
        contract_nw = self.env['hr.contract'].create({
            'name': 'Night Work Contract',
            'employee_id': emp_nw.id,
            'date_start': '2026-09-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })
        inc_nw = self.env['hr.employee.incident'].create({
            'employee_id': emp_nw.id,
            'contract_id': contract_nw.id,
            'date': '2026-09-10',
            'incident_type': 'night_work',
            'quantity_hours': 4.0,
        })
        inc_nw.action_submit()
        inc_nw.action_approve()
        # 800 / 30 / 8 = 3.333333... * 0.25 = 0.833333... * 4 = 3.33333...
        self.assertAlmostEqual(inc_nw.amount, 3.33, places=2)
        self.assertEqual(inc_nw.incident_type, 'night_work')
        self.assertEqual(inc_nw.quantity_hours, 4.0)

        # Confirmar consolidación en preplanilla
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Night Work Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_nw)
        self.assertTrue(line)
        self.assertEqual(line.night_work_hours, 4.0)
        self.assertAlmostEqual(line.night_work_amount, 3.33, places=2)
        self.assertEqual(line.overtime_hours, 0.0)
        self.assertEqual(line.overtime_amount, 0.0)

    def test_holiday_work_incident_and_prepayroll_consolidation(self):
        emp_hw = self.env['hr.employee'].create({'name': 'Emp Holiday Work'})
        contract_hw = self.env['hr.contract'].create({
            'name': 'Holiday Work Contract',
            'employee_id': emp_hw.id,
            'date_start': '2026-09-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        # Validation test: quantity_hours or quantity_days required
        with self.assertRaises(ValidationError):
            self.env['hr.employee.incident'].create({
                'employee_id': emp_hw.id,
                'contract_id': contract_hw.id,
                'date': '2026-09-15',
                'incident_type': 'holiday_work',
                'quantity_hours': 0.0,
                'quantity_days': 0.0,
            })

        # Fixed contract holiday work
        inc_hw = self.env['hr.employee.incident'].create({
            'employee_id': emp_hw.id,
            'contract_id': contract_hw.id,
            'date': '2026-09-15',
            'incident_type': 'holiday_work',
            'quantity_hours': 4.0,
        })
        inc_hw.action_submit()
        inc_hw.action_approve()
        # 800 / 30 / 8 = 3.33333... * 2.0 (doble) * 4h = 26.6666...
        self.assertAlmostEqual(inc_hw.amount, 26.67, places=2)
        self.assertEqual(inc_hw.incident_type, 'holiday_work')
        self.assertEqual(inc_hw.quantity_hours, 4.0)

        # Prepayroll consolidation
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Holiday Work Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_hw)
        self.assertTrue(line)
        self.assertEqual(line.holiday_work_hours, 4.0)
        self.assertAlmostEqual(line.holiday_work_amount, 26.67, places=2)
        self.assertEqual(line.overtime_hours, 0.0)
        self.assertEqual(line.overtime_amount, 0.0)
        self.assertEqual(line.night_work_hours, 0.0)

        # Mixed contract holiday work -> amount 0.0 with variable note
        emp_mixed = self.env['hr.employee'].create({'name': 'Emp Mixed Holiday Work'})
        contract_mixed = self.env['hr.contract'].create({
            'name': 'Mixed Holiday Contract',
            'employee_id': emp_mixed.id,
            'date_start': '2026-09-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'mixed',
            'velatek_production_unit': 'unit',
            'state': 'open',
        })
        inc_mixed = self.env['hr.employee.incident'].create({
            'employee_id': emp_mixed.id,
            'contract_id': contract_mixed.id,
            'date': '2026-09-15',
            'incident_type': 'holiday_work',
            'quantity_hours': 4.0,
        })
        inc_mixed.action_submit()
        inc_mixed.action_approve()
        self.assertEqual(inc_mixed.amount, 0.0)
        self.assertIn('requiere determinación en nómina', inc_mixed.notes)

    def test_weekly_rest_work_incident_and_prepayroll_consolidation(self):
        emp_wr = self.env['hr.employee'].create({'name': 'Emp Weekly Rest Work'})
        contract_wr = self.env['hr.contract'].create({
            'name': 'Weekly Rest Work Contract',
            'employee_id': emp_wr.id,
            'date_start': '2026-09-01',
            'wage': 800.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        # Create weekly rest work incident with compensatory rest fields
        inc_wr = self.env['hr.employee.incident'].create({
            'employee_id': emp_wr.id,
            'contract_id': contract_wr.id,
            'date': '2026-09-13',
            'incident_type': 'weekly_rest_work',
            'quantity_hours': 8.0,
            'compensatory_rest_date': '2026-09-18',
            'compensatory_rest_granted': True,
        })
        inc_wr.action_submit()
        inc_wr.action_approve()
        # 800 / 30 / 8 = 3.33333... * 1.5 * 8h = 40.0
        self.assertAlmostEqual(inc_wr.amount, 40.0, places=2)
        self.assertEqual(inc_wr.incident_type, 'weekly_rest_work')
        self.assertEqual(inc_wr.quantity_hours, 8.0)
        self.assertEqual(str(inc_wr.compensatory_rest_date), '2026-09-18')
        self.assertTrue(inc_wr.compensatory_rest_granted)

        # Prepayroll consolidation
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Weekly Rest Work Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_wr)
        self.assertTrue(line)
        self.assertEqual(line.weekly_rest_work_hours, 8.0)
        self.assertAlmostEqual(line.weekly_rest_work_amount, 40.0, places=2)
        self.assertEqual(line.overtime_hours, 0.0)
        self.assertEqual(line.overtime_amount, 0.0)
        self.assertEqual(line.holiday_work_hours, 0.0)
        self.assertEqual(line.holiday_work_amount, 0.0)
        self.assertEqual(line.night_work_hours, 0.0)

    def test_absence_range_spanning_two_biweeklies(self):
        emp_abs = self.env['hr.employee'].create({'name': 'Emp Absence Range'})
        contract_abs = self.env['hr.contract'].create({
            'name': 'Absence Range Contract',
            'employee_id': emp_abs.id,
            'date_start': '2026-09-01',
            'wage': 900.0,  # 900 / 30 = 30.0 daily
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'biweekly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        # 1. Validation test: date_from and date_to mandatory for absence
        with self.assertRaises(ValidationError):
            self.env['hr.employee.incident'].create({
                'employee_id': emp_abs.id,
                'contract_id': contract_abs.id,
                'incident_type': 'sick_leave',
                'date_from': '2026-09-14',
                # missing date_to
            })

        # 2. Validation test: date_to >= date_from
        with self.assertRaises(ValidationError):
            self.env['hr.employee.incident'].create({
                'employee_id': emp_abs.id,
                'contract_id': contract_abs.id,
                'incident_type': 'sick_leave',
                'date_from': '2026-09-18',
                'date_to': '2026-09-14',
            })

        # 3. Create sick_leave spanning 14/09 -> 18/09 (5 days total)
        inc_sick = self.env['hr.employee.incident'].create({
            'employee_id': emp_abs.id,
            'contract_id': contract_abs.id,
            'incident_type': 'sick_leave',
            'date_from': '2026-09-14',
            'date_to': '2026-09-18',
        })
        inc_sick.action_submit()
        inc_sick.action_approve()
        self.assertEqual(inc_sick.quantity_days, 5.0)

        # 4. Create unpaid_leave spanning 14/09 -> 18/09 (5 days total: $150.00 total)
        inc_unpaid = self.env['hr.employee.incident'].create({
            'employee_id': emp_abs.id,
            'contract_id': contract_abs.id,
            'incident_type': 'unpaid_leave',
            'date_from': '2026-09-14',
            'date_to': '2026-09-18',
        })
        inc_unpaid.action_submit()
        inc_unpaid.action_approve()
        self.assertEqual(inc_unpaid.quantity_days, 5.0)
        self.assertAlmostEqual(inc_unpaid.amount, 150.0, places=2)

        # 5. Prepayroll 1: Quincena 1 (01/09 -> 15/09)
        q1 = self.env['hr.prepayroll'].create({
            'name': 'Quincena 1 Septiembre 2026',
            'date_start': '2026-09-01',
            'date_end': '2026-09-15',
            'payment_frequency': 'biweekly',
            'state': 'draft',
        })
        q1.action_load_employees()
        q1.action_update_data()

        line1 = q1.line_ids.filtered(lambda l: l.employee_id == emp_abs)
        self.assertTrue(line1)
        # Intersects 14/09 and 15/09 -> 2 days
        self.assertEqual(line1.sick_leave_days, 2.0)
        self.assertEqual(line1.absence_days, 2.0)
        self.assertAlmostEqual(line1.absence_amount, 60.0, places=2)  # 2 * $30

        # 6. Prepayroll 2: Quincena 2 (16/09 -> 30/09)
        q2 = self.env['hr.prepayroll'].create({
            'name': 'Quincena 2 Septiembre 2026',
            'date_start': '2026-09-16',
            'date_end': '2026-09-30',
            'payment_frequency': 'biweekly',
            'state': 'draft',
        })
        q2.action_load_employees()
        q2.action_update_data()
        line2 = q2.line_ids.filtered(lambda l: l.employee_id == emp_abs)
        self.assertTrue(line2)
        self.assertEqual(line2.sick_leave_days, 3.0)
        self.assertEqual(line2.absence_days, 3.0)
        self.assertAlmostEqual(line2.absence_amount, 90.0, places=2)  # 3 * $30

    def test_sick_leave_traceability_and_prepayroll_consolidation(self):
        emp_sl = self.env['hr.employee'].create({'name': 'Emp Sick Leave'})
        contract_sl = self.env['hr.contract'].create({
            'name': 'Sick Leave Contract',
            'employee_id': emp_sl.id,
            'date_start': '2026-09-01',
            'wage': 1000.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'Constancia_ISSS.pdf',
            'datas': 'c21hcHQ=',
            'res_model': 'hr.employee.incident',
        })

        inc_sick = self.env['hr.employee.incident'].create({
            'employee_id': emp_sl.id,
            'contract_id': contract_sl.id,
            'incident_type': 'sick_leave',
            'date_from': '2026-09-05',
            'date_to': '2026-09-08',
            'quantity_days': 4.0,
            'quantity_hours': 2.0,
            'sick_leave_type': 'common',
            'issuing_entity': 'ISSS San Salvador',
            'certificate_reference': 'INC-2026-9988',
            'attachment_id': attachment.id,
        })
        inc_sick.action_submit()
        inc_sick.action_approve()

        self.assertEqual(inc_sick.quantity_days, 4.0)
        self.assertEqual(inc_sick.quantity_hours, 2.0)
        self.assertEqual(inc_sick.amount, 0.0)  # Sin descuento monetario automático
        self.assertEqual(inc_sick.issuing_entity, 'ISSS San Salvador')
        self.assertEqual(inc_sick.certificate_reference, 'INC-2026-9988')
        self.assertEqual(inc_sick.attachment_id, attachment)

        # Preplanilla
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Sick Leave Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_sl)
        self.assertTrue(line)
        self.assertEqual(line.sick_leave_days, 4.0)
        self.assertEqual(line.sick_leave_hours, 2.0)
        self.assertEqual(line.absence_amount, 0.0)  # Informativo, sin afectación preliminar de salario

    def test_paid_leave_with_reason_and_prepayroll_consolidation(self):
        reason = self.env['hr.incident.reason'].create({
            'name': 'Permiso personal autorizado',
            'code': 'PPA',
            'incident_type': 'paid_leave',
        })
        emp_pl = self.env['hr.employee'].create({'name': 'Emp Paid Leave'})
        contract_pl = self.env['hr.contract'].create({
            'name': 'Paid Leave Contract',
            'employee_id': emp_pl.id,
            'date_start': '2026-09-01',
            'wage': 1200.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        inc_pl = self.env['hr.employee.incident'].create({
            'employee_id': emp_pl.id,
            'contract_id': contract_pl.id,
            'incident_type': 'paid_leave',
            'date_from': '2026-09-02',
            'date_to': '2026-09-04',
            'quantity_days': 3.0,
            'reason_id': reason.id,
        })
        inc_pl.action_submit()
        inc_pl.action_approve()

        self.assertEqual(inc_pl.reason_id, reason)
        self.assertEqual(inc_pl.quantity_days, 3.0)
        self.assertEqual(inc_pl.amount, 0.0)  # No es descontable

        # Preplanilla
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Paid Leave Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_pl)
        self.assertTrue(line)
        self.assertEqual(line.paid_leave_days, 3.0)
        self.assertEqual(line.absence_days, 0.0)
        self.assertEqual(line.absence_amount, 0.0)
        self.assertEqual(line.gross_total, 1200.0)

    def test_preliminary_absence_discount_semantics(self):
        emp_pd = self.env['hr.employee'].create({'name': 'Emp Preliminary Discount'})
        contract_pd = self.env['hr.contract'].create({
            'name': 'Preliminary Discount Contract',
            'employee_id': emp_pd.id,
            'date_start': '2026-09-01',
            'wage': 900.0,  # 900 / 30 = 30.0 daily rate snapshot
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        # Register 1 day unjustified absence
        inc_abs = self.env['hr.employee.incident'].create({
            'employee_id': emp_pd.id,
            'contract_id': contract_pd.id,
            'incident_type': 'unjustified_absence',
            'date_from': '2026-09-10',
            'date_to': '2026-09-10',
            'quantity_days': 1.0,
        })
        inc_abs.action_submit()
        inc_abs.action_approve()

        # Confirm quantity and snapshot rate calculations
        self.assertEqual(inc_abs.quantity_days, 1.0)
        self.assertEqual(inc_abs.absence_base_wage, 900.0)
        self.assertEqual(inc_abs.absence_daily_rate, 30.0)
        self.assertEqual(inc_abs.amount, 30.0)  # Descuento preliminar

        # Prepayroll consolidation
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Preliminary Discount Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_pd)
        self.assertTrue(line)
        self.assertEqual(line.absence_days, 1.0)
        self.assertEqual(line.absence_amount, 30.0)  # Presentado como descuento preliminar
        self.assertEqual(line.gross_total, 870.0)    # Total preliminar (900 - 30)

    def test_suspension_traceability_and_prepayroll_consolidation(self):
        reason = self.env['hr.incident.reason'].create({
            'name': 'Medida disciplinaria Art. 82',
            'code': 'SUSP-82',
            'incident_type': 'suspension',
        })
        emp_sp = self.env['hr.employee'].create({'name': 'Emp Suspension Traceability'})
        contract_sp = self.env['hr.contract'].create({
            'name': 'Suspension Contract',
            'employee_id': emp_sp.id,
            'date_start': '2026-09-01',
            'wage': 900.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        inc_sp = self.env['hr.employee.incident'].create({
            'employee_id': emp_sp.id,
            'contract_id': contract_sp.id,
            'incident_type': 'suspension',
            'date_from': '2026-09-10',
            'date_to': '2026-09-12',
            'quantity_days': 3.0,
            'reason_id': reason.id,
            'authorization_reference': 'ACTA-DISC-2026-012',
            'authorization_date': '2026-09-09',
            'authorized_by_id': self.env.user.id,
        })
        inc_sp.action_submit()
        inc_sp.action_approve()

        self.assertEqual(inc_sp.quantity_days, 3.0)
        self.assertEqual(inc_sp.authorization_reference, 'ACTA-DISC-2026-012')
        self.assertEqual(str(inc_sp.authorization_date), '2026-09-09')
        self.assertEqual(inc_sp.authorized_by_id, self.env.user)
        self.assertEqual(inc_sp.amount, 90.0)  # Descuento preliminar (3 * $30)

        # Preplanilla
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Suspension Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_sp)
        self.assertTrue(line)
        self.assertEqual(line.suspension_days, 3.0)
        self.assertEqual(line.absence_amount, 90.0)  # Descuento preliminar
        self.assertEqual(line.gross_total, 810.0)    # 900 - 90

    def test_incident_concept_catalog_traceability(self):
        # 1. Crear conceptos en el catálogo hr.incident.concept por usuario Responsable/Manager
        concept_bonus = self.env['hr.incident.concept'].with_user(self.profiles['responsible']).create({
            'name': 'Bono productividad',
            'code': 'BONO_PROD',
            'incident_category': 'bonus',
            'is_salary_component': True,
            'is_recurring': False,
            'affects_isss': True,
            'affects_afp': True,
            'affects_isr': True,
        })

        concept_commission = self.env['hr.incident.concept'].with_user(self.profiles['manager']).create({
            'name': 'Comisión ventas',
            'code': 'COM_VENTAS',
            'incident_category': 'commission',
            'is_salary_component': True,
            'is_recurring': True,
            'affects_isss': True,
            'affects_afp': True,
            'affects_isr': True,
        })

        # Verificar flags guardados
        self.assertEqual(concept_bonus.code, 'BONO_PROD')
        self.assertEqual(concept_bonus.incident_category, 'bonus')
        self.assertTrue(concept_bonus.is_salary_component)
        self.assertFalse(concept_bonus.is_recurring)
        self.assertTrue(concept_bonus.affects_isss)
        self.assertTrue(concept_bonus.affects_afp)
        self.assertTrue(concept_bonus.affects_isr)

        self.assertEqual(concept_commission.code, 'COM_VENTAS')
        self.assertEqual(concept_commission.incident_category, 'commission')
        self.assertTrue(concept_commission.is_recurring)

        # 2. Registrar incidencias de distinto tipo usando su concept_id correspondiente
        inc_bonus = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'bonus',
            'date': '2026-09-15',
            'amount': 150.0,
            'concept_id': concept_bonus.id,
        })
        inc_bonus.action_submit()
        inc_bonus.action_approve()

        inc_commission = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'commission',
            'date': '2026-09-20',
            'amount': 300.0,
            'concept_id': concept_commission.id,
        })
        inc_commission.action_submit()
        inc_commission.action_approve()

        # 3. Confirmar trazabilidad separada por concepto
        self.assertEqual(inc_bonus.concept_id, concept_bonus)
        self.assertEqual(inc_bonus.concept_id.code, 'BONO_PROD')
        self.assertEqual(inc_bonus.concept_id.name, 'Bono productividad')

        self.assertEqual(inc_commission.concept_id, concept_commission)
        self.assertEqual(inc_commission.concept_id.code, 'COM_VENTAS')
        self.assertEqual(inc_commission.concept_id.name, 'Comisión ventas')

        self.assertNotEqual(inc_bonus.concept_id, inc_commission.concept_id)

        # 4. Seguridad: Usuario base sin rol de RRHH Responsable/Manager no puede modificar el catálogo
        with self.assertRaises(AccessError):
            self.env['hr.incident.concept'].with_user(self.profiles['internal']).create({
                'name': 'Bono no autorizado',
                'code': 'BONO_NO_AUTH',
                'incident_category': 'bonus',
            })

    def test_viaticos_requires_positive_amount(self):
        # 1. Crear con amount = 0 debe lanzar ValidationError
        with self.assertRaisesRegex(ValidationError, "el importe debe ser mayor que cero"):
            self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id,
                'contract_id': self.contract.id,
                'incident_type': 'viaticos',
                'viatic_treatment': 'remunerative',
                'date': '2026-09-15',
                'amount': 0.0,
            })

        # 2. Crear con amount > 0 debe crearse exitosamente
        inc_viaticos = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'viaticos',
            'viatic_treatment': 'remunerative',
            'date': '2026-09-15',
            'amount': 75.0,
        })
        self.assertEqual(inc_viaticos.amount, 75.0)
        self.assertEqual(inc_viaticos.incident_type, 'viaticos')

    def test_deduction_requires_concept_and_positive_amount(self):
        concept_deduction = self.env['hr.incident.concept'].create({
            'name': 'Deducción préstamo',
            'code': 'DED_PRESTAMO',
            'incident_category': 'deduction',
        })

        emp_ded = self.env['hr.employee'].create({'name': 'Emp Deduction Test'})
        contract_ded = self.env['hr.contract'].create({
            'name': 'Deduction Contract Test',
            'employee_id': emp_ded.id,
            'date_start': '2026-09-01',
            'wage': 1000.0,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': self.calendar.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
            'state': 'open',
        })

        # 1. Deducción sin concepto -> error
        with self.assertRaisesRegex(ValidationError, "especificar el concepto"):
            self.env['hr.employee.incident'].create({
                'employee_id': emp_ded.id,
                'contract_id': contract_ded.id,
                'incident_type': 'deduction',
                'date': '2026-09-10',
                'amount': 50.0,
            })

        # 2. Deducción con amount = 0 -> error
        with self.assertRaisesRegex(ValidationError, "el importe debe ser mayor que cero"):
            self.env['hr.employee.incident'].create({
                'employee_id': emp_ded.id,
                'contract_id': contract_ded.id,
                'incident_type': 'deduction',
                'date': '2026-09-10',
                'amount': 0.0,
                'deduction_concept_id': concept_deduction.id,
            })

        # 3. Deducción válida con concepto, autorización y beneficiario
        partner = self.env['res.partner'].create({'name': 'Banco Financiero'})
        inc_ded = self.env['hr.employee.incident'].create({
            'employee_id': emp_ded.id,
            'contract_id': contract_ded.id,
            'incident_type': 'deduction',
            'date': '2026-09-10',
            'amount': 120.0,
            'deduction_concept_id': concept_deduction.id,
            'authorization_reference': 'AUT-PREST-88',
            'authorization_date': '2026-09-01',
            'beneficiary_name': 'Banco Financiero',
            'beneficiary_partner_id': partner.id,
        })
        inc_ded.action_submit()
        inc_ded.action_approve()

        self.assertEqual(inc_ded.state, 'approved')
        self.assertEqual(inc_ded.amount, 120.0)
        self.assertEqual(str(inc_ded.date), '2026-09-10')
        self.assertFalse(inc_ded.processed_prepayroll_id)
        self.assertEqual(inc_ded.deduction_concept_id, concept_deduction)
        self.assertEqual(inc_ded.concept_id, concept_deduction)
        self.assertEqual(inc_ded.authorization_reference, 'AUT-PREST-88')
        self.assertEqual(str(inc_ded.authorization_date), '2026-09-01')
        self.assertEqual(inc_ded.beneficiary_name, 'Banco Financiero')
        self.assertEqual(inc_ded.beneficiary_partner_id, partner)

        # 4. Preplanilla: Consolidación en deduction_amount manteniendo concepto relacionado
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll Deduction Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()

        line = payroll.line_ids.filtered(lambda l: l.employee_id == emp_ded)
        self.assertTrue(line)
        self.assertEqual(line.deduction_amount, 120.0)
        self.assertIn(inc_ded, line.incident_ids)
        self.assertEqual(inc_ded.concept_id, concept_deduction)

    def test_other_requires_notes(self):
        # 1. Other sin notas -> error
        with self.assertRaisesRegex(ValidationError, "notas explicativas"):
            self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id,
                'contract_id': self.contract.id,
                'incident_type': 'other',
                'date': '2026-09-15',
                'notes': '',
            })

        # 2. Other con notas vacías o espacios -> error
        with self.assertRaisesRegex(ValidationError, "notas explicativas"):
            self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id,
                'contract_id': self.contract.id,
                'incident_type': 'other',
                'date': '2026-09-15',
                'notes': '   ',
            })

        # 3. Other con notas validas -> permitido
        inc_other = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'other',
            'date': '2026-09-15',
            'notes': 'Incidencia informativa no clasificada pendiente de revisión RRHH.',
        })
        self.assertEqual(inc_other.incident_type, 'other')
        self.assertTrue(inc_other.notes)

    def test_incident_state_machine_validations(self):
        # 1. draft -> approved directo por create o write -> error
        with self.assertRaisesRegex(ValidationError, "deben crearse en estado Borrador"):
            self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id,
                'contract_id': self.contract.id,
                'incident_type': 'bonus',
                'date': '2026-09-10',
                'amount': 50.0,
                'state': 'approved',
            })

        inc = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'bonus',
            'date': '2026-09-10',
            'amount': 50.0,
        })
        self.assertEqual(inc.state, 'draft')

        with self.assertRaisesRegex(ValidationError, "Debe estar en 'Por Aprobar'"):
            inc.write({'state': 'approved'})

        with self.assertRaisesRegex(ValidationError, "Por Aprobar"):
            inc.action_approve()

        # 2. draft -> to_approve -> approved = correcto
        inc.action_submit()
        self.assertEqual(inc.state, 'to_approve')

        with self.assertRaisesRegex(ValidationError, "Borrador"):
            inc.action_submit()

        inc.action_approve()
        self.assertEqual(inc.state, 'approved')

        # 3. processed -> draft = error incluso Manager
        payroll = self.env['hr.prepayroll'].create({
            'name': 'Prepayroll State Machine Test',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        payroll.action_load_employees()
        payroll.action_update_data()
        payroll.action_validate()
        payroll.action_close()

        self.assertEqual(inc.state, 'processed')

        with self.assertRaises(ValidationError):
            inc.with_user(self.profiles['manager']).action_draft()

        with self.assertRaises(ValidationError):
            inc.with_user(self.profiles['manager']).write({'state': 'draft'})

    def test_incident_periodicity_and_prepayroll_consolidation(self):
        frequencies = [
            ('weekly', '2026-09-01', '2026-09-07', '2026-09-03'),
            ('monthly', '2026-10-01', '2026-10-31', '2026-10-10'),
            ('biweekly', '2026-11-01', '2026-11-15', '2026-11-10'),
            ('fourteen_days', '2026-12-01', '2026-12-14', '2026-12-05'),
        ]
        for freq, d_start, d_end, inc_date in frequencies:
            with self.subTest(frequency=freq):
                self.contract.velatek_payment_frequency = freq
                inc = self.env['hr.employee.incident'].create({
                    'employee_id': self.employee.id,
                    'contract_id': self.contract.id,
                    'date': inc_date,
                    'incident_type': 'bonus',
                    'amount': 50.0,
                })
                self.assertEqual(inc.period_scope, freq)
                inc.action_submit()
                inc.action_approve()

                payroll = self.env['hr.prepayroll'].create({
                    'name': 'Prepayroll %s Test' % freq,
                    'date_start': d_start,
                    'date_end': d_end,
                    'payment_frequency': freq,
                    'state': 'draft',
                })
                payroll.action_load_employees()
                payroll.action_update_data()

                line = payroll.line_ids.filtered(lambda l: l.employee_id == self.employee)
                self.assertIn(inc, line.incident_ids)

        self.contract.velatek_payment_frequency = 'monthly'

    def test_prepayroll_line_preliminary_totals(self):
        self.contract.wage = 700.0
        line = self.env['hr.prepayroll.line'].create({
            'prepayroll_id': self.env['hr.prepayroll'].create({
                'name': 'Prepayroll Summary Test',
                'date_start': '2026-09-01',
                'date_end': '2026-09-30',
                'payment_frequency': 'monthly',
                'state': 'draft',                                   
            }).id,
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'remuneration_mode': 'fixed',
            'period_wage': 700.0,
            'production_amount': 100.0,
            'bonus_amount': 50.0,
            'deduction_amount': 25.0,
        })
        self.assertEqual(line.preliminary_earnings_total, 850.0)
        self.assertEqual(line.preliminary_deductions_total, 25.0)
        self.assertEqual(line.preliminary_balance, 825.0)
        self.assertEqual(line.gross_total, 825.0)

    def test_payroll_flow_with_prepayroll(self):
        prepayroll = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla Caso 1',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        prepayroll.action_load_employees()
        prepayroll.action_update_data()
        prepayroll.action_validate()
        prepayroll.action_close()
        self.assertEqual(prepayroll.state, 'closed')

        action = prepayroll.action_generate_payroll()
        payroll = self.env['hr.payroll'].browse(action['res_id'])
        self.assertEqual(payroll.prepayroll_id, prepayroll)
        self.assertEqual(payroll.payroll_type, 'regular')
        self.assertEqual(payroll.data_source, 'prepayroll')
        self.assertTrue(len(payroll.line_ids) > 0)
        self.assertEqual(payroll.line_ids[0].source_type, 'prepayroll')

    def test_payroll_direct_flow_without_prepayroll(self):
        payroll = self.env['hr.payroll'].create({
            'name': 'Planilla Directa Caso 2',
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'payroll_type': 'regular',
            'state': 'draft',
        })
        self.assertFalse(payroll.prepayroll_id)
        self.assertTrue(payroll.warning_message)
        self.assertIn("no tiene una preplanilla asociada", payroll.warning_message)

        payroll.action_load_employees()
        self.assertEqual(payroll.data_source, 'manual')
        self.assertTrue(len(payroll.line_ids) > 0)
        self.assertEqual(payroll.line_ids[0].source_type, 'manual')

    def test_payroll_double_use_of_prepayroll_blocked(self):
        prepayroll = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla Caso 3',
            'date_start': '2026-10-01',
            'date_end': '2026-10-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        prepayroll.action_load_employees()
        prepayroll.action_validate()
        prepayroll.action_close()

        prepayroll.action_generate_payroll()

        with self.assertRaisesRegex(ValidationError, "ya está vinculada a la planilla ordinaria"):
            prepayroll.action_generate_payroll()

    def test_payroll_multicompany_blocked(self):
        other_company = self.env['res.company'].create({'name': 'Company B'})
        prepayroll = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla Caso 4',
            'date_start': '2026-11-01',
            'date_end': '2026-11-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        prepayroll.action_load_employees()
        prepayroll.action_validate()
        prepayroll.action_close()

        with self.assertRaises(ValidationError):
            self.env['hr.payroll'].create({
                'name': 'Planilla Empresa B',
                'prepayroll_id': prepayroll.id,
                'company_id': other_company.id,
                'date_start': '2026-11-01',
                'date_end': '2026-11-30',
                'payment_frequency': 'monthly',
                'payroll_type': 'regular',
            })

    def test_payroll_open_prepayroll_blocked(self):
        prepayroll = self.env['hr.prepayroll'].create({
            'name': 'Preplanilla Abierta Caso 5',
            'date_start': '2026-12-01',
            'date_end': '2026-12-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        with self.assertRaisesRegex(ValidationError, "estado Cerrado"):
            self.env['hr.payroll'].create({
                'name': 'Planilla Preplanilla Abierta',
                'prepayroll_id': prepayroll.id,
                'date_start': '2026-12-01',
                'date_end': '2026-12-31',
                'payment_frequency': 'monthly',
                'payroll_type': 'regular',
            })

    def test_payroll_corporate_spreadsheet_view_and_calculations(self):
        dept_admin = self.env['hr.department'].create({'name': 'ADMINISTRACIÓN'})
        dept_sales = self.env['hr.department'].create({'name': 'VENDEDORES'})

        # Fixed wage employee
        emp_fixed = self.env['hr.employee'].create({'name': 'Emp Fixed', 'department_id': dept_admin.id})
        contract_fixed = self.env['hr.contract'].create({
            'name': 'Contract Fixed',
            'employee_id': emp_fixed.id,
            'contract_type_id': self.contract_type.id,
            'wage': 408.80,
            'state': 'open',
            'velatek_remuneration_mode': 'fixed',
            'velatek_payment_frequency': 'biweekly',
            'date_start': '2026-09-01',
        })

        # Production employee
        emp_prod = self.env['hr.employee'].create({'name': 'Emp Prod', 'department_id': dept_sales.id})
        contract_prod = self.env['hr.contract'].create({
            'name': 'Contract Prod',
            'employee_id': emp_prod.id,
            'contract_type_id': self.contract_type.id,
            'wage': 0.0,
            'state': 'open',
            'velatek_remuneration_mode': 'production',
            'velatek_production_unit': 'unit',
            'velatek_payment_frequency': 'biweekly',
            'date_start': '2026-09-01',
        })

        # Mixed employee
        emp_mixed = self.env['hr.employee'].create({'name': 'Emp Mixed', 'department_id': dept_sales.id})
        contract_mixed = self.env['hr.contract'].create({
            'name': 'Contract Mixed',
            'employee_id': emp_mixed.id,
            'contract_type_id': self.contract_type.id,
            'wage': 300.00,
            'state': 'open',
            'velatek_remuneration_mode': 'mixed',
            'velatek_production_unit': 'unit',
            'velatek_payment_frequency': 'biweekly',
            'date_start': '2026-09-01',
        })

        payroll = self.env['hr.payroll'].create({
            'name': 'Planilla Empresarial Septiembre Q2',
            'date_start': '2026-09-16',
            'date_end': '2026-09-30',
            'payment_frequency': 'biweekly',
            'payroll_type': 'regular',
            'state': 'draft',
        })

        payroll.action_load_employees()
        self.assertEqual(len(payroll.line_ids), 3)
        self.assertEqual(payroll.period_title, "PLANILLA CORRESPONDIENTE AL MES DE SEPTIEMBRE QUINCENA 2 - 2026")

        line_fixed = payroll.line_ids.filtered(lambda l: l.employee_id == emp_fixed)
        line_prod = payroll.line_ids.filtered(lambda l: l.employee_id == emp_prod)
        line_mixed = payroll.line_ids.filtered(lambda l: l.employee_id == emp_mixed)

        # Configure values for test
        line_fixed.write({'worked_days': 15, 'period_wage': 204.40, 'viaticos_amount': 20.60})
        line_prod.write({'production_amount': 675.00})
        line_mixed.write({'worked_days': 15, 'period_wage': 150.00, 'production_amount': 250.00, 'bonus_amount': 50.00, 'outside_payment_amount': 50.00})

        payroll.action_compute()

        # Fixed wage totals
        self.assertAlmostEqual(line_fixed.total_earned, 225.00, places=2)
        self.assertAlmostEqual(line_fixed.payment_amount, line_fixed.total_earned - line_fixed.isss_employee_amount - line_fixed.afp_employee_amount - line_fixed.income_tax_amount, places=2)
        self.assertAlmostEqual(line_fixed.total_to_settle, line_fixed.payment_amount, places=2)

        # Production wage totals (base wage part is 0)
        self.assertAlmostEqual(line_prod.total_earned, 675.00, places=2)
        self.assertAlmostEqual(line_prod.payment_amount, line_prod.total_earned - line_prod.isss_employee_amount - line_prod.afp_employee_amount - line_prod.income_tax_amount, places=2)

        # Mixed wage totals
        self.assertAlmostEqual(line_mixed.total_earned, 450.00, places=2)
        self.assertAlmostEqual(line_mixed.payment_amount, line_mixed.total_earned - line_mixed.isss_employee_amount - line_mixed.afp_employee_amount - line_mixed.income_tax_amount, places=2)
        self.assertAlmostEqual(line_mixed.total_to_settle, line_mixed.payment_amount + line_mixed.outside_payment_amount, places=2)

        emp_records = self.env['hr.employee'].create([{'name': 'Emp Batch %d' % i, 'department_id': dept_admin.id if i <= 30 else dept_sales.id} for i in range(1, 61)])
        contract_records = self.env['hr.contract'].create([{
            'name': 'Contract Batch %d' % i,
            'employee_id': emp.id,
            'contract_type_id': self.contract_type.id,
            'wage': 400.0,
            'state': 'open',
            'velatek_remuneration_mode': 'fixed',
            'velatek_payment_frequency': 'biweekly',
            'date_start': '2026-09-01',
        } for i, emp in enumerate(emp_records, 1)])
        lines_60 = [(0, 0, {
            'sequence': i,
            'employee_id': emp.id,
            'contract_id': contract.id,
            'department_id': emp.department_id.id,
            'remuneration_mode': 'fixed',
            'base_wage': 400.0,
            'period_wage': 200.0,
            'worked_days': 15,
        }) for i, (emp, contract) in enumerate(zip(emp_records, contract_records), 1)]

        payroll_60 = self.env['hr.payroll'].create({
            'name': 'Planilla 60 Empleados Test',
            'date_start': '2026-09-16',
            'date_end': '2026-09-30',
            'payment_frequency': 'biweekly',
            'payroll_type': 'regular',
            'state': 'draft',
            'line_ids': lines_60,
        })
        payroll_60.action_compute()
        self.assertEqual(len(payroll_60.line_ids), 60)
        self.assertEqual(sum(payroll_60.line_ids.mapped('total_earned')), 12000.0)

    def test_absence_days_calculation_by_day_count_mode(self):
        calendar_mon_fri = self.env['resource.calendar'].create({
            'name': 'Lunes a Viernes 40h Test',
            'hours_per_day': 8.0,
            'attendance_ids': [
                Command.create({'name': 'Mon Morning', 'dayofweek': '0', 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                Command.create({'name': 'Mon Afternoon', 'dayofweek': '0', 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'}),
                Command.create({'name': 'Tue Morning', 'dayofweek': '1', 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                Command.create({'name': 'Tue Afternoon', 'dayofweek': '1', 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'}),
                Command.create({'name': 'Wed Morning', 'dayofweek': '2', 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                Command.create({'name': 'Wed Afternoon', 'dayofweek': '2', 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'}),
                Command.create({'name': 'Thu Morning', 'dayofweek': '3', 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                Command.create({'name': 'Thu Afternoon', 'dayofweek': '3', 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'}),
                Command.create({'name': 'Fri Morning', 'dayofweek': '4', 'hour_from': 8, 'hour_to': 12, 'day_period': 'morning'}),
                Command.create({'name': 'Fri Afternoon', 'dayofweek': '4', 'hour_from': 13, 'hour_to': 17, 'day_period': 'afternoon'}),
            ],
        })

        employee = self.env['hr.employee'].create({'name': 'Empleado Horario L-V'})
        contract = self.env['hr.contract'].create({
            'name': 'Contrato L-V',
            'employee_id': employee.id,
            'contract_type_id': self.contract_type.id,
            'resource_calendar_id': calendar_mon_fri.id,
            'wage': 600,
            'state': 'open',
            'date_start': '2026-01-01',
        })

        concept_work_schedule = self.env['hr.incident.concept'].create({
            'name': 'Permiso no pagado por horario',
            'incident_category': 'other',
            'day_count_mode': 'work_schedule',
        })
        concept_calendar = self.env['hr.incident.concept'].create({
            'name': 'Incapacidad calendario',
            'incident_category': 'other',
            'day_count_mode': 'calendar',
        })

        absence_types = ('paid_leave', 'unpaid_leave', 'unjustified_absence', 'sick_leave', 'suspension')

        # Test Friday -> Monday (2026-09-18 to 2026-09-21)
        for inc_type in absence_types:
            inc_cal = self.env['hr.employee.incident'].create({
                'employee_id': employee.id,
                'contract_id': contract.id,
                'incident_type': inc_type,
                'concept_id': concept_calendar.id,
                'day_count_mode': 'calendar',
                'date_from': '2026-09-18',
                'date_to': '2026-09-21',
            })
            self.assertAlmostEqual(inc_cal.quantity_days, 4.0, places=2)

            inc_ws = self.env['hr.employee.incident'].create({
                'employee_id': employee.id,
                'contract_id': contract.id,
                'incident_type': inc_type,
                'concept_id': concept_work_schedule.id,
                'date_from': '2026-09-18',
                'date_to': '2026-09-21',
            })
            self.assertEqual(inc_ws.day_count_mode, 'work_schedule')
            self.assertAlmostEqual(inc_ws.quantity_days, 2.0, places=2)

        # Test Full Week Monday -> Sunday (2026-09-14 to 2026-09-20)
        inc_full_cal = self.env['hr.employee.incident'].create({
            'employee_id': employee.id,
            'contract_id': contract.id,
            'incident_type': 'unpaid_leave',
            'day_count_mode': 'calendar',
            'date_from': '2026-09-14',
            'date_to': '2026-09-20',
        })
        self.assertAlmostEqual(inc_full_cal.quantity_days, 7.0, places=2)

        inc_full_ws = self.env['hr.employee.incident'].create({
            'employee_id': employee.id,
            'contract_id': contract.id,
            'incident_type': 'unpaid_leave',
            'day_count_mode': 'work_schedule',
            'date_from': '2026-09-14',
            'date_to': '2026-09-20',
        })
        self.assertAlmostEqual(inc_full_ws.quantity_days, 5.0, places=2)

    def test_labor_rate_parametrization_and_historical_snapshot(self):
        rate_2026 = self.env['hr.sv.labor.rate'].search([
            ('code', '=', 'overtime_day'),
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
            ('date_from', '<=', '2026-09-10'),
        ], limit=1)
        if not rate_2026:
            rate_2026 = self.env['hr.sv.labor.rate'].create({
                'name': 'Horas extra 200%',
                'code': 'overtime_day',
                'rate_type': 'multiplier',
                'value': 2.0,
                'date_from': '2026-01-01',
                'company_id': self.env.company.id,
            })

        inc_2026 = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2026-09-10',
            'incident_type': 'overtime',
            'overtime_type': 'daytime',
            'quantity_hours': 2,
        })
        self.assertEqual(inc_2026.rate_parameter_id, rate_2026)
        self.assertAlmostEqual(inc_2026.rate_snapshot, 2.0)
        amount_2026 = inc_2026.amount

        rate_2026.date_to = '2026-12-31'
        rate_2027 = self.env['hr.sv.labor.rate'].create({
            'name': 'Horas extra 2027 (300%)',
            'code': 'overtime_day',
            'rate_type': 'multiplier',
            'value': 3.0,
            'date_from': '2027-01-01',
            'company_id': self.env.company.id,
        })

        inc_2026.invalidate_recordset(['rate_snapshot', 'amount'])
        self.assertEqual(inc_2026.rate_parameter_id, rate_2026)
        self.assertAlmostEqual(inc_2026.rate_snapshot, 2.0)
        self.assertAlmostEqual(inc_2026.amount, amount_2026)

        inc_2027 = self.env['hr.employee.incident'].create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'date': '2027-02-10',
            'incident_type': 'overtime',
            'overtime_type': 'daytime',
            'quantity_hours': 2,
        })
        self.assertEqual(inc_2027.rate_parameter_id, rate_2027)
        self.assertAlmostEqual(inc_2027.rate_snapshot, 3.0)

        other_company = self.env['res.company'].create({'name': 'Empresa sin tasas'})
        contract_other = self.env['hr.contract'].create({
            'name': 'Contrato Otra Empresa',
            'employee_id': self.employee.id,
            'company_id': other_company.id,
            'contract_type_id': self.contract_type.id,
            'wage': 500,
            'state': 'open',
            'date_start': '2026-01-01',
        })
        with self.assertRaises(ValidationError):
            self.env['hr.employee.incident'].create({
                'employee_id': self.employee.id,
                'contract_id': contract_other.id,
                'company_id': other_company.id,
                'date': '1980-09-10',
                'incident_type': 'overtime',
                'overtime_type': 'daytime',
                'quantity_hours': 2,
            })

        with self.assertRaises(ValidationError):
            self.env['hr.sv.labor.rate'].create({
                'name': 'Tasa solapada',
                'code': 'overtime_day',
                'rate_type': 'multiplier',
                'value': 2.5,
                'date_from': '2027-01-15',
                'company_id': self.env.company.id,
            })

    def test_statutory_engine_parametrization_and_snapshots(self):
        payroll_2026 = self.env['hr.payroll'].create({
            'name': 'Planilla Septiembre 2026',
            'company_id': self.env.company.id,
            'date_start': '2026-09-01',
            'date_end': '2026-09-30',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        line_2026 = self.env['hr.payroll.line'].create({
            'payroll_id': payroll_2026.id,
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'remuneration_mode': 'fixed',
            'period_wage': 1000.0,
            'days_to_pay': 30,
        })
        self.assertAlmostEqual(line_2026.isss_employee_amount, 30.0)
        self.assertAlmostEqual(line_2026.afp_employee_amount, 72.50)
        self.assertAlmostEqual(line_2026.income_tax_amount, 60.45)
        self.assertEqual(line_2026.isss_rate_snapshot, 3.0)
        self.assertEqual(line_2026.afp_rate_snapshot, 7.25)
        self.assertEqual(line_2026.isr_rate_snapshot, 20.0)

        payroll_2026.write({'state': 'computed'})
        payroll_2026.write({'state': 'validated'})
        payroll_2026.write({'state': 'closed'})

        isss_2026 = self.env['hr.sv.social.security.parameter'].get_active_parameter('2026-09-15', self.env.company)
        isss_2026.write({'date_to': '2026-12-31'})

        afp_2026 = self.env['hr.sv.pension.parameter'].get_active_parameter('2026-09-15', self.env.company)
        afp_2026.write({'date_to': '2026-12-31'})

        self.env['hr.sv.social.security.parameter'].create({
            'name': 'ISSS 2027',
            'code': 'isss_general',
            'date_from': '2027-01-01',
            'employee_rate': 4.0,
            'employer_rate': 8.0,
            'max_base': 1000.0,
            'company_id': self.env.company.id,
        })
        self.env['hr.sv.pension.parameter'].create({
            'name': 'AFP 2027',
            'code': 'afp_general',
            'date_from': '2027-01-01',
            'employee_rate': 8.0,
            'employer_rate': 9.0,
            'max_base': 7045.06,
            'company_id': self.env.company.id,
        })

        self.assertEqual(line_2026.isss_rate_snapshot, 3.0)
        self.assertEqual(line_2026.afp_rate_snapshot, 7.25)
        self.assertAlmostEqual(line_2026.isss_employee_amount, 30.0)

        payroll_2027 = self.env['hr.payroll'].create({
            'name': 'Planilla Enero 2027',
            'company_id': self.env.company.id,
            'date_start': '2027-01-01',
            'date_end': '2027-01-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        line_2027 = self.env['hr.payroll.line'].create({
            'payroll_id': payroll_2027.id,
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'remuneration_mode': 'fixed',
            'period_wage': 1000.0,
            'days_to_pay': 30,
        })

        self.assertEqual(line_2027.isss_rate_snapshot, 4.0)
        self.assertEqual(line_2027.afp_rate_snapshot, 8.0)
        self.assertAlmostEqual(line_2027.isss_employee_amount, 40.0)
        self.assertAlmostEqual(line_2027.afp_employee_amount, 80.0)

        payroll_1980 = self.env['hr.payroll'].create({
            'name': 'Planilla 1980 Sin Parametros',
            'company_id': self.env.company.id,
            'date_start': '1980-01-01',
            'date_end': '1980-01-31',
            'payment_frequency': 'monthly',
            'state': 'draft',
        })
        # Missing historical parameters must block calculation, never invent rates.
        with self.assertRaises(ValidationError):
            self.env['hr.payroll.line'].create({
                'payroll_id': payroll_1980.id,
                'employee_id': self.employee.id,
                'contract_id': self.contract.id,
                'remuneration_mode': 'fixed',
                'period_wage': 500.0,
                'days_to_pay': 30,
            })















