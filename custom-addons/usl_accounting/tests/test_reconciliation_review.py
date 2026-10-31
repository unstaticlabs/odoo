"""Shared marks compare the complete native Remaining aggregate only."""
from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_reconciliation_review")
class TestReconciliationReview(AccountTestInvoicingCommon):
    def test_project_only_user_metadata_is_false_without_journal_access(self):
        from odoo.exceptions import AccessError
        from odoo.tests import new_test_user
        actor = new_test_user(self.env, login="qa.review.project-only",
                              groups="project.group_project_user")
        Review = self.env["account.reconciliation.review"].with_user(actor)
        self.assertFalse(Review.has_access("read"))
        with self.assertRaises(AccessError):
            Review.search([])
        # A standalone review ACL must not imply access to journal items.
        role = self.env["res.groups"].create({"name": "[QA review] Metadata only"})
        actor.group_ids = [Command.link(role.id)]
        self.env["ir.model.access"].create({
            "name": "[QA review] Standalone metadata read",
            "model_id": self.env["ir.model"]._get_id(Review._name),
            "group_id": role.id, "perm_read": True,
        })
        self.env.flush_all()
        self.env.registry.clear_cache("stable")
        self.assertFalse(self.env["account.move.line"].with_user(actor).has_access("read"))
        self.assertFalse(Review.has_access("read"))
        with self.assertRaises(AccessError):
            Review.search([])

    def test_required_account_read_and_optional_partner_read_boundaries(self):
        from unittest.mock import patch
        reviewer = self._reviewer("related-metadata")
        domain, path = self._scope()
        self._post(100)
        Review = self.env["account.reconciliation.review"].with_user(reviewer)
        plain = self._mark(Review, domain, path, 0)
        nested = self._mark(Review, domain, path + [["partner_id", self.partner_a.id]], 0)
        ids = self.env["account.reconciliation.review"].search([
            ("scope_key", "in", [plain["scope_key"], nested["scope_key"]]),
        ]).ids
        accounts = type(self.env["account.account"])
        partners = type(self.env["res.partner"])
        with patch.object(accounts, "has_access", return_value=False), patch.object(
            accounts, "_search", side_effect=AssertionError("Unreadable accounts must not be queried"),
        ):
            self.assertFalse(Review.has_access("read"))
        with patch.object(partners, "has_access", return_value=False), patch.object(
            partners, "_search", side_effect=AssertionError("Unreadable partners must not be queried"),
        ):
            visible = Review.search([("id", "in", ids)])
            self.assertEqual(visible.mapped("scope_key"), [plain["scope_key"]])

    def test_shared_remaining_review_and_acknowledgement(self):
        self.assertIn("account.reconciliation.review", self.env,
                      "General Reconciliation must retain a shared Remaining review baseline")
        Review = self.env["account.reconciliation.review"]
        account = self.company_data["default_account_receivable"]
        move = self.env["account.move"].create({
            "move_type": "entry", "journal_id": self.company_data["default_journal_misc"].id,
            "date": "2026-10-01", "line_ids": [
                Command.create({"account_id": account.id, "partner_id": self.partner_a.id, "debit": 100}),
                Command.create({"account_id": self.company_data["default_account_revenue"].id, "credit": 100}),
            ],
        })
        move.action_post()
        domain = [("date", ">=", "2026-10-01"), ("date", "<=", "2026-10-31")]
        path = [["account_id", account.id]]
        before = move.line_ids.read(["debit", "credit", "amount_residual", "matched_debit_ids", "matched_credit_ids"])
        initial = Review.get_review_status(domain, [path])[0]
        self.assertIsNone(initial["amounts"][0]["baseline"])
        self.assertIsNone(initial["amounts"][0]["delta"])
        status = self._mark(Review, domain, path, expected_revision=0)
        self.assertEqual(status["state"], "reviewed")
        self.assertEqual(status["amounts"][0]["current"], 100)
        self.assertEqual(Review.get_review_status(domain, [path])[0]["state"], "reviewed")
        self.assertEqual(move.line_ids.read(["debit", "credit", "amount_residual", "matched_debit_ids", "matched_credit_ids"]), before)

    def _mark(self, api, domain, path, expected_revision):
        status = api.get_review_status(domain, [path])[0]
        return api.mark_reviewed(domain, path, expected_revision, status["observed_balance"])

    def _dismiss(self, api, domain, path, expected_revision):
        status = api.get_review_status(domain, [path])[0]
        return api.dismiss_variance(domain, path, expected_revision, status["observed_balance"])

    def test_stale_displayed_remaining_cannot_be_reviewed_or_acknowledged(self):
        from odoo.exceptions import UserError
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        displayed = Review.get_review_status(domain, [path])[0]
        self._post(25)
        with self.assertRaisesRegex(UserError, "Remaining balance changed"):
            Review.mark_reviewed(domain, path, 0, displayed["observed_balance"])
        self.assertEqual(Review.get_review_status(domain, [path])[0]["state"], "unreviewed")
        reviewed = self._mark(Review, domain, path, 0)
        self._post(25)
        warning = Review.get_review_status(domain, [path])[0]
        self._post(25)
        with self.assertRaisesRegex(UserError, "Remaining balance changed"):
            Review.dismiss_variance(domain, path, reviewed["revision"], warning["observed_balance"])
        current = Review.get_review_status(domain, [path])[0]
        self.assertEqual(current["state"], "changed")
        self.assertEqual(current["revision"], reviewed["revision"])
        self.assertEqual(current["amounts"][0]["baseline"], 125)
        self.assertFalse(current["acknowledged_at"])
        acknowledged = self._dismiss(Review, domain, path, current["revision"])
        self.assertEqual(acknowledged["state"], "acknowledged")
        self.assertEqual(acknowledged["amounts"][0]["current"], 175)

    def _move(self, amount, date="2026-10-01", partner=None, company_data=None, account=None):
        data = company_data or self.company_data
        return self.env["account.move"].with_company(data["company"]).create({
            "move_type": "entry", "journal_id": data["default_journal_misc"].id, "date": date,
            "line_ids": [
                Command.create({"account_id": (account or data["default_account_receivable"]).id,
                                "partner_id": (partner or self.partner_a).id,
                                "debit": max(amount, 0), "credit": max(-amount, 0)}),
                Command.create({"account_id": data["default_account_revenue"].id,
                                "credit": max(amount, 0), "debit": max(-amount, 0)}),
            ],
        })

    def _scope(self):
        return ([("date", ">=", "2026-10-01"), ("date", "<", "2026-11-01")],
                [["account_id", self.company_data["default_account_receivable"].id]])

    def _post(self, *args, **kwargs):
        move = self._move(*args, **kwargs)
        move.action_post()
        return move

    def test_warning_acknowledgement_new_change_rereview_and_return(self):
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        first = self._mark(Review, domain, path, 0)
        self._post(25)
        changed = Review.get_review_status(domain, [path])[0]
        self.assertEqual(changed["state"], "changed")
        self.assertEqual(changed["amounts"][0]["delta"], 25)
        acknowledged = self._dismiss(Review, domain, path, first["revision"])
        self.assertEqual(acknowledged["state"], "acknowledged")
        self.assertEqual(acknowledged["amounts"][0]["baseline"], 100)
        self._post(10)
        self.assertEqual(Review.get_review_status(domain, [path])[0]["state"], "changed")
        self._post(-35)
        self.assertEqual(Review.get_review_status(domain, [path])[0]["state"], "reviewed")
        self._post(7)
        final = self._mark(Review, domain, path, acknowledged["revision"])
        self.assertEqual(final["state"], "reviewed")
        self.assertEqual(final["amounts"][0]["baseline"], 107)

    def test_offsetting_lines_and_outside_filters_preserve_green(self):
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        self._mark(Review, domain, path, 0)
        self._post(17)
        self._post(-17)
        self._post(20, date="2026-11-01")
        status = Review.get_review_status(domain, [path])[0]
        self.assertEqual(status["state"], "reviewed")
        self.assertEqual(status["amounts"][0]["current"], 100)
        self.assertEqual(Review.get_review_status([("date", ">=", "2026-11-01")], [path])[0]["state"], "unreviewed")

    def test_full_parent_and_nested_month_partner_groups_are_batched(self):
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        for _ in range(85):
            self._post(10)
        self._post(9, partner=self.partner_b)
        paths = [path, path + [["partner_id", self.partner_a.id]], path + [["partner_id", self.partner_b.id]],
                 path + [["partner_id", self.partner_a.id], ["date:month", "2026-10-01"]]]
        from unittest.mock import patch
        Lines = type(self.env["account.move.line"])
        original = Lines._read_group
        calls = []
        def aggregate(records, *args, **kwargs):
            calls.append(1)
            return original(records, *args, **kwargs)
        with patch.object(Lines, "_read_group", aggregate):
            statuses = Review.get_review_status(domain, paths)
        self.assertEqual(len(calls), 3)
        self.assertEqual([item["amounts"][0]["current"] for item in statuses], [859, 850, 9, 850])
        for status, group_path in zip(statuses, paths):
            self._mark(Review, domain, group_path, status["revision"])
        self.assertEqual({item["state"] for item in Review.get_review_status(domain, paths)}, {"reviewed"})

    def _reviewer(self, suffix, readonly=False):
        from odoo.tests import new_test_user
        return new_test_user(self.env, login=f"qa.review.{suffix}@example.invalid",
                             groups="account.group_account_readonly" if readonly else "account.group_account_user",
                             company_id=self.env.company.id, company_ids=[Command.set(self.env.company.ids)])

    def test_shared_accountants_and_native_readonly_annotation_denial(self):
        from odoo.exceptions import AccessError
        domain, path = self._scope()
        self._post(100)
        first, second = self._reviewer("first"), self._reviewer("second")
        Review = self.env["account.reconciliation.review"]
        first_api, second_api = Review.with_user(first), Review.with_user(second)
        marked = self._mark(first_api, domain, path, 0)
        visible = second_api.get_review_status(domain, [path])[0]
        self.assertEqual(visible["state"], "reviewed")
        self.assertEqual(visible["reviewer"], first.display_name)
        self.assertEqual(marked["scope_key"], visible["scope_key"])
        readonly = Review.with_user(self._reviewer("readonly", readonly=True))
        self.assertFalse(readonly.get_review_status(domain, [path])[0]["can_review"])
        with self.assertRaises(AccessError):
            self._mark(readonly, domain, path, visible["revision"])

    def test_revision_rejects_stale_review_and_acknowledgement(self):
        from odoo.exceptions import UserError
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        first = self._mark(Review, domain, path, 0)
        second = self._mark(Review, domain, path, first["revision"])
        with self.assertRaises(UserError):
            self._dismiss(Review, domain, path, first["revision"])
        self.assertEqual(Review.get_review_status(domain, [path])[0]["revision"], second["revision"])

    def test_metadata_cannot_spoof_totals_reviewer_or_identity(self):
        from odoo.exceptions import UserError
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        status = self._mark(Review, domain, path, 0)
        snapshot = Review.search([("scope_key", "=", status["scope_key"])])
        with self.assertRaises(UserError):
            snapshot.write({"baseline": {str(self.env.company.currency_id.id): 999}, "reviewer_id": self.env.ref("base.user_admin").id})
        self.assertEqual(Review.get_review_status(domain, [path])[0]["amounts"][0]["baseline"], 100)

    def test_same_currency_company_offsets_do_not_change_remaining(self):
        data_b = self.setup_other_company(name="[QA review] Second company")
        data_b["company"].currency_id = self.env.company.currency_id
        account = self.env["account.account"].create({
            "name": "[QA review] Shared receivable", "code": "QA3241001", "account_type": "asset_receivable",
            "reconcile": True, "company_ids": [Command.set((self.env.company | data_b["company"]).ids)],
        })
        self._post(100, account=account)
        Review = self.env["account.reconciliation.review"].with_context(allowed_company_ids=(self.env.company | data_b["company"]).ids)
        domain = [("date", ">=", "2026-10-01")]
        path = [["account_id", account.id]]
        self._mark(Review, domain, path, 0)
        self._post(5, account=account)
        self._post(-5, account=account, company_data=data_b)
        status = Review.get_review_status(domain, [path])[0]
        self.assertEqual(status["state"], "reviewed")
        self.assertEqual(status["amounts"][0]["current"], 100)
        self.assertEqual(len(status["amounts"]), 1)
        self.assertEqual(Review.with_context(allowed_company_ids=self.env.company.ids).get_review_status(domain, [path])[0]["state"], "unreviewed")

    def test_native_mixed_currency_remaining_and_midnight_keep_shared_scope(self):
        from unittest.mock import patch
        from datetime import date
        data_b = self.setup_other_company(name="[QA review] Foreign currency company")
        currency_b = self.env.ref("base.EUR") if self.env.company.currency_id != self.env.ref("base.EUR") else self.env.ref("base.USD")
        currency_b.active = True
        data_b["company"].currency_id = currency_b
        for currency, rate in ((self.env.company.currency_id, 1), (currency_b, 2)):
            self.env["res.currency.rate"].create({"currency_id": currency.id, "company_id": self.env.company.id,
                                                 "name": "2026-10-01", "rate": rate})
        account = self.env["account.account"].create({
            "name": "[QA review] Mixed receivable", "code": "QA3241002", "account_type": "asset_receivable",
            "reconcile": True, "company_ids": [Command.set((self.env.company | data_b["company"]).ids)],
        })
        self._post(100, account=account)
        self._post(50, account=account, company_data=data_b)
        Review = self.env["account.reconciliation.review"].with_context(allowed_company_ids=(self.env.company | data_b["company"]).ids)
        domain, path = [("date", ">=", "2026-10-01")], [["account_id", account.id]]
        with patch.object(fields.Date, "context_today", return_value=date(2026, 10, 17)):
            first = self._mark(Review, domain, path, 0)
            native = self.env["account.move.line"].with_context(allowed_company_ids=Review.env.companies.ids)._read_group(
                [("account_id", "=", account.id), ("parent_state", "=", "posted")], [], ["amount_residual:sum_currency"],
            )[0][0]
        self.assertEqual(first["amounts"][0]["current"], self.env.company.currency_id.round(native))
        with patch.object(fields.Date, "context_today", return_value=date(2026, 10, 18)):
            tomorrow = Review.get_review_status(domain, [path])[0]
        self.assertEqual(tomorrow["scope_key"], first["scope_key"])
        self.assertEqual(tomorrow["state"], "reviewed")
        self.assertEqual(tomorrow["reviewed_context"]["valuation_date"], "2026-10-17")
        self.env["res.currency.rate"].create({"currency_id": currency_b.id, "company_id": self.env.company.id,
                                             "name": "2026-10-17", "rate": 4})
        with patch.object(fields.Date, "context_today", return_value=date(2026, 10, 18)):
            moved = Review.get_review_status(domain, [path])[0]
        self.assertEqual(moved["scope_key"], first["scope_key"])
        self.assertEqual(moved["state"], "changed")

    def test_native_reconciliation_changes_only_the_in_scope_remaining(self):
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        first = self._post(100)
        counterpart = self._post(-20, date="2026-11-01")
        self._mark(Review, domain, path, 0)
        lines = (first | counterpart).line_ids.filtered(lambda line: line.account_id.id == path[0][1])
        lines.reconcile()
        changed = Review.get_review_status(domain, [path])[0]
        self.assertEqual(changed["state"], "changed")
        self.assertEqual(changed["amounts"][0]["baseline"], 100)
        self.assertEqual(changed["amounts"][0]["current"], 80)
        self.assertEqual(changed["amounts"][0]["delta"], -20)

    def test_raw_saved_search_literals_are_technical_but_exact_status_scope_is_readable(self):
        from odoo.exceptions import AccessError
        domain, path = self._scope()
        domain.append(("name", "not ilike", "[QA private search literal]"))
        writer = self._reviewer("private-scope")
        self._post(100)
        Review = self.env["account.reconciliation.review"].with_user(writer)
        marked = self._mark(Review, domain, path, 0)
        record = Review.search([("scope_key", "=", marked["scope_key"])])
        self.assertTrue(record)
        with self.assertRaises(AccessError):
            record.read(["domain", "path"])
        self.assertIn("[QA private search literal]", str(Review.get_review_status(domain, [path])[0]["domain"]))
        self.assertEqual(record.reviewer_id, writer)
        self.assertEqual(record.create_uid, writer)

    def test_archived_partner_does_not_erase_a_native_readable_review(self):
        domain, path = self._scope()
        self._post(100)
        path.append(["partner_id", self.partner_a.id])
        Review = self.env["account.reconciliation.review"].with_user(self._reviewer("archived-partner"))
        first = self._mark(Review, domain, path, 0)
        self.partner_a.active = False
        current = Review.get_review_status(domain, [path])[0]
        self.assertEqual(current["scope_key"], first["scope_key"])
        self.assertEqual(current["state"], "reviewed")

    def test_review_and_dismiss_leave_all_financial_rows_identical(self):
        Review = self.env["account.reconciliation.review"]
        domain, path = self._scope()
        self._post(100)
        def fingerprint():
            result = {}
            self.env.flush_all()
            for table in ("account_move", "account_move_line", "account_partial_reconcile", "account_full_reconcile"):
                self.env.cr.execute(f"SELECT md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY id), '')) FROM {table} t")
                result[table] = self.env.cr.fetchone()[0]
            return result
        before = fingerprint()
        reviewed = self._mark(Review, domain, path, 0)
        self._dismiss(Review, domain, path, reviewed["revision"])
        self.assertEqual(fingerprint(), before)

    def test_membership_filter_order_is_not_snapshot_identity(self):
        from itertools import permutations
        from odoo.addons.usl_accounting.models.reconciliation_review import _canonical
        expected = _canonical([("company_id", "in", [1, 2, 3])])
        for values in permutations([1, 2, 3]):
            self.assertEqual(_canonical([("company_id", "in", list(values))]), expected)
        self.assertNotEqual(_canonical([["account_id", 1], ["partner_id", 2]]),
                            _canonical([["partner_id", 2], ["account_id", 1]]))

    def test_native_company_and_relation_visibility_remains_required(self):
        from odoo.exceptions import AccessError
        data_b = self.setup_other_company(name="[QA review] Inaccessible company")
        actor = self._reviewer("single-company")
        Review = self.env["account.reconciliation.review"].with_user(actor).with_context(allowed_company_ids=self.env.company.ids)
        domain, path = self._scope()
        foreign_account = data_b["default_account_receivable"]
        with self.assertRaises(AccessError):
            Review.get_review_status(domain, [[["account_id", foreign_account.id]]])
        foreign_partner = self.env["res.partner"].create({"name": "[QA review] Private partner", "company_id": data_b["company"].id})
        with self.assertRaises(AccessError):
            Review.get_review_status(domain, [path + [["partner_id", foreign_partner.id]]])
        with self.assertRaises(AccessError):
            Review.with_context(allowed_company_ids=data_b["company"].ids).get_review_status(domain, [path])

    def test_optional_review_does_not_block_native_empty_group_deletion(self):
        account = self.env["account.account"].create({"name": "[QA review] Empty group", "code": "QA3241DEL", "account_type": "asset_receivable", "reconcile": True})
        partner = self.env["res.partner"].create({"name": "[QA review] Empty partner"})
        Review = self.env["account.reconciliation.review"]
        domain = [("date", ">=", "2026-10-01")]
        account_path = [["account_id", account.id]]
        partner_path = account_path + [["partner_id", partner.id]]
        plain = self._mark(Review, domain, account_path, 0)
        nested = self._mark(Review, domain, partner_path, 0)
        nested_record = Review.search([("scope_key", "=", nested["scope_key"])])
        partner.unlink()
        self.assertFalse(nested_record.exists())
        plain_record = Review.search([("scope_key", "=", plain["scope_key"])])
        self.assertTrue(plain_record)
        account.unlink()
        self.assertFalse(plain_record.exists())

    def test_review_does_not_bypass_native_posted_account_deletion_refusal(self):
        from odoo.exceptions import UserError
        move = self._post(100)
        domain, path = self._scope()
        Review = self.env["account.reconciliation.review"]
        marked = self._mark(Review, domain, path, 0)
        record = Review.search([("scope_key", "=", marked["scope_key"])])
        with self.assertRaisesRegex(UserError, "contains journal items"):
            self.company_data["default_account_receivable"].unlink()
        self.assertTrue(record.exists())
        self.assertTrue(move.exists())
        self.assertEqual(move.state, "posted")

    def test_review_scope_identity_survives_existing_agent_payload_sanitization(self):
        if "usl.agent" not in self.env:
            self.skipTest("Existing delegated Agent payload sanitizer is qualified in the full product registry.")
        from odoo.addons.usl_access_control.models.agent_secrets import sanitize_agent_payload
        self._post(100)
        domain, path = self._scope()
        status = self.env["account.reconciliation.review"].get_review_status(domain, [path])[0]
        self.assertIn("scope_key", status)
        self.assertEqual(sanitize_agent_payload(status, model_name="account.reconciliation.review"), status)

    def test_native_delegated_readers_share_the_owners_effective_scope(self):
        if "usl.agent" not in self.env:
            self.skipTest("Existing delegated application authority is qualified in the full product registry.")
        owner = self._reviewer("agent-owner")
        self._post(100)
        domain, path = self._scope()
        Review = self.env["account.reconciliation.review"]
        human = Review.with_user(owner).get_review_status(domain, [path])[0]
        for mode in ("read_write", "read_only"):
            agent = self.env["usl.agent"].with_user(owner).create({
                "name": "[QA review] " + mode, "purpose": "[QA review] Native scope equality",
                "owner_id": owner.id, "company_id": owner.company_id.id,
                "company_ids": [Command.set(owner.company_ids.ids)],
                "delegated_group_ids": [Command.set(self.env.ref("account.group_account_user").ids)],
                "access_mode": mode,
            })
            visible = Review.with_user(agent.user_id).get_review_status(domain, [path])[0]
            self.assertEqual(visible["scope_key"], human["scope_key"])
            self.assertEqual(visible["amounts"], human["amounts"])
            self.assertEqual(visible["can_review"], mode == "read_write")

    def test_genuinely_different_native_visible_queries_do_not_share_a_mark(self):
        limited, other = self._reviewer("limited-scope"), self._reviewer("full-scope")
        self._post(100, partner=self.partner_a)
        self._post(20, partner=self.partner_b)
        self.env["ir.rule"].create({
            "name": "[QA review] Synthetic native visibility difference",
            "model_id": self.env["ir.model"]._get_id("account.move.line"),
            "domain_force": f"[('partner_id', '!=', {self.partner_b.id})] if user.id == {limited.id} else []",
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        domain, path = self._scope()
        Review = self.env["account.reconciliation.review"]
        reviewed = self._mark(Review.with_user(limited), domain, path, 0)
        visible = Review.with_user(other).get_review_status(domain, [path])[0]
        self.assertEqual(reviewed["amounts"][0]["current"], 100)
        self.assertEqual(visible["amounts"][0]["current"], 120)
        self.assertNotEqual(reviewed["scope_key"], visible["scope_key"])
        self.assertEqual(visible["state"], "unreviewed")

    def _metadata_reviewer(self, suffix, *, create, write):
        reviewer = self._reviewer(suffix, readonly=True)
        role = self.env["res.groups"].create({"name": "[QA review] " + suffix})
        reviewer.group_ids = [Command.link(role.id)]
        self.env["ir.model.access"].create({
            "name": "[QA review] Native metadata grant " + suffix,
            "model_id": self.env["ir.model"]._get_id("account.reconciliation.review"),
            "group_id": role.id, "perm_read": True, "perm_create": create,
            "perm_write": write, "perm_unlink": False,
        })
        # Apply the fixture grant across the native ACL/group cache boundary.
        self.env.flush_all()
        self.env.registry.clear_cache("stable")
        self.assertIn(role, reviewer.all_group_ids)
        return reviewer

    def test_native_metadata_writer_needs_journal_read_not_journal_write(self):
        reviewer = self._metadata_reviewer("metadata-only", create=True, write=True)
        self._post(100)
        domain, path = self._scope()
        self.assertTrue(self.env["account.reconciliation.review"].with_user(reviewer).has_access("create"))
        self.assertTrue(self.env["account.move.line"].with_user(reviewer).has_access("read"))
        self.assertFalse(self.env["account.move.line"].with_user(reviewer).has_access("write"))
        status = self._mark(self.env["account.reconciliation.review"].with_user(reviewer), domain, path, 0)
        self.assertEqual(status["state"], "reviewed")
        self.assertEqual(status["amounts"][0]["baseline"], 100)
        self.assertEqual(status["reviewer"], reviewer.display_name)

    def test_native_metadata_write_only_reviewer_can_rereview_without_create(self):
        from odoo.exceptions import AccessError
        self._post(100)
        domain, path = self._scope()
        Review = self.env["account.reconciliation.review"]
        first = self._mark(Review, domain, path, 0)
        reviewer = self._metadata_reviewer("metadata-write-only", create=False, write=True)
        api = Review.with_user(reviewer)
        self.assertFalse(api.has_access("create"))
        self.assertTrue(api.get_review_status(domain, [path])[0]["can_review"])
        self.assertFalse(self.env["account.move.line"].with_user(reviewer).has_access("write"))
        self._post(25)
        updated = self._mark(api, domain, path, first["revision"])
        self.assertEqual(updated["state"], "reviewed")
        self.assertEqual(updated["amounts"][0]["baseline"], 125)
        self.assertEqual(updated["reviewer"], reviewer.display_name)
        self._post(5)
        acknowledged = self._dismiss(api, domain, path, updated["revision"])
        self.assertEqual(acknowledged["state"], "acknowledged")
        self.assertEqual(acknowledged["amounts"][0]["baseline"], 125)
        other_domain = domain + [("name", "not ilike", "[QA review] Separate scope")]
        self.assertFalse(api.get_review_status(other_domain, [path])[0]["can_review"])
        with self.assertRaises(AccessError):
            self._mark(api, other_domain, path, 0)
        self.assertEqual(api.get_review_status(other_domain, [path])[0]["state"], "unreviewed")

    def test_native_metadata_create_only_reviewer_cannot_replace_existing_baseline(self):
        from odoo.exceptions import AccessError
        reviewer = self._metadata_reviewer("metadata-create-only", create=True, write=False)
        self._post(100)
        domain, path = self._scope()
        api = self.env["account.reconciliation.review"].with_user(reviewer)
        self.assertTrue(api.get_review_status(domain, [path])[0]["can_review"])
        first = self._mark(api, domain, path, 0)
        self.assertFalse(first["can_review"])
        self._post(25)
        with self.assertRaises(AccessError):
            self._mark(api, domain, path, first["revision"])
        with self.assertRaises(AccessError):
            self._dismiss(api, domain, path, first["revision"])
        current = api.get_review_status(domain, [path])[0]
        self.assertEqual(current["state"], "changed")
        self.assertEqual(current["revision"], first["revision"])
        self.assertEqual(current["amounts"][0]["baseline"], 100)
        self.assertFalse(current["acknowledged_at"])
