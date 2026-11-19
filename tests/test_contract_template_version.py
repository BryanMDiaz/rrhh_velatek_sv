import time
from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError


@tagged('post_install', '-at_install')
class TestContractTemplateVersion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': 'Empleado Versionado'})
        cls.contract = cls.env['hr.contract'].create({
            'name': 'Contrato Versionado',
            'employee_id': cls.employee.id,
            'date_start': '2026-01-01',
            'wage': 1000,
        })

    def test_generated_document_keeps_template_version_snapshot(self):
        template = self.env['hr.contract.template'].create({
            'name': 'Servicios Profesionales',
            'version': 1,
            'body_html': '<p>Contenido versión 1</p>',
        })
        wizard = self.env['hr.contract.generate.wizard'].create({
            'contract_id': self.contract.id,
            'template_id': template.id,
            'signature_date': '2026-09-28',
            'preview_html': '<p>Documento generado con versión 1</p>',
            'generated_name': 'Servicios Profesionales v1',
        })

        wizard.action_generate()
        document = self.env['hr.contract.generated.document'].search([
            ('contract_id', '=', self.contract.id),
        ], limit=1)
        self.assertEqual(document.template_version, 1)

        template.version = 2
        self.assertEqual(template.version, 2)
        self.assertEqual(document.template_version, 1)

    def test_generated_pdf_is_stored_and_remains_immutable(self):
        template = self.env['hr.contract.template'].create({
            'name': 'Contrato PDF Histórico',
            'version': 1,
            'body_html': '<p>Salario histórico: $700</p>',
        })
        self.employee.name = 'Fátima Villatoro'
        self.contract.wage = 700
        wizard = self.env['hr.contract.generate.wizard'].create({
            'contract_id': self.contract.id,
            'template_id': template.id,
            'signature_date': '2026-09-28',
            'preview_html': '<p>Salario histórico: $700</p>',
            'generated_name': 'Contrato Fátima Villatoro',
        })

        download_action = wizard.action_generate()
        document = self.env['hr.contract.generated.document'].search([
            ('contract_id', '=', self.contract.id),
        ], order='id desc', limit=1)
        attachment = document.pdf_attachment_id
        original_pdf = attachment.raw

        self.assertTrue(attachment)
        self.assertEqual(attachment.res_model, document._name)
        self.assertEqual(attachment.res_id, document.id)
        self.assertEqual(attachment.mimetype, 'application/pdf')
        self.assertEqual(attachment.name, 'Contrato_Fátima_Villatoro_2026.pdf')
        self.assertTrue(original_pdf.startswith(b'%PDF'))
        self.assertEqual(download_action['type'], 'ir.actions.act_url')
        self.assertEqual(download_action['url'], f'/web/content/{attachment.id}?download=1')
        self.assertEqual(download_action['target'], 'new')
        self.assertTrue(download_action['close'])

        self.employee.name = 'Fátima Villatoro Actualizada'
        self.contract.wage = 900
        template.write({
            'version': 2,
            'body_html': '<p>Contenido de plantilla versión 2</p>',
        })
        document.invalidate_recordset(['pdf_attachment_id'])
        attachment.invalidate_recordset(['raw'])

        self.assertEqual(document.pdf_attachment_id, attachment)
        self.assertEqual(attachment.raw, original_pdf)
        self.assertEqual(document.template_version, 1)

    def test_same_name_allows_different_versions(self):
        import uuid
        values = {
            'name': 'Servicios Profesionales por versión %s' % uuid.uuid4().hex,
            'company_id': self.env.company.id,
        }
        version_1 = self.env['hr.contract.template'].create({**values, 'version': 1})
        version_2 = self.env['hr.contract.template'].create({**values, 'version': 2})

        self.assertTrue(version_1.exists())
        self.assertTrue(version_2.exists())
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env['hr.contract.template'].create({**values, 'version': 2})

    def test_mark_signed_opens_contract(self):
        contract_type = self.env['hr.contract.type'].create({'name': 'Contrato firmado'})
        self.contract.write({
            'contract_type_id': contract_type.id,
            'resource_calendar_id': self.env.company.resource_calendar_id.id,
            'velatek_payment_frequency': 'monthly',
            'velatek_work_schedule_type': 'full_time',
            'velatek_remuneration_mode': 'fixed',
        })
        signed_attachment = self.env['ir.attachment'].create({
            'name': 'Contrato_Firmado.pdf',
            'type': 'binary',
            'raw': b'%PDF-1.4 signed contract',
            'mimetype': 'application/pdf',
        })
        document = self.env['hr.contract.generated.document'].create({
            'name': 'Contrato firmado',
            'contract_id': self.contract.id,
            'state': 'generated',
            'signed_attachment_ids': [Command.set(signed_attachment.ids)],
        })

        document.action_mark_signed()

        self.assertEqual(document.state, 'signed')
        self.assertEqual(self.contract.state, 'open')
        employee_document = self.env['hr.employee.document'].search([
            ('employee_id', '=', self.employee.id),
            ('attachment_id', '=', signed_attachment.id),
        ])
        self.assertTrue(employee_document)

    def test_mark_signed_requires_uploaded_file(self):
        document = self.env['hr.contract.generated.document'].create({
            'name': 'Contrato sin archivo firmado',
            'contract_id': self.contract.id,
            'state': 'generated',
        })

        with self.assertRaisesRegex(
            ValidationError,
            'Debe subir el contrato firmado antes de marcarlo como firmado.',
        ):
            document.action_mark_signed()

        self.assertEqual(document.state, 'generated')
        self.assertEqual(self.contract.state, 'draft')
