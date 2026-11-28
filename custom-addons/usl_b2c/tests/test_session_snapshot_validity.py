"""A monthly snapshot belongs to the scope that was actually refreshed."""
from datetime import date, datetime
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "b2c_snapshot_validity")
class TestSessionSnapshotValidity(TransactionCase):
    METRICS = (
        "order_count", "units_sold", "revenue_company_amount", "refund_company_amount",
        "fee_company_amount", "cogs_company_amount", "gross_margin_company_amount",
        "unallocated_revenue_company_amount", "unknown_amount_company_amount",
        "line_revenue_coverage_percent", "accounting_link_coverage_percent",
        "direct_accounting_link_coverage_percent", "direct_accounting_link_count",
        "aggregate_covered_count", "not_applicable_link_count", "pending_mapping_count",
        "pending_link_count", "pending_conversion_count",
    )
    SCOPE = ("company_id", "channel_id", "source_provider", "period_start", "period_end")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({
            "name": "[QA] Scope workshop Europe", "currency_id": cls.env.ref("base.EUR").id,
        })
        cls.operator = new_test_user(
            cls.env, login="qa.snapshot.operator",
            groups="base.group_user,base.group_multi_company,usl_b2c.group_b2c_operator",
            company_id=cls.company.id, company_ids=[Command.set((cls.company | cls.other_company).ids)],
        )
        cls.reader = new_test_user(
            cls.env, login="qa.snapshot.reader", groups="base.group_user,usl_b2c.group_b2c_reader",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        plan = cls.env["account.analytic.plan"].search([("name", "=", "Channel")], limit=1)
        if not plan:
            plan = cls.env["account.analytic.plan"].create({"name": "Channel"})
        cls.channel = cls.env["b2c.channel"].create({
            "name": "[QA] Snapshot workshop", "code": "qa_snapshot", "company_id": cls.company.id,
            "analytic_account_id": cls.env["account.analytic.account"].create({
                "name": "[QA] Snapshot workshop", "plan_id": plan.id, "company_id": cls.company.id,
            }).id,
        })
        for month, amount, disposition in ((11, 17, "not_applicable"), (12, 29, "pending")):
            cls.env["b2c.order"].create({
                "name": "[QA] Scope order %s" % month, "canonical_key": "qa:snapshot:%s" % month,
                "company_id": cls.company.id, "channel_id": cls.channel.id,
                "source_provider": "manual", "origin": "manual", "state": "fulfilled",
                "order_date": datetime(2026, month, 3, 10), "currency_id": cls.company.currency_id.id,
                "revenue_amount": amount, "revenue_company_amount": amount,
                "total_amount": amount, "total_company_amount": amount,
                "net_amount": amount, "net_company_amount": amount,
                "amount_completeness": "complete", "conversion_state": "not_needed",
                "accounting_link_state": disposition,
            })

    def _session(self, **values):
        session = self.env["b2c.accounting.session"].create({
            "company_id": self.company.id, "channel_id": self.channel.id,
            "period_start": "2026-11-01", **values,
        }).with_user(self.operator).with_context(allowed_company_ids=(self.company | self.other_company).ids)
        session.action_refresh()
        self.assertEqual(session.revenue_company_amount, 17 if session.period_start.month == 11 else 29)
        return session

    def _snapshot(self, session):
        session = session.with_env(self.env)
        session.invalidate_recordset()
        return session.read([*self.SCOPE, *self.METRICS, "state", "refreshed_at", "reviewed_by_id", "reviewed_at"])

    def _assert_invalid(self, session):
        self.assertFalse(session.refreshed_at, "A different scope has not been refreshed")
        self.assertEqual(session.state, "draft")
        self.assertFalse(session.reviewed_by_id)
        self.assertFalse(session.reviewed_at)
        self.assertEqual(session.review_status, "Not refreshed")
        for name in self.METRICS:
            self.assertEqual(session[name], 0, name)
        self.assertFalse(session.search_count([("id", "=", session.id), ("refreshed_at", "!=", False)]))

    def test_period_edit_invalidates_metrics_and_requires_native_refresh_before_review(self):
        session = self._session()
        session.write({"period_start": "2026-12-01"})
        self._assert_invalid(session)
        with self.assertRaises(UserError):
            session.action_mark_reviewed()
        session.action_refresh()
        self.assertEqual(session.revenue_company_amount, 29)
        self.assertEqual(session.pending_link_count, 1)
        self.assertEqual(session.state, "draft")
        session.action_mark_reviewed()
        self.assertEqual(session.state, "reviewed")

    def test_channel_provider_and_end_date_changes_invalidate_the_same_snapshot(self):
        for values in ({"channel_id": False}, {"source_provider": "medusa"}, {"period_end": "2026-11-29"}):
            session = self._session()
            session.write(values)
            self._assert_invalid(session)
            session.with_env(self.env).unlink()

    def test_company_change_never_relabels_old_currency_amounts(self):
        session = self._session()
        session.write({"company_id": self.other_company.id, "channel_id": False})
        self._assert_invalid(session)
        self.assertEqual(session.company_currency_id, self.other_company.currency_id)
        self.assertEqual(self.env["b2c.order"].search_count([
            ("company_id", "=", self.company.id), ("channel_id", "=", self.channel.id),
        ]), 2)

    def test_company_move_keeps_linked_evidence_and_does_not_partially_write(self):
        linked = self._session()
        unlinked = self._session(period_start="2026-12-01")
        document = self.env["ir.attachment"].create({
            "name": "Synthetic statement evidence.txt",
            "raw": b"Synthetic statement evidence",
            "company_id": self.company.id,
            "res_model": linked._name,
            "res_id": linked.id,
        })
        evidence = self.env["b2c.accounting.link"].create({
            "name": "Synthetic supporting evidence",
            "company_id": self.company.id,
            "session_id": linked.id,
            "attachment_id": document.id,
            "link_type": "supporting",
            "link_state": "verified",
        })
        before = self._snapshot(linked | unlinked)
        with self.assertRaisesRegex(UserError, "linked accounting evidence"):
            (unlinked | linked).write({"company_id": self.other_company.id, "channel_id": False})
        self.assertEqual(self._snapshot(linked | unlinked), before)
        self.assertEqual(evidence.company_id, self.company)
        self.assertEqual(evidence.session_id.id, linked.id)
        self.assertEqual(bytes(evidence.attachment_id.raw), b"Synthetic statement evidence")

        linked.write({"company_id": self.company.id, "review_note": "[QA] Verified evidence"})
        self.assertEqual(self._snapshot(linked | unlinked), before)
        unlinked.write({"company_id": self.other_company.id, "channel_id": False})
        self._assert_invalid(unlinked)

    def test_delegated_operator_can_link_evidence_and_keeps_the_same_company_rule(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent parity runs in the complete Distribution registry")
        operator_group = self.env.ref("usl_b2c.group_b2c_operator")
        companies = self.company | self.other_company
        agent = self.env["usl.agent"].with_user(self.operator).with_context(
            allowed_company_ids=companies.ids,
        ).create({
            "name": "[QA] Evidence collaborator", "purpose": "Check monthly supporting documents",
            "company_id": self.company.id, "company_ids": [Command.set(companies.ids)],
            "delegated_group_ids": [Command.set(operator_group.ids)], "access_mode": "read_write",
        })
        session = self._session().with_user(agent.user_id)
        document = self.env["ir.attachment"].create({
            "name": "Synthetic statement.txt", "raw": b"Synthetic statement",
            "company_id": self.company.id, "res_model": session._name, "res_id": session.id,
        })
        evidence = session.env["b2c.accounting.link"].create({
            "name": "Synthetic supporting document", "link_type": "supporting",
            "session_id": session.id, "attachment_id": document.id, "company_id": self.company.id,
        })
        self.assertEqual(evidence.create_uid.id, agent.user_id.id)
        self.assertEqual(session.write_uid.id, agent.user_id.id)
        with self.assertRaisesRegex(UserError, "linked accounting evidence"):
            session.write({"company_id": self.other_company.id, "channel_id": False})
        self.assertEqual(session.company_id, self.company)

    def test_noop_scope_and_unrelated_note_preserve_valid_snapshot(self):
        session = self._session()
        before = self._snapshot(session)
        session.write({
            "company_id": self.company.id, "channel_id": self.channel.id, "source_provider": False,
            "period_start": "2026-11-01", "period_end": "2026-11-30",
        })
        session.write({"company_id": self.company, "channel_id": self.channel,
                       "period_start": date(2026, 11, 1), "period_end": date(2026, 11, 30)})
        session.write({"review_note": "[QA] Evidence checked separately"})
        self.assertEqual(self._snapshot(session), before)

    def test_bulk_write_preserves_records_whose_scope_did_not_change(self):
        changed = self._session()
        unchanged = self._session(period_start="2026-12-01", source_provider="manual")
        before = self._snapshot(unchanged)
        (changed | unchanged).write({"source_provider": "manual"})
        self._assert_invalid(changed)
        self.assertEqual(self._snapshot(unchanged), before)

    def test_onchange_and_cancel_do_not_write_and_returning_to_original_scope_restores_preview(self):
        session = self._session()
        before = self._snapshot(session)
        form = Form(session, view="usl_b2c.view_b2c_accounting_session_form")
        with patch.object(type(session), "_write", side_effect=AssertionError("Preview wrote a session")):
            form.period_start = date(2026, 12, 1)
            self.assertFalse(form.refreshed_at)
            self.assertIn("Not refreshed", form.review_next_step)
            self.assertEqual(form.revenue_company_amount, 0)
            form.period_start = date(2026, 11, 1)
            self.assertTrue(form.refreshed_at)
            self.assertEqual(form.revenue_company_amount, 17)
        self.assertEqual(self._snapshot(session), before)

    def test_reader_and_invalid_scope_changes_preserve_original_snapshot(self):
        session = self._session()
        before = self._snapshot(session)
        with self.assertRaises(AccessError):
            session.with_user(self.reader).write({"period_start": "2026-12-01"})
        self.assertEqual(self._snapshot(session), before)
        with self.assertRaises(ValidationError):
            session.write({"period_start": "2026-12-14"})
        self.assertEqual(self._snapshot(session), before)

    def test_reviewed_scope_change_reopens_review_but_locked_session_stays_immutable(self):
        session = self._session()
        session.action_mark_reviewed()
        session.write({"period_start": "2026-12-01"})
        self._assert_invalid(session)
        session.action_refresh()
        session.action_mark_reviewed()
        session.action_lock()
        before = self._snapshot(session)
        with self.assertRaises(UserError):
            session.write({"period_start": "2027-01-01"})
        self.assertEqual(self._snapshot(session), before)

    def test_unreadable_target_company_cannot_receive_the_snapshot(self):
        only_here = new_test_user(
            self.env, login="qa.snapshot.single.company", groups="base.group_user,usl_b2c.group_b2c_operator",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
        )
        session = self._session()
        before = self._snapshot(session)
        with self.assertRaises(AccessError):
            session.with_user(only_here).with_context(allowed_company_ids=self.company.ids).write({
                "company_id": self.other_company.id, "channel_id": False,
            })
        self.assertEqual(self._snapshot(session), before)


    def test_managed_scope_write_keeps_native_owner_and_readonly_boundaries(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent parity runs in the complete Distribution registry")
        group = self.env.ref("usl_b2c.group_b2c_operator")
        for mode in ("read_write", "read_only"):
            agent = self.env["usl.agent"].with_user(self.operator).create({
                "name": "[QA] Snapshot collaborator", "purpose": "Check monthly scope validity",
                "company_id": self.company.id, "company_ids": [Command.set(self.company.ids)],
                "delegated_group_ids": [Command.set(group.ids)],
                "read_only_group_ids": [Command.set(group.ids)] if mode == "read_only" else [Command.clear()],
                "access_mode": mode,
            })
            session = self._session()
            actor = session.with_user(agent.user_id).with_context(allowed_company_ids=self.company.ids)
            before = self._snapshot(session)
            if mode == "read_only":
                with self.assertRaises(AccessError):
                    actor.write({"period_start": "2026-12-01"})
                self.assertEqual(self._snapshot(session), before)
            else:
                actor.write({"channel_id": False})
                self._assert_invalid(session)
                denied = self._snapshot(session)
                with self.assertRaises(AccessError):
                    actor.write({"company_id": self.other_company.id})
                self.assertEqual(self._snapshot(session), denied)
