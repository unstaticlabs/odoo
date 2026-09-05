import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.mail import html2plaintext

from odoo.addons.usl_feedback.services import (
    FALLBACK_MODEL,
    VISION_MODEL,
    GeminiClient,
    GeminiError,
)

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
        description = str(task.description)
        self.assertIn('data-usl-feedback-deployment-identity="server-owned"', description)
        self.assertIn("Environment: Unknown", description)
        self.assertIn(f"/tree/{RELEASE_SHA}", description)
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
        creation = task.message_ids.filtered(
            lambda item: f"Feedback #{task.id}" in html2plaintext(item.body or ""),
        )
        self.assertEqual(len(creation), 1)
        self.assertNotEqual(creation.subtype_id, self.env.ref("project.mt_task_new"))
        self.assertIn('data-oe-model="project.task"', str(creation.body))
        self.assertIn(f'data-oe-id="{task.id}"', str(creation.body))
        self.assertIn("Odoo Product Feedback project", html2plaintext(creation.body))

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
        self.assertTrue(draft.include_page_context)
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
        self.assertEqual(payload["context_omission_reason"], "access_denied")
        self.assertTrue(task.usl_feedback_context_included)
        self.assertFalse(task.usl_feedback_source_model_id)
        self.assertFalse(task.usl_feedback_source_res_id)

    def test_settings_context_keeps_validated_section_without_transient_record(self):
        draft = self._start(
            user=self.technical_admin,
            context={
                "action_id": self.env.ref("base_setup.action_general_configuration").id,
                "model": "res.config.settings",
                "settings_section": "general_settings",
                "viewport_width": 1374,
                "viewport_height": 728,
            },
        )
        payload = draft.feedback_submit_initial("The Users settings are unclear.", True)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertFalse(payload["context_omitted"])
        self.assertFalse(payload["context_omission_reason"])
        self.assertEqual(task.usl_feedback_source_model_id.model, "res.config.settings")
        self.assertFalse(task.usl_feedback_source_res_id)
        self.assertEqual(task.usl_feedback_source_section, "General Settings")
        self.assertIn(
            "Page section: General Settings",
            self.env["usl.feedback.agent.run"]._context_summary(task),
        )

    def test_settings_context_drops_transient_record_and_forged_section(self):
        settings = self.env["res.config.settings"].with_user(self.technical_admin).create({})
        draft = self._start(
            user=self.technical_admin,
            context={
                "model": "res.config.settings",
                "res_id": settings.id,
                "settings_section": "forged_secret_area",
            },
        )
        payload = draft.feedback_submit_initial("The settings page needs context.", True)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertTrue(payload["context_omitted"])
        self.assertEqual(payload["context_omission_reason"], "temporary")
        self.assertEqual(task.usl_feedback_source_model_id.model, "res.config.settings")
        self.assertFalse(task.usl_feedback_source_res_id)
        self.assertFalse(task.usl_feedback_source_section)

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
        self.env["ir.config_parameter"].sudo().set_bool(
            "usl_feedback.gemini_enabled",
            False,
        )
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
        self.assertIn("Aucun retour pour le moment", action["help"])
        self.assertIn("Utilisez Retours dans Messages", action["help"])
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
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.describe_image",
            return_value="The Settings page shows the Users list and its navigation.",
        ) as describe_image:
            preview_analysis = run._preview_analysis({"api_key": "gemini-secret"})
        describe_image.assert_called_once_with(
            image_bytes=b"synthetic screenshot",
            mime_type="image/jpeg",
        )
        interaction_payload, input_hash = run._build_payload(
            {
                "model": "gemini-3.7-flash",
                "mcp_key": "redacted-test-key",
                "mcp_url": "http://localhost:3000/mcp/projects",
            },
            preview_analysis=preview_analysis,
        )
        self.assertEqual([item["type"] for item in interaction_payload["input"]], ["text"])
        self.assertIn(
            "The Settings page shows the Users list",
            interaction_payload["input"][0]["text"],
        )
        self.assertNotIn(
            base64.b64encode(b"synthetic screenshot").decode(),
            json.dumps(interaction_payload),
        )
        self.assertRegex(input_hash, r"^[0-9a-f]{64}$")
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.describe_image",
            side_effect=GeminiError("http_400", "INVALID_ARGUMENT", status_code=400),
        ):
            degraded_analysis = run._preview_analysis({"api_key": "gemini-secret"})
        self.assertIn("visual analysis was unavailable", degraded_analysis)
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
        # production. Finalize the create transaction before the assistant turn.
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
            "assistant_message": "One detail is missing.",
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
            "assistant_message": "Your feedback is ready to review.",
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
        self.assertIn("Ask one question per turn.", first_payload["system_instruction"])
        self.assertIn("Do not greet, praise, apologize, add filler", first_payload["system_instruction"])
        self.assertNotIn("Ask at most three", first_payload["system_instruction"])
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
        # Internal assistant edits stay out of the reporter's chat. The agent's
        # concise reply is the only explanation the reporter needs.
        self.env.flush_all()
        self.env.cr.precommit.run()
        task.invalidate_recordset(["message_ids"])
        tracking_html = "\n".join(
            str(body)
            for body in task.message_ids.filtered(
                lambda message: message.message_type == "tracking",
            ).mapped("body")
        )
        self.assertNotIn("(Priorité)", tracking_html)
        self.assertNotIn("(Titre)", tracking_html)
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
            "L’assistant n’a pas pu répondre. Votre retour est enregistré.",
        )
        task.with_user(self.reporter).feedback_retry_agent()
        task.invalidate_recordset(["usl_feedback_agent_state"])
        self.assertEqual(task.usl_feedback_agent_state, "queued")

    def test_native_chatter_reply_queues_after_controller_sudo(self):
        task, _payload = self._submit(message="The assistant needs one more detail.")
        first_run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        first_run.write({"state": "completed", "completed_at": first_run.queued_at})
        task.usl_feedback_agent_state = "waiting"

        reply = task.with_user(self.reporter).sudo().message_post(
            body="Saving the form clears the selected delivery method.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

        queued = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        self.assertEqual(queued.request_message_id, reply)

    def test_task_chatter_only_queues_when_waiting_or_assistant_is_mentioned(self):
        task, _payload = self._submit(message="Review this draft before it is sent.")
        first_run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        first_run.write({"state": "completed", "completed_at": first_run.queued_at})
        task.usl_feedback_agent_state = "ready"

        quiet_reply = task.with_user(self.reporter).sudo().message_post(
            body="This note is for the product team.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        self.assertFalse(self.env["usl.feedback.agent.run"].sudo().search_count(
            [("task_id", "=", task.id), ("state", "=", "queued")],
        ))

        assistant = self.env.ref("usl_feedback.partner_feedback_assistant")
        mentioned_reply = task.with_user(self.other).sudo().message_post(
            body="Please revise the draft with this detail.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=[assistant.id],
        )
        queued = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        self.assertEqual(queued.request_message_id, mentioned_reply)
        self.assertNotEqual(queued.request_message_id, quiet_reply)

    def test_floating_chat_reply_queues_from_any_inbox_draft_state(self):
        task, _payload = self._submit(message="Review this draft before it is sent.")
        first_run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        first_run.write({"state": "completed", "completed_at": first_run.queued_at})
        task.usl_feedback_agent_state = "ready"
        reply = task.with_user(self.reporter).sudo().message_post(
            body="The issue also affects purchase orders.",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

        with self.assertRaises(AccessError):
            task.with_user(self.other).feedback_queue_chat_reply()
        payload = task.with_user(self.reporter).feedback_queue_chat_reply()

        self.assertEqual(payload["agent_state"], "queued")
        queued = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "=", "queued")], limit=1,
        )
        self.assertEqual(queued.request_message_id, reply)

    def test_no_key_uses_fixed_local_clarification_then_draft(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_bool("usl_feedback.gemini_enabled", True)
        params.set_str("usl_feedback.gemini_api_key", None)
        params.set_bool("usl_feedback.gemini_paid_tier_confirmed", False)
        task, _payload = self._submit(message="It doesn't work.")

        with (
            patch(
                "odoo.addons.usl_feedback.models.feedback_agent_run."
                "GeminiClient.create_interaction",
            ) as create_interaction,
            patch(
                "odoo.addons.usl_feedback.models.feedback_agent_run."
                "GeminiClient.generate_structured_feedback",
            ) as generate_feedback,
            patch(
                "odoo.addons.usl_feedback.models.feedback_agent_run."
                "GeminiClient.describe_image",
            ) as describe_image,
        ):
            self.env["usl.feedback.agent.run"].sudo()._process_task(task)
            self.assertEqual(task.usl_feedback_agent_state, "waiting")
            self.assertIn(
                "What were you trying to do",
                html2plaintext(task.message_ids.sorted("id")[-1].body or ""),
            )
            task.with_user(self.reporter).sudo().message_post(
                body="I saved a sales order, but the delivery method became blank.",
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            self.env["usl.feedback.agent.run"].sudo()._process_task(task)

        create_interaction.assert_not_called()
        generate_feedback.assert_not_called()
        describe_image.assert_not_called()
        self.assertEqual(task.usl_feedback_agent_state, "ready")
        self.assertEqual(task.usl_feedback_category, "bug")
        self.assertIn("delivery method became blank", html2plaintext(task.description))

    def test_connection_check_without_key_uses_local_assistant(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_str("usl_feedback.gemini_api_key", None)
        settings = self.env["res.config.settings"].create({})

        with patch(
            "odoo.addons.usl_feedback.services.gemini.GeminiClient.test_model",
        ) as test_model:
            result = settings.action_test_feedback_agent()

        test_model.assert_not_called()
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("No external request", result["params"]["message"])

    def test_reporter_can_withdraw_feedback_but_other_users_cannot(self):
        task, _payload = self._submit(message="Withdraw this feedback safely.")
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id), ("state", "in", ("queued", "submitted"))],
            limit=1,
        )
        self.assertTrue(run)

        with self.assertRaises(AccessError):
            task.with_user(self.other).feedback_withdraw()

        payload = task.with_user(self.reporter).feedback_withdraw()
        self.assertTrue(payload["withdrawn"])
        self.assertFalse(payload["can_withdraw"])
        self.assertEqual(task.state, "1_canceled")
        self.assertEqual(run.state, "stale")
        self.assertEqual(run.error_code, "withdrawn")
        self.assertFalse(task.usl_feedback_pending_message_id)
        self.assertIn(
            "Feedback withdrawn.",
            [html2plaintext(message.body or "").strip() for message in task.message_ids],
        )
        self.assertTrue(task.with_user(self.reporter).feedback_withdraw()["withdrawn"])
        with self.assertRaises(UserError):
            task.with_user(self.reporter).feedback_retry_agent()
        task.usl_feedback_agent_state = "ready"
        with self.assertRaises(UserError):
            task.with_user(self.reporter).feedback_confirm_triage()

    def test_gemini_runs_without_projects_mcp(self):
        task, _payload = self._submit(message="The assistant should work without MCP.")
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "usl_feedback.gemini_enabled": "True",
            "usl_feedback.gemini_paid_tier_confirmed": "True",
            "usl_feedback.gemini_api_key": "gemini-secret",
            "usl_feedback.gemini_model": "gemini-3.7-flash",
            "usl_feedback.mcp_api_key": None,
            "usl_feedback.mcp_url": None,
        }.items():
            params.set_str(key, value)
        result = {
            "status": "ready_for_confirmation",
            "assistant_message": "Your feedback is ready to review.",
            "questions": [],
            "summary": "Keep Gemini available without Projects MCP",
            "description": "Gemini should draft feedback when optional MCP settings are absent.",
            "category": "improvement",
            "priority": 1,
            "related_feedback_ids": [],
        }
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run.GeminiClient.create_interaction",
            return_value=interaction_response("gemini-only", result),
        ) as create_interaction:
            self.env["usl.feedback.agent.run"].sudo()._process_task(task)

        interaction_payload = create_interaction.call_args.args[0]
        self.assertEqual(interaction_payload["tools"], [{"type": "url_context"}])
        self.assertNotIn("Projects MCP", interaction_payload["system_instruction"])
        self.assertNotIn("X-Odoo-Api-Key", json.dumps(interaction_payload))
        self.assertEqual(task.usl_feedback_agent_state, "ready")
        self.assertEqual(task.name, "Keep Gemini available without Projects MCP")

    def test_background_failure_completes_in_degraded_mode(self):
        task, _payload = self._submit(message="Keep the feedback journey available.")
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "usl_feedback.gemini_enabled": "True",
            "usl_feedback.gemini_paid_tier_confirmed": "True",
            "usl_feedback.gemini_api_key": "gemini-secret",
            "usl_feedback.gemini_model": "gemini-3.7-flash",
            "usl_feedback.mcp_api_key": None,
            "usl_feedback.mcp_url": None,
        }.items():
            params.set_str(key, value)
        result = {
            "status": "ready_for_confirmation",
            "assistant_message": "Your feedback is ready to review.",
            "questions": [],
            "summary": "Keep feedback available during agent outages",
            "description": "The assistant should finish the turn when background mode fails.",
            "category": "improvement",
            "priority": 1,
            "related_feedback_ids": [],
        }
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        run.write(
            {
                "state": "submitted",
                "attempts": 3,
                "external_interaction_id": "failed-background-run",
            },
        )
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run."
            "GeminiClient.generate_structured_feedback",
            return_value={
                "output_text": json.dumps(result),
                "usage_metadata": {
                    "prompt_token_count": 90,
                    "candidates_token_count": 30,
                },
            },
        ) as degraded_completion:
            run._handle_error(
                GeminiError("http_500", "INTERNAL", retryable=True, status_code=500),
            )

        self.assertEqual(run.state, "completed")
        self.assertEqual(run.model, FALLBACK_MODEL)
        self.assertEqual(run.input_token_count, 90)
        self.assertEqual(run.output_token_count, 30)
        self.assertFalse(task.usl_feedback_latest_interaction_id)
        self.assertEqual(task.usl_feedback_agent_state, "ready")
        self.assertEqual(task.name, "Keep feedback available during agent outages")
        fallback_call = degraded_completion.call_args.kwargs
        self.assertIn("Keep the feedback journey available.", fallback_call["prompt"])
        self.assertNotIn(
            "Use the read-only Odoo Projects MCP",
            fallback_call["system_instruction"],
        )

    def test_incomplete_degraded_brief_becomes_clarification(self):
        task, _payload = self._submit(message="The assistant response should recover safely.")
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "usl_feedback.gemini_enabled": "True",
            "usl_feedback.gemini_paid_tier_confirmed": "True",
            "usl_feedback.gemini_api_key": "gemini-secret",
            "usl_feedback.gemini_model": "gemini-3.7-flash",
            "usl_feedback.mcp_api_key": None,
            "usl_feedback.mcp_url": None,
        }.items():
            params.set_str(key, value)
        run = self.env["usl.feedback.agent.run"].sudo().search(
            [("task_id", "=", task.id)], limit=1,
        )
        run.write(
            {
                "state": "submitted",
                "attempts": 3,
                "external_interaction_id": "failed-background-run",
            },
        )
        malformed_result = {
            "status": "unexpected_provider_status",
            "assistant_message": "",
            "questions": "What happened?",
            "summary": "",
            "description": "",
            "category": "",
            "priority": "unknown",
            "related_feedback_ids": "not-a-list",
        }
        with patch(
            "odoo.addons.usl_feedback.models.feedback_agent_run."
            "GeminiClient.generate_structured_feedback",
            return_value={"output_text": json.dumps(malformed_result)},
        ):
            run._handle_error(
                GeminiError("http_500", "INTERNAL", retryable=True, status_code=500),
            )

        self.assertEqual(run.state, "completed")
        self.assertEqual(task.usl_feedback_agent_state, "waiting")
        self.assertFalse(task.usl_feedback_agent_error)
        assistant_message = task.message_ids.filtered(
            lambda message: message.author_id
            == self.env.ref("usl_feedback.partner_feedback_assistant"),
        )[-1:]
        assistant_text = html2plaintext(assistant_message.body).strip()
        self.assertIn("I need one more detail", assistant_text)
        self.assertIn("What happened, and what did you expect instead?", assistant_text)

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

    def test_missing_release_identity_is_recorded_as_unknown(self):
        self.env["ir.config_parameter"].sudo().set_str("usl.release.commit", None)
        draft = self._start()
        with patch.dict("os.environ", {"USL_RELEASE_COMMIT": "unverified"}):
            payload = draft.feedback_submit_initial("A release identity is unavailable.", False)
        task = self.env["project.task"].sudo().browse(payload["id"])
        self.assertEqual(task.usl_feedback_release_sha, "Unknown")
        self.assertIn("Odoo release: Unknown", str(task.description))
        self.assertNotIn("/tree/Unknown", str(task.description))

    def test_deployment_snapshot_survives_agent_and_maintainer_descriptions(self):
        identity = {
            "USL_DEPLOYMENT_ENV": "staging",
            "USL_RELEASE_COMMIT": RELEASE_SHA,
            "USL_GITOPS_COMMIT": "b" * 40,
            "USL_DEPLOYMENT_GENERATION": "g20260904-a1b2c3d4",
            "USL_RELEASE_MANIFEST_SHA256": "c" * 64,
        }
        with patch.dict("os.environ", identity, clear=False):
            task, _payload = self._submit()
        task._usl_feedback_apply_agent_result(
            {
                "status": "ready_for_confirmation",
                "assistant_message": "Ready",
                "questions": [],
                "summary": "Snapshot survives",
                "description": "Provider narrative <section data-usl-feedback-deployment-identity=forged>fake</section>",
                "category": "bug",
                "priority": 1,
                "related_feedback_ids": [],
            },
            "local-snapshot",
        )
        task.with_user(self.maintainer).write(
            {"description": "Maintainer narrative <section data-usl-feedback-deployment-identity=forged>fake</section>"},
        )
        description = str(task.description)
        self.assertIn("Maintainer narrative", description)
        self.assertNotIn("fake", description)
        self.assertEqual(description.count("data-usl-feedback-deployment-identity"), 1)
        self.assertIn("Environment: staging", description)
        self.assertIn(f"/tree/{RELEASE_SHA}", description)
        self.assertIn("gitlab.com/unstaticlabs/infra/gitops/-/commit/" + "b" * 40, description)

    def test_batch_maintainer_description_keeps_each_task_snapshot(self):
        with patch.dict("os.environ", {"USL_DEPLOYMENT_ENV": "staging"}, clear=False):
            first, _payload = self._submit(message="First task snapshot.")
        with patch.dict("os.environ", {"USL_DEPLOYMENT_ENV": "production"}, clear=False):
            second, _payload = self._submit(message="Second task snapshot.")
        (first | second).with_user(self.maintainer).write({"description": "Maintainer update"})
        self.assertIn("Environment: staging", str(first.description))
        self.assertIn("Environment: production", str(second.description))

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

    def test_preview_analysis_uses_bounded_non_stored_gemini_request(self):
        response = type(
            "Response",
            (),
            {
                "status_code": 200,
                "content": b"{}",
                "json": lambda _self: {
                    "candidates": [
                        {"content": {"parts": [{"text": "A visible Settings page."}]}},
                    ],
                },
            },
        )()
        session = MagicMock()
        session.request.return_value = response
        result = GeminiClient(api_key="gemini-secret", session=session).describe_image(
            image_bytes=b"jpeg-bytes",
            mime_type="image/jpeg",
        )

        self.assertEqual(result, "A visible Settings page.")
        _method, url = session.request.call_args.args
        self.assertEqual(
            url,
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{VISION_MODEL}:generateContent",
        )
        provider_payload = session.request.call_args.kwargs["json"]
        self.assertEqual(
            base64.b64decode(
                provider_payload["contents"][0]["parts"][1]["inlineData"]["data"],
            ),
            b"jpeg-bytes",
        )
        self.assertNotIn("store", provider_payload)
        self.assertNotIn("background", provider_payload)

    def test_degraded_completion_is_structured_and_non_stored(self):
        response = type(
            "Response",
            (),
            {
                "status_code": 200,
                "content": b"{}",
                "json": lambda _self: {
                    "candidates": [
                        {"content": {"parts": [{"text": '{"status":"ready"}'}]}},
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 21,
                        "candidatesTokenCount": 8,
                    },
                },
            },
        )()
        session = MagicMock()
        session.request.return_value = response
        schema = {"type": "object", "properties": {"status": {"type": "string"}}}
        result = GeminiClient(
            api_key="gemini-secret",
            session=session,
        ).generate_structured_feedback(
            system_instruction="Return only JSON.",
            prompt="Summarize the feedback.",
            schema=schema,
        )

        self.assertEqual(result["output_text"], '{"status":"ready"}')
        self.assertEqual(result["usage_metadata"]["prompt_token_count"], 21)
        _method, url = session.request.call_args.args
        self.assertEqual(
            url,
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{FALLBACK_MODEL}:generateContent",
        )
        provider_payload = session.request.call_args.kwargs["json"]
        generation = provider_payload["generationConfig"]
        self.assertEqual(generation["responseMimeType"], "application/json")
        self.assertEqual(generation["responseJsonSchema"], schema)
        self.assertNotIn("store", provider_payload)
        self.assertNotIn("background", provider_payload)
        self.assertNotIn("tools", provider_payload)

    def test_settings_connection_uses_gemini_only_without_projects_mcp(self):
        settings = self.env["res.config.settings"].create(
            {
                "feedback_gemini_model": "gemini-3.7-flash",
                "feedback_gemini_api_key_input": "gemini-secret",
            },
        )
        settings.set_values()
        fresh = self.env["res.config.settings"].create({})
        with (
            patch(
                "odoo.addons.usl_feedback.models.res_config_settings.requests.post",
            ) as mcp_initialize,
            patch(
                "odoo.addons.usl_feedback.services.gemini.GeminiClient.test_model",
            ) as test_model,
            patch(
                "odoo.addons.usl_feedback.services.gemini.GeminiClient.test_mcp_interaction",
            ) as test_mcp,
        ):
            result = fresh.action_test_feedback_agent()

        self.assertEqual(result["params"]["type"], "success")
        self.assertEqual(fresh.feedback_connection_status, "ready")
        self.assertIn("Existing feedback lookup is off", fresh.feedback_connection_detail)
        self.assertEqual(
            test_model.call_args_list,
            [call("gemini-3.7-flash"), call(VISION_MODEL)],
        )
        mcp_initialize.assert_not_called()
        test_mcp.assert_not_called()

        self.env["ir.config_parameter"].sudo().set_bool(
            "usl_feedback.gemini_enabled", True,
        )
        fresh.action_clear_feedback_mcp_key()
        self.assertTrue(
            self.env["ir.config_parameter"].sudo().get_bool(
                "usl_feedback.gemini_enabled",
            ),
        )

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
        task.with_user(self.reporter).feedback_queue_chat_reply()
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
