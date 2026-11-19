"""Date/company scoped payroll configuration checks; unrelated to user roles."""
from odoo import _
from odoo.exceptions import ValidationError


def check_effective_periods(records, scope_fields):
    for record in records:
        if record.date_to and record.date_to < record.date_from:
            raise ValidationError(_('La fecha final de vigencia no puede ser anterior a la inicial.'))
        if not record.active:
            continue
        domain = [('id', '!=', record.id), ('active', '=', True)]
        for name in scope_fields:
            value = record[name]
            if record._fields[name].type == 'many2one':
                value = value.id or False
            domain.append((name, '=', value))
        domain += [
            ('date_from', '<=', record.date_to or '9999-12-31'),
            '|', ('date_to', '=', False), ('date_to', '>=', record.date_from),
        ]
        duplicate = records.sudo().search(domain, limit=1)
        if duplicate:
            raise ValidationError(_('La vigencia de %s se superpone con %s para la misma configuración. Cierre la vigencia anterior antes de activar la nueva.') % (record.display_name, duplicate.display_name))


def find_effective_parameter(model, domain):
    records = model.sudo().search(domain, limit=2, order='date_from desc, id desc')
    if len(records) > 1:
        raise ValidationError(_('Hay más de una configuración vigente de %s para la fecha solicitada. Corrija las vigencias superpuestas antes de calcular.') % model._description)
    return records
