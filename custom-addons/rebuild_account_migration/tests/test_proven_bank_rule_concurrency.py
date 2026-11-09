"""Real RR cursors; run only in the dedicated disposable race database.

These tests intentionally commit synthetic financial fixtures. The maintained
addon-test helper owns/removes that database; no existing QA evidence is reset.
"""
import threading
import unittest
import uuid
from unittest.mock import patch

import psycopg2

from odoo import Command, SUPERUSER_ID, api, fields
from odoo.exceptions import UserError
from odoo.http.retrying import retrying
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user, release_test_lock


@tagged("post_install", "-at_install", "rebuild_proven_rule_races")
class TestProvenBankRuleConcurrency(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env.cr.dbname.startswith("odoo_proven_rule_races_"):
            raise unittest.SkipTest("Committed race fixtures require the owned disposable odoo_proven_rule_races_* database")

    def _seed(self, *, remove_seed=False):
        # Real cursors deliberately bypass the test framework's shared
        # TestCursor. Environments never cross thread/cursor boundaries.
        with self.registry._db.cursor() as cr:
            cr.execute("SET LOCAL lock_timeout = '12s'")
            cr.execute("SET LOCAL statement_timeout = '25s'")
            env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True, "no_reset_password": True})
            company = env["res.company"].create({"name": "[QA] Rule concurrency " + uuid.uuid4().hex})
            env = env(context=dict(env.context, allowed_company_ids=company.ids))
            accounts = {}
            for code, kind in (("981001", "asset_cash"), ("981002", "asset_current"),
                               ("981003", "expense"), ("981004", "expense")):
                accounts[code] = env["account.account"].create({
                    "name": "[QA] Rule race " + code, "code": code,
                    "account_type": kind, "company_ids": [Command.set(company.ids)],
                })
            journal = env["account.journal"].create({
                "name": "[QA] Rule race bank", "code": "QARR", "type": "bank", "company_id": company.id,
                "default_account_id": accounts["981001"].id, "suspense_account_id": accounts["981002"].id,
            })
            manager = new_test_user(env, login="qa.rule.race.%s" % company.id,
                groups="account.group_account_manager", company_id=company.id,
                company_ids=[Command.set(company.ids)])
            statements = env["account.bank.statement.line"]
            for _index in range(3):
                statement = env["account.bank.statement.line"].create({
                    "journal_id": journal.id, "date": fields.Date.today(),
                    "payment_ref": "[QA] Proven race service", "amount": -36,
                })
                _liquidity, suspense, _other = statement._seek_for_lines()
                suspense.write({"account_id": accounts["981003"].id})
                self.assertTrue(statement.is_reconciled)
                statements |= statement
            model = env["account.reconcile.model"].with_user(manager)
            model.action_rebuild_analyze_rule_opportunities()
            proposal = model.search([("rebuild_is_proposal", "=", True)])
            self.assertEqual(len(proposal), 1)
            if remove_seed:
                cr.execute("DELETE FROM rebuild_reconcile_rule_coordination WHERE company_id = %s", [company.id])
            fixture = {"company": company.id, "manager": manager.id, "proposal": proposal.id,
                "journal": journal.id, "expense": accounts["981003"].id,
                "other": accounts["981004"].id, "statements": statements.ids,
                "lines": statements.move_id.line_ids.ids}
            cr.commit()
        return fixture

    def _env(self, cr, fixture):
        cr.execute("SET LOCAL lock_timeout = '12s'")
        cr.execute("SET LOCAL statement_timeout = '25s'")
        return api.Environment(cr, fixture["manager"], {"allowed_company_ids": [fixture["company"]]})

    def _manual(self, env, fixture, *, overlaps=True):
        return env["account.reconcile.model"].create({
            "name": "[QA] Concurrent manual rule", "company_id": fixture["company"],
            "match_journal_ids": [Command.set([fixture["journal"]])],
            "match_label": "contains", "match_label_param": "[QA] Proven race" if overlaps else "[QA] Separate native purpose",
            "line_ids": [Command.create({"account_id": fixture["other"], "amount_type": "percentage", "amount_string": "100"})],
        })

    def _race(self, *, native_retry=False, first_seed=False, rollback_writer=False, overlaps=True, writer_kind="create"):
        fixture = self._seed(remove_seed=first_seed)
        if writer_kind != "create":
            with self.registry._db.cursor() as cr:
                env = self._env(cr, fixture)
                writer = self._manual(env, fixture, overlaps=writer_kind == "archive")
                fixture["writer"] = writer.id
                if writer_kind == "line_reparent":
                    fixture["destination"] = self._manual(env, fixture, overlaps=False).id
                cr.commit()
        prepared, proceed = threading.Event(), threading.Event()
        result, calls = {}, []

        def contender():
            with self.registry._db.cursor() as cr:
                env = self._env(cr, fixture)

                def attempt():
                    calls.append(1)
                    rule = env["account.reconcile.model"].browse(fixture["proposal"])
                    # Establish a real RR snapshot before A changes the graph.
                    self.assertTrue(rule.rebuild_is_proposal)
                    if len(calls) == 1:
                        prepared.set()
                        if not proceed.wait(20):
                            raise AssertionError("Writer did not release contender")
                    rule.action_rebuild_approve_proposal()
                    return True

                try:
                    result["value"] = retrying(attempt, env) if native_retry else attempt()
                    if not native_retry:
                        cr.commit()
                except Exception as error:
                    result["error"] = error
                    cr.rollback()

        # The native test server holds Registry._lock while preloading tests.
        # Its maintained test-mode context releases that startup lock, while
        # _db.cursor still gives each worker a genuinely independent RR cursor.
        with self.enter_registry_test_mode(), release_test_lock():
            thread = threading.Thread(target=contender, daemon=True)
            thread.start()
            try:
                self.assertTrue(prepared.wait(20), "Contender did not establish its snapshot")
                with self.registry._db.cursor() as cr:
                    env = self._env(cr, fixture)
                    if writer_kind == "create":
                        writer = self._manual(env, fixture, overlaps=overlaps)
                    else:
                        writer = env["account.reconcile.model"].browse(fixture["writer"])
                        if writer_kind == "write":
                            writer.write({"match_label_param": "[QA] Proven race"})
                        elif writer_kind == "archive":
                            writer.write({"active": False})
                        elif writer_kind == "line_create":
                            env["account.reconcile.model.line"].create({"model_id": writer.id,
                                "account_id": fixture["other"], "amount_type": "percentage", "amount_string": "10"})
                        elif writer_kind == "line_write":
                            writer.line_ids.write({"amount_string": "75"})
                        elif writer_kind == "line_unlink":
                            writer.line_ids.unlink()
                        elif writer_kind == "line_reparent":
                            writer.line_ids.write({"model_id": fixture["destination"]})
                        elif writer_kind == "nested_line_write":
                            writer.write({"line_ids": [Command.update(writer.line_ids.id, {"amount_string": "75"})]})
                        else:
                            raise AssertionError("Unrecognized writer kind")
                    writer_id = writer.id
                    cr.rollback() if rollback_writer else cr.commit()
            finally:
                proceed.set()
                thread.join(30)
        self.assertFalse(thread.is_alive(), "Native race exceeded its bounded timeout")
        with self.registry._db.cursor() as cr:
            env = self._env(cr, fixture)
            proposal = env["account.reconcile.model"].browse(fixture["proposal"])
            if native_retry and not rollback_writer and overlaps:
                self.assertIsInstance(result.get("error"), UserError)
                self.assertIn("overlap", str(result["error"]).lower())
                self.assertEqual(len(calls), 2)
                self.assertTrue(proposal.rebuild_is_proposal)
                self.assertTrue(env["account.reconcile.model"].browse(writer_id).exists())
            elif rollback_writer or native_retry:
                self.assertNotIn("error", result)
                self.assertTrue(result.get("value"))
                self.assertFalse(proposal.rebuild_is_proposal)
                self.assertEqual(len(calls), 1 if rollback_writer else 2)
            else:
                self.assertIsInstance(result.get("error"), psycopg2.errors.SerializationFailure)
                self.assertEqual(result["error"].pgcode, "40001")
                self.assertTrue(proposal.rebuild_is_proposal)
            if writer_kind != "create":
                writer = env["account.reconcile.model"].browse(fixture["writer"])
                if writer_kind == "write":
                    self.assertEqual(writer.match_label_param, "[QA] Proven race")
                elif writer_kind == "archive":
                    self.assertFalse(writer.active)
                elif writer_kind == "line_create":
                    self.assertEqual(len(writer.line_ids), 2)
                    self.assertIn("10", writer.line_ids.mapped("amount_string"))
                elif writer_kind in ("line_write", "nested_line_write"):
                    self.assertEqual(writer.line_ids.amount_string, "75")
                elif writer_kind == "line_unlink":
                    self.assertFalse(writer.line_ids)
                elif writer_kind == "line_reparent":
                    self.assertFalse(writer.line_ids)
                    self.assertEqual(len(env["account.reconcile.model"].browse(fixture["destination"]).line_ids), 2)
            # Successful/manual conflicts do not reclassify the three sources.
            lines = env["account.move.line"].browse(fixture["lines"])
            self.assertEqual(len(lines), 6)
            self.assertEqual(len(lines.filtered(lambda line: line.account_id.id == fixture["expense"])), 3)
            self.assertEqual(sum(lines.mapped("balance")), 0)

    def test_old_snapshot_receives_real_40001_before_activation(self):
        self._race()

    def test_native_outer_retry_rechecks_and_refuses_committed_overlap(self):
        self._race(native_retry=True)

    def test_native_outer_retry_preserves_legitimate_disjoint_activation(self):
        self._race(native_retry=True, overlaps=False)

    def test_first_seed_race_also_receives_real_40001(self):
        self._race(first_seed=True)

    def test_rolled_back_first_seed_does_not_lose_the_proposal(self):
        self._race(first_seed=True, rollback_writer=True)

    def test_manual_rule_edit_is_rechecked_after_native_retry(self):
        self._race(native_retry=True, writer_kind="write")

    def test_archiving_conflict_allows_native_retry_to_activate(self):
        self._race(native_retry=True, writer_kind="archive", overlaps=False)

    def test_direct_line_create_retries_without_losing_legitimate_activation(self):
        self._race(native_retry=True, writer_kind="line_create", overlaps=False)

    def test_direct_line_write_retries_without_losing_legitimate_activation(self):
        self._race(native_retry=True, writer_kind="line_write", overlaps=False)

    def test_direct_line_unlink_retries_without_losing_legitimate_activation(self):
        self._race(native_retry=True, writer_kind="line_unlink", overlaps=False)

    def test_direct_line_reparent_retries_without_losing_legitimate_activation(self):
        self._race(native_retry=True, writer_kind="line_reparent", overlaps=False)

    def test_nested_line_write_retries_without_losing_legitimate_activation(self):
        self._race(native_retry=True, writer_kind="nested_line_write", overlaps=False)

    def test_financial_writer_race_is_retried_without_joining_rule_coordination(self):
        fixture = self._seed()
        prepared, proceed = threading.Event(), threading.Event()
        result, calls = {}, []
        model_type = type(self.env["account.reconcile.model"])
        original = model_type._rebuild_lock_evidence

        def delayed_lock(rule, statements):
            if threading.current_thread().name == "qa-financial-proof-contender" and len(calls) == 1:
                prepared.set()
                if not proceed.wait(20):
                    raise AssertionError("Financial writer did not release proof")
            return original(rule, statements)

        def contender():
            with self.registry._db.cursor() as cr:
                env = self._env(cr, fixture)
                def attempt():
                    calls.append(1)
                    return env["account.reconcile.model"].browse(fixture["proposal"]).action_rebuild_approve_proposal()
                try:
                    result["value"] = retrying(attempt, env)
                except Exception as error:
                    result["error"] = error
                    cr.rollback()

        with self.enter_registry_test_mode(), release_test_lock(), patch.object(model_type, "_rebuild_lock_evidence", delayed_lock):
            thread = threading.Thread(target=contender, name="qa-financial-proof-contender", daemon=True)
            thread.start()
            try:
                self.assertTrue(prepared.wait(20))
                with self.registry._db.cursor() as cr:
                    env = self._env(cr, fixture)
                    lines = env["account.move.line"].browse(fixture["lines"])
                    lines.filtered(lambda line: line.account_id.id == fixture["expense"])[:1].write({"account_id": fixture["other"]})
                    cr.commit()
            finally:
                proceed.set()
                thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(result.get("error"), UserError)
        with self.registry._db.cursor() as cr:
            env = self._env(cr, fixture)
            self.assertTrue(env["account.reconcile.model"].browse(fixture["proposal"]).rebuild_is_proposal)
