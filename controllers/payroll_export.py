from odoo import http
from odoo.http import content_disposition, request


class PayrollExport(http.Controller):

    @http.route('/rrhh_velatek_sv/payroll/<int:payroll_id>/xlsx', type='http', auth='user', methods=['GET'])
    def download_payroll(self, payroll_id):
        payroll = request.env['hr.payroll'].browse(payroll_id).exists()
        if not payroll:
            return request.not_found()
        content, filename = payroll._export_xlsx()
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(filename)),
            ('Cache-Control', 'no-store'),
        ])
