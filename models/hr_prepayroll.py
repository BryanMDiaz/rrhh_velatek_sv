from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import format_amount, format_date
from .operational_security import (
    USER, RESPONSIBLE, MANAGER, require_group, check_create, check_write, check_edit, check_workflow_write,
)

class HrPrepayroll(models.Model):
    _name = 'hr.prepayroll'
    _description = 'Preplanilla / Operación Mensual'
    _order = 'date_start desc'

    _ALLOWED_TRANSITIONS = {
        'draft': {'loaded', 'cancelled'},
        'loaded': {'draft', 'validated', 'cancelled'},
        'validated': {'draft', 'closed', 'cancelled'},
        'closed': set(),
        'cancelled': set(),
    }

    name = fields.Char(string='Nombre', required=True)
    is_test = fields.Boolean(string='De prueba', copy=False)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company, required=True)
    date_start = fields.Date(string='Fecha Inicio', required=True)
    date_end = fields.Date(string='Fecha Fin', required=True)
    payment_frequency = fields.Selection([
        ('monthly', 'Mensual'),
        ('biweekly', 'Quincenal'),
        ('weekly', 'Semanal'),
        ('fourteen_days', 'Catorcenal'),
        ('other', 'Otro')
    ], string='Frecuencia', required=True)
    
    prepayroll_type = fields.Selection([
        ('regular', 'Planilla ordinaria'),
        ('professional_services', 'Servicios Profesionales'),
    ], string='Tipo de Preplanilla', required=True, default='regular')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('loaded', 'Empleados cargados'),
        ('validated', 'Validado'),
        ('closed', 'Cerrado'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='draft', copy=False, required=True)
    
    notes = fields.Text(string='Notas')

    _MONTH_NAMES = (
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
    )

    period_name = fields.Char(
        string='Período de preplanilla', compute='_compute_period_name',
        store=True, readonly=True, index=True,
    )
    
    line_ids = fields.One2many('hr.prepayroll.line', 'prepayroll_id', string='Líneas')
    allocation_ids = fields.One2many('hr.prepayroll.incident.allocation', 'prepayroll_id', string='Asignaciones de Incidencias')
    payroll_ids = fields.One2many('hr.payroll', 'prepayroll_id', string='Planillas asociadas')
    payroll_count = fields.Integer(compute='_compute_payroll_count')

    @api.depends('date_start', 'payment_frequency')
    def _compute_period_name(self):
        for record in self:
            ref_date = record.date_start
            if not ref_date:
                record.period_name = False
                continue

            month_year = '%s %s' % (
                self._MONTH_NAMES[ref_date.month], ref_date.year,
            )
            if record.payment_frequency == 'biweekly':
                half = 1 if ref_date.day <= 15 else 2
                record.period_name = 'Quincena %s - %s' % (half, month_year)
            elif record.payment_frequency == 'weekly':
                week = ref_date.isocalendar()[1]
                record.period_name = 'Semana %s - %s' % (week, month_year)
            elif record.payment_frequency == 'fourteen_days':
                record.period_name = 'Catorcena - %s' % month_year
            else:
                record.period_name = month_year

    @api.depends('payroll_ids', 'payroll_ids.state')
    def _compute_payroll_count(self):
        for record in self:
            record.payroll_count = len(record.payroll_ids.filtered(lambda p: p.state != 'cancelled'))

    @api.model_create_multi
    def create(self, vals_list):
        check_create(self, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        return self._write_operational_values(vals)

    def _write_operational_values(self, vals, *, from_workflow=False):
        check_workflow_write(self, vals, from_workflow)
        if 'is_test' in vals and any(record.is_test != bool(vals['is_test']) for record in self):
            raise ValidationError(_('Seleccione el modo de prueba al crear la preplanilla.'))
        if set(vals) - {'state'} and self.filtered(lambda prep: prep.state in ('validated', 'closed')):
            raise ValidationError(_('La preplanilla está validada o cerrada. No puede modificar sus datos.'))
        if 'state' in vals:
            target_state = vals['state']
            for prepayroll in self:
                if (
                    target_state != prepayroll.state
                    and target_state not in self._ALLOWED_TRANSITIONS.get(prepayroll.state, set())
                ):
                    raise ValidationError(_(
                        "No se permite cambiar la preplanilla de '%(current_state)s' a '%(target_state)s'.",
                        current_state=prepayroll.state,
                        target_state=target_state,
                    ))
        check_write(self, vals)
        return super().write(vals)

    def unlink(self):
        for prepayroll in self:
            if prepayroll.state in ('validated', 'closed'):
                raise ValidationError(_("No se puede eliminar una preplanilla validada o cerrada. Debe utilizarse un proceso formal de reversión."))
        cancelled = self.filtered(lambda prepayroll: prepayroll.state == 'cancelled')
        if cancelled:
            require_group(cancelled, MANAGER)
        check_edit(self - cancelled)
        return super().unlink()

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for prepayroll in self:
            if prepayroll.date_start and prepayroll.date_end and prepayroll.date_end < prepayroll.date_start:
                raise ValidationError("La fecha de fin no puede ser anterior a la de inicio.")

    @api.constrains('date_start', 'date_end', 'company_id', 'payment_frequency', 'state', 'is_test')
    def _check_overlap(self):
        for prepayroll in self:
            if prepayroll.is_test or prepayroll.state == 'cancelled':
                continue
            if not prepayroll.date_start or not prepayroll.date_end or not prepayroll.company_id or not prepayroll.payment_frequency:
                continue
            
            domain = [
                ('id', '!=', prepayroll.id),
                ('company_id', '=', prepayroll.company_id.id),
                ('payment_frequency', '=', prepayroll.payment_frequency),
                ('state', '!=', 'cancelled'),
                ('is_test', '=', False),
                ('date_start', '<=', prepayroll.date_end),
                ('date_end', '>=', prepayroll.date_start),
            ]
            overlapping = self.search(domain, limit=1)
            if overlapping:
                raise ValidationError("Ya existe una preplanilla activa que se superpone con este período para la misma compañía y frecuencia.")

    def action_load_employees(self):
        require_group(self, USER)
        self.ensure_one()
        if self.state not in ('draft', 'loaded'):
            raise ValidationError("Solo se pueden cargar empleados en estado Borrador o Cargados.")
        
        # Obtener contratos activos en el periodo
        domain = [
            '|', ('state', '=', 'open'), '&', ('state', '=', 'close'), ('date_end', '!=', False),
            ('company_id', '=', self.company_id.id),
            '|', ('date_start', '<=', self.date_end), ('date_start', '=', False),
            '|', ('date_end', '>=', self.date_start), ('date_end', '=', False),
        ]
        
        if self.prepayroll_type == 'professional_services':
            domain.append(('employee_id.velatek_employment_type', '=', 'professional_services'))
        else:
            domain.append(('employee_id.velatek_employment_type', '!=', 'professional_services'))
        
        if self.payment_frequency:
            domain.append(('velatek_payment_frequency', '=', self.payment_frequency))
            
        contracts = self.env['hr.contract'].search(domain)
        
        existing_keys = {
            (line.employee_id.id, line.contract_id.id)
            for line in self.line_ids
        }
        
        lines_to_create = []
        for contract in contracts:
            key = (contract.employee_id.id, contract.id)
            if key in existing_keys:
                continue
                
            existing_keys.add(key)
            
            # Calculate days to pay
            start_date = max(contract.date_start, self.date_start) if contract.date_start else self.date_start
            end_date = min(contract.date_end, self.date_end) if contract.date_end else self.date_end
            
            if start_date <= end_date:
                days_to_pay = (end_date - start_date).days + 1
            else:
                days_to_pay = 0

            base_wage, period_wage = contract._velatek_wage_for_period(
                self.date_start, self.date_end, self.payment_frequency,
            )
            lines_to_create.append((0, 0, {
                'employee_id': contract.employee_id.id,
                'contract_id': contract.id,
                'department_id': (contract.department_id or contract.employee_id.department_id).id,
                'job_id': contract.job_id.id,
                'remuneration_mode': contract.velatek_remuneration_mode,
                'base_wage': base_wage,
                'days_to_pay': days_to_pay,
                'period_wage': period_wage,
                'production_unit': contract.velatek_production_unit,
                'production_rate': contract.velatek_production_rate,
            }))
                
        if lines_to_create:
            self.write({'line_ids': lines_to_create})
            
        self._write_operational_values({'state': 'loaded'}, from_workflow=True)
        self.action_update_data()

    def _refresh_line_contract_values(self):
        for line in self.line_ids:
            contract = line.contract_id
            department = contract.department_id or line.employee_id.department_id
            base_wage, period_wage = contract._velatek_wage_for_period(
                self.date_start, self.date_end, self.payment_frequency,
            )
            values = {
                'base_wage': base_wage,
                'period_wage': period_wage,
                'department_id': department.id,
            }
            changes = {
                field: value for field, value in values.items()
                if (line.department_id.id if field == 'department_id' else line[field]) != value
            }
            if changes:
                line.write(changes)

    def _period_incident_domain(self, line_employees, line_contracts):
        self.ensure_one()
        return [
            ('employee_id', 'in', line_employees.ids),
            ('contract_id', 'in', line_contracts.ids),
            ('state', 'in', ('approved', 'processed') if self.is_test else ('approved',)),
            '|',
            '&', '&', ('date_from', '=', False), ('date', '>=', self.date_start), ('date', '<=', self.date_end),
            '&', '&', ('date_from', '!=', False), ('date_from', '<=', self.date_end), ('date_to', '>=', self.date_start),
        ]

    def action_update_data(self):
        require_group(self, USER)
        self.ensure_one()
        if self.state not in ('loaded', 'draft'):
            raise ValidationError("No se pueden actualizar datos en este estado.")

        lines = self.line_ids
        if not lines:
            return

        self._refresh_line_contract_values()
        line_employees = lines.mapped('employee_id')
        line_contracts = lines.mapped('contract_id')

        # 1. Batch query: producciones ya vinculadas en otras preplanillas validadas/cerradas
        other_validated_lines = self.env['hr.prepayroll.line'].search([
            ('prepayroll_id', '!=', self.id),
            ('prepayroll_id.is_test', '=', False),
            ('prepayroll_id.state', 'in', ['validated', 'closed'])
        ])
        already_linked_prod_ids = set(other_validated_lines.mapped('production_ids').ids)

        # 2. Batch query: producciones candidatas aprobadas
        prod_domain = [
            ('employee_id', 'in', line_employees.ids),
            ('contract_id', 'in', line_contracts.ids),
            ('state', '=', 'approved'),
            ('date', '>=', self.date_start),
            ('date', '<=', self.date_end),
            ('processed_prepayroll_id', '=', False),
        ]
        all_candidate_prods = self.env['hr.employee.production'].search(prod_domain)

        productions_by_key = {}
        for prod in all_candidate_prods:
            if prod.id in already_linked_prod_ids:
                continue
            key = (prod.employee_id.id, prod.contract_id.id)
            if key not in productions_by_key:
                productions_by_key[key] = self.env['hr.employee.production']
            productions_by_key[key] |= prod

        # 3. Batch query: incidencias candidatas aprobadas
        inc_domain = self._period_incident_domain(line_employees, line_contracts)
        all_candidate_incidents = self.env['hr.employee.incident'].search(inc_domain)

        incidents_by_key = {}
        for inc in all_candidate_incidents:
            key = (inc.employee_id.id, inc.contract_id.id)
            if key not in incidents_by_key:
                incidents_by_key[key] = self.env['hr.employee.incident']
            incidents_by_key[key] |= inc

        # 4. Batch query: asignaciones actuales en esta preplanilla
        all_current_allocations = self.env['hr.prepayroll.incident.allocation'].search([
            ('prepayroll_id', '=', self.id),
        ])
        current_allocs_by_line = {}
        for alloc in all_current_allocations:
            line_id = alloc.prepayroll_line_id.id
            if line_id not in current_allocs_by_line:
                current_allocs_by_line[line_id] = self.env['hr.prepayroll.incident.allocation']
            current_allocs_by_line[line_id] |= alloc

        # 5. Batch query: asignaciones en otras preplanillas validadas/cerradas
        other_allocations = self.env['hr.prepayroll.incident.allocation'].search([
            ('incident_id', 'in', all_candidate_incidents.ids),
            ('prepayroll_id', '!=', self.id),
            ('prepayroll_id.is_test', '=', False),
            ('prepayroll_id.state', 'in', ('validated', 'closed')),
        ])
        other_allocs_by_inc = {}
        for oa in other_allocations if not self.is_test else ():
            inc_id = oa.incident_id.id
            if inc_id not in other_allocs_by_inc:
                other_allocs_by_inc[inc_id] = []
            other_allocs_by_inc[inc_id].append((oa.date_from, oa.date_to))

        # 6. Batch query: Cuotas de préstamo
        loan_domain = [
            ('loan_id.employee_id', 'in', line_employees.ids),
            ('loan_id.contract_id', 'in', line_contracts.ids),
            ('state', 'in', ['pending', 'scheduled']),
            ('scheduled_date', '>=', self.date_start),
            ('scheduled_date', '<=', self.date_end),
            ('loan_id.state', '=', 'active'),
            ('loan_id.advance_prepayroll_id', '=', False),
        ]
        all_candidate_installments = self.env['hr.employee.loan.installment'].search(loan_domain)

        installments_by_key = {}
        for inst in all_candidate_installments:
            key = (inst.loan_id.employee_id.id, inst.loan_id.contract_id.id)
            if key not in installments_by_key:
                installments_by_key[key] = self.env['hr.employee.loan.installment']
            installments_by_key[key] |= inst

        for line in lines:
            key = (line.employee_id.id, line.contract_id.id)

            # Reset values
            line.production_quantity = 0.0
            line.production_amount = 0.0
            line.overtime_hours = 0.0
            line.overtime_amount = 0.0
            line.night_work_hours = 0.0
            line.night_work_amount = 0.0
            line.holiday_work_hours = 0.0
            line.holiday_work_amount = 0.0
            line.weekly_rest_work_hours = 0.0
            line.weekly_rest_work_amount = 0.0
            line.absence_days = 0.0
            line.absence_hours = 0.0
            line.absence_amount = 0.0
            line.paid_leave_days = 0.0
            line.paid_leave_hours = 0.0
            line.sick_leave_days = 0.0
            line.sick_leave_hours = 0.0
            line.suspension_days = 0.0
            line.suspension_hours = 0.0
            line.bonus_amount = 0.0
            line.commission_amount = 0.0
            line.viaticos_amount = 0.0
            line.other_income = 0.0
            line.deduction_amount = 0.0
            line.loan_deduction_amount = 0.0

            # Préstamos
            candidate_installments = installments_by_key.get(key, self.env['hr.employee.loan.installment'])
            line.loan_installment_ids = [(6, 0, candidate_installments.ids)]
            line.loan_deduction_amount = sum(candidate_installments.mapped('remaining_amount'))
            
            # Produccion
            productions = productions_by_key.get(key, self.env['hr.employee.production'])
            line.production_ids = [(6, 0, productions.ids)]

            if productions:
                line.production_quantity = sum(productions.mapped('quantity'))
                line.production_amount = sum(productions.mapped('amount'))

            # Incidencias
            candidate_incidents = incidents_by_key.get(key, self.env['hr.employee.incident'])
            current_allocations = current_allocs_by_line.get(line.id, self.env['hr.prepayroll.incident.allocation'])
            kept_allocations = self.env['hr.prepayroll.incident.allocation']

            for inc in candidate_incidents:
                if inc.date_from and inc.date_to:
                    inter_start = max(inc.date_from, self.date_start)
                    inter_end = min(inc.date_to, self.date_end)
                    if inter_start > inter_end:
                        continue

                    # Excluir si este rango de fechas ya fue asignado en otra preplanilla validada o cerrada
                    has_conflict = False
                    for (o_from, o_to) in other_allocs_by_inc.get(inc.id, []):
                        if o_from and o_to and o_from <= inter_end and o_to >= inter_start:
                            has_conflict = True
                            break
                    if has_conflict:
                        continue

                    total_range_days = (inc.date_to - inc.date_from).days + 1
                    overlap_days = (inter_end - inter_start).days + 1
                    ratio = overlap_days / total_range_days
                    if inc.quantity_days and inc.day_count_mode == 'work_schedule':
                        overlap_days = inc._get_absence_days(
                            inter_start, inter_end, inc.day_count_mode, inc.contract_id, inc.employee_id,
                        )
                        scheduled_days = inc._get_absence_days(
                            inc.date_from, inc.date_to, inc.day_count_mode, inc.contract_id, inc.employee_id,
                        )
                        ratio = overlap_days / scheduled_days if scheduled_days else 0.0
                    alloc_days = inc.quantity_days * ratio

                    alloc_hours = inc.quantity_hours * ratio
                    alloc_amount = inc.amount * ratio
                else:
                    inter_start = inc.date
                    inter_end = inc.date

                    if inc.id in other_allocs_by_inc:
                        continue

                    alloc_days = inc.quantity_days
                    alloc_hours = inc.quantity_hours
                    alloc_amount = inc.amount

                alloc = current_allocations.filtered(
                    lambda a: a.incident_id == inc and a.date_from == inter_start and a.date_to == inter_end
                )
                if not alloc:
                    alloc = self.env['hr.prepayroll.incident.allocation'].create({
                        'prepayroll_id': self.id,
                        'prepayroll_line_id': line.id,
                        'incident_id': inc.id,
                        'date_from': inter_start,
                        'date_to': inter_end,
                        'allocated_days': alloc_days,
                        'allocated_hours': alloc_hours,
                        'allocated_amount': alloc_amount,
                    })
                else:
                    alloc.write({
                        'allocated_days': alloc_days,
                        'allocated_hours': alloc_hours,
                        'allocated_amount': alloc_amount,
                    })
                kept_allocations |= alloc

            (current_allocations - kept_allocations).unlink()

            line.incident_ids = [(6, 0, kept_allocations.mapped('incident_id').ids)]

            def _sum_alloc(incident_type, field_name):
                allocs = kept_allocations.filtered(lambda a: a.incident_id.incident_type == incident_type)
                return sum(getattr(a, field_name) for a in allocs)

            def _sum_alloc_types(incident_types, field_name):
                allocs = kept_allocations.filtered(lambda a: a.incident_id.incident_type in incident_types)
                return sum(getattr(a, field_name) for a in allocs)

            line.overtime_hours = sum(a.allocated_hours for a in kept_allocations if a.incident_id.incident_type == 'overtime' and a.incident_id.overtime_type == 'daytime')
            line.overtime_amount = sum(a.allocated_amount for a in kept_allocations if a.incident_id.incident_type == 'overtime' and a.incident_id.overtime_type == 'daytime')

            line.night_overtime_hours = sum(a.allocated_hours for a in kept_allocations if a.incident_id.incident_type == 'overtime' and a.incident_id.overtime_type == 'nighttime')
            line.night_overtime_amount = sum(a.allocated_amount for a in kept_allocations if a.incident_id.incident_type == 'overtime' and a.incident_id.overtime_type == 'nighttime')


            line.night_work_hours = _sum_alloc('night_work', 'allocated_hours')
            line.night_work_amount = _sum_alloc('night_work', 'allocated_amount')

            line.holiday_work_hours = sum(
                (a.allocated_hours + (a.allocated_days * (a.contract_id._velatek_terms_on(a.date_from)[2])))
                for a in kept_allocations.filtered(lambda x: x.incident_id.incident_type == 'holiday_work')
            )
            line.holiday_work_amount = _sum_alloc('holiday_work', 'allocated_amount')

            line.weekly_rest_work_hours = sum(
                (a.allocated_hours + (a.allocated_days * (a.contract_id._velatek_terms_on(a.date_from)[2])))
                for a in kept_allocations.filtered(lambda x: x.incident_id.incident_type == 'weekly_rest_work')
            )
            line.weekly_rest_work_amount = _sum_alloc('weekly_rest_work', 'allocated_amount')

            absence_types = ('unpaid_leave', 'unjustified_absence', 'suspension')
            line.absence_days = _sum_alloc_types(absence_types, 'allocated_days')
            line.absence_hours = _sum_alloc_types(absence_types, 'allocated_hours')
            line.absence_amount = _sum_alloc_types(absence_types, 'allocated_amount')

            line.paid_leave_days = _sum_alloc('paid_leave', 'allocated_days')
            line.paid_leave_hours = _sum_alloc('paid_leave', 'allocated_hours')
            line.sick_leave_days = _sum_alloc('sick_leave', 'allocated_days')
            line.sick_leave_hours = _sum_alloc('sick_leave', 'allocated_hours')
            sick_allocations = kept_allocations.filtered(lambda a: a.incident_id.incident_type == 'sick_leave')
            for amount_field, rate_field in (
                ('sick_ordinary_amount', 'sick_ordinary_daily'),
                ('sick_employer_amount', 'sick_employer_daily'),
                ('sick_isss_amount', 'sick_isss_daily'),
            ):
                line[amount_field] = line.currency_id.round(sum(
                    a.allocated_days * a.incident_id[rate_field] for a in sick_allocations
                ))
            line.suspension_days = _sum_alloc('suspension', 'allocated_days')
            line.suspension_hours = _sum_alloc('suspension', 'allocated_hours')

            line.bonus_amount = _sum_alloc('bonus', 'allocated_amount')
            line.commission_amount = _sum_alloc('commission', 'allocated_amount')
            line.viaticos_amount = _sum_alloc('viaticos', 'allocated_amount')
            line.other_income = _sum_alloc('other_income', 'allocated_amount')
            line.deduction_amount = _sum_alloc('deduction', 'allocated_amount')

    @api.constrains('state')
    def _check_minimum_wage_on_validation(self):
        for period in self.filtered(lambda record: record.state in ('validated', 'closed')):
            period.line_ids._validate_period_minimum_wages(period)

    def action_validate(self):
        require_group(self, RESPONSIBLE)
        self.ensure_one()

        if self.state in ('draft', 'loaded'):
            self.action_update_data()

        errors = []

        # 14. No existen líneas duplicadas
        seen_keys = set()
        for line in self.line_ids:
            if line.employee_id and line.contract_id:
                key = (line.employee_id.id, line.contract_id.id)
                if key in seen_keys:
                    emp_name = line.employee_id.display_name or f"Empleado {line.employee_id.id}"
                    errors.append(f"{emp_name}: existen líneas de preplanilla duplicadas para el mismo contrato.")
                else:
                    seen_keys.add(key)

        # Precalculate allocations for all lines in self
        all_prepayroll_allocations = self.env['hr.prepayroll.incident.allocation'].search([('prepayroll_id', '=', self.id)])
        allocations_by_line = {}
        for alloc in all_prepayroll_allocations:
            line_id = alloc.prepayroll_line_id.id
            if line_id not in allocations_by_line:
                allocations_by_line[line_id] = self.env['hr.prepayroll.incident.allocation']
            allocations_by_line[line_id] |= alloc

        for line in self.line_ids:
            emp_name = line.employee_id.display_name if line.employee_id else "Empleado sin definir"

            # 1. employee_id existe
            if not line.employee_id:
                errors.append("Faltan datos de empleado en una de las líneas de preplanilla.")
                continue

            # 2. contract_id existe
            if not line.contract_id:
                errors.append(f"{emp_name}: falta el contrato en la línea de preplanilla.")
                continue

            contract = line.contract_id

            # 3. contrato pertenece al empleado
            if contract.employee_id != line.employee_id:
                errors.append(f"{emp_name}: el contrato '{contract.display_name}' no pertenece al empleado.")

            # 4. contrato pertenece a la compañía & 15. compañía coherente
            if contract.company_id != self.company_id:
                errors.append(f"{emp_name}: la compañía del contrato '{contract.display_name}' no coincide con la compañía de la preplanilla.")

            if line.company_id and line.company_id != self.company_id:
                errors.append(f"{emp_name}: la compañía de la línea no coincide con la compañía de la preplanilla.")

            # 5. contrato intersecta el período
            if self.date_start and self.date_end:
                if contract.date_start and contract.date_start > self.date_end:
                    errors.append(f"{emp_name}: contrato fuera de período (inicia {contract.date_start}, período fin {self.date_end}).")
                if contract.date_end and contract.date_end < self.date_start:
                    errors.append(f"{emp_name}: contrato fuera de período (finaliza {contract.date_end}, período inicio {self.date_start}).")

            # 6. frecuencia contractual compatible
            if contract.velatek_payment_frequency and self.payment_frequency:
                if contract.velatek_payment_frequency != self.payment_frequency:
                    errors.append(f"{emp_name}: la frecuencia contractual '{contract.velatek_payment_frequency}' no coincide con la frecuencia de la preplanilla '{self.payment_frequency}'.")

            # 7. modalidad de remuneración válida & 13. tarifa/unidad de producción válida
            if line.remuneration_mode in ('production', 'mixed'):
                if not line.production_unit:
                    errors.append(f"{emp_name}: tiene modalidad por producción pero no se definió unidad de producción.")
                if line.production_rate < 0:
                    errors.append(f"{emp_name}: la tarifa de producción es negativa ({line.production_rate}).")

            # 8, 10, 11, 13, 15. Fuentes de Producción
            for prod in line.production_ids:
                prod_name = prod.display_name or f"Producción ID {prod.id}"
                if prod.employee_id != line.employee_id or prod.contract_id != line.contract_id:
                    errors.append(f"{emp_name}: producción vinculada a otro contrato.")
                if prod.company_id != self.company_id:
                    errors.append(f"{emp_name}: producción '{prod_name}' pertenece a otra compañía.")
                if prod.state != 'approved':
                    errors.append(f"{emp_name}: la producción '{prod_name}' no está aprobada.")
                if prod.processed_prepayroll_id and prod.processed_prepayroll_id != self:
                    errors.append(f"{emp_name}: producción '{prod_name}' ya fue procesada por otra preplanilla.")
                if prod.quantity <= 0:
                    errors.append(f"{emp_name}: producción '{prod_name}' tiene cantidad menor o igual a cero.")
                if prod.rate < 0:
                    errors.append(f"{emp_name}: producción '{prod_name}' tiene tarifa negativa.")

            # 9, 10, 11, 15. Fuentes de Incidencias
            allocations = allocations_by_line.get(line.id, self.env['hr.prepayroll.incident.allocation'])
            incidents = line.incident_ids | allocations.mapped('incident_id')
            for inc in incidents:
                inc_name = inc.display_name or f"Incidencia ID {inc.id}"
                if inc.state == 'approved':
                    try:
                        inc._check_payroll_rules()
                    except ValidationError as error:
                        errors.append(f"{emp_name}: {error}")
                if inc.employee_id != line.employee_id or inc.contract_id != line.contract_id:
                    errors.append(f"{emp_name}: incidencia vinculada a otro contrato.")
                if inc.company_id != self.company_id:
                    errors.append(f"{emp_name}: incidencia '{inc_name}' pertenece a otra compañía.")
                if inc.state not in ('approved', 'processed'):
                    errors.append(f"{emp_name}: la incidencia '{inc_name}' no está aprobada.")
                if (inc.incident_type in ('overtime', 'night_work', 'holiday_work', 'weekly_rest_work')
                        and contract.velatek_remuneration_mode in ('production', 'mixed')
                        and inc.amount <= 0):
                    errors.append(f"{emp_name}: la incidencia '{inc_name}' tiene remuneración variable y carece de importe.")
                if not self.is_test and inc.state == 'processed' and inc.processed_prepayroll_id and inc.processed_prepayroll_id != self:
                    errors.append(f"{emp_name}: incidencia '{inc_name}' ya fue procesada por otra preplanilla.")

            payable_wage = 0.0 if line.remuneration_mode == 'production' else line.period_wage
            if line.currency_id.compare_amounts(line.sick_ordinary_amount, payable_wage) > 0:
                errors.append(f"{emp_name}: el salario ordinario sustituido por incapacidad excede el salario del período.")

        # 12. Allocations de incidencias no se solapan indebidamente / coherencia
        for alloc in all_prepayroll_allocations:
            if alloc.date_from and alloc.date_to and alloc.date_to < alloc.date_from:
                emp = alloc.employee_id.display_name if alloc.employee_id else "Empleado"
                errors.append(f"{emp}: asignación de incidencia tiene fecha de fin anterior a fecha de inicio.")

        if errors:
            formatted_errors = "\n".join(f"- {e}" for e in errors)
            raise ValidationError(f"No se puede validar la preplanilla:\n{formatted_errors}")

        self._write_operational_values({'state': 'validated'}, from_workflow=True)
        
        # Reservar cuotas de préstamo
        installments = self.line_ids.mapped('loan_installment_ids')
        if not self.is_test:
            installments.write({'state': 'scheduled'})

    def action_close(self):
        require_group(self, MANAGER)
        self.ensure_one()
        if self.state != 'validated':
            raise ValidationError("Debe validar la preplanilla antes de cerrarla.")

        productions = self.line_ids.mapped('production_ids')
        allocations = self.env['hr.prepayroll.incident.allocation'].search([('prepayroll_id', '=', self.id)])
        incidents = allocations.mapped('incident_id')

        if not self.is_test:
            candidates = self.env['hr.employee.incident'].search(self._period_incident_domain(
                self.line_ids.employee_id, self.line_ids.contract_id,
            ))
            pending = candidates - incidents
            if pending:
                allocated_elsewhere = self.env['hr.prepayroll.incident.allocation'].search([
                    ('incident_id', 'in', pending.ids),
                    ('prepayroll_id', '!=', self.id),
                    ('prepayroll_id.is_test', '=', False),
                    ('prepayroll_id.state', 'in', ('validated', 'closed')),
                    ('date_from', '<=', self.date_end),
                    ('date_to', '>=', self.date_start),
                ]).incident_id
                pending -= allocated_elsewhere
            if pending or allocations.filtered(lambda allocation: allocation.incident_id.write_date > allocation.write_date):
                raise ValidationError(_(
                    'Hay incidencias aprobadas o modificadas después de consolidar. '
                    'Vuelva a borrador, actualice los datos y valide de nuevo.'
                ))

        for line in self.line_ids:
            for prod in line.production_ids:
                if prod.processed_prepayroll_id and prod.processed_prepayroll_id != self:
                    raise ValidationError("Uno o más registros utilizados ya fueron procesados por otra preplanilla.")
                if prod.state != 'approved':
                    raise ValidationError(f"La producción '{prod.display_name}' ya no está aprobada.")
                if prod.company_id != self.company_id:
                    raise ValidationError(f"La producción '{prod.display_name}' pertenece a otra compañía.")
                if prod.employee_id != line.employee_id or prod.contract_id != line.contract_id:
                    raise ValidationError(f"La producción '{prod.display_name}' no coincide con el empleado/contrato de la línea.")

            for inc in line.incident_ids:
                if not self.is_test and inc.state == 'processed' and inc.processed_prepayroll_id and inc.processed_prepayroll_id != self:
                    raise ValidationError("Uno o más registros utilizados ya fueron procesados por otra preplanilla.")
                if inc.state != 'approved' and inc.state != 'processed':
                    raise ValidationError(f"La incidencia '{inc.display_name}' ya no está aprobada.")
                if inc.company_id != self.company_id:
                    raise ValidationError(f"La incidencia '{inc.display_name}' pertenece a otra compañía.")
                if inc.employee_id != line.employee_id or inc.contract_id != line.contract_id:
                    raise ValidationError(f"La incidencia '{inc.display_name}' no coincide con el empleado/contrato de la línea.")

        if self.is_test:
            self._write_operational_values({'state': 'closed'}, from_workflow=True)
            return

        if productions:
            productions.write({
                'state': 'processed',
                'processed_prepayroll_id': self.id,
            })

        self._write_operational_values({'state': 'closed'}, from_workflow=True)

        if incidents:
            all_closed_allocs = self.env['hr.prepayroll.incident.allocation'].search([
                ('incident_id', 'in', incidents.ids),
                ('prepayroll_id.state', '=', 'closed'),
                ('prepayroll_id.is_test', '=', False),
            ])
            closed_dates_by_inc = {}
            for ca in all_closed_allocs:
                inc_id = ca.incident_id.id
                covered_days = (ca.date_to - ca.date_from).days + 1
                closed_dates_by_inc[inc_id] = closed_dates_by_inc.get(inc_id, 0) + covered_days

            # Determinar si el consumo de cada incidencia fue completo
            for inc in incidents:
                total_closed_days = closed_dates_by_inc.get(inc.id, 0)
                if inc.date_from and inc.date_to:
                    total_inc_days = (inc.date_to - inc.date_from).days + 1
                    if total_closed_days >= (total_inc_days - 0.01):
                        inc.write({
                            'state': 'processed',
                            'processed_prepayroll_id': self.id,
                        })
                else:
                    if total_closed_days:
                        inc.write({
                            'state': 'processed',
                            'processed_prepayroll_id': self.id,
                        })
        
    def action_draft(self):
        require_group(self, MANAGER)
        self.ensure_one()
        if self.state == 'closed':
            raise ValidationError("No se puede volver a borrador directamente una preplanilla cerrada.")
        if self.state != 'validated':
            raise ValidationError("Solo se puede volver a borrador desde el estado validado.")
        self._write_operational_values({'state': 'draft'}, from_workflow=True)

        # Liberar cuotas de préstamo
        installments = self.line_ids.mapped('loan_installment_ids')
        if not self.is_test:
            installments.write({'state': 'pending'})
        
    def action_cancel(self):
        require_group(self, MANAGER)
        self.ensure_one()
        if self.state == 'closed':
            raise ValidationError("Una preplanilla cerrada requiere un proceso de reversión.")
        if self.state == 'cancelled':
            return
        self._write_operational_values({'state': 'cancelled'}, from_workflow=True)

        # Liberar cuotas de préstamo
        installments = self.line_ids.mapped('loan_installment_ids')
        if not self.is_test:
            installments.write({'state': 'pending'})

    def action_generate_payroll(self):
        self.ensure_one()
        require_group(self, USER)
        if self.state != 'closed':
            raise ValidationError(_("Solo se puede generar una planilla a partir de una preplanilla cerrada."))

        existing = self.env['hr.payroll'].search([
            ('prepayroll_id', '=', self.id),
            ('payroll_type', '=', 'regular'),
            ('state', '!=', 'cancelled'),
        ], limit=1)
        if existing:
            raise ValidationError(_(
                "La preplanilla '%(prep_name)s' ya está vinculada a la planilla ordinaria '%(payroll_name)s'.",
                prep_name=self.name,
                payroll_name=existing.name,
            ))

        payroll = self.env['hr.payroll'].create({
            'name': 'Planilla - ' + self.name,
            'prepayroll_id': self.id,
            'is_test': self.is_test,
            'company_id': self.company_id.id,
            'date_start': self.date_start,
            'date_end': self.date_end,
            'payment_frequency': self.payment_frequency,
            'payroll_type': self.prepayroll_type,
            'state': 'draft',
        })
        payroll.action_load_from_prepayroll()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla',
            'res_model': 'hr.payroll',
            'res_id': payroll.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_payroll(self):
        self.ensure_one()
        payrolls = self.payroll_ids.filtered(lambda p: p.state != 'cancelled')
        if len(payrolls) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Planilla',
                'res_model': 'hr.payroll',
                'res_id': payrolls.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planillas',
            'res_model': 'hr.payroll',
            'domain': [('id', 'in', payrolls.ids)],
            'view_mode': 'list,form',
            'target': 'current',
        }


class HrPrepayrollLine(models.Model):
    _name = 'hr.prepayroll.line'
    _inherit = ['hr.sv.minimum.wage.review']
    _check_company_auto = True
    _description = 'Línea de Preplanilla'

    prepayroll_id = fields.Many2one('hr.prepayroll', string='Preplanilla', ondelete='cascade', required=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, check_company=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, check_company=True)
    department_id = fields.Many2one('hr.department', string='Departamento')
    department_name = fields.Char(related='department_id.name')
    job_id = fields.Many2one('hr.job', string='Puesto')
    
    production_ids = fields.Many2many(
        'hr.employee.production',
        'hr_prepayroll_line_production_rel',
        'line_id',
        'production_id',
        string='Producciones'
    )
    incident_ids = fields.Many2many(
        'hr.employee.incident',
        'hr_prepayroll_line_incident_rel',
        'line_id',
        'incident_id',
        string='Incidencias'
    )
    allocation_ids = fields.One2many(
        'hr.prepayroll.incident.allocation',
        'prepayroll_line_id',
        string='Asignaciones de Incidencias'
    )
    incident_cell_details = fields.Json(compute='_compute_incident_cell_details')

    @api.depends(
        'allocation_ids.allocated_days', 'allocation_ids.allocated_hours',
        'allocation_ids.allocated_amount', 'allocation_ids.date_from', 'allocation_ids.date_to',
        'allocation_ids.incident_id.incident_type', 'allocation_ids.incident_id.overtime_type',
        'allocation_ids.incident_id.reason_id.name', 'allocation_ids.incident_id.concept_id.name',
    )
    def _compute_incident_cell_details(self):
        incident_model = self.env['hr.employee.incident']
        types = dict(incident_model._fields['incident_type']._description_selection(self.env))
        overtime_types = dict(incident_model._fields['overtime_type']._description_selection(self.env))
        absence_columns = ('absence_days', 'absence_hours', 'absence_amount',
                           'net_days_to_pay', 'preliminary_deductions_total')
        columns_by_type = {
            'night_work': ('night_work_hours', 'night_work_amount'),
            'holiday_work': ('holiday_work_hours', 'holiday_work_amount'),
            'weekly_rest_work': ('weekly_rest_work_hours', 'weekly_rest_work_amount'),
            'unpaid_leave': absence_columns,
            'unjustified_absence': absence_columns,
            'suspension': (*absence_columns, 'suspension_days', 'suspension_hours'),
            'paid_leave': ('paid_leave_days', 'paid_leave_hours'),
            'sick_leave': ('sick_leave_days', 'sick_leave_hours'),
            'viaticos': ('viaticos_amount',),
            'bonus': ('bonus_amount',),
            'commission': ('commission_amount',),
            'other_income': ('other_income',),
            'deduction': ('deduction_amount', 'preliminary_deductions_total'),
        }
        for line in self:
            details = {}
            for allocation in line.allocation_ids:
                incident = allocation.incident_id
                incident_type = incident.incident_type
                columns = columns_by_type.get(incident_type, ())
                parts = [types[incident_type]]
                if incident_type == 'overtime':
                    prefix = 'night_overtime' if incident.overtime_type == 'nighttime' else 'overtime'
                    columns = (f'{prefix}_hours', f'{prefix}_amount')
                    if incident.overtime_type:
                        parts.append(overtime_types[incident.overtime_type])
                if not columns:
                    continue
                if incident.reason_id:
                    parts.append(incident.reason_id.name)
                if incident.concept_id:
                    parts.append(incident.concept_id.name)
                start = format_date(self.env, allocation.date_from)
                end = format_date(self.env, allocation.date_to)
                parts.append(start if start == end else f'{start} – {end}')
                if allocation.allocated_days:
                    parts.append(_('%s días', f'{allocation.allocated_days:g}'))
                if allocation.allocated_hours:
                    parts.append(_('%s horas', f'{allocation.allocated_hours:g}'))
                if allocation.allocated_amount:
                    parts.append(format_amount(self.env, allocation.allocated_amount, line.currency_id))
                description = ' · '.join(parts)
                for column in columns:
                    details.setdefault(column, []).append(description)
            line.incident_cell_details = details
    
    remuneration_mode = fields.Selection([
        ('fixed', 'Salario fijo'),
        ('production', 'Por producción'),
        ('mixed', 'Mixto')
    ], string='Modalidad')
    
    base_wage = fields.Monetary(string='Salario Base')
    days_to_pay = fields.Float(string='Días del período')
    net_days_to_pay = fields.Float(string='Días a Pagar', compute='_compute_net_days_to_pay', store=True)

    @api.depends(
        'days_to_pay', 'absence_days', 'absence_hours', 'contract_id.resource_calendar_id.hours_per_day',
        'allocation_ids.allocated_hours', 'allocation_ids.incident_id.absence_hours_per_day',
    )
    def _compute_net_days_to_pay(self):
        for line in self:
            hours_per_day = line.contract_id.resource_calendar_id.hours_per_day or 8.0
            absences = line.allocation_ids.filtered(
                lambda allocation: allocation.incident_id.incident_type in ('unpaid_leave', 'unjustified_absence', 'suspension')
            )
            partial_days = sum(
                allocation.allocated_hours / (allocation.incident_id.absence_hours_per_day or hours_per_day)
                for allocation in absences
            ) if absences else line.absence_hours / hours_per_day
            line.net_days_to_pay = max(0.0, line.days_to_pay - line.absence_days - partial_days)

    period_wage = fields.Monetary(string='Salario preliminar del período')
    
    production_quantity = fields.Float(string='Producción')
    production_unit = fields.Selection([
        ('sqm', 'Metro cuadrado (m²)'),
        ('linear_meter', 'Metro lineal'),
        ('unit', 'Unidad'),
        ('day', 'Día'),
        ('other', 'Otro')
    ], string='Unidad Prod.')
    production_rate = fields.Monetary(string='Tarifa Prod.')
    production_amount = fields.Monetary(string='Importe Prod.')
    
    overtime_hours = fields.Float(string='Horas Extra Diurnas')
    overtime_amount = fields.Monetary(string='Importe preliminar H.E.D.')
    
    night_overtime_hours = fields.Float(string='Horas Extra Nocturnas')
    night_overtime_amount = fields.Monetary(string='Importe preliminar H.E.N.')
    
    night_work_hours = fields.Float(string='Horas Nocturnas Ord.')
    night_work_amount = fields.Monetary(string='Importe preliminar nocturnidad')
    
    holiday_work_hours = fields.Float(string='Horas Asueto Trab.')
    holiday_work_amount = fields.Monetary(string='Importe preliminar asueto trab.')
    
    weekly_rest_work_hours = fields.Float(string='Horas Descanso Trab.')
    weekly_rest_work_amount = fields.Monetary(string='Importe preliminar descanso trab.')
    
    absence_days = fields.Float(string='Días Ausente')
    absence_hours = fields.Float(string='Horas Ausente')
    absence_amount = fields.Monetary(string='Descuento preliminar')
    
    # Informativo: sin monetización, referencia para RRHH
    paid_leave_days = fields.Float(string='Perm. c/goce (días)')
    paid_leave_hours = fields.Float(string='Perm. c/goce (horas)')
    sick_leave_days = fields.Float(string='Incapacidad (días)')
    sick_leave_hours = fields.Float(string='Incapacidad (horas)')
    sick_ordinary_amount = fields.Monetary(string='Salario ordinario sustituido por incapacidad', readonly=True)
    sick_employer_amount = fields.Monetary(string='Incapacidad a cargo del empleador', readonly=True)
    sick_isss_amount = fields.Monetary(string='Subsidio ISSS (informativo)', readonly=True)
    suspension_days = fields.Float(string='Suspensión (días)')
    suspension_hours = fields.Float(string='Suspensión (horas)')
    
    bonus_amount = fields.Monetary(string='Bonos')
    commission_amount = fields.Monetary(string='Comisiones')
    viaticos_amount = fields.Monetary(string='Viáticos')
    other_income = fields.Monetary(string='Otros Ingresos')
    deduction_amount = fields.Monetary(string='Deducciones')
    
    loan_installment_ids = fields.Many2many(
        'hr.employee.loan.installment',
        'hr_prepayroll_line_loan_rel',
        'line_id',
        'installment_id',
        string='Cuotas de Préstamo'
    )
    loan_deduction_amount = fields.Monetary(string='Préstamos')
    
    period_wage_with_viaticos = fields.Monetary(
        string='Devengos con viáticos', compute='_compute_period_wage_with_viaticos',
        help='Salario del período más los viáticos registrados.',
    )
    preliminary_earnings_total = fields.Monetary(string='Devengos preliminares', compute='_compute_preliminary_totals', store=True)
    preliminary_deductions_total = fields.Monetary(string='Descuentos preliminares', compute='_compute_preliminary_totals', store=True)
    preliminary_balance = fields.Monetary(string='Balance preliminar', compute='_compute_preliminary_totals', store=True)
    gross_total = fields.Monetary(string='Total preliminar', compute='_compute_preliminary_totals', store=True)
    
    notes = fields.Char(string='Notas')
    state = fields.Selection(related='prepayroll_id.state', string='Estado Preplanilla', store=True)
    
    company_id = fields.Many2one(related='prepayroll_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.model_create_multi
    def create(self, vals_list):
        require_group(self, USER)
        default_parent = self.default_get(['prepayroll_id']).get('prepayroll_id')
        parents = self.env['hr.prepayroll'].browse([
            vals.get('prepayroll_id', default_parent)
            for vals in vals_list if vals.get('prepayroll_id', default_parent)
        ])
        for parent in parents:
            if parent.state in ('validated', 'closed', 'cancelled'):
                raise ValidationError("La preplanilla está validada o cerrada. Debe volver a borrador mediante el flujo autorizado antes de modificarla.")
        check_edit(parents)
        return super().create(vals_list)

    def write(self, vals):
        parents = self.mapped('prepayroll_id')
        if vals.get('prepayroll_id'):
            parents |= self.env['hr.prepayroll'].browse(vals['prepayroll_id'])
        for parent in parents:
            if parent.state in ('validated', 'closed', 'cancelled'):
                raise ValidationError("La preplanilla está validada o cerrada. Debe volver a borrador mediante el flujo autorizado antes de modificarla.")
        check_edit(parents)
        return super().write(vals)

    def unlink(self):
        parents = self.mapped('prepayroll_id')
        for parent in parents:
            if parent.state in ('validated', 'closed', 'cancelled'):
                raise ValidationError("La preplanilla está validada o cerrada. Debe volver a borrador mediante el flujo autorizado antes de modificarla.")
        check_edit(parents)
        return super().unlink()

    @api.depends('period_wage', 'viaticos_amount')
    def _compute_period_wage_with_viaticos(self):
        for line in self:
            line.period_wage_with_viaticos = line.period_wage + line.viaticos_amount

    @api.depends(
        'remuneration_mode', 'period_wage', 'production_amount', 'overtime_amount',
        'night_overtime_amount', 'night_work_amount', 'holiday_work_amount',
        'weekly_rest_work_amount', 'bonus_amount', 'commission_amount', 'viaticos_amount',
        'other_income', 'absence_amount', 'deduction_amount', 'loan_deduction_amount',
        'sick_ordinary_amount', 'sick_employer_amount',
    )
    def _compute_preliminary_totals(self):
        for line in self:
            base_part = 0.0 if line.remuneration_mode == 'production' else (line.period_wage or 0.0)
            earnings = (
                base_part - line.sick_ordinary_amount + line.sick_employer_amount +
                (line.production_amount or 0.0) +
                (line.overtime_amount or 0.0) +
                (line.night_overtime_amount or 0.0) +
                (line.night_work_amount or 0.0) +
                (line.holiday_work_amount or 0.0) +
                (line.weekly_rest_work_amount or 0.0) +
                (line.bonus_amount or 0.0) +
                (line.commission_amount or 0.0) +
                (line.viaticos_amount or 0.0) +
                (line.other_income or 0.0)
            )
            deductions = (
                (line.absence_amount or 0.0) +
                (line.deduction_amount or 0.0) +
                (line.loan_deduction_amount or 0.0)
            )
            line.preliminary_earnings_total = earnings
            line.preliminary_deductions_total = deductions
            line.preliminary_balance = earnings - deductions
            line.gross_total = line.preliminary_balance

    _sql_constraints = [
        ('unique_prepayroll_employee_contract', 'unique(prepayroll_id, employee_id, contract_id)', 'El empleado ya tiene una línea con el mismo contrato en esta preplanilla.')
    ]
