from odoo.tests import TransactionCase, tagged


@tagged("usl_accounting", "post_install", "-at_install")
class TestMultiCompanyCards(TransactionCase):
    def test_journal_company_label_uses_active_company_context(self):
        view = self.env.ref("account.account_journal_dashboard_kanban_view")
        arch = view._get_combined_arch()
        company_labels = arch.xpath(
            "//t[@t-name='JournalTop']//t[@t-if='dashboard.show_company']",
        )

        self.assertEqual(len(company_labels), 1)
        self.assertNotIn("groups", company_labels[0].attrib)
        self.assertTrue(company_labels[0].xpath(".//field[@name='company_id']"))
