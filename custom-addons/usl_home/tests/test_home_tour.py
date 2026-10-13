from datetime import timedelta

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_home_tour")
class TestUslHomeTour(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login="usl-home-tour-user",
            password="home-tour",
            groups="project.group_project_user",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )

    def test_home_shell_and_personalization_tour(self):
        self.start_tour(
            "/odoo",
            "usl_home_core_journey",
            login=self.user.login,
        )

    def test_home_metrics_override_default_saved_search(self):
        project = self.env["project.project"].create({
            "name": "[QA Navigation] Home metric plan", "company_id": self.env.company.id,
            "privacy_visibility": "employees",
        })
        stage = self.env["project.task.type"].create({
            "name": "[QA Navigation] Next", "project_ids": [Command.link(project.id)],
        })
        today = fields.Date.context_today(self.user)
        self.env["project.task"].create([
            {"name": name, "project_id": project.id, "stage_id": stage.id,
             "date_deadline": today + timedelta(days=offset), "user_ids": [Command.set(self.user.ids)]}
            for name, offset in [("[QA Navigation] Overdue", -1), ("[QA Navigation] Due soon", 2)]
        ])
        self.env["ir.filters"].create({
            "name": "[QA Navigation] My usual tasks", "model_id": "project.task",
            "user_ids": [Command.set(self.user.ids)],
            "action_id": self.env.ref("project.action_view_my_task").id,
            "domain": "[]", "context": "{}", "is_default": True,
        })
        self.start_tour("/odoo/home", "usl_home_task_metrics_saved_search", login=self.user.login)
