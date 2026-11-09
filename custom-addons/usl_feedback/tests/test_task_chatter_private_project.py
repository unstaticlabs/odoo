from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_feedback")
class TestTaskChatterInPrivateProject(TransactionCase):
    """An assignee who does not follow a followers-only project still gets the task's chatter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignee = new_test_user(
            cls.env, login="qa-chatter-private-assignee",
            groups="project.group_project_user", company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA Chatter] Private plan", "company_id": cls.env.company.id,
            "privacy_visibility": "followers",
        })
        cls.task = cls.env["project.task"].create({
            "name": "[QA Chatter] Assigned task", "project_id": cls.project.id,
            "user_ids": [Command.set(cls.assignee.ids)],
        })

    def setUp(self):
        super().setUp()
        # A request starts with a cold cache; a warm one would hide the refused read.
        self.env.flush_all()
        self.env.invalidate_all()

    def test_the_assignee_reads_the_task_but_not_its_project(self):
        task = self.task.with_user(self.assignee)
        self.assertTrue(task.has_access("read"))
        self.assertFalse(task.project_id.has_access("read"))

    def test_suggested_recipients_load_for_an_assignee_outside_the_project(self):
        task = self.task.with_user(self.assignee)
        # The chatter asks for these when the task form opens; a refused read
        # of the project made the whole /mail/action answer fail.
        recipients = task._message_get_suggested_recipients(reply_discussion=True, no_create=True)
        # The assignee is already on the task, so nobody is left to suggest.
        self.assertEqual(recipients, [])

    def test_the_assignee_moves_their_own_task_to_another_stage(self):
        stages = self.env["project.task.type"].create([
            {"name": "[QA Chatter] To do", "project_ids": [Command.link(self.project.id)]},
            {"name": "[QA Chatter] Doing", "project_ids": [Command.link(self.project.id)]},
        ])
        self.task.stage_id = stages[0]
        self.env.flush_all()
        self.env.invalidate_all()
        task = self.task.with_user(self.assignee)
        # The constraint on stage_id used to read the project's feedback flag
        # as this user, whom a followers-only project refuses.
        task.write({"stage_id": stages[1].id})
        self.env.flush_all()
        self.assertEqual(self.task.stage_id, stages[1])

    def test_a_feedback_task_without_a_reporter_is_still_refused(self):
        feedback_project = self.env["project.project"].create({
            "name": "[QA Chatter] Feedback", "company_id": False,
            "privacy_visibility": "followers", "usl_feedback_project": True,
        })
        with self.assertRaises(ValidationError):
            self.env["project.task"].with_context(tracking_disable=True).sudo().create({
                "name": "[QA Chatter] Orphan feedback card", "project_id": feedback_project.id,
                "company_id": False,
                "stage_id": self.env.ref("usl_feedback.stage_feedback_new").id,
            })

    def test_a_feedback_task_is_still_recognised_for_that_assignee(self):
        feedback_project = self.env["project.project"].create({
            "name": "[QA Chatter] Feedback", "company_id": False,
            "privacy_visibility": "followers", "usl_feedback_project": True,
        })
        task = self.env["project.task"].with_context(tracking_disable=True).sudo().create({
            "name": "[QA Chatter] Feedback card", "project_id": feedback_project.id,
            "company_id": False, "usl_feedback_reporter_id": self.assignee.id,
            "usl_feedback_company_id": self.env.company.id,
            "stage_id": self.env.ref("usl_feedback.stage_feedback_new").id,
            "usl_feedback_agent_state": "triaged",
            "user_ids": [Command.set(self.assignee.ids)],
        })
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(task.with_user(self.assignee)._usl_feedback_is_task())
        self.assertFalse(self.task.with_user(self.assignee)._usl_feedback_is_task())
