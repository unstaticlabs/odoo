from odoo import Command
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class ActivityParentTaskFixture:
    @classmethod
    def _create_fixture(cls):
        cls.user = new_test_user(
            cls.env, login="qa-activity-link-user", password="qa-activity-link",
            groups="project.group_project_user", company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA Activity] Plan", "company_id": cls.env.company.id,
            "privacy_visibility": "followers",
        })
        cls.task = cls.env["project.task"].create({
            "name": "[QA Activity] Parent task", "project_id": cls.project.id,
            "user_ids": [Command.set(cls.user.ids)],
        })
        cls.activity = cls.env["mail.activity"].create({
            "res_model_id": cls.env["ir.model"]._get_id("project.task"),
            "res_id": cls.task.id,
            "activity_type_id": cls.env.ref("mail.mail_activity_data_todo").id,
            "summary": "[QA Activity] Call the printer",
            "user_id": cls.user.id,
        })


@tagged("post_install", "-at_install", "usl_project")
class TestActivityParentTaskLink(ActivityParentTaskFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._create_fixture()

    def test_open_document_opens_the_parent_task_for_a_reader(self):
        action = self.activity.with_user(self.user).action_open_document()
        self.assertEqual((action["res_model"], action["res_id"]), ("project.task", self.task.id))

    def test_open_document_never_reveals_a_task_the_user_cannot_read(self):
        outsider = new_test_user(
            self.env, login="qa-activity-link-outsider",
            groups="project.group_project_user", company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        self.activity.user_id = outsider
        # Being assigned an activity subscribes you to its document; the task
        # is private to followers, so drop that to model a real outsider.
        self.task.message_unsubscribe(outsider.partner_id.ids)
        action = self.activity.with_user(outsider).action_open_document()
        self.assertEqual((action["res_model"], action["res_id"]), ("mail.activity", self.activity.id))

    def test_activity_overview_form_names_the_document_exactly_once(self):
        arch = self.env.ref("mail.mail_activity_view_form")._get_combined_arch()
        self.assertEqual(len(arch.xpath("//field[@name='res_name']")), 1)


@tagged("post_install", "-at_install", "usl_project_tour")
class TestActivityParentTaskLinkTour(ActivityParentTaskFixture, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._create_fixture()

    def test_activity_record_page_leads_to_the_parent_task(self):
        self.start_tour(
            f"/odoo/mail.activity/{self.activity.id}",
            "usl_activity_parent_task_link", login=self.user.login,
        )

    def test_edit_dialog_opened_from_the_task_stays_uncluttered(self):
        self.start_tour(
            f"/odoo/project.task/{self.task.id}",
            "usl_activity_edit_dialog_from_task", login=self.user.login,
        )
