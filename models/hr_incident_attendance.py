from datetime import datetime, time, timedelta

import pytz
from odoo import _, api, fields, models
from odoo.addons.resource.models.utils import Intervals, sum_intervals


class HrIncidentAttendance(models.Model):
    _inherit = 'hr.employee.incident'

    attendance_reconciliation = fields.Text(
        string='Comparación diaria de marcaciones y jornada',
        compute='_compute_attendance_reconciliation',
        groups='hr_attendance.group_hr_attendance_officer',
    )

    @api.depends('employee_id', 'contract_id', 'date', 'date_from', 'date_to')
    def _compute_attendance_reconciliation(self):
        attendance_model = self.env['hr.attendance']
        timezone = pytz.timezone('America/El_Salvador')
        for incident in self:
            incident.attendance_reconciliation = False
            employee = incident.employee_id
            first = incident.date_from or incident.date
            last = incident.date_to or incident.date
            if not employee or not incident.contract_id or not first or not last or last < first:
                continue

            def midnight(day):
                return timezone.localize(datetime.combine(day, time.min))

            def utc_naive(value):
                return value.astimezone(pytz.utc).replace(tzinfo=None)

            period_end = midnight(last + timedelta(days=1))
            punches = attendance_model.search([
                ('employee_id', '=', employee.id),
                ('check_in', '<', utc_naive(period_end)),
                '|', ('check_out', '=', False), ('check_out', '>', utc_naive(midnight(first))),
            ])
            resource = employee.resource_id
            lines = []
            for offset in range((last - first).days + 1):
                day = first + timedelta(days=offset)
                start, end = midnight(day), midnight(day + timedelta(days=1))
                _wage, calendar, _hours = incident.contract_id._velatek_terms_on(day)
                if not calendar:
                    lines.append(_('%(day)s · Sin horario contractual', day=day))
                    continue
                planned = calendar._work_intervals_batch(start, end, resources=resource)[resource.id]
                lunch = calendar._attendance_intervals_batch(start, end, resources=resource, lunch=True)[resource.id]
                intervals = []
                open_punch = False
                for punch in punches:
                    check_in = pytz.utc.localize(punch.check_in)
                    if not punch.check_out:
                        if check_in < end:
                            open_punch = True
                        continue
                    check_out = pytz.utc.localize(punch.check_out)
                    lower, upper = max(start, check_in), min(end, check_out)
                    if lower < upper:
                        intervals.append((lower, upper, punch))
                worked = Intervals(intervals) - lunch
                detail = _(
                    '%(day)s · %(schedule)s · Previstas %(planned).2f h · Marcadas %(worked).2f h · '
                    'Fuera de jornada %(extra).2f h · Sin marcación en jornada %(missing).2f h',
                    day=day, schedule=calendar.name, planned=sum_intervals(planned),
                    worked=sum_intervals(worked), extra=sum_intervals(worked - planned),
                    missing=sum_intervals(planned - worked),
                )
                if open_punch:
                    detail += _(' · Marcación abierta')
                lines.append(detail)
            incident.attendance_reconciliation = '\n'.join(lines)
