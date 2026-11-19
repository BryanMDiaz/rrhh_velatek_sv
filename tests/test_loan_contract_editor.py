import sys
from unittest.mock import patch

from lxml import html
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLoanContractEditor(TransactionCase):
    def test_visual_editor_without_cssselect_dependency(self):
        wizard = self.env['hr.loan.template.preview.wizard'].new({})
        content = '''
            <style>
                .contract h1 { color: navy; }
                .contract .amount, #total { font-weight: bold; }
                .contract td { padding: 8px; }
            </style>
            <div class="contract">
                <h1>Contrato</h1>
                <table><tbody><tr><td class="amount" id="total"
                    style="text-align:right">$40.00</td></tr></tbody></table>
                <div class="signature-line" style="border-top:1px solid black"></div>
            </div>
        '''
        # Reproduce a server where the optional CSS selector package is absent.
        with patch.dict(sys.modules, {'cssselect': None, 'lxml.cssselect': None}):
            rendered = wizard._prepare_editable_html(content)
        root = html.fromstring(rendered)
        self.assertFalse(root.xpath('.//style'))
        self.assertIn('color: navy', root.xpath('.//h1')[0].get('style'))
        cell = root.xpath('.//td')[0]
        self.assertIn('font-weight: bold', cell.get('style'))
        self.assertIn('padding: 8px', cell.get('style'))
        self.assertIn('text-align:right', cell.get('style'))
        self.assertIn('border:0 !important', cell.get('style'))
        self.assertEqual(cell.text, '$40.00')
        signature = root.xpath('.//div[@class="signature-line"]')[0]
        self.assertIn('border-top:1px solid black', signature.get('style'))
