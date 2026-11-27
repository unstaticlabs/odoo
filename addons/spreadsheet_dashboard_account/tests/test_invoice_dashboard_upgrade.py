import json

from odoo.tests import TransactionCase
from odoo.tools import BinaryBytes, convert_file, file_open


class TestInvoiceDashboardUpgrade(TransactionCase):
    def test_upgrade_reloads_metadata_without_rewriting_existing_workbook(self):
        dashboard = self.env.ref('spreadsheet_dashboard_account.dashboard_invoicing')
        workbook = json.loads(dashboard.spreadsheet_binary_data.content)
        workbook['settings']['locale'] = {'code': 'fr_FR'}
        content = json.dumps(workbook).encode()
        dashboard.write({'name': 'Synthetic dashboard', 'spreadsheet_binary_data': BinaryBytes(content)})
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', dashboard._name),
            ('res_field', '=', 'spreadsheet_binary_data'),
            ('res_id', '=', dashboard.id),
        ])
        attachment.ensure_one()
        checksum = attachment.checksum

        convert_file(self.env, 'spreadsheet_dashboard_account', 'data/dashboards.xml', {}, mode='update')

        self.env.invalidate_all()
        self.assertEqual(dashboard.name, 'Invoicing')
        self.assertEqual(attachment.checksum, checksum)
        self.assertEqual(dashboard.spreadsheet_binary_data.content, content)

    def test_fresh_dashboard_gets_the_upstream_workbook(self):
        dashboard = self.env.ref('spreadsheet_dashboard_account.dashboard_invoicing')
        with file_open('spreadsheet_dashboard_account/data/files/invoicing_dashboard.json', 'rb') as source:
            self.assertEqual(dashboard.spreadsheet_binary_data.content, source.read())
