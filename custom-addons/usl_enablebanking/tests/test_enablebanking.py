"""Synthetic end-to-end contract using native journals and bank-line creation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import Mock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..services.protocol import ProviderDataError, import_identity


def resource(uid="session-a", primary="hash-a", aliases=None, **extra):
    return {"uid": uid, "identification_hash": primary, "identification_hashes": aliases or [primary],
            "currency": "EUR", "name": "Synthetic current account", "cash_account_type": "CACC", **extra}


def session(accounts=None):
    return {"session_id": "synthetic-session", "access": {"valid_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
            "accounts": accounts if accounts is not None else [resource()], "aspsp": {"name": "Fortuneo", "country": "FR"}, "psu_type": "personal"}


def movement(reference="booked-a", **extra):
    return {"entry_reference": reference, "status": "BOOK", "booking_date": "2026-10-01", "value_date": "2026-10-02",
            "transaction_amount": {"amount": "10.00", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
            "remittance_information": ["Synthetic bank transaction"], **extra}


@tagged("post_install", "-at_install", "usl_enablebanking")
class TestEnableBanking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.env.ref("base.EUR")
        cls.currency.active = True
        cls.manager = new_test_user(cls.env, login="banking-qa-manager", groups="account.group_account_manager,account.group_account_user",
                                    company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.accountant = new_test_user(cls.env, login="banking-qa-accountant", groups="account.group_account_user",
                                       company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.reader = new_test_user(cls.env, login="banking-qa-reader", groups="account.group_account_readonly",
                                   company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.other_company = cls.env["res.company"].create({"name": "Synthetic other company", "currency_id": cls.currency.id})
        cls.other_manager = new_test_user(cls.env, login="banking-qa-other-manager", groups="account.group_account_manager,account.group_account_user",
                                          company_id=cls.other_company.id, company_ids=[Command.set(cls.other_company.ids)])
        cls.outsider = new_test_user(cls.env, login="banking-qa-outsider", groups="base.group_user")

    def setUp(self):
        super().setUp()
        self.connection = self.env["usl.enablebanking.connection"].with_user(self.manager).create({"name": "Synthetic Fortuneo", "app_id": "synthetic-app", "company_id": self.company.id})
        self.connection._apply_session(session())
        self.account = self.connection.account_ids
        self.journal = self.env["account.journal"].create({"name": "Synthetic connected bank", "code": "EBQA", "type": "bank", "company_id": self.company.id, "currency_id": self.currency.id})
        self.account.write({"journal_id": self.journal.id, "import_from": "2026-10-01"})
        self.provider = self.account.provider_ids
        self.client = Mock()
        self.client.transactions.return_value = {"transactions": [movement()], "continuation_key": None}
        self.client.balances.return_value = {"balances": [{"name": "Available balance", "balance_type": "CLAV", "balance_amount": {"amount": "90.00", "currency": "EUR"}, "reference_date": "2026-10-01"}]}
        self.client_patch = patch.object(type(self.connection), "_client", return_value=self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def pull(self, user=None):
        return self.provider.with_user(user or self.accountant)._pull_bank_account(fields.Date.to_date("2026-10-01"), fields.Date.to_date("2026-10-02"))

    def imports(self):
        return self.env["usl.enablebanking.import"].search([("account_id", "=", self.account.id)])

    def test_all_bank_supported_products_are_discovered(self):
        payload = session([resource(), resource("session-savings", "hash-savings", cash_account_type="SVGS", name="Synthetic savings"), resource("session-other", "hash-other", cash_account_type="OTHR", name="Synthetic other product")])
        payload["aspsp"]["provider_note"] = "Additional synthetic metadata"
        self.connection._apply_session(payload)
        self.assertEqual(len(self.connection.account_ids), 3)
        self.assertEqual(set(self.connection.account_ids.mapped("account_type")), {"CACC", "SVGS", "OTHR"})

    def test_identical_movements_and_repeat_pages_create_two_native_lines_once(self):
        self.client.transactions.side_effect = [
            {"transactions": [movement("a")], "continuation_key": "page-2"},
            {"transactions": [movement("a"), movement("b")], "continuation_key": None},
        ]
        self.assertTrue(self.pull())
        receipts = self.imports()
        self.assertEqual(len(receipts), 2)
        self.assertEqual(len(receipts.line_id), 2)
        self.assertEqual(receipts.line_id.mapped("amount"), [-10.0, -10.0])
        self.assertEqual(set(receipts.line_id.mapped("date")), {fields.Date.to_date("2026-10-01")})
        self.assertEqual(set(receipts.line_id.journal_id.ids), {self.journal.id})
        ids = receipts.line_id.ids
        self.client.transactions.side_effect = None
        self.client.transactions.return_value = {"transactions": [movement("a"), movement("b")], "continuation_key": None}
        self.assertTrue(self.pull())
        self.assertEqual(self.imports().line_id.ids, ids)
        self.assertEqual(self.account.last_imported_count, 0)
        self.assertEqual(self.account.last_duplicate_count, 2)
        self.assertFalse(receipts.line_id.statement_id)

    def test_reconnect_matches_any_old_alias_and_keeps_journal_and_history(self):
        self.assertTrue(self.pull())
        ids = self.imports().line_id.ids
        account_id = self.account.id
        self.connection._apply_session(session([resource("renewed-uid", "new-hash", ["new-hash", "hash-a"])]))
        self.assertEqual(self.connection.account_ids.id, account_id)
        self.assertEqual(self.account.journal_id, self.journal)
        self.assertEqual(set(self.account.alias_ids.mapped("identity_hash")), {"new-hash", "hash-a"})
        self.assertEqual(self.account.sudo().session_uid, "renewed-uid")
        self.assertTrue(self.pull())
        self.assertEqual(self.imports().line_id.ids, ids)

    def test_later_page_failure_is_atomic_and_does_not_advance_checkpoint(self):
        before = self.provider.next_run
        self.client.transactions.side_effect = [{"transactions": [movement()], "continuation_key": "next"}, ProviderDataError("provider_unavailable")]
        self.assertFalse(self.pull())
        self.assertFalse(self.imports())
        self.assertFalse(self.account.last_import_at)
        self.assertFalse(self.provider.last_successful_run)
        self.assertEqual(self.provider.next_run, before)
        self.assertEqual(self.account.last_error_code, "provider_unavailable")

    def test_missing_bank_reference_never_uses_session_transaction_id(self):
        self.client.transactions.return_value = {"transactions": [movement(None, transaction_id="unstable-detail-id")], "continuation_key": None}
        self.assertFalse(self.pull())
        self.assertFalse(self.imports())
        self.assertEqual(self.account.last_error_code, "missing_entry_reference")

    def test_conflicting_replay_refuses_history_rewrite_and_whole_batch(self):
        self.assertTrue(self.pull())
        old = self.imports().line_id
        self.client.transactions.return_value = {"transactions": [movement("new"), movement(transaction_amount={"amount": "99.00", "currency": "EUR"})], "continuation_key": None}
        self.assertFalse(self.pull())
        self.assertEqual(self.imports().line_id, old)
        self.assertEqual(old.amount, -10)
        self.assertEqual(self.account.last_error_code, "identity_conflict")

    def test_expired_access_and_scheduled_failure_keep_checkpoint(self):
        self.connection.sudo().write({"consent_until": fields.Datetime.now() - timedelta(seconds=1)})
        before = (self.provider.next_run, self.provider.last_successful_run)
        self.provider.with_user(self.accountant).with_context(scheduled=True)._pull(datetime(2026, 10, 1), datetime(2026, 10, 2))
        self.assertEqual((self.provider.next_run, self.provider.last_successful_run), before)
        self.assertEqual(self.account.last_error_code, "consent_expired")
        self.client.transactions.assert_not_called()

    def test_provider_balance_keeps_type_currency_reference_without_statement_certification(self):
        self.assertTrue(self.pull())
        balance = self.account.balance_ids
        self.assertEqual((balance.balance_type, balance.currency_id, balance.reference_date, balance.amount), ("CLAV", self.currency, fields.Date.to_date("2026-10-01"), 90.0))
        self.assertFalse(self.imports().line_id.statement_id)

    def test_balance_failure_keeps_prior_cache_and_imports_complete_booked_period(self):
        self.assertTrue(self.pull())
        self.env.flush_all()
        self.env.cr.execute("SELECT to_jsonb(b) FROM usl_enablebanking_balance b WHERE account_id = %s ORDER BY id", [self.account.id])
        before = self.env.cr.fetchall()
        self.client.transactions.return_value = {"transactions": [movement("another-booked-entry")], "continuation_key": None}
        self.client.balances.side_effect = ProviderDataError("provider_unavailable")
        action = self.account.with_user(self.accountant).action_import()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertEqual(len(self.imports()), 2)
        self.assertEqual(self.account.last_imported_count, 1)
        self.assertFalse(self.account.last_error_code)
        self.assertEqual(self.account.balance_error_code, "provider_unavailable")
        self.assertIn("original dates", self.account.balance_warning)
        self.assertTrue(self.provider.last_successful_run)
        self.env.flush_all()
        self.env.cr.execute("SELECT to_jsonb(b) FROM usl_enablebanking_balance b WHERE account_id = %s ORDER BY id", [self.account.id])
        self.assertEqual(self.env.cr.fetchall(), before)
        self.client.balances.side_effect = None
        self.assertTrue(self.pull())
        self.assertFalse(self.account.balance_warning)
        self.assertFalse(self.account.balance_error_code)
        self.assertEqual(len(self.imports()), 2)

    def test_malformed_balances_warn_without_discarding_valid_booked_transactions(self):
        for index, balances in enumerate((None, {"balances": None}, {"balances": [None]}, {"balances": [{"balance_amount": {"amount": "Infinity", "currency": "EUR"}}]})):
            with self.subTest(index=index):
                self.client.transactions.return_value = {"transactions": [movement(f"valid-entry-{index}")], "continuation_key": None}
                self.client.balances.return_value = balances
                self.assertTrue(self.pull())
                self.assertEqual(self.account.balance_error_code, "invalid_balance")
                self.assertFalse(self.account.last_error_code)
        self.assertEqual(len(self.imports()), 4)
        self.assertFalse(self.account.balance_ids)

    def test_balance_consent_expiry_retains_complete_booked_import_and_prompts_renewal(self):
        self.client.balances.side_effect = ProviderDataError("consent_expired")
        self.assertTrue(self.pull())
        self.assertEqual(len(self.imports()), 1)
        self.assertTrue(self.provider.last_successful_run)
        self.assertEqual(self.connection.last_error_code, "consent_expired")
        self.assertIn("Renew bank access", self.account.balance_warning)
        self.assertFalse(self.account.last_error_code)

    def test_native_preview_balance_failure_does_not_change_cache_or_checkpoint(self):
        before = (self.provider.next_run, self.provider.last_successful_run)
        self.client.balances.side_effect = ProviderDataError("provider_unavailable")
        result = self.provider.with_user(self.accountant).with_context(account_statement_online_import_debug=True)._pull(datetime(2026, 10, 1), datetime(2026, 10, 2))
        self.assertEqual(len(result[0]["booked_transactions"]), 1)
        self.assertEqual(result[0]["balance_warning_code"], "provider_unavailable")
        self.assertFalse(self.imports())
        self.assertFalse(self.account.balance_error_code)
        self.assertEqual((self.provider.next_run, self.provider.last_successful_run), before)

    def test_readonly_and_foreign_company_calls_are_refused_before_provider_request(self):
        for user in (self.other_manager, self.outsider, self.reader):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.pull(user)
        self.client.transactions.assert_not_called()
        self.assertEqual(self.connection.with_user(self.reader).read(["name"])[0]["name"], "Synthetic Fortuneo")
        self.assertEqual(self.account.with_user(self.reader).action_transactions()["res_model"], "account.bank.statement.line")
        self.assertEqual(self.account.with_user(self.reader).read()[0]["name"], self.account.name)
        with self.assertRaises(AccessError):
            self.connection.with_user(self.accountant).action_connect()

    def test_managed_source_identity_and_import_provenance_cannot_be_forged(self):
        with self.assertRaises(AccessError):
            self.account.with_user(self.accountant).write({"stable_key": "forged"})
        with self.assertRaises(AccessError):
            self.connection.write({"session_id": "forged"})
        with self.assertRaises(AccessError):
            self.env["usl.enablebanking.account"].with_user(self.manager).create({"name": "forged", "company_id": self.company.id, "connection_id": self.connection.id, "currency_id": self.currency.id})
        with self.assertRaises(AccessError):
            self.env["usl.enablebanking.import"].with_user(self.manager).create({"account_id": self.account.id})

    def test_journal_currency_and_company_are_native_boundaries(self):
        usd = self.env.ref("base.USD")
        wrong = self.env["account.journal"].create({"name": "Synthetic foreign currency", "code": "EBUS", "type": "bank", "company_id": self.company.id, "currency_id": usd.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.account.write({"journal_id": wrong.id})
        self.assertTrue(self.pull())
        old_line = self.imports().line_id
        replacement = self.env["account.journal"].create({"name": "Synthetic replacement bank", "code": "EBR2", "type": "bank", "company_id": self.company.id, "currency_id": self.currency.id})
        self.account.write({"journal_id": replacement.id})
        self.assertTrue(self.pull())
        self.assertEqual(self.imports().line_id, old_line)
        self.assertEqual(old_line.journal_id, self.journal)

    def test_return_state_is_user_company_expiry_bound_and_single_use(self):
        state = "synthetic-state"
        self.connection.sudo().write({"state_hash": hashlib.sha256(state.encode()).hexdigest(), "state_user_id": self.manager.id, "state_until": fields.Datetime.now() + timedelta(minutes=5)})
        with self.assertRaises(AccessError):
            self.connection._consume_state("wrong-state")
        with self.assertRaises(AccessError):
            self.connection.with_user(self.other_manager)._consume_state(state)
        self.connection._consume_state(state)
        self.assertFalse(self.connection.sudo().state_hash)
        with self.assertRaises(AccessError):
            self.connection._consume_state(state)

    def test_missing_account_after_reconnect_is_unavailable_without_deleting_history(self):
        self.assertTrue(self.pull())
        old = self.imports().line_id
        self.connection._apply_session(session([]))
        self.assertFalse(self.account.available)
        self.assertFalse(self.pull())
        self.assertEqual(self.imports().line_id, old)

    def test_native_preview_does_not_create_lines_or_move_checkpoint(self):
        before = (self.provider.next_run, self.provider.last_successful_run)
        result = self.provider.with_user(self.accountant).with_context(account_statement_online_import_debug=True)._pull(datetime(2026, 10, 1), datetime(2026, 10, 2))
        self.assertEqual(len(result[0]["booked_transactions"]), 1)
        self.assertFalse(self.imports())
        self.assertFalse(self.account.balance_ids)
        self.assertEqual((self.provider.next_run, self.provider.last_successful_run), before)

    def test_native_wizard_opens_bank_lines_instead_of_empty_statements(self):
        wizard = self.env["online.bank.statement.pull.wizard"].with_user(self.accountant).with_context(active_model="online.bank.statement.provider", active_id=self.provider.id).create({"date_since": "2026-10-01 00:00:00", "date_until": "2026-10-02 23:59:59"})
        action = wizard.action_pull()
        self.assertEqual(action["res_model"], "account.bank.statement.line")
        self.assertEqual(action["domain"], [("id", "in", self.imports().line_id.ids)])

    def test_unknown_status_and_currency_precision_are_visible_atomic_failures(self):
        for row, expected in [(movement(status=None), "invalid_transaction_status"), (movement(transaction_amount={"amount": "1.001", "currency": "EUR"}), "invalid_currency_precision")]:
            with self.subTest(code=expected):
                self.client.transactions.return_value = {"transactions": [row], "continuation_key": None}
                self.assertFalse(self.pull())
                self.assertFalse(self.imports())
                self.assertEqual(self.account.last_error_code, expected)

    def test_malformed_status_or_direction_leaves_no_partial_native_import_or_checkpoint(self):
        before = (self.provider.next_run, self.provider.last_successful_run)
        for field in ("status", "credit_debit_indicator"):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    self.client.transactions.return_value = {"transactions": [movement("valid-first"), movement("invalid-second", **{field: value})], "continuation_key": None}
                    self.assertFalse(self.pull())
                    self.assertFalse(self.imports())
                    self.assertFalse(self.env["account.bank.statement.line"].search([("journal_id", "=", self.journal.id)]))
                    self.assertFalse(self.account.last_import_at)
                    self.assertEqual((self.provider.next_run, self.provider.last_successful_run), before)

    def test_one_number_with_distinct_currency_resources_retains_both(self):
        self.env.ref("base.USD").active = True
        self.connection._apply_session(session([resource(), resource("usd-session", currency="USD", name="Synthetic currency account")]))
        self.assertEqual(len(self.connection.account_ids), 2)
        self.assertEqual(set(self.connection.account_ids.currency_id.mapped("name")), {"EUR", "USD"})

    def test_auth_request_uses_provider_maximum_personal_ais_without_restricting_accounts(self):
        self.env["ir.config_parameter"].sudo().set_str("web.base.url", "https://banking-qa.example.invalid")
        self.client.aspsps.return_value = {"aspsps": [{"name": "Fortuneo", "country": "FR", "psu_types": ["personal"], "maximum_consent_validity": 3600}]}
        self.client.authorize.return_value = "https://auth.enablebanking.com/ais/start?synthetic=1"
        result = self.connection.action_connect()
        payload = self.client.authorize.call_args.args[0]
        self.assertEqual(payload["psu_type"], "personal")
        self.assertNotIn("accounts", payload["access"])
        self.assertTrue(payload["access"]["balances"])
        self.assertTrue(payload["access"]["transactions"])
        self.assertEqual(result["type"], "ir.actions.act_url")
        expiry = datetime.fromisoformat(payload["access"]["valid_until"])
        self.assertLessEqual((expiry - datetime.now(timezone.utc)).total_seconds(), 3600)
        self.assertGreater((expiry - datetime.now(timezone.utc)).total_seconds(), 3590)

    def test_same_bank_reference_on_two_supported_accounts_remains_two_native_entries(self):
        self.connection._apply_session(session([resource(), resource("savings-uid", "savings-hash", cash_account_type="SVGS")]))
        savings = self.connection.account_ids - self.account
        journal = self.env["account.journal"].create({"name": "Synthetic savings bank", "code": "EBSV", "type": "bank", "company_id": self.company.id, "currency_id": self.currency.id})
        savings.write({"journal_id": journal.id, "import_from": "2026-10-01"})
        self.assertTrue(self.pull())
        self.assertTrue(savings.provider_ids.with_user(self.accountant)._pull_bank_account(fields.Date.to_date("2026-10-01"), fields.Date.to_date("2026-10-02")))
        receipts = self.env["usl.enablebanking.import"].search([("account_id", "in", (self.account | savings).ids)])
        self.assertEqual(len(receipts), 2)
        self.assertEqual(len(receipts.line_id), 2)
        self.assertEqual(len(set(receipts.mapped("entry_reference"))), 1)
        self.assertEqual(set(receipts.line_id.journal_id.ids), {self.journal.id, journal.id})

    def test_native_agent_write_delegation_imports_and_readonly_refuses_before_provider(self):
        if "usl.agent" not in self.env:
            self.skipTest("Native Agent parity requires the complete product registry")
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        from odoo.addons.usl_access_control.exceptions import AgentPolicyAccessError
        groups = self.env.ref("account.group_account_user")
        agent = self.env["usl.agent"].with_user(self.manager).create({
            "name": "Synthetic banking Agent", "purpose": "Prove the native delegated import contract",
            "company_id": self.company.id, "company_ids": [Command.set(self.company.ids)],
            "delegated_group_ids": [Command.set(groups.ids)], "access_mode": "read_write",
        })
        access = UslAgentJson2Controller._check_agent_call(agent=agent, model_name=self.account._name, method_name="action_import", kwargs={"ids": self.account.ids})
        self.assertEqual(access, "write")
        context = UslAgentJson2Controller._agent_call_context(context={}, agent=agent, model_name=self.account._name, method_name="action_import", access=access)
        self.account.with_user(agent.user_id).with_context(context).action_import()
        self.assertEqual(self.imports().line_id.create_uid, agent.user_id)
        self.assertEqual(self.imports().create_uid, agent.user_id)
        native_ids = self.imports().line_id.ids
        self.account.with_user(agent.user_id).with_context(context).action_import()
        self.assertEqual(self.imports().line_id.ids, native_ids)
        self.assertEqual(self.account.last_duplicate_count, 1)
        agent.with_user(self.manager).write({"access_mode": "read_only"})
        self.client.reset_mock()
        with self.assertRaises(AgentPolicyAccessError):
            UslAgentJson2Controller._check_agent_call(agent=agent, model_name=self.account._name, method_name="action_import", kwargs={"ids": self.account.ids})
        self.client.transactions.assert_not_called()
        read_access = UslAgentJson2Controller._check_agent_call(agent=agent, model_name=self.account._name, method_name="action_transactions", kwargs={"ids": self.account.ids})
        self.assertEqual(read_access, "read_only")
        self.assertEqual(self.account.with_user(agent.user_id).action_transactions()["domain"], [("id", "in", native_ids)])

    def test_accounting_reader_can_view_native_imports_without_mutation(self):
        self.assertTrue(self.pull())
        ids = self.imports().line_id.ids
        action = self.account.with_user(self.reader).action_transactions()
        self.assertEqual(action["domain"], [("id", "in", ids)])
        self.assertEqual(self.env["account.bank.statement.line"].with_user(self.reader).browse(ids).read(["amount"])[0]["amount"], -10)
        with self.assertRaises(AccessError):
            self.account.with_user(self.reader).action_import()
        self.assertEqual(self.imports().line_id.ids, ids)

    def test_selected_other_company_does_not_change_journal_ownership_or_exchange_rate(self):
        usd = self.env.ref("base.USD")
        self.other_company.currency_id = usd if self.company.currency_id != usd else self.currency
        self.manager.company_ids = [Command.set((self.company | self.other_company).ids)]
        rate_company = self.company if self.company.currency_id != self.currency else self.other_company
        self.env["res.currency.rate"].create({"currency_id": self.currency.id, "company_id": rate_company.id, "name": "2026-10-01", "rate": 2.0})
        provider = self.provider.with_user(self.manager).with_context(allowed_company_ids=[self.other_company.id, self.company.id])
        self.assertEqual(provider.env.company, self.other_company)
        self.assertTrue(provider._pull_bank_account(fields.Date.to_date("2026-10-01"), fields.Date.to_date("2026-10-02")))
        line = self.imports().line_id
        self.assertEqual(line.company_id, self.company)
        self.assertEqual(line.move_id.company_id, self.company)
        self.assertEqual(line.journal_id, self.journal)
        self.assertEqual(line.amount, -10)
        liquidity, _suspense, _other = line._seek_for_lines()
        self.assertEqual(liquidity.currency_id, self.currency)
        self.assertEqual(liquidity.amount_currency, -10)
        self.assertAlmostEqual(sum(line.move_id.line_ids.mapped("balance")), 0)
        if self.company.currency_id == usd:
            self.assertEqual(liquidity.balance, -5)
        elif self.company.currency_id == self.currency:
            self.assertEqual(liquidity.balance, -10)

    def _existing_native_line(self, reference="booked-a", **values):
        key = {"unique_import_id": import_identity(self.company.id, self.account.stable_key, reference)}
        self.journal._statement_line_import_update_unique_import_id(key, self.provider.account_number)
        return self.env["account.bank.statement.line"].create({
            "journal_id": self.journal.id, "date": "2026-10-01", "amount": -10,
            "payment_ref": "Original synthetic native memo", **key, **values,
        })

    def _native_fingerprints(self, line):
        self.env.flush_all()
        result = {}
        for table, predicate, identifier in (
            ("account_bank_statement_line", "id", line.id),
            ("account_move", "id", line.move_id.id),
            ("account_move_line", "move_id", line.move_id.id),
        ):
            self.env.cr.execute(f"SELECT to_jsonb(r) FROM {table} r WHERE {predicate} = %s ORDER BY r.id", [identifier])
            value = self.env.cr.fetchall()
            result[table] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return result

    def test_exact_native_existing_identity_is_adopted_with_all_original_columns_unchanged(self):
        existing = self._existing_native_line()
        before = self._native_fingerprints(existing)
        self.assertTrue(self.pull())
        receipt = self.imports()
        self.assertEqual(receipt.line_id, existing)
        self.assertEqual(receipt.line_origin, "matched_existing")
        self.assertEqual(self._native_fingerprints(existing), before)
        self.assertEqual(existing.payment_ref, "Original synthetic native memo")
        self.assertEqual(self.account.last_imported_count, 0)
        self.assertEqual(self.account.last_duplicate_count, 1)
        self.assertTrue(self.pull())
        self.assertEqual(self.imports(), receipt)
        self.assertEqual(self._native_fingerprints(existing), before)

    def test_existing_identity_financial_or_tenant_conflicts_leave_original_lines_and_checkpoint(self):
        other_journal = self.env["account.journal"].create({"name": "Synthetic different journal", "code": "EBCF", "type": "bank", "company_id": self.company.id, "currency_id": self.currency.id})
        self.env["account.chart.template"].try_loading("generic_coa", company=self.other_company, install_demo=False)
        foreign_journal = self.env["account.journal"].with_company(self.other_company).create({"name": "Synthetic foreign bank", "code": "EBCX", "type": "bank", "company_id": self.other_company.id, "currency_id": self.currency.id})
        for number, values in enumerate((
            {"amount": -11}, {"date": "2026-10-02"}, {"journal_id": other_journal.id}, {"journal_id": foreign_journal.id},
        )):
            with self.subTest(values=list(values)):
                reference = f"conflict-{number}"
                existing = self._existing_native_line(reference, **values)
                before = self._native_fingerprints(existing)
                checkpoint = (self.provider.last_successful_run, self.provider.next_run)
                self.client.transactions.return_value = {"transactions": [movement("new-entry"), movement(reference)], "continuation_key": None}
                self.assertFalse(self.pull())
                self.assertEqual(self._native_fingerprints(existing), before)
                self.assertFalse(self.imports())
                self.assertEqual(self.account.last_error_code, "identity_conflict")
                self.assertEqual((self.provider.last_successful_run, self.provider.next_run), checkpoint)

    def test_ordinary_accountant_public_import_keeps_configured_journal_unchanged(self):
        self.env.flush_all()
        self.env.cr.execute("SELECT md5(to_jsonb(j)::text) FROM account_journal j WHERE id = %s", [self.journal.id])
        before = self.env.cr.fetchone()[0]
        action = self.account.with_user(self.accountant).action_import()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(self.account.last_imported_count, 1)
        self.env.flush_all()
        self.env.cr.execute("SELECT md5(to_jsonb(j)::text) FROM account_journal j WHERE id = %s", [self.journal.id])
        self.assertEqual(self.env.cr.fetchone()[0], before)

    def test_native_oca_scheduler_imports_and_preserves_checkpoint_on_failure(self):
        self.provider.next_run = fields.Datetime.now() - timedelta(hours=1)
        checkpoint = (self.provider.last_successful_run, self.provider.next_run)
        self.client.transactions.side_effect = ProviderDataError("provider_unavailable")
        self.env["online.bank.statement.provider"].with_user(self.accountant)._scheduled_pull()
        self.assertEqual((self.provider.last_successful_run, self.provider.next_run), checkpoint)
        self.assertFalse(self.imports())
        self.client.transactions.side_effect = None
        self.env["online.bank.statement.provider"].with_user(self.accountant)._scheduled_pull()
        self.assertEqual(len(self.imports().line_id), 1)
        self.assertTrue(self.provider.last_successful_run)
        self.assertGreater(self.provider.next_run, fields.Datetime.now())
