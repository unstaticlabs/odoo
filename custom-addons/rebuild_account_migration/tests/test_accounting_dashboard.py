from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged


@tagged(
    "post_install",
    "-at_install",
    "rebuild_account_migration_unit",
    "usl_accounting_dashboard",
)
class TestAccountingDashboard(AccountTestInvoicingCommon):
    def _document(
        self,
        journal,
        move_type,
        invoice_date,
        amount,
        *,
        currency=None,
        post=True,
    ):
        move = self.env["account.move"].with_company(journal.company_id).create(
            {
                "move_type": move_type,
                "journal_id": journal.id,
                "currency_id": (currency or journal.company_id.currency_id).id,
                "partner_id": self.partner_a.id,
                "invoice_date": invoice_date,
                "date": invoice_date,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [],
                        },
                    ),
                ],
            },
        )
        if post:
            move.action_post()
        return move

    @freeze_time("2026-09-04")
    def test_sale_purchase_graph_shows_posted_monthly_activity(self):
        sale = self.company_data["default_journal_sale"]
        purchase = self.company_data["default_journal_purchase"]

        self._document(sale, "out_invoice", "2026-04-10", 100)
        self._document(sale, "out_refund", "2026-04-12", 20)
        self._document(sale, "out_invoice", "2026-05-10", 999, post=False)
        self._document(sale, "out_invoice", "2026-07-10", 50)
        self._document(sale, "out_invoice", "2026-09-02", 25)
        self._document(sale, "out_invoice", "2026-09-25", 500)
        self._document(sale, "out_invoice", "2026-03-31", 777)
        self._document(purchase, "in_invoice", "2026-06-10", 40)
        self._document(purchase, "in_refund", "2026-06-12", 10)

        graphs = (sale | purchase)._get_sale_purchase_graph_data()
        sale_graph = graphs[sale.id][0]
        purchase_graph = graphs[purchase.id][0]

        self.assertEqual(
            [point["label"] for point in sale_graph["values"]],
            [
                "Apr 2026",
                "May 2026",
                "Jun 2026",
                "Jul 2026",
                "Aug 2026",
                "Sep 2026",
            ],
        )
        self.assertEqual(
            [point["value"] for point in sale_graph["values"]],
            [80, 0, 0, 50, 0, 25],
        )
        self.assertEqual(
            [point["value"] for point in purchase_graph["values"]],
            [0, 0, 30, 0, 0, 0],
        )
        self.assertFalse(sale_graph["is_sample_data"])
        self.assertEqual(sale_graph["key"], "Net posted amount — refunds deducted")
        self.assertIn("80", sale_graph["values"][0]["formatted_value"])
        self.assertIn(
            self.env.company.currency_id.symbol,
            sale_graph["values"][0]["formatted_value"],
        )

        dashboard = {sale.id: {}, purchase.id: {}}
        (sale | purchase)._fill_sale_purchase_dashboard_data(dashboard)
        self.assertIn("Sep MTD", dashboard[sale.id]["monthly_activity_caption"])

    @freeze_time("2026-09-04")
    def test_graph_converts_foreign_documents_to_company_currency(self):
        sale = self.company_data["default_journal_sale"]
        foreign_code = "EUR" if self.env.company.currency_id.name != "EUR" else "USD"
        foreign_currency = self.setup_other_currency(
            foreign_code,
            rates=[("2026-01-01", 3.0)],
        )
        self.assertNotEqual(foreign_currency, self.env.company.currency_id)
        move = self._document(
            sale,
            "out_invoice",
            "2026-09-02",
            300,
            currency=foreign_currency,
        )

        self.assertEqual(move.currency_id, foreign_currency)
        self.assertEqual(move.amount_total, 300)
        self.assertEqual(move.amount_total_signed, 100)
        graph = sale._get_sale_purchase_graph_data()[sale.id][0]
        self.assertEqual(graph["values"][-1]["value"], 100)

    @freeze_time("2026-09-04")
    def test_empty_sale_graph_uses_truthful_zero_months(self):
        journal = self.env["account.journal"].create(
            {
                "name": "[QA dashboard] Empty Sales",
                "code": "QASL",
                "type": "sale",
                "company_id": self.env.company.id,
            },
        )

        graph = journal._get_sale_purchase_graph_data()[journal.id][0]

        self.assertEqual(len(graph["values"]), 6)
        self.assertEqual({point["value"] for point in graph["values"]}, {0})
        self.assertFalse(graph["is_sample_data"])
        self.assertNotEqual(graph["key"], "Sample data")

    @freeze_time("2026-09-04")
    def test_graph_honors_accounting_access_and_move_rules(self):
        sale = self.company_data["default_journal_sale"]
        move = self._document(sale, "out_invoice", "2026-09-02", 80)
        accountant = new_test_user(
            self.env,
            login="dashboard.accountant@example.invalid",
            groups="account.group_account_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        basic_user = new_test_user(
            self.env,
            login="dashboard.basic@example.invalid",
            groups="base.group_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )

        accountant_sale = sale.with_user(accountant).with_context(
            allowed_company_ids=self.env.company.ids,
        )
        visible = accountant_sale._get_sale_purchase_graph_data()[sale.id][0]
        self.assertEqual(visible["values"][-1]["value"], 80)

        self.env["ir.rule"].create(
            {
                "name": "[QA dashboard] Exclude one posted move",
                "model_id": self.env["ir.model"]._get_id("account.move"),
                "domain_force": f"[('id', '!=', {move.id})]",
                "perm_read": True,
                "perm_write": False,
                "perm_create": False,
                "perm_unlink": False,
            },
        )
        hidden = accountant_sale._get_sale_purchase_graph_data()[sale.id][0]
        self.assertEqual(hidden["values"][-1]["value"], 0)

        with self.assertRaises(AccessError):
            sale.with_user(basic_user)._get_sale_purchase_graph_data()

    @freeze_time("2026-09-04")
    def test_graph_isolates_selected_companies_and_currencies(self):
        usd = self.setup_other_currency("USD")
        company_b_data = self.setup_other_company(currency_id=usd.id)
        sale_a = self.company_data["default_journal_sale"]
        sale_b = company_b_data["default_journal_sale"]
        self._document(sale_a, "out_invoice", "2026-09-02", 70)
        self._document(sale_b, "out_invoice", "2026-09-03", 120)

        graph_a = sale_a.with_context(
            allowed_company_ids=self.env.company.ids,
        )._get_sale_purchase_graph_data()[sale_a.id][0]
        graph_b = sale_b.with_context(
            allowed_company_ids=company_b_data["company"].ids,
        )._get_sale_purchase_graph_data()[sale_b.id][0]

        self.assertEqual(graph_a["values"][-1]["value"], 70)
        self.assertEqual(graph_b["values"][-1]["value"], 120)
        self.assertIn(
            self.env.company.currency_id.symbol,
            graph_a["values"][-1]["formatted_value"],
        )
        self.assertIn(usd.symbol, graph_b["values"][-1]["formatted_value"])

        dashboard_a = {sale_a.id: {}}
        sale_a.with_context(
            allowed_company_ids=self.env.company.ids,
        )._fill_sale_purchase_dashboard_data(dashboard_a)
        dashboard_b = {sale_b.id: {}}
        sale_b.with_context(
            allowed_company_ids=company_b_data["company"].ids,
        )._fill_sale_purchase_dashboard_data(dashboard_b)
        self.assertIn(
            self.env.company.currency_id.name,
            dashboard_a[sale_a.id]["monthly_activity_caption"],
        )
        self.assertIn(
            usd.name,
            dashboard_b[sale_b.id]["monthly_activity_caption"],
        )

    def test_dashboard_view_labels_the_metric_and_shortcuts(self):
        journal_arch = self.env.ref(
            "account.account_journal_dashboard_kanban_view",
        )._get_combined_arch()
        captions = journal_arch.xpath(
            "//t[@t-name='JournalBodyGraph']"
            "//*[contains(@class, 'o_usl_journal_trend_caption')]",
        )
        self.assertEqual(len(captions), 1)
        self.assertEqual(
            captions[0].get("t-att-title"),
            "dashboard.monthly_activity_help",
        )

        overview_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration.view_rebuild_accounting_home_form",
            ).arch_db,
        )
        shortcuts = overview_arch.xpath(
            "//div[contains(@class, 'o_usl_overview_shortcuts')]"
            "//field[@widget='statinfo']/@string",
        )
        self.assertEqual(
            shortcuts,
            ["Review", "Bank", "Match", "Invoices", "Bills", "Expenses", "Alerts"],
        )
        pending = overview_arch.xpath(
            "//field[contains(@class, 'o_usl_pending_declarations_table')]/list",
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0].xpath("./field/@name"),
            ["form_code", "name", "deadline_date", "status"],
        )
        pending_mobile = overview_arch.xpath(
            "//field[contains(@class, 'o_usl_pending_declarations_mobile')]/kanban",
        )
        self.assertEqual(len(pending_mobile), 1)
        self.assertEqual(
            pending_mobile[0].xpath(".//t[@t-name='card']//field/@name"),
            ["form_code", "status", "name", "deadline_date"],
        )
