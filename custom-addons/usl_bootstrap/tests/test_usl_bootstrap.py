from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestUslBootstrap(TransactionCase):
    def test_baseline_records(self):
        company = self.env.ref("base.main_company")
        self.assertEqual(company.name, "Unstatic Labs")
        self.assertEqual(company.country_id.code, "FR")
        self.assertEqual(company.currency_id.name, "EUR")
        self.assertEqual(company.chart_template, "fr_comp")

        installed = self.env["ir.module.module"].search([
            ("name", "in", ["contacts", "mail", "account", "l10n_fr", "l10n_fr_account", "hr", "hr_expense", "project", "sale_management"]),
            ("state", "=", "installed"),
        ])
        self.assertEqual(len(installed), 9)

        journals = self.env["account.journal"].search([("company_id", "=", company.id)]).mapped("name")
        for name in ["Banque Shine", "Revolut USD", "Revolut GBP", "Wise USD", "Expense Journal"]:
            self.assertIn(name, journals)

        for name in ["USL Admin", "SBFH Production", "Yoshi", "Odoo Rebuild"]:
            self.assertTrue(self.env["project.project"].search([("name", "=", name), ("company_id", "=", company.id)]))

        self.assertTrue(self.env["hr.employee"].search([("name", "=", "Valentin"), ("company_id", "=", company.id)]))
        self.assertGreaterEqual(self.env["hr.expense"].search_count([("company_id", "=", company.id)]), 4)
        self.assertGreaterEqual(self.env["res.partner"].search_count([("email", "like", ".test")]), 6)

    def test_development_safe_data(self):
        forbidden_patterns = ["@gmail.com", "@unstatic.fr", "@unstaticlabs.com", "sk-", "ghp_", "live_"]
        for model_name, field_name in [
            ("res.partner", "email"),
            ("res.users", "email"),
            ("hr.employee", "work_email"),
            ("ir.attachment", "name"),
        ]:
            model = self.env[model_name]
            for pattern in forbidden_patterns:
                self.assertFalse(model.search([(field_name, "ilike", pattern)], limit=1), f"{model_name}.{field_name} contains {pattern}")
