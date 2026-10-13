from odoo import Command
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestTaskNavigation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="qa.navigation.tests", groups="project.group_project_user",
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA Navigation] Release plan", "allow_milestones": True,
            "privacy_visibility": "employees",
        })
        cls.milestone = cls.env["project.milestone"].create({
            "name": "[QA Navigation] Launch readiness", "project_id": cls.project.id,
        })
        cls.tasks = cls.env["project.task"].create([
            {"name": f"[QA Navigation] Work {index}", "project_id": cls.project.id,
             "milestone_id": cls.milestone.id, "user_ids": [Command.set(cls.user.ids)]}
            for index in range(2)
        ])

    def test_milestone_task_action_has_a_durable_identity(self):
        action = self.milestone.with_user(self.user).action_view_tasks()
        self.assertEqual(action["name"], self.milestone.display_name)
        self.assertEqual(action["context"]["active_id"], self.milestone.id)
        self.assertEqual(action["context"]["default_milestone_id"], self.milestone.id)
        self.assertEqual(action["context"]["default_project_id"], self.project.id)
        self.assertEqual(action["context"]["active_model"], "project.milestone")

    def test_single_task_keeps_native_direct_form_behavior(self):
        self.tasks[1].unlink()
        action = self.milestone.with_user(self.user).action_view_tasks()
        self.assertEqual(action["res_id"], self.tasks[0].id)
        self.assertEqual(action["view_mode"], "form")

    def test_task_cards_expose_the_existing_identifier(self):
        arch = self.env.ref("project.view_task_kanban")._get_combined_arch()
        self.assertTrue(arch.xpath("//t[@t-name='card']//*[@class='o_usl_task_reference text-muted small']"))
        self.assertTrue(arch.xpath("//t[@t-name='card']//*[@class='o_usl_task_reference text-muted small']/field[@name='id']"))

    def test_my_activities_summary_precedes_the_linked_document(self):
        arch = self.env.ref("mail.mail_activity_view_tree_without_record_access")._get_combined_arch()
        fields = [node.get("name") for node in arch.xpath("/list/field")]
        self.assertLess(fields.index("summary"), fields.index("res_name"))


@tagged("usl_project_tour", "post_install", "-at_install")
class TestTaskNavigationTour(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="qa.navigation.tour", password="navigation-tour",
            groups="project.group_project_user",
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA Navigation] Release plan", "allow_milestones": True,
            "privacy_visibility": "employees", "company_id": cls.env.company.id,
        })
        cls.stage = cls.env["project.task.type"].create({
            "name": "[QA Navigation] Next", "project_ids": [Command.link(cls.project.id)],
        })
        cls.milestone = cls.env["project.milestone"].create({
            "name": "[QA Navigation] Launch readiness", "project_id": cls.project.id,
        })
        cls.tasks = cls.env["project.task"].create([
            {"name": name, "project_id": cls.project.id, "milestone_id": cls.milestone.id,
             "stage_id": cls.stage.id,
             "user_ids": [Command.set(cls.user.ids)]}
            for name in ["[QA Navigation] Overdue", "[QA Navigation] Due soon"]
        ])

    def test_milestone_title_cards_and_view_switches(self):
        action_id = self.env.ref("project.action_view_task_from_milestone").id
        self.start_tour(
            f"/odoo/action-{action_id}?active_id={self.milestone.id}",
            "usl_task_navigation_milestone", login=self.user.login,
        )
