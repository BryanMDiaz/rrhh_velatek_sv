from odoo import SUPERUSER_ID, api
from odoo.tools.convert import convert_file


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref('rrhh_velatek_sv.contract_template_sv_permanent')
    if template.version < 3:
        convert_file(env, 'rrhh_velatek_sv', 'data/hr_contract_template_data.xml', {}, mode='init')
