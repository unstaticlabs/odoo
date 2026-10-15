from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'usl_task_content_search')
class TestTaskContentSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(
            cls.env, login='qa-content-search-user', groups='project.group_project_user',
        )
        cls.project = cls.env['project.project'].create({
            'name': '[QA Search] Launch plan',
            'company_id': cls.env.company.id,
            'task_properties_definition': [
                {'name': 'qa_sources', 'string': 'Sources', 'type': 'char'},
            ],
        })

    def _task(self, **values):
        return self.env['project.task'].create({
            'name': '[QA Search] Ordinary task',
            'project_id': self.project.id,
            'user_ids': [Command.link(self.actor.id)],
            **values,
        })

    def _matches(self, query):
        return self.env['project.task'].with_user(self.actor).search([
            ('usl_search_text', 'ilike', query),
        ])

    def test_description_content_is_searchable_without_name_match(self):
        task = self._task(description='<p>Amber heliograph specification</p>')
        self.assertEqual(self._matches('Amber heliograph'), task)

    def test_related_activity_note_is_searchable(self):
        task = self._task()
        self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('project.task'),
            'res_id': task.id,
            'user_id': self.actor.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Review the plan',
            'note': '<p>Indigo heliograph instructions</p>',
        })
        self.assertEqual(self._matches('Indigo heliograph'), task)

    def test_native_property_value_is_searchable(self):
        task = self._task(task_properties=[
            {'name': 'qa_sources', 'value': 'Cobalt heliograph notes'},
        ])
        self.assertEqual(self._matches('Cobalt heliograph'), task)

    def test_readable_related_project_name_is_searchable(self):
        project = self.env["project.project"].create({"name": "[QA Search] Jade heliograph"})
        task = self._task(project_id=project.id)
        self.assertEqual(self._matches("Jade heliograph"), task)

    def test_unreadable_project_name_cannot_influence_readable_task_match(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Vermilion heliograph", "privacy_visibility": "followers",
        })
        task = self._task(project_id=project.id)
        self.assertTrue(task.with_user(self.actor).has_access("read"))
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        self.assertFalse(self._matches("Vermilion heliograph"))

    def test_readable_task_properties_do_not_require_project_business_access(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Private plan", "privacy_visibility": "followers",
            "task_properties_definition": [
                {"name": "qa_private_sources", "string": "Sources", "type": "char"},
            ],
        })
        task = self._task(project_id=project.id, task_properties=[
            {"name": "qa_private_sources", "value": "Copper heliograph"},
        ])
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        values = task.with_user(self.actor).read(["task_properties"])[0]["task_properties"]
        self.assertIn("Copper heliograph", [item.get("value") for item in values])
        self.assertEqual(self._matches("Copper heliograph"), task)

    def test_assignee_visible_activity_does_not_expose_unreadable_task(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Private plan", "privacy_visibility": "followers",
        })
        task = self._task(project_id=project.id, user_ids=[Command.clear()])
        activity = self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "note": "<p>Teal heliograph instructions</p>",
        })
        task.message_unsubscribe(partner_ids=self.actor.partner_id.ids)
        self.assertTrue(activity.with_user(self.actor).has_access("read"))
        self.assertFalse(task.with_user(self.actor).has_access("read"))
        self.assertFalse(self._matches("Teal heliograph"))

    def test_selected_company_limits_description_and_activity_matches(self):
        company = self.env["res.company"].create({"name": "[QA Search test] Company B"})
        self.actor.company_ids = [Command.link(company.id)]
        project = self.env["project.project"].with_company(company).create({
            "name": "[QA Search] Other company plan", "company_id": company.id,
        })
        task = self._task(project_id=project.id, description="<p>Silver heliograph</p>")
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "note": "<p>Silver heliograph</p>",
        })
        Task = self.env["project.task"].with_user(self.actor)
        self.assertFalse(Task.with_context(allowed_company_ids=[self.env.company.id]).search([
            ("usl_search_text", "ilike", "Silver heliograph"),
        ]))
        self.assertEqual(Task.with_context(allowed_company_ids=[company.id]).search([
            ("usl_search_text", "ilike", "Silver heliograph"),
        ]), task)

    def test_credentials_and_signature_properties_are_not_search_content(self):
        self.project.task_properties_definition = [
            {"name": "qa_opaque", "string": "API Token", "type": "char"},
        ]
        self._task(access_token="Quartz heliograph", task_properties=[
            {"name": "qa_opaque", "value": "Quartz heliograph"},
        ])
        self.assertFalse(self._matches("Quartz heliograph"))

    def test_native_field_group_denial_prevents_content_match(self):
        task = self._task(description="<p>Coral heliograph</p>")
        restricted = new_test_user(
            self.env, login="qa-content-field-user", groups="project.group_project_user",
        )
        with patch.object(self.env["project.task"]._fields["description"], "groups", "base.group_system"):
            with self.assertRaises(AccessError):
                task.with_user(restricted).read(["description"])
            self.assertFalse(self.env["project.task"].with_user(restricted).search([
                ("usl_search_text", "ilike", "Coral heliograph"),
            ]))
        self.assertEqual(self._matches("Coral heliograph"), task)

    def test_selection_label_and_task_id_are_searchable(self):
        task = self._task(priority="3")
        self.assertIn(task, self._matches("Urgent"))
        self.assertIn(task, self._matches(str(task.id)))

    def test_ordinary_description_can_discuss_credentials(self):
        task = self._task(description="<p>API token rollout instructions for synthetic QA</p>")
        self.assertEqual(self._matches("API token rollout"), task)

    def test_related_activity_type_label_is_searchable(self):
        task = self._task()
        kind = self.env["mail.activity.type"].create({"name": "[QA Search] Ochre heliograph review"})
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": kind.id,
        })
        self.assertEqual(self._matches("Ochre heliograph"), task)

    def test_property_schema_cannot_borrow_another_projects_plain_label(self):
        public = self._task(task_properties=[{"name": "qa_sources", "value": "Ordinary"}])
        secret_project = self.env["project.project"].create({
            "name": "[QA Search] Credential schema", "company_id": self.env.company.id,
            "task_properties_definition": [{"name": "qa_sources", "string": "API Token", "type": "char"}],
        })
        secret = self._task(project_id=secret_project.id, task_properties=[{"name": "qa_sources", "value": "Quartz heliograph"}])
        self.assertNotEqual(public.project_id, secret.project_id)
        self.assertFalse(self._matches("Quartz heliograph"))

    def test_date_boundary_is_valid_search_input(self):
        self.assertFalse(self._matches("9999-12-31"))

    def test_native_property_tag_label_is_searchable(self):
        self.project.task_properties_definition = [{"name": "qa_tags", "string": "Topics", "type": "tags",
            "tags": [["qa_tag", "Lavender heliograph", 1]]}]
        task = self._task(task_properties=[{"name": "qa_tags", "value": ["qa_tag"]}])
        self.assertEqual(self._matches("Lavender heliograph"), task)

    def test_private_property_metadata_is_prefetched_as_a_cohort(self):
        projects = self.env["project.project"].create([
            {"name": f"[QA Search] Private schema {index}", "privacy_visibility": "followers",
             "task_properties_definition": [{"name": f"qa_schema_{index}", "string": "Sources", "type": "char"}]}
            for index in range(8)
        ])
        tasks = self.env["project.task"].create([
            {"name": "[QA Search] Cohort work", "project_id": project.id, "user_ids": [Command.link(self.actor.id)],
             "task_properties": [{"name": f"qa_schema_{index}", "value": "Copper heliograph"}]}
            for index, project in enumerate(projects)
        ])
        self.env.flush_all()
        self.env.invalidate_all()
        model = self.env["project.task"].with_user(self.actor)
        # Warm ordinary ACL/rule metadata, not task/property rows.
        model.fields_get()
        model.has_access("read")
        from ..models.content_search import _property_domains, _typed_values
        from odoo.tools.profiler import Profiler, SQLCollector
        sql = SQLCollector()
        with Profiler(collectors=[sql], db=False):
            clauses = _property_domains(model, "task_properties", "Copper heliograph", _typed_values(model, "Copper heliograph"), {})
        schema_queries = [entry for entry in sql.entries if '"project_project"."task_properties_definition"' in entry["query"]]
        self.assertEqual(len(schema_queries), 1)
        from odoo.fields import Domain
        self.assertEqual(model.search(Domain.OR(clauses)), tasks)
        self.assertTrue(all(not project.with_user(self.actor).has_access("read") for project in projects))

    def test_native_portal_name_sql_does_not_bypass_related_user_record_rules(self):
        company = self.env["res.company"].create({"name": "[QA Search] Assignee company B"})
        other = new_test_user(self.env, login="qa-related-assignee", name="Vermilion heliograph reviewer", groups="base.group_user",
            company_id=company.id, company_ids=[Command.set(company.ids)])
        other.partner_id.company_id = company
        self.env["ir.rule"].create({"name": "[QA Search] User record separation", "model_id": self.env["ir.model"]._get_id("res.users"),
            "domain_force": f"[('id', '!=', {other.id})]", "global": True})
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)])
        scoped = self.env["project.task"].with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids)
        self.assertFalse(other.with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids).has_access("read"))
        self.assertTrue(task.with_user(self.actor).has_access("read"))
        self.assertFalse(scoped.search([("usl_search_text", "ilike", "Vermilion heliograph")]))

    def test_public_assignee_identity_remains_searchable_across_default_companies(self):
        company = self.env["res.company"].create({"name": "[QA Search] Public identity company B"})
        other = new_test_user(self.env, login="qa-public-assignee", name="Vermilion heliograph reviewer", groups="base.group_user",
            company_id=company.id, company_ids=[Command.set(company.ids)])
        other.partner_id.company_id = company
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)])
        scoped = self.env["project.task"].with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids)
        self.assertTrue(other.with_user(self.actor).has_access("read"))
        self.assertEqual(other.with_user(self.actor).read(["name"])[0]["name"], "Vermilion heliograph reviewer")
        self.assertEqual(scoped.search([("usl_search_text", "ilike", "Vermilion heliograph")]), task)

    def test_readable_description_matches_even_when_same_assignee_is_denied(self):
        other = new_test_user(self.env, login="qa-denied-assignee-text", name="Vermilion heliograph reviewer", groups="base.group_user")
        self.env["ir.rule"].create({"name": "[QA Search] Other user separation", "model_id": self.env["ir.model"]._get_id("res.users"),
            "domain_force": f"[('id', '!=', {other.id})]", "global": True})
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)], description="<p>Vermilion heliograph instructions</p>")
        self.assertFalse(other.with_user(self.actor).has_access("read"))
        self.assertEqual(self._matches("Vermilion heliograph"), task)
