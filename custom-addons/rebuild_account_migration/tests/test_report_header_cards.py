import hashlib
import json

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_report_header_cards")
class TestReportHeaderCards(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "[QA] Report card scope",
            "currency_id": cls.env.ref("base.EUR").id,
        })
        cls.reader = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Report reader",
            "login": "qa.report.cards@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set(cls.env.ref("account.group_account_readonly").ids)],
        })
        accounts = cls.env["account.account"].with_company(cls.company).create([
            {"name": "[QA] Cash", "code": "512001", "account_type": "asset_cash"},
            {"name": "[QA] Other asset", "code": "467001", "account_type": "asset_current"},
            {"name": "[QA] Sales", "code": "706001", "account_type": "income"},
        ])
        cls.cash, cls.other, cls.income = accounts
        cls.journal = cls.env["account.journal"].create({
            "name": "[QA] Header controls", "code": "QAHC", "type": "general",
            "company_id": cls.company.id,
        })
        cls.moves = cls.env["account.move"].create([
            {
                "company_id": cls.company.id, "journal_id": cls.journal.id,
                "date": "2026-03-15", "move_type": "entry",
                "line_ids": [
                    Command.create({"name": "[QA] Evidence", "account_id": account.id, "debit": amount}),
                    Command.create({"name": "[QA] Sale", "account_id": cls.income.id, "credit": amount}),
                ],
            }
            for account, amount in [(cls.cash, 73), (cls.other, 27)]
        ])
        cls.moves.action_post()

    def _wizard(self, report_type="trial_balance", **values):
        return self.env["rebuild.account.report.export.wizard"].with_user(
            self.reader,
        ).with_context(allowed_company_ids=self.company.ids).create({
            "report_type": report_type, "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "date_from": "2026-01-01", "date_to": "2026-12-31",
            "target_move": "posted", "group_by": "none", "show_details": True,
            "hide_zero_accounts": False, "preview_limit": 500,
            **values,
        })

    def _cards(self, wizard):
        wizard.action_preview_report()
        return [card["value"] for card in wizard._report_client_summary()["cards"]]

    def _ledger_fingerprint(self):
        lines = self.moves.line_ids.read([
            "move_id", "account_id", "date", "debit", "credit", "balance",
            "amount_currency", "currency_id", "amount_residual", "write_date",
        ])
        return hashlib.sha256(json.dumps(lines, sort_keys=True, default=str).encode()).hexdigest()

    def test_trial_balance_counts_posted_movements_once(self):
        before = self._ledger_fingerprint()
        self.assertEqual(self._cards(self._wizard()), [100, 100, 0])
        self.assertEqual(self._ledger_fingerprint(), before)

    def test_account_name_is_not_a_presentation_role(self):
        self.cash.name = "Total clients — invented ordinary account"
        self.env.flush_all()
        wizard = self._wizard()
        self.assertEqual(self._cards(wizard), [100, 100, 0])
        totals = [row for row in wizard._report_rows() if row.get("presentation_role") == "total"]
        self.assertEqual([sum(float(row.get(key) or 0) for row in totals) for key in ("debit", "credit")], [100, 100])

    def test_preview_limit_does_not_limit_financial_header(self):
        self.assertEqual(self._cards(self._wizard(preview_limit=1)), [100, 100, 0])

    def test_display_search_does_not_redefine_financial_header(self):
        for search in ("[QA] Cash", "Total débit", "no matching invented label"):
            with self.subTest(search=search):
                self.assertEqual(self._cards(self._wizard(search_text=search)), [100, 100, 0])

    def test_financial_account_filter_does_redefine_header(self):
        self.assertEqual(self._cards(self._wizard(account_ids=[Command.set(self.cash.ids)])), [73, 0, 73])

    def test_balance_sheet_totals_are_not_added_to_their_details(self):
        self.assertEqual(self._cards(self._wizard("balance_sheet")), [100, 100, 0])

    def test_journal_movements_are_not_doubled_by_total_rows(self):
        self.assertEqual(self._cards(self._wizard("journal_report")), [100, 100, 0])

    def test_grouping_and_collapse_do_not_change_full_controls(self):
        for report, grouping in [("trial_balance", "section"), ("journal_report", "journal"),
                                 ("general_ledger", "account")]:
            for details in (True, False):
                with self.subTest(report=report, details=details):
                    wizard = self._wizard(report, group_by=grouping, show_details=details, preview_limit=1)
                    self.assertEqual(self._cards(wizard), [100, 100, 0])

    def test_old_partial_preview_recalculates_without_fabricating_zero(self):
        wizard = self._wizard(preview_limit=1)
        wizard.action_preview_report()
        for metadata in ("{}", "not valid json", "[]"):
            with self.subTest(metadata=metadata):
                wizard.preview_metadata = metadata
                self.assertEqual([c["value"] for c in wizard._report_client_summary()["cards"]], [100, 100, 0])

    def test_aged_receivable_and_payable_total_original_open_items_once(self):
        partner = self.env["res.partner"].create({"name": "[QA] Open item counterpart"})
        for report, account_type, is_receivable in [
            ("aged_receivable", "asset_receivable", True),
            ("aged_payable", "liability_payable", False),
        ]:
            with self.subTest(report=report):
                account = self.env["account.account"].with_company(self.company).create({
                    "name": "[QA] " + report, "code": "411001" if is_receivable else "401001",
                    "account_type": account_type, "reconcile": True,
                })
                for amount in (25, 40):
                    move = self.env["account.move"].create({
                        "company_id": self.company.id, "journal_id": self.journal.id,
                        "date": "2026-03-20", "move_type": "entry",
                        "line_ids": [
                            Command.create({"account_id": account.id, "partner_id": partner.id,
                                            "date_maturity": "2026-04-20",
                                            "debit" if is_receivable else "credit": amount}),
                            Command.create({"account_id": self.cash.id,
                                            "credit" if is_receivable else "debit": amount}),
                        ],
                    })
                    move.action_post()
                # The next request's report reads SQL; finish the native seed
                # exactly as transaction commit does before a browser opens it.
                self.env.flush_all()
                self.assertEqual(self._cards(self._wizard(report, preview_limit=1)), [65])
