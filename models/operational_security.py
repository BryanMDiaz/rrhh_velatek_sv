"""Authorization helpers for operational records; no context-based bypass."""
from odoo.exceptions import AccessError, ValidationError

USER = 'hr.group_hr_user'
RESPONSIBLE = 'rrhh_velatek_sv.group_hr_responsible'
MANAGER = 'hr.group_hr_manager'
IMMUTABLE_PROCESSED_MODELS = {
    'hr.employee.production',
    'hr.employee.incident',
}


def check_processed_immutable(records, values=None):
    if records._name not in IMMUTABLE_PROCESSED_MODELS:
        return
    locked = records.filtered(lambda record: record.state == 'processed' or record.processed_prepayroll_id)
    if locked:
        raise ValidationError(records.env._('El registro ya fue procesado en una preplanilla cerrada y no puede modificarse.'))
    if records._name == 'hr.employee.incident' and records.ids:
        allocations = records.env['hr.prepayroll.incident.allocation'].sudo().search([
            ('incident_id', 'in', records.ids), ('prepayroll_id.state', '=', 'closed'),
            ('prepayroll_id.is_test', '=', False),
        ], limit=1)
        # A later prepayroll can finish consuming a multi-period incident,
        # but financial data and deletion remain protected after first use.
        completing = values and set(values) <= {'state', 'processed_prepayroll_id'} and values.get('state') == 'processed'
        if allocations and not completing:
            raise ValidationError(records.env._('La incidencia ya tiene importes o días utilizados en una preplanilla cerrada. No puede modificarla ni eliminarla.'))

def is_manager(records):
    return records.env.su or records.env.user.has_group(MANAGER)


def require_group(records, group):
    if not is_manager(records) and not records.env.user.has_group(group):
        raise AccessError(records.env._("No tiene permisos para realizar esta operación de RRHH."))


def check_edit(records):
    check_processed_immutable(records)


def check_create(records, vals_list):
    require_group(records, USER)
    if records.env.su:
        return
    default_fields = ['state']
    if 'processed_prepayroll_id' in records._fields:
        default_fields.append('processed_prepayroll_id')
    defaults = records.default_get(default_fields)
    for vals in vals_list:
        if (vals.get('state', defaults.get('state', 'draft')) != 'draft'
                or vals.get('processed_prepayroll_id', defaults.get('processed_prepayroll_id'))):
            raise AccessError(records.env._("Solo puede crear registros en borrador."))


def check_write(records, vals):
    check_processed_immutable(records, vals)
    require_group(records, USER)
    # Preserve existing state actions, but never combine reopening with edits.
    if records._name == 'hr.prepayroll' and set(vals) - {'state'}:
        check_edit(records)
    if is_manager(records):
        return
    if 'processed_prepayroll_id' in vals:
        require_group(records, MANAGER)
    target = vals.get('state')
    if 'state' not in vals:
        check_edit(records)
        return
    if records._name == 'hr.payroll':
        require_group(records, RESPONSIBLE)
        if target not in ('computed', 'validated'):
            require_group(records, MANAGER)
        return
    prepayroll = records._name == 'hr.prepayroll'
    approval = 'validated' if prepayroll else 'approved'
    submission = 'loaded' if prepayroll else 'to_approve'
    if target == approval:
        require_group(records, RESPONSIBLE)
        allowed_sources = ('draft', 'loaded', 'validated') if prepayroll else ('draft', 'to_approve', 'approved')
        if any(record.state not in allowed_sources for record in records):
            require_group(records, MANAGER)
    elif target == submission:
        check_edit(records)
    elif target == 'draft' and all(record.state == 'draft' for record in records):
        check_edit(records)
    else:
        require_group(records, MANAGER)
    if set(vals) - {'state'}:
        check_processed_immutable(records, vals)


def check_workflow_write(records, values, from_workflow):
    if ('state' in values and not from_workflow and not records.env.su
            and any(record.state != values['state'] for record in records)):
        raise AccessError(records.env._('Use las acciones del flujo para cambiar el estado.'))
