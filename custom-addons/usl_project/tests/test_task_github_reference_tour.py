from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_project")
class TestTaskGithubReferenceTour(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="qa-github-tour-user", password="qa-github-tour",
            groups="project.group_project_user", company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA GitHub] Tour plan", "company_id": cls.env.company.id,
            "privacy_visibility": "employees", "task_properties_definition": [
                {"name": "qa_github", "string": "GitHub", "type": "char", "view_in_cards": True},
                {"name": "qa_sources", "string": "Sources", "type": "char"},
            ],
        })

    def test_empty_reference_can_be_saved_and_reloaded(self):
        task = self.env["project.task"].create({
            "name": "[QA GitHub] Empty tour task", "project_id": self.project.id,
            "user_ids": [Command.set(self.user.ids)],
        })
        self.start_tour(f"/odoo/project.task/{task.id}", "usl_task_github_reference_edit", login=self.user.login)
        task.invalidate_recordset(["github_url"])
        self.assertEqual(task.github_url, "https://github.com/example/demo/issues/42")

    def test_hidden_legacy_survives_other_property_edit_and_conflict(self):
        task = self.env["project.task"].create({
            "name": "[QA GitHub] Legacy tour task", "project_id": self.project.id,
            "github_url": "https://github.com/example/demo/issues/42",
            "user_ids": [Command.set(self.user.ids)], "task_properties": [
                {"name": "qa_github", "value": "https://github.com/example/demo/issues/42"},
                {"name": "qa_sources", "value": "Original synthetic notes"},
            ],
        })
        self.start_tour(f"/odoo/project.task/{task.id}", "usl_task_github_reference_legacy", login=self.user.login)
        task.invalidate_recordset(["github_url", "task_properties"])
        self.assertEqual(task.github_url, "https://github.com/example/demo/pull/43")
        task.flush_recordset(["task_properties"])
        self.env.cr.execute("SELECT task_properties FROM project_task WHERE id=%s", (task.id,))
        self.assertEqual(self.env.cr.fetchone()[0], {
            "qa_github": "https://github.com/example/demo/issues/42", "qa_sources": "Updated synthetic notes",
        })


@tagged("post_install", "-at_install", "usl_project")
class TestTaskGithubReferenceMobileTour(TestTaskGithubReferenceTour):
    browser_size = "390x844"
    allow_inherited_tests_method = True
