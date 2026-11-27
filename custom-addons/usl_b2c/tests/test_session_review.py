"""Monthly review uses existing native evidence, never new financial totals."""
from datetime import datetime
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install", "b2c_session_review")
class TestB2cSessionReview(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({
            "name": "[QA] Another review company", "currency_id": cls.company.currency_id.id,
        })
        cls.reader = new_test_user(cls.env, login="qa.session.review.reader", groups="base.group_user,usl_b2c.group_b2c_reader")
        cls.both_reader = new_test_user(cls.env, login="qa.session.review.both", groups="base.group_user,base.group_multi_company,usl_b2c.group_b2c_reader",
                                       company_id=cls.company.id, company_ids=[Command.set((cls.company | cls.other_company).ids)])
        cls.outsider = new_test_user(cls.env, login="qa.session.review.outsider", groups="base.group_user")
        plan = cls.env["account.analytic.plan"].search([("name", "=", "Channel")], limit=1)
        if not plan:
            plan = cls.env["account.analytic.plan"].create({"name": "Channel"})
        account = cls.env["account.analytic.account"].create({
            "name": "[QA] Review channel", "plan_id": plan.id, "company_id": cls.company.id,
        })
        cls.channel = cls.env["b2c.channel"].create({
            "name": "[QA] Review workshop", "code": "qa_review", "company_id": cls.company.id,
            "analytic_account_id": account.id,
        })
        cls.Session = cls.env["b2c.accounting.session"]
        cls.gaps = cls.Session.browse()
        for month, kind in enumerate(("link", "mapping", "conversion") * 2, 1):
            pending = kind == "conversion"
            currency = cls.env.ref("base.EUR") if cls.company.currency_id != cls.env.ref("base.EUR") else cls.env.ref("base.USD")
            order = cls.env["b2c.order"].create({
                "name": "[QA] Review example %s" % month,
                "canonical_key": "qa:session-review:%s" % month,
                "external_order_id": "qa-session-review-%s" % month,
                "company_id": cls.company.id, "channel_id": cls.channel.id,
                "source_provider": "manual", "origin": "manual", "state": "fulfilled",
                "order_date": datetime(2026, month, 3, 10),
                "currency_id": currency.id if pending else cls.company.currency_id.id,
                "revenue_amount": 17, "total_amount": 17, "net_amount": 17,
                "revenue_company_amount": 0 if pending else 17,
                "total_company_amount": 0 if pending else 17,
                "net_company_amount": 0 if pending else 17,
                "conversion_state": "pending" if pending else "not_needed",
                "amount_completeness": "unknown" if pending else "complete",
                "accounting_link_state": "pending" if kind == "link" else "not_applicable",
            })
            if kind == "mapping":
                cls.env["b2c.order.line"].create({
                    "order_id": order.id, "line_key": "qa:session-review:line:%s" % month,
                    "original_name": "[QA] Unmapped workshop item", "quantity": 1,
                    "revenue_amount": 17, "revenue_company_amount": 17,
                    "mapping_state": "pending", "amount_completeness": "complete",
                })
            session = cls.Session.create({
                "company_id": cls.company.id, "channel_id": cls.channel.id,
                "period_start": "2026-%02d-01" % month,
            })
            session.action_refresh()
            cls.gaps |= session
        cls.unrefreshed = cls.Session.create({"company_id": cls.company.id, "channel_id": cls.channel.id, "period_start": "2026-07-01"})
        cls.ready = cls.Session.create({"company_id": cls.company.id, "channel_id": cls.channel.id, "period_start": "2026-08-01"})
        cls.ready.action_refresh()
        cls.reviewed = cls.Session.create({"company_id": cls.company.id, "channel_id": cls.channel.id, "period_start": "2026-09-01"})
        cls.reviewed.action_refresh()
        cls.reviewed.action_mark_reviewed()
        cls.locked = cls.Session.create({"company_id": cls.company.id, "channel_id": cls.channel.id, "period_start": "2026-10-01"})
        cls.locked.action_refresh()
        cls.locked.action_mark_reviewed()
        cls.locked.action_lock()
        cls.foreign = cls.Session.create({"company_id": cls.other_company.id, "period_start": "2026-07-01"})

    def _filter(self, name):
        arch = self.env.ref("usl_b2c.view_b2c_accounting_session_search")._get_combined_arch()
        nodes = arch.xpath("//filter[@name='%s']" % name)
        self.assertTrue(nodes, "Monthly review must offer the %s filter" % name)
        return safe_eval(nodes[0].get("domain"))

    def _matching(self, name):
        # Keep acceptance examples isolated even on a populated QA registry.
        records = self.gaps | self.unrefreshed | self.ready | self.reviewed | self.locked
        return self.Session.with_user(self.reader).search([("id", "in", records.ids), *self._filter(name)])

    def test_review_entry_keeps_native_form_and_complete_financial_history(self):
        action = self.env.ref("usl_b2c.action_b2c_session_review").read()[0]
        self.assertEqual(action["res_model"], "b2c.accounting.session")
        self.assertEqual(action["search_view_id"][0], self.env.ref("usl_b2c.view_b2c_accounting_session_search").id)
        self.assertTrue(safe_eval(action["context"])["search_default_blocked_coverage"])
        old = self.env.ref("usl_b2c.action_b2c_accounting_session")
        self.assertEqual(old.res_model, action["res_model"])
        reader = self.Session.with_user(self.reader)
        rows = reader.search(safe_eval(old.domain or "[]"))
        self.assertTrue((self.reviewed | self.locked) <= rows)
        full_view = self.env.ref("usl_b2c.view_b2c_accounting_session_list")
        self.assertEqual(reader.get_view(view_type="list")["id"], full_view.id)
        self.assertEqual(action["view_id"][0], self.env.ref("usl_b2c.view_b2c_session_review_list").id)
        financial = full_view._get_combined_arch()
        self.assertTrue(financial.xpath("//field[@name='revenue_company_amount']"))
        self.assertTrue(financial.xpath("//field[@name='gross_margin_company_amount']"))
        for name in ("refund_company_amount", "fee_company_amount", "cogs_company_amount", "line_revenue_coverage_percent"):
            self.assertTrue(financial.xpath("//field[@name='%s']" % name))
        compact = self.env.ref("usl_b2c.view_b2c_session_review_list")._get_combined_arch()
        for name in ("name", "review_status", "review_next_step", "pending_link_count", "pending_mapping_count", "pending_conversion_count"):
            self.assertTrue(compact.xpath("//field[@name='%s']" % name))

    def test_filters_partition_native_evidence_without_approving_sessions(self):
        self.assertEqual(set(self._matching("blocked_coverage").ids), set(self.gaps.ids))
        self.assertEqual(self._matching("needs_refresh"), self.unrefreshed)
        self.assertEqual(self._matching("ready_for_review"), self.ready)
        arch = self.env.ref("usl_b2c.view_b2c_accounting_session_search")._get_combined_arch()
        for name in ("needs_refresh", "ready_for_review"):
            self.assertEqual(arch.xpath("//filter[@name='%s']" % name)[0].getprevious().tag, "separator")
        self.assertFalse(self.Session.search(self._filter("blocked_coverage") + self._filter("ready_for_review")))
        self.assertTrue(all(s.state == "draft" for s in self.gaps | self.unrefreshed | self.ready))
        self.assertEqual([(s.pending_link_count, s.pending_mapping_count, s.pending_conversion_count) for s in self.gaps],
                         [(1, 0, 0), (0, 1, 0), (0, 0, 1)] * 2)
        self.gaps[0].action_mark_reviewed()
        self.assertIn(self.gaps[0], self._matching("blocked_coverage"))
        self.assertNotIn(self.gaps[0], self._matching("ready_for_review"))

    def test_company_scope_and_native_reader_permissions_remain_authoritative(self):
        model = self.Session.with_user(self.both_reader)
        own = self.gaps | self.unrefreshed | self.ready | self.reviewed | self.locked | self.foreign
        domain = [("id", "in", own.ids)]
        a = model.with_context(allowed_company_ids=self.company.ids).search(domain)
        b = model.with_context(allowed_company_ids=self.other_company.ids).search(domain)
        both = model.with_context(allowed_company_ids=(self.company | self.other_company).ids).search(domain)
        self.assertNotIn(self.foreign, a)
        self.assertIn(self.foreign, b)
        self.assertEqual(set(a.ids) | set(b.ids), set(both.ids))
        self.assertFalse(self.Session.with_user(self.outsider).has_access("read"))
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.ready.with_user(self.reader).action_mark_reviewed()
        self.assertEqual(self.ready.state, "draft")

    def test_reading_status_and_next_step_does_not_refresh_or_write(self):
        fields = ["state", "refreshed_at", "write_date", "revenue_company_amount", "pending_link_count", "pending_mapping_count", "pending_conversion_count"]
        records = self.gaps | self.unrefreshed | self.ready | self.reviewed | self.locked
        before = records.read(fields)
        reader = self.Session.with_user(self.reader)
        with patch.object(type(self.Session), "action_refresh", side_effect=AssertionError("Review read refreshed data")), \
             patch.object(type(self.Session), "write", side_effect=AssertionError("Review read wrote a session")), \
             patch.object(type(self.env["account.move"]), "_write", side_effect=AssertionError("Review read wrote Accounting")):
            rows = reader.search_read([("id", "in", records.ids)], ["name", "review_status", "review_next_step", *fields])
            self.assertEqual(len(rows), len(records))
        self.assertEqual(records.read(fields), before)

    def test_french_review_copy_loads_through_native_translation_import(self):
        from lxml import etree
        self.env["res.lang"]._activate_lang("fr_FR")
        self.env["ir.module.module"]._load_module_terms(["usl_b2c"], ["fr_FR"], overwrite=True)
        model = self.Session.with_user(self.reader).with_context(lang="fr_FR")
        result = model.get_view(self.env.ref("usl_b2c.view_b2c_accounting_session_search").id, "search")
        arch = etree.fromstring(result["arch"])
        for name, label in (("awaiting_review", "Brouillons actualisés"), ("blocked_coverage", "Avec écarts"), ("needs_refresh", "À actualiser"), ("ready_for_review", "Prêt pour vérification")):
            self.assertEqual(arch.xpath("//filter[@name='%s']" % name)[0].get("string"), label)
        action = self.env.ref("usl_b2c.action_b2c_session_review").with_context(lang="fr_FR")
        self.assertEqual(action.name, "Revue mensuelle")
        self.assertIn("filtres sélectionnés", action.help)


    def test_managed_readers_use_native_company_scope(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent parity runs in the complete Distribution registry")
        group = self.env.ref("usl_b2c.group_b2c_reader")
        for mode in ("read_write", "read_only"):
            agent = self.env["usl.agent"].with_user(self.reader).create({
                "name": "[QA] Monthly review reader", "purpose": "Read synthetic monthly review evidence",
                "company_id": self.company.id, "company_ids": [Command.set(self.company.ids)],
                "delegated_group_ids": [Command.set(group.ids)],
                "read_only_group_ids": [Command.set(group.ids)] if mode == "read_only" else [Command.clear()],
                "access_mode": mode,
            })
            model = self.Session.with_user(agent.user_id).with_context(allowed_company_ids=self.company.ids)
            rows = model.search([("id", "in", (self.gaps | self.foreign).ids), *self._filter("blocked_coverage")])
            self.assertEqual(set(rows.ids), set(self.gaps.ids))
            self.assertTrue(all(row["review_status"] == "1 open gap" for row in rows.read(["review_status", "review_next_step"])))
            with self.assertRaises(AccessError), self.cr.savepoint():
                self.ready.with_user(agent.user_id).action_mark_reviewed()
            self.assertEqual(self.ready.state, "draft")
