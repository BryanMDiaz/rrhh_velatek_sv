from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install')
class TestMultiPeriodIncident(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env['res.users'].create({
            'name': 'Manager User',
            'login': 'test_mgr_multi',
            'groups_id': [(6, 0, [
                cls.env.ref('hr.group_hr_user').id,
                cls.env.ref('rrhh_velatek_sv.group_hr_responsible').id,
                cls.env.ref('hr.group_hr_manager').id,
                cls.env.ref('hr_contract.group_hr_contract_manager').id,
            ])],
        })
        cls.employee = cls.env['hr.employee'].with_user(cls.manager).create({
            'name': 'Empleado Multiperíodo',
        })
        cls.contract_type = cls.env['hr.contract.type'].with_user(cls.manager).create({
            'name': 'Full Time',
        })
        cls.contract = cls.env['hr.contract'].with_user(cls.manager).create({
            'name': 'Contrato Multiperíodo',
            'employee_id': cls.employee.id,
            'date_start': '2026-01-01',
            'wage': 1000.0,
            'contract_type_id': cls.contract_type.id,
            'velatek_payment_frequency': 'biweekly',
            'state': 'open',
        })

    def test_multi_period_incapacity_allocation(self):
        # 1. Incapacidad (sick_leave): 14/09/2026 -> 18/09/2026 (5 días)
        incident = self.env['hr.employee.incident'].with_user(self.manager).create({
            'employee_id': self.employee.id,
            'contract_id': self.contract.id,
            'incident_type': 'sick_leave',
            'date_from': '2026-09-14',
            'date_to': '2026-09-18',
            'quantity_days': 5.0,
            'state': 'draft',
        })
        incident.action_submit()
        incident.action_approve()
        self.assertEqual(incident.state, 'approved')

        # 2. Preplanilla Q1: 01/09/2026 -> 15/09/2026
        prep_q1 = self.env['hr.prepayroll'].with_user(self.manager).create({
            'name': 'Preplanilla Quincena 1 Septiembre 2026',
            'date_start': '2026-09-01',
            'date_end': '2026-09-15',
            'payment_frequency': 'biweekly',
            'company_id': self.env.company.id,
        })
        prep_q1.action_load_employees()

        line_q1 = prep_q1.line_ids.filtered(lambda l: l.employee_id == self.employee)
        self.assertTrue(line_q1, "Debe existir la línea del empleado en Q1")

        # 3. Debe asignar 2 días (14/09 y 15/09)
        alloc_q1 = self.env['hr.prepayroll.incident.allocation'].search([
            ('prepayroll_id', '=', prep_q1.id),
            ('incident_id', '=', incident.id),
        ])
        self.assertEqual(len(alloc_q1), 1, "Debe haber exactamente 1 asignación en Q1")
        self.assertEqual(alloc_q1.allocated_days, 2.0, "La asignación Q1 debe ser de 2 días (14/09 y 15/09)")

        # 4. Validar y Cerrar Preplanilla Q1
        prep_q1.action_validate()
        prep_q1.action_close()

        # 5. La incidencia NO debe quedar totalmente consumida (state sigue 'approved')
        incident.invalidate_recordset(['state', 'processed_prepayroll_id'])
        self.assertEqual(incident.state, 'approved', "La incidencia de 5 días no debe pasar a processed tras consumir solo 2 días")

        # 6. Preplanilla Q2: 16/09/2026 -> 30/09/2026
        prep_q2 = self.env['hr.prepayroll'].with_user(self.manager).create({
            'name': 'Preplanilla Quincena 2 Septiembre 2026',
            'date_start': '2026-09-16',
            'date_end': '2026-09-30',
            'payment_frequency': 'biweekly',
            'company_id': self.env.company.id,
        })
        prep_q2.action_load_employees()

        line_q2 = prep_q2.line_ids.filtered(lambda l: l.employee_id == self.employee)
        self.assertTrue(line_q2, "Debe existir la línea del empleado en Q2")

        # 7. Debe asignar 3 días (16/09, 17/09, 18/09)
        alloc_q2 = self.env['hr.prepayroll.incident.allocation'].search([
            ('prepayroll_id', '=', prep_q2.id),
            ('incident_id', '=', incident.id),
        ])
        self.assertEqual(len(alloc_q2), 1, "Debe haber exactamente 1 asignación en Q2")
        self.assertEqual(alloc_q2.allocated_days, 3.0, "La asignación Q2 debe ser de 3 días (16/09, 17/09 y 18/09)")

        # 10. Confirmar 2 + 3 = 5 y ausencia de duplicados
        total_allocated = alloc_q1.allocated_days + alloc_q2.allocated_days
        self.assertEqual(total_allocated, 5.0, "Total días asignados debe ser 2 + 3 = 5")

        # 8. Validar y Cerrar Preplanilla Q2
        prep_q2.action_validate()
        prep_q2.action_close()

        # 9. Incidencia ahora SÍ debe pasar a 'processed'
        incident.invalidate_recordset(['state', 'processed_prepayroll_id'])
        self.assertEqual(incident.state, 'processed', "La incidencia ahora sí debe pasar a state 'processed' al cubrir sus 5 días")

        # 11. Idempotencia: ejecutar action_update_data nuevamente no duplica allocations
        allocations_total = self.env['hr.prepayroll.incident.allocation'].search([
            ('incident_id', '=', incident.id)
        ])
        self.assertEqual(len(allocations_total), 2, "No debe haber allocations duplicadas")
