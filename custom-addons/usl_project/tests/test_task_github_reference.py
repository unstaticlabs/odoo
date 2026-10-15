from copy import deepcopy

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_project")
class TestTaskGithubReference(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({
            "name": "[QA GitHub] Development plan",
            "company_id": cls.env.company.id,
            "privacy_visibility": "employees",
            "task_properties_definition": [
                {"name": "qa_github", "type": "char", "string": "GitHub", "view_in_cards": True},
                {"name": "qa_sources", "type": "char", "string": "Sources"},
            ],
        })
        cls.user = new_test_user(
            cls.env, login="qa-github-project-user", groups="project.group_project_user",
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )

    def _task(self, legacy=False, **values):
        return self.env["project.task"].create({
            "name": "[QA GitHub] Reference task", "project_id": self.project.id,
            "task_properties": [{"name": "qa_github", "value": legacy},
                                {"name": "qa_sources", "value": "Synthetic notes"}],
            **values,
        })

    def _raw_properties(self, task):
        task.flush_recordset(["task_properties"])
        self.env.cr.execute("SELECT task_properties FROM project_task WHERE id = %s", (task.id,))
        return deepcopy(self.env.cr.fetchone()[0])

    def _raw_project(self):
        self.project.flush_recordset()
        self.env.cr.execute("SELECT to_jsonb(p) - 'write_uid' - 'write_date' FROM project_project p WHERE id = %s", (self.project.id,))
        return deepcopy(self.env.cr.fetchone()[0])

    def test_canonical_field_and_native_clickable_views(self):
        self.assertIn("github_url", self.env["project.task"]._fields)
        form = etree.fromstring(self.env["project.task"].get_view(view_type="form")["arch"])
        self.assertTrue(form.xpath("//field[@name='github_url'][@widget='url']"))
        self.assertTrue(form.xpath("//field[@name='task_properties'][@widget='usl_task_properties']"))
        card = etree.fromstring(self.env["project.task"].get_view(view_type="kanban")["arch"])
        self.assertTrue(card.xpath("//field[@name='github_url'][@widget='url'][@text='GitHub']"))

    def test_migration_preserves_legacy_data_and_is_idempotent(self):
        task = self._task("https://github.com/example/demo/issues/42")
        legacy = self._raw_properties(task)
        definitions = deepcopy(self.project.task_properties_definition)
        project_row = self._raw_project()
        self.env["project.task"]._usl_migrate_github_references()
        self.assertEqual(task.github_url, "https://github.com/example/demo/issues/42")
        self.assertEqual(self._raw_properties(task), legacy)
        self.assertEqual(self.project.task_properties_definition, definitions)
        self.assertEqual(self._raw_project(), project_row)
        self.env["project.task"]._usl_migrate_github_references()
        self.assertEqual(task.github_url, "https://github.com/example/demo/issues/42")
        self.assertEqual(self._raw_properties(task), legacy)
        self.assertEqual(self.project.task_properties_definition, definitions)
        self.assertEqual(self._raw_project(), project_row)

    def test_migration_never_overwrites_existing_canonical_reference(self):
        task = self._task("https://github.com/example/demo/issues/42",
                          github_url="https://github.com/example/demo/pull/43")
        legacy = self._raw_properties(task)
        self.env["project.task"]._usl_migrate_github_references()
        self.assertEqual(task.github_url, "https://github.com/example/demo/pull/43")
        self.assertEqual(self._raw_properties(task), legacy)

    def test_invalid_and_empty_legacy_values_are_retained(self):
        for value in (False, "", "Not a URL", "https://example.com/demo", "javascript:alert(1)",
                      "https://github.com.evil.example/demo", "https://github.com:invalid/demo", "https://person@github.com/example/demo"):
            with self.subTest(value=value):
                task = self._task(value)
                legacy = self._raw_properties(task)
                self.env["project.task"]._usl_migrate_github_references()
                self.assertFalse(task.github_url)
                self.assertEqual(self._raw_properties(task), legacy)

    def test_conflicting_legacy_properties_are_not_guessed(self):
        self.project.task_properties_definition += [
            {"name": "qa_github_second", "type": "char", "string": "GitHub"},
        ]
        task = self._task("https://github.com/example/demo/issues/42")
        task.task_properties = [
            {"name": "qa_github", "value": "https://github.com/example/demo/issues/42"},
            {"name": "qa_github_second", "value": "https://github.com/example/demo/pull/43"},
            {"name": "qa_sources", "value": "Synthetic notes"},
        ]
        legacy = self._raw_properties(task)
        self.env["project.task"]._usl_migrate_github_references()
        self.assertFalse(task.github_url)
        self.assertEqual(self._raw_properties(task), legacy)

    def test_archived_tasks_keep_their_reference(self):
        company = self.env["res.company"].create({"name": "[QA GitHub] Archived company"})
        project = self.env["project.project"].create({
            "name": "[QA GitHub] Archived plan", "company_id": company.id, "active": False,
            "task_properties_definition": deepcopy(self.project.task_properties_definition),
        })
        task = self._task("https://github.com/example/demo/pull/43", active=False,
                          project_id=project.id, user_ids=[Command.clear()])
        legacy = self._raw_properties(task)
        self.env["project.task"]._usl_migrate_github_references()
        self.assertEqual(task.github_url, "https://github.com/example/demo/pull/43")
        self.assertEqual(self._raw_properties(task), legacy)
        with self.assertRaises(AccessError):
            task.with_user(self.user).read(["github_url"])

    def test_canonical_reference_uses_native_task_permissions(self):
        task = self._task()
        task.with_user(self.user).write({"github_url": "https://github.com/example/demo/issues/42"})
        self.assertEqual(task.with_user(self.user).github_url, "https://github.com/example/demo/issues/42")
        company = self.env["res.company"].create({"name": "[QA GitHub] Other company"})
        other_project = self.env["project.project"].create({
            "name": "[QA GitHub] Other company plan", "company_id": company.id,
        })
        hidden = self.env["project.task"].create({
            "name": "[QA GitHub] Inaccessible task", "project_id": other_project.id,
            "github_url": "https://github.com/example/demo/pull/43",
        })
        with self.assertRaises(AccessError):
            hidden.with_user(self.user).read(["github_url"])
        with self.assertRaises(AccessError):
            hidden.with_user(self.user).write({"github_url": "https://github.com/example/demo/issues/44"})
