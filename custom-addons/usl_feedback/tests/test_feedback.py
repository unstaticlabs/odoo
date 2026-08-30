import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged

RELEASE_SHA = "a" * 40


def interaction_response(interaction_id, result, *, input_tokens=0, output_tokens=0):
    return {
        "id": interaction_id,
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(result)}],
            },
        ],
        "usage": {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
        },
    }


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
            groups="base.group_user",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.other = new_test_user(
            cls.env,
            login="feedback-other",
            groups="base.group_user",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.company_a_user = new_test_user(
            cls.env,
            login="feedback-company-a-only",
            groups="base.group_user",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.company_b_user = new_test_user(
            cls.env,
            login="feedback-company-b-only",
            groups="base.group_user",
            company_id=cls.company_b.id,
            company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.maintainer = new_test_user(
            cls.env,
            login="feedback-maintainer",
            groups="usl_feedback.group_feedback_maintainer",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.project_manager = new_test_user(
            cls.env,
            login="feedback-project-manager",
            groups="project.group_project_manager",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.agent = new_test_user(
            cls.env,
            login="feedback-agent",
            groups="usl_feedback.group_feedback_agent",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.technical_admin = new_test_user(
            cls.env,
            login="feedback-technical-admin",
            groups="base.group_system",
            company_id=cls.company_a.id,
            company_ids=companies,
        )
        cls.project = cls.env.ref("usl_feedback.project_product_feedback")
        cls.env["ir.config_parameter"].sudo().set_str("usl.release.commit", RELEASE_SHA)

    def _start(self, user=None, company=None, context=None):
        user = user or self.reporter
        company = company or self.company_a
        model = self.env["usl.feedback.submission"].with_user(user).with_context(
            allowed_company_ids=[company.id],
        )
        result = model.feedback_start(context or {})
        return model.browse(result["draft_id"])

    def _submit(self, user=None, company=None, message="The status is unclear after reload.", **context):
        user = user or self.reporter
        draft = self._start(user=user, company=company, context=context)
        payload = draft.feedback_submit_initial(message, bool(context))
        return self.env["project.task"].sudo().browse(payload["id"]), payload

    def test_first_message_creates_partial_native_inbox_before_provider(self):
        task, payload = self._submit(
            action_id=self.env.ref("project.action_view_my_task").id,
            model="res.users",
            res_id=self.reporter.id,
            viewport_width=1440,
            viewport_height=900,
        )
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.stage_id, self.env.ref("usl_feedback.stage_feedback_new"))
        self.assertEqual(task.usl_feedback_reporter_id, self.reporter)
        self.assertEqual(task.create_uid, self.reporter)
        self.assertFalse(task.company_id)
        self.assertEqual(task.usl_feedback_company_id, self.company_a)
        self.assertEqual(task.usl_feedback_release_sha, RELEASE_SHA)
        self.assertFalse(task.usl_feedback_category)
        self.assertEqual(task.usl_feedback_agent_state, "queued")
        self.assertEqual(task.usl_feedback_source_model_id.model, "res.users")
        self.assertEqual(task.usl_feedback_source_res_id, self.reporter.id)
        self.assertIn(self.reporter.partner_id, task.message_partner_ids)
        self.assertEqual(payload["agent_state"], "queued")
        self.assertEqual(
            self.env["usl.feedback.agent.run"].sudo().search_count([("task_id", "=", task.id)]),
            1,
        )

    def test_context_opt_out_retains_no_page_state(self):
        draft = self._start(
            context={
                "action_id": self.env.ref("project.action_view_my_task").id,
                "model": "res.users",
                "res_id": self.reporter.id,
                "viewport_width": 390,
                "viewport_height": 844,
            },
        )
        payload = draft.feedback_submit_initial("Mobile context should remain private.", False)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertFalse(task.usl_feedback_context_included)
        self.assertFalse(task.usl_feedback_source_action_id)
        self.assertFalse(task.usl_feedback_source_model_id)
        self.assertFalse(task.usl_feedback_source_res_id)
        self.assertFalse(task.usl_feedback_viewport_width)
        self.assertFalse(task.usl_feedback_viewport_height)

    def test_inaccessible_source_record_is_omitted(self):
        private_project = self.env["project.project"].sudo().create(
            {"name": "Private source", "privacy_visibility": "followers"},
        )
        hidden = self.env["project.task"].sudo().create(
            {"name": "Hidden source", "project_id": private_project.id},
        )
        draft = self._start(context={"model": "project.task", "res_id": hidden.id})
        payload = draft.feedback_submit_initial("This references a source I cannot read.", True)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertTrue(payload["context_omitted"])
        self.assertTrue(task.usl_feedback_context_included)
        self.assertFalse(task.usl_feedback_source_model_id)
        self.assertFalse(task.usl_feedback_source_res_id)

    def test_all_internal_users_share_cards_chatter_and_attachments(self):
        task, _payload = self._submit()
        outgoing_before = self.env["mail.mail"].sudo().search_count([])
        reporter_message = task.with_user(self.reporter).message_post(
            body="Reporter evidence",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachments=[("report.txt", b"report")],
        )
        collaborator_message = task.with_user(self.other).message_post(
            body="I can reproduce this.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachments=[("reproduction.txt", b"reproduction")],
        )
        self.assertEqual(task.with_user(self.other).read(["name"])[0]["name"], task.name)
        self.assertTrue(reporter_message.with_user(self.other).read(["body"]))
        self.assertTrue(reporter_message.attachment_ids.with_user(self.other).read(["name"]))
        self.assertTrue(collaborator_message.with_user(self.reporter).read(["body"]))
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), outgoing_before)
        self.assertEqual(
            task.with_user(self.other)._message_get_suggested_recipients(
                reply_discussion=True,
                no_create=False,
            ),
            [],
        )
        grouped = self.env["project.task"].with_user(self.other).web_read_group(
            [("project_id.usl_feedback_project", "=", True)],
            ["stage_id"],
            ["__count"],
        )
        self.assertTrue(grouped["groups"])
        # Collaborator comments never queue the reporter's assistant.
        self.assertEqual(
            self.env["usl.feedback.agent.run"].sudo().search_count([("task_id", "=", task.id)]),
            1,
        )
        task.with_user(self.other).message_subscribe(partner_ids=[self.other.partner_id.id])
        with self.assertRaises(AccessError):
            task.with_user(self.other).message_subscribe(partner_ids=[self.maintainer.partner_id.id])
        reporter_follower = task.message_follower_ids.filtered(
            lambda follower: follower.partner_id == self.reporter.partner_id,
        )
        with self.assertRaises(AccessError):
            reporter_follower.with_user(self.other).unlink()
        with self.assertRaises(AccessError):
            self.env["mail.followers"].with_user(self.other).create(
                {
                    "res_model": "project.task",
                    "res_id": task.id,
                    "partner_id": self.maintainer.partner_id.id,
                },
            )
        with self.assertRaises(AccessError):
            reporter_message.attachment_ids.with_user(self.other).write({"name": "forged.txt"})
        with self.assertRaises(AccessError):
            reporter_message.attachment_ids.with_user(self.other).unlink()

    def test_only_reporter_or_maintainer_can_drive_agent_rpc(self):
        task, _payload = self._submit()
        with self.assertRaises(AccessError):
            task.with_user(self.other).feedback_poll_agent()
        self.assertTrue(task.with_user(self.maintainer).feedback_poll_agent())

    def test_shared_board_uses_native_project_stage_expansion(self):
        self.reporter.lang = "fr_FR"
        action = (
            self.env["project.project"]
            .with_user(self.reporter)
            .with_context(lang=self.reporter.lang)
            .feedback_open_board()
        )
        self.assertEqual(
            action["id"], self.env.ref("usl_feedback.action_feedback_collaborator").id,
        )
        self.assertTrue(action["context"]["project_kanban"])
        self.assertFalse(action["context"]["create"])
        self.assertFalse(action["context"]["edit"])
        self.assertEqual(action["name"], "Retours produit")
        self.assertEqual(action["domain"], [("project_id", "=", self.project.id)])
        self.assertNotIn(
            "active_id",
            self.env.ref("usl_feedback.action_feedback_collaborator").context,
        )
        collaborator_arch = self.env.ref(
            "usl_feedback.view_feedback_task_kanban_collaborator",
        ).arch
        self.assertIn('records_draggable="false"', collaborator_arch)
        self.assertIn('group_create="false"', collaborator_arch)
        collaborator_form_arch = self.env.ref(
            "usl_feedback.view_feedback_task_form_collaborator",
        ).arch
        self.assertIn('name="project_id" invisible="1"', collaborator_form_arch)
        maintainer_action = (
            self.env["project.project"].with_user(self.maintainer).feedback_open_board()
        )
        self.assertEqual(
            maintainer_action["id"], self.env.ref("usl_feedback.action_feedback_maintainer").id,
        )
        self.assertTrue(maintainer_action["context"]["edit"])

    def test_upgrade_is_idempotent_and_removes_private_workflow_artifacts(self):
        def register_xmlid(record, name):
            self.env["ir.model.data"].sudo().create(
                {
                    "module": "usl_feedback",
                    "name": name,
                    "model": record._name,
                    "res_id": record.id,
                    "noupdate": True,
                },
            )

        def legacy_record(name, model, values):
            record = self.env.ref(f"usl_feedback.{name}", raise_if_not_found=False)
            if record:
                return record
            record = self.env[model].sudo().create(values)
            register_xmlid(record, name)
            return record

        task, _payload = self._submit()
        attachment = task.with_user(self.reporter).message_post(
            body="Preserve this upgrade evidence.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachments=[("upgrade-evidence.txt", b"preserve")],
        ).attachment_ids
        task_id = task.id
        stage_id = task.stage_id.id
        message_ids = task.message_ids.ids
        follower_ids = task.message_follower_ids.ids
        attachment_ids = attachment.ids

        obsolete_xmlids = []
        for name in ("action_feedback_submission", "action_my_feedback"):
            legacy_record(
                name,
                "ir.actions.act_window",
                {"name": name, "res_model": "project.task"},
            )
            obsolete_xmlids.append(name)
        for name, model, arch in (
            ("view_feedback_submission_form", "usl.feedback.submission", "<form/>"),
            ("view_feedback_task_reporter_list", "project.task", "<list/>"),
            ("view_feedback_task_reporter_form", "project.task", "<form/>"),
            ("view_feedback_task_maintainer_list", "project.task", "<list/>"),
            ("view_feedback_task_maintainer_form", "project.task", "<form/>"),
        ):
            legacy_record(
                name,
                "ir.ui.view",
                {"name": name, "model": model, "arch": arch},
            )
            obsolete_xmlids.append(name)
        for name, model_id in (
            ("rule_feedback_project_boundary", self.env["ir.model"]._get_id("project.project")),
            ("rule_feedback_task_boundary", self.env["ir.model"]._get_id("project.task")),
        ):
            legacy_record(
                name,
                "ir.rule",
                {
                    "name": name,
                    "model_id": model_id,
                    "domain_force": "[('id', '=', -1)]",
                },
            )
            obsolete_xmlids.append(name)

        self.env.flush_all()
        self.env.cr.execute(
            """
                UPDATE project_project
                   SET company_id = %s,
                       privacy_visibility = 'followers',
                       user_id = %s
                 WHERE id = %s
            """,
            [self.company_a.id, self.maintainer.id, self.project.id],
        )
        self.project.invalidate_recordset(
            ["company_id", "privacy_visibility", "user_id"],
            flush=False,
        )
        self.env.cr.execute(
            """
                UPDATE project_task
                   SET company_id = %s,
                       usl_feedback_company_id = NULL,
                       usl_feedback_agent_state = NULL
                 WHERE id = %s
            """,
            [self.company_a.id, task.id],
        )
        task.invalidate_recordset(
            ["company_id", "usl_feedback_company_id", "usl_feedback_agent_state"],
            flush=False,
        )
        feedback_tags = self.env["project.tags"].sudo().browse(
            [
                self.env.ref(f"usl_feedback.tag_feedback_{name}").id
                for name in ("bug", "improvement", "question", "ux")
            ],
        )
        feedback_tags.write({"usl_feedback_tag": False})

        migration_path = (
            Path(get_module_path("usl_feedback"))
            / "migrations"
            / "saas~19.3.2.0.0"
            / "post-migrate.py"
        )
        spec = importlib.util.spec_from_file_location("usl_feedback_post_migrate", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def semantic_snapshot():
            migrated_task = self.env["project.task"].sudo().browse(task_id)
            return {
                "project": (
                    self.project.company_id.id,
                    self.project.privacy_visibility,
                    self.project.user_id.id,
                ),
                "task": (
                    migrated_task.id,
                    migrated_task.company_id.id,
                    migrated_task.usl_feedback_company_id.id,
                    migrated_task.usl_feedback_agent_state,
                    migrated_task.stage_id.id,
                    migrated_task.message_ids.ids,
                    migrated_task.message_follower_ids.ids,
                    migrated_task.message_ids.attachment_ids.ids,
                ),
                "stages": [
                    (stage.id, stage.name, stage.sequence, stage.fold)
                    for stage in self.env["project.task.type"].sudo().search(
                        [("project_ids", "in", self.project.id)],
                        order="sequence,id",
                    )
                ],
                "tags": [
                    (tag.id, tag.name, tag.color, tag.usl_feedback_tag)
                    for tag in feedback_tags.sorted("id")
                ],
            }

        migration.migrate(self.env.cr, "saas~19.3.1.0.0")
        self.env.invalidate_all()
        first = semantic_snapshot()
        migration.migrate(self.env.cr, "saas~19.3.2.0.0")
        self.env.invalidate_all()
        second = semantic_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(first["project"], (False, "employees", False))
        self.assertEqual(first["task"][:5], (task_id, False, self.company_a.id, "waiting", stage_id))
        self.assertEqual(first["task"][5], message_ids)
        self.assertEqual(first["task"][6], follower_ids)
        self.assertEqual(first["task"][7], attachment_ids)
        self.assertTrue(all(tag[3] for tag in first["tags"]))
        for xmlid in obsolete_xmlids:
            self.assertFalse(
                self.env.ref(f"usl_feedback.{xmlid}", raise_if_not_found=False),
                xmlid,
            )

    def test_screenshot_and_attachment_ownership_are_validated(self):
        draft = self._start()
        screenshot = draft.feedback_add_attachment(
            "screen.jpg",
            "image/jpeg",
            base64.b64encode(b"synthetic screenshot"),
            True,
        )
        draft.feedback_add_attachment(
            "trace.txt", "text/plain", base64.b64encode(b"trace"), False,
        )
        payload = draft.feedback_submit_initial("The screenshot shows the issue.", False)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertEqual(task.usl_feedback_screenshot_attachment_id.id, screenshot["id"])
        self.assertEqual(task.usl_feedback_screenshot_attachment_id.res_model, "project.task")
        self.assertEqual(task.usl_feedback_screenshot_attachment_id.create_uid, self.reporter)
        self.assertEqual(len(task.message_ids.attachment_ids), 2)
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        interaction_payload, _input_hash = run._build_payload(
            {
                "model": "gemini-3.7-flash",
                "mcp_key": "redacted-test-key",
                "mcp_url": "http://localhost:3000/mcp/projects",
            },
        )
        self.assertEqual(
            [item["type"] for item in interaction_payload["input"]],
            ["text", "image"],
        )
        self.assertEqual(interaction_payload["input"][1]["mime_type"], "image/jpeg")
        self.assertEqual(
            base64.b64decode(interaction_payload["input"][1]["data"]),
            b"synthetic screenshot",
        )
        other_attachment = self.env["ir.attachment"].with_user(self.other).sudo().create(
            {"name": "other.txt", "raw": b"other", "res_model": draft._name, "res_id": draft.id},
        )
        replacement = self._start()
        replacement.sudo().write({"attachment_ids": [Command.link(other_attachment.id)]})
        with self.assertRaises(AccessError):
            replacement.feedback_submit_initial("Forged attachment ownership.", False)

    def test_reporters_cannot_mutate_cards_or_activities(self):
        task, _payload = self._submit()
        with self.assertRaises(AccessError):
            task.with_user(self.reporter).write({"name": "Forged title"})
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(self.reporter).create(
                {
                    "name": "Forged card",
                    "project_id": self.project.id,
                    "usl_feedback_reporter_id": self.reporter.id,
                },
            )
        with self.assertRaises(AccessError):
            task.with_user(self.reporter).unlink()
        with self.assertRaises(AccessError):
            self.env["mail.activity"].with_user(self.reporter).create(
                {
                    "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                    "summary": "Forge workflow",
                    "user_id": self.reporter.id,
                    "res_model_id": self.env["ir.model"]._get_id("project.task"),
                    "res_id": task.id,
                },
            )

    def test_feedback_metadata_is_governed_even_for_generic_project_managers(self):
        task, _payload = self._submit()
        stage = self.env.ref("usl_feedback.stage_feedback_new")
        tag = self.env.ref("usl_feedback.tag_feedback_bug")
        with self.assertRaises(AccessError):
            self.project.with_user(self.project_manager).write({"name": "Forged project"})
        with self.assertRaises(AccessError):
            stage.with_user(self.project_manager).write({"name": "Forged stage"})
        with self.assertRaises(AccessError):
            tag.with_user(self.project_manager).write({"name": "Forged tag"})
        import_result = self.env["project.task"].with_user(self.project_manager).load(
            ["name", "project_id/.id"],
            [["Forged import", str(self.project.id)]],
        )
        self.assertTrue(import_result["messages"])
        self.assertFalse(
            self.env["project.task"].sudo().search(
                [("name", "=", "Forged import"), ("project_id", "=", self.project.id)],
            ),
        )
        with self.assertRaises(ValidationError):
            task.with_user(self.maintainer).write(
                {"stage_id": self.env.ref("usl_feedback.stage_feedback_triaged").id},
            )

        stage.with_user(self.maintainer).write({"sequence": stage.sequence + 1})
        tag.with_user(self.maintainer).write({"color": tag.color + 1})
        self.assertTrue(stage.sequence)
        self.assertTrue(tag.usl_feedback_tag)

        same_name = self.env["project.tags"].sudo().create({"name": "Bug"})
        self.assertNotIn(
            same_name,
            self.env["project.tags"].with_user(self.agent).search([("name", "=", "Bug")]),
        )

    def test_maintainer_operates_and_service_identity_is_strictly_read_only(self):
        task, _payload = self._submit()
        task.with_user(self.maintainer).write(
            {
                "usl_feedback_category": "bug",
                "stage_id": self.env.ref("usl_feedback.stage_feedback_triaged").id,
            },
        )
        self.assertEqual(task.stage_id.name, "Triage")
        self.assertEqual(task.with_user(self.agent).read(["name"])[0]["name"], task.name)
        feedback_message = task.message_ids.filtered(lambda message: message.message_type == "comment")[:1]
        feedback_attachment = feedback_message.attachment_ids
        activity = self.env["mail.activity"].with_user(self.maintainer).create(
            {
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Triage feedback",
                "user_id": self.maintainer.id,
                "res_model_id": self.env["ir.model"]._get_id("project.task"),
                "res_id": task.id,
            },
        )
        self.assertTrue(feedback_message.with_user(self.agent).read(["body"]))
        if feedback_attachment:
            self.assertTrue(feedback_attachment.with_user(self.agent).read(["name"]))
        self.assertTrue(activity.with_user(self.agent).read(["summary"]))
        with self.assertRaises(AccessError):
            task.with_user(self.agent).write({"name": "Service mutation"})
        with self.assertRaises(AccessError):
            task.with_user(self.agent).message_post(body="Service comment")
        regular = self.env["project.project"].sudo().create(
            {"name": "Unrelated project", "privacy_visibility": "employees"},
        )
        regular_task = self.env["project.task"].sudo().create(
            {"name": "Unrelated task", "project_id": regular.id},
        )
        regular_message = regular_task.sudo().message_post(
            body="Unrelated project message", message_type="comment",
        )
        regular_attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "unrelated.txt",
                "raw": b"unrelated",
                "res_model": "project.task",
                "res_id": regular_task.id,
            },
        )
        regular_activity = self.env["mail.activity"].sudo().create(
            {
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Unrelated project activity",
                "user_id": self.maintainer.id,
                "res_model_id": self.env["ir.model"]._get_id("project.task"),
                "res_id": regular_task.id,
            },
        )
        self.assertFalse(
            self.env["project.project"].with_user(self.agent).search([("id", "=", regular.id)]),
        )
        self.assertFalse(
            self.env["project.task"].with_user(self.agent).search(
                [("id", "=", regular_task.id)],
            ),
        )
        self.assertFalse(
            self.env["mail.message"].with_user(self.agent).search([("id", "=", regular_message.id)]),
        )
        self.assertFalse(
            self.env["ir.attachment"].with_user(self.agent).search(
                [("id", "=", regular_attachment.id)],
            ),
        )
        self.assertFalse(
            self.env["mail.activity"].with_user(self.agent).search(
                [("id", "=", regular_activity.id)],
            ),
        )

    def test_feedback_agent_cannot_become_an_internal_or_maintainer_user(self):
        with self.assertRaises(ValidationError):
            self.agent.sudo().write(
                {"group_ids": [Command.link(self.env.ref("base.group_user").id)]},
            )
        with self.assertRaises(ValidationError):
            self.agent.sudo().write(
                {
                    "group_ids": [
                        Command.link(self.env.ref("usl_feedback.group_feedback_maintainer").id),
                    ],
                },
            )

    def test_shared_board_preserves_source_company_without_cross_company_business_access(self):
        task_a, _payload = self._submit(company=self.company_a, message="Company A report")
        task_b, _payload = self._submit(company=self.company_b, message="Company B report")
        tasks = task_a | task_b
        visible_a = self.env["project.task"].with_user(self.reporter).with_context(
            allowed_company_ids=[self.company_a.id],
        ).search([("id", "in", tasks.ids)])
        visible_b = self.env["project.task"].with_user(self.reporter).with_context(
            allowed_company_ids=[self.company_b.id],
        ).search([("id", "in", tasks.ids)])
        self.assertEqual(visible_a, tasks)
        self.assertEqual(visible_b, tasks)
        self.assertEqual(task_a.usl_feedback_company_id, self.company_a)
        self.assertEqual(task_b.usl_feedback_company_id, self.company_b)

        private_project = self.env["project.project"].sudo().create(
            {
                "name": "Company B private source",
                "company_id": self.company_b.id,
                "privacy_visibility": "followers",
            },
        )
        private_task = self.env["project.task"].sudo().create(
            {
                "name": "Company B confidential record",
                "project_id": private_project.id,
                "company_id": self.company_b.id,
                "user_ids": [Command.set(self.company_b_user.ids)],
            },
        )
        feedback, _payload = self._submit(
            user=self.company_b_user,
            company=self.company_b,
            message="Feedback from a protected Company B record",
            model="project.task",
            res_id=private_task.id,
        )
        shared_feedback = feedback.with_user(self.company_a_user).with_context(
            allowed_company_ids=[self.company_a.id],
        )
        self.assertEqual(shared_feedback.read(["name"])[0]["name"], feedback.name)
        self.assertEqual(shared_feedback.usl_feedback_company_id, self.company_b)
        self.assertEqual(shared_feedback.usl_feedback_source_res_id, private_task.id)
        with self.assertRaises(AccessError):
            private_task.with_user(self.company_a_user).with_context(
                allowed_company_ids=[self.company_a.id],
            ).read(["name"])

    def test_stateful_agent_clarifies_updates_and_waits_for_human_confirmation(self):
        self.reporter.lang = "fr_FR"
        task, _payload = self._submit(message="The workflow is confusing.")
        # Submission and assistant processing are separate RPC transactions in
        # production. Finalize the create transaction so later assistant writes
        # produce the same native tracking messages as the live conversation.
        self.env.flush_all()
        self.env.cr.precommit.run()
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "usl_feedback.gemini_enabled": "True",
            "usl_feedback.gemini_paid_tier_confirmed": "True",
            "usl_feedback.gemini_api_key": "gemini-secret",
            "usl_feedback.mcp_api_key": "odoo-secret",
            "usl_feedback.mcp_url": "http://localhost:3000/mcp/projects",
            "usl_feedback.gemini_model": "gemini-3.7-flash",
            "web.base.url": "http://odoo.test",
        }.items():
            params.set_str(key, value)
        clarification = {
            "status": "needs_clarification",
            "assistant_message": "Which workflow step was unclear?",
            "questions": ["What did you expect to happen?"],
            "summary": "Clarify the workflow status",
            "description": "The reporter finds a workflow status unclear.",
            "category": "ux",
            "priority": 1,
            "related_feedback_ids": [],
        }
        ready = {
            **clarification,
            "status": "ready_for_confirmation",
            "assistant_message": "I have prepared the feedback brief for confirmation.",
            "questions": [],
            "summary": "Clarify status after workflow reload",
            "description": "After a reload, the status does not explain the next available action.",
            "priority": 2,
        }
        responses = [
            interaction_response("interaction-one", clarification, input_tokens=120, output_tokens=42),
            interaction_response("interaction-two", ready, input_tokens=80, output_tokens=31),
        ]
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.create_interaction",
            side_effect=responses,
        ) as create_interaction:
            self.env["usl.feedback.agent.run"].sudo()._process_task(task)
            self.assertEqual(task.usl_feedback_agent_state, "waiting")
            task.with_user(self.reporter).message_post(
                body="The next action disappears after reload.",
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            self.env["usl.feedback.agent.run"].sudo()._process_task(task)
        second_payload = create_interaction.call_args_list[1].args[0]
        self.assertEqual(second_payload["previous_interaction_id"], "interaction-one")
        self.assertEqual(second_payload["input"][0]["type"], "text")
        self.assertNotIn("role", second_payload["input"][0])
        first_payload = create_interaction.call_args_list[0].args[0]
        self.assertEqual([item["type"] for item in first_payload["input"]], ["text"])
        completed_run = self.env["usl.feedback.agent.run"].sudo().search(
            [("external_interaction_id", "=", "interaction-two")], limit=1,
        )
        self.assertEqual(completed_run.input_token_count, 80)
        self.assertEqual(completed_run.output_token_count, 31)
        assistant_messages = task.message_ids.filtered(
            lambda message: message.author_id
            == self.env.ref("usl_feedback.partner_feedback_assistant"),
        )
        self.assertEqual(len(assistant_messages), 2)
        self.assertFalse(self.env["res.users"].sudo().search(
            [("partner_id", "=", assistant_messages[:1].author_id.id)],
        ))
        self.assertEqual(task.usl_feedback_agent_state, "ready")
        self.assertEqual(task.name, "Clarify status after workflow reload")
        self.assertEqual(task.usl_feedback_category, "ux")
        self.assertEqual(task.stage_id, self.env.ref("usl_feedback.stage_feedback_new"))
        # Mail tracking is finalized by Odoo's pre-commit callback. Run it here
        # so this transaction-level assertion observes the persisted chatter.
        self.env.flush_all()
        self.env.cr.precommit.run()
        task.invalidate_recordset(["message_ids"])
        tracking_html = "\n".join(
            str(body)
            for body in task.message_ids.filtered(
                lambda message: message.message_type == "tracking",
            ).mapped("body")
        )
        self.assertIn("(Priorité)", tracking_html)
        self.assertIn("(Titre)", tracking_html)
        self.assertNotIn("(Priority)", tracking_html)
        task.with_user(self.reporter).feedback_confirm_triage()
        self.assertEqual(task.stage_id, self.env.ref("usl_feedback.stage_feedback_triaged"))
        self.assertEqual(task.usl_feedback_agent_state, "triaged")

    def test_provider_configuration_failure_keeps_partial_task_and_safe_error(self):
        self.reporter.lang = "fr_FR"
        task, _payload = self._submit(message="Persist this before the provider call.")
        self.env["ir.config_parameter"].sudo().set_str("usl_feedback.gemini_enabled", "False")
        self.env["usl.feedback.agent.run"].sudo()._process_task(task)
        self.assertTrue(task.exists())
        self.assertEqual(task.stage_id, self.env.ref("usl_feedback.stage_feedback_new"))
        self.assertEqual(task.usl_feedback_agent_state, "error")
        self.assertNotIn("key", (task.usl_feedback_agent_error or "").lower())
        self.assertEqual(
            task.usl_feedback_agent_error,
            "L’assistant de retours n’a pas pu terminer cette étape. "
            "Votre retour est enregistré.",
        )
        task.with_user(self.reporter).feedback_retry_agent()
        self.assertEqual(task.usl_feedback_agent_state, "queued")

    def test_expired_state_rebuilds_from_bounded_chatter_once(self):
        task, _payload = self._submit(message="The stored conversation should recover.")
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "usl_feedback.gemini_enabled": "True",
            "usl_feedback.gemini_paid_tier_confirmed": "True",
            "usl_feedback.gemini_api_key": "gemini-secret",
            "usl_feedback.mcp_api_key": "odoo-secret",
            "usl_feedback.mcp_url": "http://localhost:3000/mcp/projects",
            "usl_feedback.gemini_model": "gemini-3.7-flash",
        }.items():
            params.set_str(key, value)
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.create_interaction",
            return_value={"id": "interaction-expiring", "status": "in_progress"},
        ):
            run._process_one()
        run.next_poll_at = False
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.get_interaction",
            return_value={"id": "interaction-expiring", "status": "expired"},
        ):
            run._process_one()
        replacement = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        self.assertEqual(run.state, "stale")
        self.assertTrue(replacement.reconstructed_from_expiry)
        self.assertFalse(replacement.previous_interaction_id)
        payload, input_hash = replacement._build_payload(replacement._configuration())
        self.assertTrue(input_hash)
        self.assertNotIn("previous_interaction_id", payload)

    def test_mismatched_provider_result_is_rejected_as_stale(self):
        task, _payload = self._submit(message="Reject a stale provider result.")
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        run.write(
            {
                "state": "submitted",
                "external_interaction_id": "expected-interaction",
                "submitted_at": run.queued_at,
            },
        )
        result = {
            "id": "different-interaction",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "status": "ready_for_confirmation",
                    "assistant_message": "Ready",
                    "questions": [],
                    "summary": "This must not be applied",
                    "description": "Stale result",
                    "category": "bug",
                    "priority": 1,
                    "related_feedback_ids": [],
                },
            ),
        }
        self.assertFalse(run._complete(result))
        self.assertEqual(run.state, "stale")
        self.assertNotEqual(task.name, "This must not be applied")

    def test_agent_run_audit_is_read_only_even_for_technical_admin(self):
        task, _payload = self._submit()
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        self.assertTrue(run.with_user(self.technical_admin).read(["state"]))
        with self.assertRaises(AccessError):
            run.with_user(self.technical_admin).write({"error_detail": "tampered"})
        with self.assertRaises(AccessError):
            run.with_user(self.technical_admin).unlink()

    def test_verified_release_identity_is_required(self):
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", None)
        draft = self._start()
        with patch.dict("os.environ", {"USL_RELEASE_COMMIT": "unverified"}):
            with self.assertRaises(UserError):
                draft.feedback_submit_initial("A release identity is mandatory.", False)

    def test_settings_secrets_are_write_only_and_connection_is_end_to_end(self):
        settings = self.env["res.config.settings"].create(
            {
                "feedback_gemini_model": "gemini-3.7-flash",
                "feedback_mcp_url": "http://localhost:3000/mcp/projects",
                "feedback_gemini_api_key_input": "gemini-secret",
                "feedback_mcp_api_key_input": "odoo-secret",
            },
        )
        settings.set_values()
        self.assertFalse(settings.feedback_gemini_api_key_input)
        self.assertFalse(settings.feedback_mcp_api_key_input)
        fresh = self.env["res.config.settings"].create({})
        self.assertFalse(fresh.feedback_gemini_api_key_input)
        self.assertFalse(fresh.feedback_mcp_api_key_input)
        response = interaction_response(
            "connection-test",
            {
                "project_name": "Odoo Product Feedback",
                "read_only_verified": True,
            },
        )
        with (
            patch(
                "odoo.addons.usl_feedback.models.res_config_settings.requests.post",
            ) as mcp_initialize,
            patch(
                "odoo.addons.usl_feedback.services.gemini.GeminiClient.test_model",
            ),
            patch(
                "odoo.addons.usl_feedback.services.gemini.GeminiClient.create_interaction",
                return_value=response,
            ) as create_interaction,
        ):
            mcp_initialize.return_value.raise_for_status.return_value = None
            result = fresh.action_test_feedback_agent()
        self.assertEqual(result["params"]["type"], "success")
        self.assertEqual(fresh.feedback_connection_status, "ready")
        payload = create_interaction.call_args.args[0]
        self.assertEqual(payload["tools"][0]["url"], "http://localhost:3000/mcp/projects")
        self.assertEqual(
            payload["tools"][0]["headers"]["X-Odoo-Api-Key"], "odoo-secret",
        )
        self.assertNotIn("gemini-secret", json.dumps(payload))

    def test_concurrent_reporter_turn_is_queued_after_active_run(self):
        task, _payload = self._submit(message="First turn")
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        run.write(
            {
                "state": "submitted",
                "external_interaction_id": "active-turn",
                "submitted_at": run.queued_at,
            },
        )
        second_message = task.with_user(self.reporter).message_post(
            body="Second turn while the first is processing",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        task.invalidate_recordset(["usl_feedback_pending_message_id"])
        self.assertEqual(task.usl_feedback_pending_message_id.id, second_message.id)
        result = {
            "id": "active-turn",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "status": "needs_clarification",
                    "assistant_message": "I need one more detail.",
                    "questions": ["What changed?"],
                    "summary": "Concurrent feedback",
                    "description": "A second detail arrived during processing.",
                    "category": "bug",
                    "priority": 1,
                    "related_feedback_ids": [],
                },
            ),
        }
        self.assertTrue(run._complete(result))
        queued = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        self.assertEqual(queued.request_message_id.id, second_message.id)

    def test_claimed_agent_run_is_not_processed_twice(self):
        task, _payload = self._submit(message="Only one worker may process this turn.")
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        with (
            patch.object(type(run), "_claim_for_processing", return_value=False),
            patch(
                "odoo.addons.usl_feedback.models.feedback_agent_run."
                "GeminiClient.create_interaction",
            ) as create_interaction,
        ):
            self.assertFalse(run._process_one())
        create_interaction.assert_not_called()
        self.assertEqual(run.state, "queued")
