from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_home_content_search")
class TestHomeContentSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(cls.env, login="qa-home-content-user", groups="project.group_project_user")
        cls.project = cls.env["project.project"].create({"name": "[QA Search] Home plan", "company_id": cls.env.company.id})
        cls.service = cls.env["usl.home.service"].with_user(cls.actor)

    def _task(self, **values):
        return self.env["project.task"].create({"name": "[QA Search] Ordinary work", "project_id": self.project.id, "user_ids": [Command.link(self.actor.id)], **values})

    def test_direct_task_content_and_excerpt(self):
        task = self._task(description="<p>Amber heliograph instructions</p>")
        result = self.service.search_records("Amber heliograph", categories=["tasks"])
        self.assertEqual([row["id"] for row in result["groups"][0]["records"]], task.ids)
        self.assertEqual(result["groups"][0]["records"][0]["excerpt"], "Amber heliograph instructions")

    def test_projects_and_tasks_are_separate_destinations(self):
        task = self._task(name="[QA Search] Copper heliograph")
        self.project.name = "[QA Search] Copper heliograph plan"
        groups = self.service.search_records("Copper heliograph", categories=["tasks", "projects"])["groups"]
        self.assertEqual([group["key"] for group in groups], ["tasks", "projects"])
        self.assertEqual(groups[0]["records"][0]["id"], task.id)
        self.assertEqual(groups[1]["records"][0]["id"], self.project.id)

    def test_open_result_removes_stale_project_defaults(self):
        task = self._task()
        action = self.service.with_context(default_project_id=999, search_default_open_tasks=1, allowed_company_ids=self.env.company.ids).get_search_result_action("project.task", task.id)
        self.assertEqual(action["res_id"], task.id)
        self.assertNotIn("default_project_id", action["context"])
        self.assertNotIn("search_default_open_tasks", action["context"])

    def test_open_rechecks_access_and_does_not_accept_arbitrary_models(self):
        private = self.env["project.project"].create({"name": "[QA Search] Private", "privacy_visibility": "followers"})
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.project", private.id)
        with self.assertRaises(AccessError):
            self.service.get_search_result_action("res.users", self.actor.id)

    def test_live_update_and_paging(self):
        tasks = self.env["project.task"].create([{"name": "[QA Search] Violet heliograph", "project_id": self.project.id} for _index in range(7)])
        first = self.service.search_records("Violet heliograph", categories=["tasks"], limit=5)["groups"][0]
        second = self.service.search_records("Violet heliograph", categories=["tasks"], limit=5, offset=5)["groups"][0]
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual(set(row["id"] for row in first["records"] + second["records"]), set(tasks.ids))
        tasks[0].name = "[QA Search] Updated"
        self.assertEqual(len(self.service.search_records("Violet heliograph", categories=["tasks"], limit=25)["groups"][0]["records"]), 6)

    def test_empty_and_validation(self):
        self.assertEqual(self.service.search_records(" "), {"groups": []})
        for kwargs in ({"limit": 0}, {"offset": -1}, {"categories": ["res.users"]}):
            with self.assertRaises(ValidationError):
                self.service.search_records("Amber", **kwargs)

    def test_portal_cannot_use_search(self):
        portal = new_test_user(self.env, login="qa-content-portal", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self.env["usl.home.service"].with_user(portal).search_records("Amber")

    def test_activity_note_has_a_useful_excerpt(self):
        task = self._task()
        self.env["mail.activity"].create({"res_model_id": self.env["ir.model"]._get_id("project.task"), "res_id": task.id,
            "user_id": self.actor.id, "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "note": "<p>Indigo heliograph instructions</p>"})
        row = self.service.search_records("Indigo heliograph", categories=["tasks"])["groups"][0]["records"][0]
        self.assertEqual(row["excerpt"], "Indigo heliograph instructions")

    def test_property_match_excerpt_is_not_replaced_by_a_secret(self):
        self.project.task_properties_definition = [
            {"name": "qa_sources", "string": "Sources", "type": "char"},
            {"name": "qa_token", "string": "API Token", "type": "char"},
        ]
        self._task(task_properties=[{"name": "qa_sources", "value": "Cobalt heliograph notes"},
            {"name": "qa_token", "value": "Cobalt heliograph credential"}])
        row = self.service.search_records("Cobalt heliograph", categories=["tasks"])["groups"][0]["records"][0]
        self.assertEqual(row["excerpt"], "Cobalt heliograph notes")

    def test_all_installed_readable_categories_can_be_searched(self):
        groups = self.service.search_records("[QA Search] nonexistent unique phrase")["groups"]
        self.assertIn("tasks", [group["key"] for group in groups])
        self.assertTrue(all(not group["records"] for group in groups))

    def test_selected_company_scope_applies_to_matches_and_opening(self):
        company = self.env["res.company"].create({"name": "[QA Search] Home company B"})
        self.actor.company_ids = [Command.link(company.id)]
        project = self.env["project.project"].with_company(company).create({"name": "[QA Search] Shared plan", "company_id": company.id})
        task = self._task(project_id=project.id, description="<p>Silver heliograph instructions</p>")
        scoped = self.service.with_context(allowed_company_ids=self.env.company.ids)
        self.assertFalse(scoped.search_records("Silver heliograph", categories=["tasks"])["groups"][0]["records"])
        with self.assertRaises(UserError):
            scoped.get_search_result_action("project.task", task.id)
        other = self.service.with_context(allowed_company_ids=company.ids)
        self.assertEqual(other.search_records("Silver heliograph", categories=["tasks"])["groups"][0]["records"][0]["id"], task.id)

    def test_private_project_name_is_absent_from_task_excerpt_and_context(self):
        project = self.env["project.project"].create({"name": "[QA Search] Private vermilion plan", "privacy_visibility": "followers"})
        self._task(project_id=project.id, description="<p>Amber heliograph instructions</p>")
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        result = self.service.search_records("Amber heliograph", categories=["tasks"])
        self.assertNotIn("vermilion", str(result).casefold())

    def test_accounting_line_text_is_searchable_with_native_readonly_access(self):
        if "account.move.line" not in self.env.registry:
            self.skipTest("Accounting integration runs in the canonical installed module closure")
        reader = new_test_user(self.env, login="qa-search-account-reader", groups="account.group_account_readonly")
        journal = self.env["account.journal"].create({"name": "[QA Search] Journal", "code": "QASR", "type": "general", "company_id": self.env.company.id})
        accounts = self.env["account.account"].create([
            {"name": "[QA Search] Expense", "code": "990001", "account_type": "expense", "company_ids": [Command.link(self.env.company.id)]},
            {"name": "[QA Search] Asset", "code": "990002", "account_type": "asset_current", "company_ids": [Command.link(self.env.company.id)]},
        ])
        move = self.env["account.move"].create({"journal_id": journal.id, "line_ids": [
            Command.create({"name": "Saffron heliograph instructions", "account_id": accounts[0].id, "debit": 10}),
            Command.create({"name": "[QA Search] Counterpart", "account_id": accounts[1].id, "credit": 10}),
        ]})
        service = self.env["usl.home.service"].with_user(reader)
        rows = service.search_records("Saffron heliograph", categories=["accounting_lines"])["groups"][0]["records"]
        self.assertEqual([row["id"] for row in rows], move.line_ids.filtered(lambda line: line.debit).ids)
        self.assertIn("Saffron heliograph", rows[0]["excerpt"])
        self.assertFalse(move.with_user(reader).has_access("write"))

    def test_documents_actual_content_uses_native_scoped_engine(self):
        if "usl.document" not in self.env.registry:
            self.skipTest("Documents integration runs in the canonical installed module closure")
        from odoo.addons.usl_documents.models.paperless_client import PaperlessClient
        reader = new_test_user(self.env, login="qa-home-search-doc-reader", groups="usl_documents.group_documents_user")
        document = self.env["usl.document"].create({"name": "[QA Search] Ordinary document", "paperless_id": 97101,
            "company_id": self.env.company.id, "review_state": "classified", "availability_state": "available", "permission_sync_state": "synchronized"})
        with (
            patch.object(PaperlessClient, "scoped_search", return_value={"results": [{"id": document.paperless_id, "rank": 1, "excerpt": "Azure heliograph OCR instructions"}], "truncated": False}) as lexical,
            patch.object(PaperlessClient, "semantic_search", return_value={"results": [], "warnings": []}),
        ):
            group = self.env["usl.home.service"].with_user(reader).search_documents("Azure heliograph")["group"]
        self.assertEqual([row["id"] for row in group["records"]], document.ids)
        self.assertEqual(group["records"][0]["excerpt"], "Azure heliograph OCR instructions")
        self.assertIn(document.paperless_id, lexical.call_args.kwargs["document_ids"])

    def test_document_failure_is_local_and_bounded_window_has_native_destination(self):
        if "usl.document" not in self.env.registry:
            self.skipTest("Documents integration runs in the canonical installed module closure")
        reader = new_test_user(self.env, login="qa-home-search-doc-outage", groups="usl_documents.group_documents_user")
        service = self.env["usl.home.service"].with_user(reader)
        with patch.object(type(self.env["usl.document"]), "mcp_search", side_effect=UserError("Synthetic provider outage")):
            self.assertTrue(service.search_documents("Azure")["group"]["unavailable"])
        with patch.object(type(self.env["usl.document"]), "mcp_search") as search:
            self.assertTrue(service.search_documents("Azure", offset=50)["group"]["continue_in_documents"])
            search.assert_not_called()

    def test_progressive_batches_cover_the_same_authorized_catalog(self):
        self._task(description='<p>Progressive amber evidence</p>')
        all_groups = self.service.search_records('Progressive amber evidence')['groups']
        primary = self.service.search_records('Progressive amber evidence', categories=['tasks', 'projects'])['groups']
        remaining = self.service.search_records('Progressive amber evidence', exclude_categories=['tasks', 'projects'])['groups']
        self.assertEqual(primary + remaining, all_groups)
        for invalid in ('tasks', ['unknown'], [{}]):
            with self.assertRaises(ValidationError):
                self.service.search_records('Amber', exclude_categories=invalid)
