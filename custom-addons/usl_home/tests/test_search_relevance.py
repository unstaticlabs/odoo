"""Annotated synthetic relevance corpus, native authorization and paging."""

import json
from pathlib import Path
from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_search_relevance")
class TestSearchRelevance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env["res.company"].create({"name": "Carnet des ateliers"})
        cls.env = cls.env(context={**cls.env.context, "allowed_company_ids": company.ids})
        cls.actor = new_test_user(cls.env, login="qa-search-relevance", groups="project.group_project_user",
            company_id=company.id, company_ids=[Command.set(company.ids)])
        cls.workspace = cls.env["project.project"].with_context(tracking_disable=True).create({
            "name": "Carnet des visites", "company_id": cls.env.company.id,
        })
        cls.service = cls.env["usl.home.service"].with_user(cls.actor)
        cls.corpus = json.loads((Path(__file__).parent / "data/search_relevance.json").read_text())

    def _record(self, category, title, content=""):
        model, field = {
            "tasks": ("project.task", "description"),
            "projects": ("project.project", "description"),
            "contacts": ("res.partner", "comment"),
        }[category]
        values = {"name": title, field: content}
        if category == "tasks":
            values.update(project_id=self.workspace.id, user_ids=[Command.link(self.actor.id)])
        else:
            values["company_id"] = self.env.company.id
        return self.env[model].with_context(tracking_disable=True).create(values)

    def _index(self):
        self.env["project.task"]._cron_usl_index_task_content()
        for model in ("project.project", "res.partner"):
            self.env[model]._cron_usl_index_content()
        self.env["mail.message"]._cron_usl_index_task_messages()

    def _rows(self, query, category, **kwargs):
        group, = self.service.search_records(query, categories=[category], **kwargs)["groups"]
        return group

    def test_annotated_french_english_corpus(self):
        expected = {}
        for case in self.corpus["cases"]:
            if not case["expected_order"]:
                continue
            expected[case["id"]] = [self._record(case["category"], case["titles"][kind],
                case["mention"] if kind == "mention" else "").id for kind in case["expected_order"]]
        self._index()
        self.assertGreaterEqual(len(self.corpus["cases"]), 40)
        for case in self.corpus["cases"]:
            with self.subTest(search=case["id"], intent=case["intent"]):
                rows = self._rows(case["query"], case["category"], limit=4)["records"]
                self.assertEqual([row["id"] for row in rows], expected.get(case["id"], []))

    def test_equal_rank_is_stable_across_all_page_boundaries(self):
        for category in ("tasks", "projects", "contacts"):
            with self.subTest(category=category):
                query = "Visite opaline " + category
                records = [self._record(category, query) for _ in range(7)]
                mentions = [self._record(category, "Carnet", query) for _ in range(5)]
                self._index()
                expected = [record.id for record in reversed(records)] + [record.id for record in reversed(mentions)]
                full = self._rows(query, category, limit=25)
                self.assertEqual([row["id"] for row in full["records"]], expected)
                for size in (1, 3, 5):
                    ids = []
                    for offset in range(0, len(expected), size):
                        page = self._rows(query, category, limit=size, offset=offset)
                        ids.extend(row["id"] for row in page["records"])
                        self.assertEqual(page["has_more"], offset + size < len(expected))
                    self.assertEqual(ids, expected)
                self.assertFalse(self._rows(query, category, offset=len(expected))["records"])

    def test_pending_title_edit_changes_order_without_reindex(self):
        exact = self._record("tasks", "Atelier corail")
        mention = self._record("tasks", "Carnet", "Atelier corail")
        self._index()
        self.assertEqual([r["id"] for r in self._rows("Atelier corail", "tasks")["records"]], [exact.id, mention.id])
        exact.write({"name": "Carnet", "description": "Atelier corail"})
        mention.name = "Atelier corail"
        self.assertEqual([r["id"] for r in self._rows("Atelier corail", "tasks")["records"]], [mention.id, exact.id])

    def test_hidden_title_never_contributes_a_ranking_signal(self):
        first = self._record("tasks", "Atelier grenat", "Atelier grenat")
        newer = self._record("tasks", "Dossier", "Atelier grenat")
        self._index()
        with patch.object(self.env["project.task"]._fields["name"], "groups", "base.group_system"):
            # Inspect only ids: native display_name itself requires name access.
            Task = self.env["project.task"].with_user(self.actor)
            from odoo.fields import Domain
            records = self.service._search_ranked_records(Task, Domain("usl_search_text", "ilike", "Atelier grenat"), "Atelier grenat", 5, 0)
            self.assertEqual(records.ids, [newer.id, first.id])
            self.assertEqual([r["id"] for r in self._native_ranked("tasks", "Atelier grenat")], [newer.id, first.id])

    def test_native_record_and_message_revocation_precedes_ranking(self):
        exact = self._record("tasks", "Atelier nacre")
        mention = self._record("tasks", "Carnet")
        message = mention.with_user(self.actor).message_post(
            body=Markup("<p>Atelier nacre</p>"), subtype_xmlid="mail.mt_comment",
        )
        self._index()
        rows = self._rows("Atelier nacre", "tasks")["records"]
        self.assertEqual([r["id"] for r in rows], [exact.id, mention.id])
        self.assertEqual(rows[1]["source"]["id"], message.id)
        self.assertEqual([r["id"] for r in self._native_ranked("tasks", "Atelier nacre")], [exact.id, mention.id])
        message_rule = self.env["ir.rule"].create({
            "name": "Withdraw one readable source", "model_id": self.env["ir.model"]._get_id("mail.message"),
            "domain_force": repr([("id", "!=", message.id)]), "global": True,
        })
        self.assertEqual([r["id"] for r in self._rows("Atelier nacre", "tasks")["records"]], [exact.id])
        self.assertEqual([r["id"] for r in self._native_ranked("tasks", "Atelier nacre")], [exact.id])
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.task", mention.id, source_message_id=message.id, source_field="body")
        message_rule.unlink()
        self.env["ir.rule"].create({
            "name": "Withdraw exact title record", "model_id": self.env["ir.model"]._get_id("project.task"),
            "domain_force": repr([("id", "!=", exact.id)]), "global": True,
        })
        rows = self._rows("Atelier nacre", "tasks")["records"]
        self.assertEqual([r["id"] for r in rows], [mention.id])
        self.assertEqual(rows[0]["source"]["id"], message.id)
        self.assertEqual([r["id"] for r in self._native_ranked("tasks", "Atelier nacre")], [mention.id])
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.task", exact.id)

    def test_selected_company_excludes_higher_rank_and_source(self):
        company = self.env["res.company"].create({"name": "Atelier Lune"})
        self.actor.company_ids = [Command.link(company.id)]
        for category in ("tasks", "projects", "contacts"):
            with self.subTest(category=category):
                visible = self._record(category, "Carnet", "Atelier azur")
                hidden = self._record(category, "Atelier azur")
                message = hidden.with_user(self.actor).message_post(
                    body=Markup("<p>Atelier azur</p>"), subtype_xmlid="mail.mt_comment",
                ) if category == "tasks" else None
                hidden.write({"company_id": company.id, **({"project_id": False} if category == "tasks" else {})})
                scoped = self.service.with_context(allowed_company_ids=self.env.company.ids)
                group, = scoped.search_records("Atelier azur", categories=[category])["groups"]
                self.assertEqual([r["id"] for r in group["records"]], [visible.id])
                self.assertEqual([r["id"] for r in self._native_ranked(category, "Atelier azur", context={"allowed_company_ids": self.env.company.ids})], [visible.id])
                with self.assertRaises(UserError):
                    scoped.get_search_result_action(hidden._name, hidden.id)
                if message:
                    with self.assertRaises(UserError):
                        scoped.get_search_result_action(hidden._name, hidden.id,
                            source_message_id=message.id, source_field="body")

    def test_category_filters_and_response_contract_stay_unchanged(self):
        self._record("tasks", "Atelier pastel")
        self._record("projects", "Atelier pastel")
        self._record("contacts", "Atelier pastel")
        self._index()
        result = self.service.search_records("Atelier pastel", categories=["tasks", "projects", "contacts"], exclude_categories=["projects"])
        self.assertEqual(set(result), {"groups"})
        self.assertEqual([g["key"] for g in result["groups"]], ["tasks", "contacts"])
        for group in result["groups"]:
            self.assertEqual(set(group), {"key", "label", "records", "has_more", "offset"})
            self.assertEqual(set(group["records"][0]), {"model", "id", "title", "excerpt", "source", "context_label"})

    def test_ranking_uses_current_readable_title_translation(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        exact = self._record("projects", "Ordinary catalogue")
        exact.with_context(lang="fr_FR").name = "Catalogue amarante"
        mention = self._record("projects", "Catalogue amarante", "Catalogue amarante")
        mention.with_context(lang="fr_FR").name = "Archives du catalogue"
        self._index()
        group, = self.service.with_context(lang="fr_FR").search_records("Catalogue amarante", categories=["projects"])["groups"]
        self.assertEqual([row["id"] for row in group["records"]], [exact.id, mention.id])
        self.assertEqual([r["id"] for r in self._native_ranked("projects", "Catalogue amarante", context={"lang": "fr_FR"})], [exact.id, mention.id])

    def test_search_keeps_original_sources_and_pending_projection_unchanged(self):
        task = self._record("tasks", "Atelier topaze")
        task.with_user(self.actor).message_post(
            body=Markup("<p>Visite Atelier topaze</p>"), subtype_xmlid="mail.mt_comment",
        )
        self._index()
        task.description = "Atelier topaze — note à relire"
        self.env.flush_all()
        tables = ("project_task", "mail_message", "usl_task_search_queue",
            "usl_task_search_fragment", "usl_task_message_search_queue", "usl_task_message_search_fragment")
        from odoo.tools import SQL
        def snapshot():
            result = {}
            for table in tables:
                self.env.cr.execute(SQL("SELECT count(*), md5(coalesce(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text), '')) FROM %s t", SQL.identifier(table)))
                result[table] = self.env.cr.fetchone()
            return result
        before = snapshot()
        self.env.cr.execute("SHOW jit")
        previous_jit, = self.env.cr.fetchone()
        self._rows("Atelier topaze", "tasks")
        self._native_ranked("tasks", "Atelier topaze")
        self.assertEqual(snapshot(), before)
        self.env.cr.execute("SHOW jit")
        self.assertEqual(self.env.cr.fetchone(), (previous_jit,))

    def _native_ranked(self, category, query, *, limit=25, offset=0, order="usl_search_text", context=None):
        model = {"tasks": "project.task", "projects": "project.project", "contacts": "res.partner"}[category]
        return self.env[model].with_user(self.actor).with_context(
            active_test=False, search_relevance_query=query, **(context or {}),
        ).search_read([("usl_search_text", "ilike", query)], ["id"], limit=limit, offset=offset, order=order)

    def test_native_relevance_matches_home_and_annotated_corpus(self):
        expected = {}
        for case in self.corpus["cases"]:
            if case["expected_order"]:
                expected[case["id"]] = [self._record(case["category"], case["titles"][kind],
                    case["mention"] if kind == "mention" else "").id for kind in case["expected_order"]]
        self._index()
        for case in self.corpus["cases"]:
            with self.subTest(search=case["id"]):
                actual = [row["id"] for row in self._native_ranked(case["category"], case["query"], limit=4)]
                self.assertEqual(actual, expected.get(case["id"], []))
                self.assertEqual(actual, [row["id"] for row in self._rows(case["query"], case["category"], limit=4)["records"]])

    def test_native_relevance_paging_and_explicit_ordinary_order(self):
        for category in ("tasks", "projects", "contacts"):
            with self.subTest(category=category):
                query = "Atelier cuivre " + category
                exact = [self._record(category, query) for _ in range(3)]
                mentions = [self._record(category, "Carnet", query) for _ in range(3)]
                self._index()
                expected = [r.id for r in reversed(exact)] + [r.id for r in reversed(mentions)]
                pages = [row["id"] for offset in range(0, 6, 2)
                    for row in self._native_ranked(category, query, limit=2, offset=offset)]
                self.assertEqual(pages, expected)
                self.assertEqual([r["id"] for r in self._native_ranked(category, query, order="usl_search_text desc")], list(reversed(expected)))
                for order, ordinary in (("id asc", exact + mentions), ("id desc", list(reversed(exact + mentions)))):
                    self.assertEqual([r["id"] for r in self._native_ranked(category, query, order=order)], [r.id for r in ordinary])

    def test_native_relevance_requires_a_bounded_explicit_query(self):
        self._record("tasks", "Atelier ambre")
        Task = self.env["project.task"].with_user(self.actor)
        for query in (None, False, "", " " , "x" * 2049, 12):
            with self.subTest(query_type=type(query).__name__, query_length=len(query) if isinstance(query, str) else None):
                with self.assertRaisesRegex(ValueError, "non-empty search"):
                    Task.with_context(search_relevance_query=query).search_read(
                        [("usl_search_text", "ilike", "Atelier ambre")], ["id"], order="usl_search_text")
        self.assertEqual(Task.with_context(search_relevance_query="unrelated").search([], order="id asc").ids,
            Task.search([], order="id asc").ids)
