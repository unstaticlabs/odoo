from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

RELEASE_SHA = "a" * 40


@tagged("post_install", "-at_install", "usl_feedback")
class TestProductFeedback(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Feedback Company B"})
        companies = [Command.set((cls.company_a | cls.company_b).ids)]
        cls.reporter = new_test_user(
            cls.env,
            login="feedback-reporter",
            groups="project.group_project_user",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.other_reporter = new_test_user(
            cls.env,
            login="feedback-other-reporter",
            groups="project.group_project_user",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.maintainer = new_test_user(
            cls.env,
            login="feedback-maintainer",
            groups="usl_feedback.group_feedback_maintainer",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.service = new_test_user(
            cls.env,
            login="feedback-service",
            groups="usl_feedback.group_feedback_maintainer",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.project = cls.env.ref("usl_feedback.project_product_feedback")
        cls.env["ir.config_parameter"].sudo().set_str(
            "usl.release.commit",
            RELEASE_SHA,
        )

    def _submit(self, user=None, company=None, **values):
        user = user or self.reporter
        company = company or self.company_a
        submission_values = {
            "summary": "Make the reconciliation warning clearer",
            "description": "<p>The recovery action needs a more specific label.</p>",
            "category": "ux",
            "priority": "2",
            "company_id": company.id,
            **values,
        }
        wizard = (
            self.env["usl.feedback.submission"]
            .with_user(user)
            .with_context(allowed_company_ids=[company.id])
            .create(submission_values)
        )
        action = wizard.action_submit()
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", user.id)],
            order="id desc",
            limit=1,
        )
        return task, action

    def test_submission_creates_native_task_with_owned_routing_and_audit(self):
        task, action = self._submit(
            source_action_id=self.env.ref("project.action_view_my_task").id,
            source_model_name="res.users",
            source_record_id=self.reporter.id,
            viewport_width=1440,
            viewport_height=900,
        )

        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.stage_id, self.env.ref("usl_feedback.stage_feedback_new"))
        self.assertEqual(task.usl_feedback_reporter_id, self.reporter)
        self.assertEqual(task.create_uid, self.reporter)
        self.assertEqual(task.company_id, self.company_a)
        self.assertEqual(task.usl_feedback_release_sha, RELEASE_SHA)
        self.assertEqual(task.priority, "2")
        self.assertEqual(task.usl_feedback_category, "ux")
        self.assertEqual(task.tag_ids, self.env.ref("usl_feedback.tag_feedback_ux"))
        self.assertEqual(task.usl_feedback_source_model_id.model, "res.users")
        self.assertEqual(task.usl_feedback_source_res_id, self.reporter.id)
        self.assertIn(self.reporter.partner_id, task.message_partner_ids)
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(action["params"]["next"]["type"], "ir.actions.act_window_close")

    def test_context_opt_out_retains_no_page_state(self):
        task, _action = self._submit(
            include_page_context=False,
            source_action_id=self.env.ref("project.action_view_my_task").id,
            source_model_name="res.users",
            source_record_id=self.reporter.id,
            viewport_width=390,
            viewport_height=844,
        )
        self.assertFalse(task.usl_feedback_context_included)
        self.assertFalse(task.usl_feedback_source_action_id)
        self.assertFalse(task.usl_feedback_source_model_id)
        self.assertFalse(task.usl_feedback_source_res_id)
        self.assertFalse(task.usl_feedback_viewport_width)
        self.assertFalse(task.usl_feedback_viewport_height)

    def test_feedback_priorities_are_translated_in_french(self):
        selection = dict(
            self.env["usl.feedback.submission"]
            .with_context(lang="fr_FR")
            .fields_get(["priority"])["priority"]["selection"],
        )
        self.assertEqual(selection["0"], "Priorité faible")
        self.assertEqual(selection["1"], "Priorité moyenne")
        self.assertEqual(selection["2"], "Priorité élevée")
        self.assertEqual(selection["3"], "Urgent")

    def test_inaccessible_source_record_is_omitted_without_blocking_submission(self):
        hidden_task, _action = self._submit(user=self.other_reporter)
        task, action = self._submit(
            source_model_name="project.task",
            source_record_id=hidden_task.id,
            viewport_width=1280,
            viewport_height=720,
        )
        self.assertTrue(task.usl_feedback_context_included)
        self.assertFalse(task.usl_feedback_source_model_id)
        self.assertFalse(task.usl_feedback_source_res_id)
        self.assertIn("omitted", action["params"]["message"])

    def test_reporter_visibility_isolated_across_rpc_aggregates_and_project(self):
        own, _action = self._submit()
        other, _action = self._submit(user=self.other_reporter)
        reporter_tasks = self.env["project.task"].with_user(self.reporter)

        self.assertEqual(reporter_tasks.search([("id", "in", (own | other).ids)]), own)
        grouped = reporter_tasks.formatted_read_group(
            [("usl_feedback_reporter_id", "!=", False)],
            ["usl_feedback_category"],
            ["__count"],
        )
        self.assertEqual(sum(row["__count"] for row in grouped), 1)
        with self.assertRaises(AccessError):
            other.with_user(self.reporter).read(["name", "description"])
        self.assertFalse(
            self.env["project.project"]
            .with_user(self.reporter)
            .search([("id", "=", self.project.id)]),
        )

    def test_chatter_activity_and_attachments_follow_task_access(self):
        own, _action = self._submit()
        other, _action = self._submit(user=self.other_reporter)
        own_message = own.with_user(self.reporter).message_post(
            body="One more detail",
            subtype_xmlid="mail.mt_comment",
            attachments=[("detail.txt", b"private detail")],
        )
        other_message = other.with_user(self.other_reporter).message_post(
            body="Private conversation",
            subtype_xmlid="mail.mt_comment",
            attachments=[("other-private.txt", b"other private detail")],
        )
        activity = self.env["mail.activity"].sudo().create(
            {
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Triage privately",
                "user_id": self.maintainer.id,
                "res_model_id": self.env["ir.model"]._get_id("project.task"),
                "res_id": other.id,
            },
        )

        self.assertTrue(own_message.attachment_ids)
        self.assertEqual(
            own_message.attachment_ids.with_user(self.reporter).read(["name"])[0]["name"],
            "detail.txt",
        )
        self.assertFalse(
            self.env["mail.message"]
            .with_user(self.reporter)
            .search([("id", "=", other_message.id)]),
        )
        self.assertFalse(
            self.env["ir.attachment"]
            .with_user(self.reporter)
            .search([("id", "=", other_message.attachment_ids.id)]),
        )
        self.assertFalse(
            self.env["mail.activity"]
            .with_user(self.reporter)
            .search([("id", "=", activity.id)]),
        )
        own.with_user(self.reporter).message_unsubscribe(
            partner_ids=self.reporter.partner_id.ids,
        )
        own.with_user(self.reporter).message_subscribe(
            partner_ids=self.reporter.partner_id.ids,
        )
        with self.assertRaises(AccessError):
            other.with_user(self.reporter).message_subscribe(
                partner_ids=self.reporter.partner_id.ids,
            )

    def test_reporter_cannot_mutate_or_forge_feedback_tasks(self):
        own, _action = self._submit()
        with self.assertRaises(AccessError):
            own.with_user(self.reporter).write({"name": "Rewritten outside chatter"})
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(self.reporter).create(
                {
                    "name": "Forged feedback",
                    "project_id": self.project.id,
                    "usl_feedback_reporter_id": self.reporter.id,
                    "usl_feedback_category": "bug",
                    "usl_feedback_release_sha": RELEASE_SHA,
                },
            )
        with self.assertRaises(AccessError):
            own.with_user(self.reporter).unlink()

    def test_maintainer_and_approved_service_identity_operate_all_feedback(self):
        task_a, _action = self._submit()
        task_b, _action = self._submit(user=self.other_reporter)
        tasks = task_a | task_b

        self.assertEqual(
            self.env["project.task"]
            .with_user(self.maintainer)
            .search([("id", "in", tasks.ids)]),
            tasks,
        )
        task_a.with_user(self.maintainer).write(
            {"stage_id": self.env.ref("usl_feedback.stage_feedback_triaged").id},
        )
        task_b.with_user(self.service).write(
            {"stage_id": self.env.ref("usl_feedback.stage_feedback_planned").id},
        )
        self.assertEqual(task_a.stage_id, self.env.ref("usl_feedback.stage_feedback_triaged"))
        self.assertEqual(task_b.stage_id, self.env.ref("usl_feedback.stage_feedback_planned"))

    def test_multi_company_rule_uses_active_company_context(self):
        task_a, _action = self._submit()
        task_b, _action = self._submit(company=self.company_b)
        reporter_tasks = self.env["project.task"].with_user(self.reporter)

        visible_a = reporter_tasks.with_context(allowed_company_ids=[self.company_a.id]).search(
            [("id", "in", (task_a | task_b).ids)],
        )
        visible_b = reporter_tasks.with_context(allowed_company_ids=[self.company_b.id]).search(
            [("id", "in", (task_a | task_b).ids)],
        )
        self.assertEqual(visible_a, task_a)
        self.assertEqual(visible_b, task_b)

    def test_submission_accepts_only_reporter_owned_pending_attachments(self):
        wizard = self.env["usl.feedback.submission"].with_user(self.reporter).create(
            {
                "summary": "Attachment context",
                "description": "<p>See the attached screenshot.</p>",
                "category": "bug",
                "priority": "1",
            },
        )
        attachment = self.env["ir.attachment"].with_user(self.reporter).create(
            {
                "name": "screenshot.txt",
                "raw": b"synthetic screenshot",
                "res_model": wizard._name,
                "res_id": wizard.id,
            },
        )
        wizard.write({"attachment_ids": [Command.link(attachment.id)]})
        wizard.action_submit()
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.reporter.id)],
            order="id desc",
            limit=1,
        )
        self.assertEqual(attachment.sudo().res_model, "project.task")
        self.assertEqual(attachment.sudo().res_id, task.id)

    def test_verified_release_identity_is_required(self):
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", None)
        with patch.dict("os.environ", {"USL_RELEASE_COMMIT": "unverified"}):
            with self.assertRaises(UserError):
                self._submit()
