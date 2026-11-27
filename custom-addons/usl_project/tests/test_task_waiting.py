from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_project", "usl_task_waiting", "post_install", "-at_install")
class TestTaskWaiting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.writer = new_test_user(
            cls.env, login="waiting.writer@example.invalid",
            groups="base.group_user,project.group_project_user,base.group_allow_export",
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA] Explained waiting", "allow_task_dependencies": True,
        })

    def _task(self, **values):
        task = self.env["project.task"].create({
            "name": "[QA] Task awaiting a cause", "project_id": self.project.id,
            "user_ids": [Command.set(self.writer.ids)], **values,
        }).with_user(self.writer)
        self.assertTrue(task.has_access("write"))
        return task

    def test_entering_waiting_without_a_cause_is_refused(self):
        task = self._task()
        self.assertEqual(task.state, "01_in_progress")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            task.write({"state": "04_waiting_normal"})
        self.assertEqual(task.state, "01_in_progress")

    def test_creating_waiting_without_a_cause_is_refused(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env["project.task"].with_user(self.writer).create({
                "name": "[QA] Unexplained waiting must not be saved",
                "project_id": self.project.id, "state": "04_waiting_normal",
            })

    def test_native_task_dependencies_still_resume_after_the_last_blocker(self):
        first, second = self._task(name="[QA] First blocker"), self._task(name="[QA] Last blocker")
        task = self._task()
        self.assertTrue(task.allow_task_dependencies)
        task.write({"depend_on_ids": [Command.set((first | second).ids)]})
        self.assertEqual(task.depend_on_ids, first | second)
        self.assertEqual(task.state, "04_waiting_normal")
        first.state = "1_done"
        self.assertEqual(task.state, "04_waiting_normal")
        second.state = "1_done"
        self.assertEqual(task.state, "01_in_progress")

    def test_unrelated_write_preserves_an_existing_unexplained_waiting_task(self):
        task = self._task()
        self.env.flush_all()
        self.cr.execute("UPDATE project_task SET state = %s WHERE id = %s", (
            "04_waiting_normal", task.id,
        ))
        task.invalidate_recordset(["state"])
        task.write({"description": "Synthetic legacy-task description edit."})
        self.assertEqual(task.state, "04_waiting_normal")


    def test_external_causes_resume_only_after_the_last_is_lifted(self):
        task = self._task()
        task.action_set_waiting(reason="Awaiting a synthetic external review")
        first = task.usl_waiting_reason_ids
        task.action_set_waiting(reason="Awaiting a synthetic delivery")
        second = task.usl_waiting_reason_ids - first
        self.assertEqual(task.state, "04_waiting_normal")
        task.action_lift_waiting_cause(first.id)
        self.assertEqual(task.state, "04_waiting_normal")
        self.assertTrue(first.lifted)
        task.action_lift_waiting_cause(second.id)
        self.assertEqual(task.state, "01_in_progress")
        self.assertEqual(task.get_waiting_details()["tasks"][0]["has_open_causes"], False)

    def test_task_reference_uses_native_dependencies_and_resolution(self):
        task, blocker = self._task(), self._task(name="[QA] Native resolution")
        task.action_set_waiting(references=[{"model": "project.task", "id": blocker.id}])
        self.assertEqual(task.depend_on_ids, blocker)
        self.assertEqual(task.state, "04_waiting_normal")
        blocker.state = "1_done"
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.get_waiting_details()["tasks"][0]["causes"][0]["is_blocking"])

    def test_personal_task_can_wait_without_changing_project_preferences(self):
        task = self._task(project_id=False)
        task.action_set_waiting(reason="Awaiting an external answer")
        self.assertEqual(task.state, "04_waiting_normal")
        task.action_lift_waiting_cause(task.usl_waiting_reason_ids.id)
        self.assertEqual(task.state, "01_in_progress")

    def test_reference_revocation_applies_to_ordinary_orm_and_projection(self):
        private = self.env["project.project"].create({
            "name": "[QA] Confidential synthetic review", "privacy_visibility": "invited_users",
        })
        private.message_subscribe(partner_ids=self.writer.partner_id.ids)
        self.assertTrue(private.with_user(self.writer).has_access("read"))
        task = self._task()
        task.action_set_waiting(references=[{"model": "project.project", "id": private.id}])
        identifier = task.usl_waiting_reason_ids.id
        self.assertEqual(task.get_waiting_details()["tasks"][0]["causes"][0]["reference"]["id"], private.id)
        private.message_unsubscribe(partner_ids=self.writer.partner_id.ids)
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        self.assertFalse(private.with_user(self.writer).has_access("read"))
        task = self.env["project.task"].with_user(self.writer).browse(task.id)
        self.assertEqual(task.read(["usl_waiting_reason_ids"], load=None)[0]["usl_waiting_reason_ids"], [])
        Reason = self.env["project.task.waiting.reason"].with_user(self.writer)
        self.assertFalse(Reason.search([("id", "=", identifier)]))
        self.assertEqual(Reason.search_read([("id", "=", identifier)], ["target_model", "target_id"]), [])
        raw = task.read(["usl_waiting_details"])[0]["usl_waiting_details"]
        self.assertNotIn("reference", raw["causes"][0])
        self.assertNotIn(private.name, str(raw))
        self.assertEqual(task.export_data(["usl_waiting_reason_ids/target_model", "usl_waiting_reason_ids/target_id"])["datas"], [["", ""]])
        with self.assertRaises(AccessError):
            Reason.browse(identifier).read(["target_model", "target_id", "reason"])
        cause = task.get_waiting_details()["tasks"][0]["causes"][0]
        self.assertEqual(cause["kind"], "unavailable")
        self.assertNotIn("reference", cause)
        self.assertNotIn("reason", cause)
        task.action_lift_waiting_cause(identifier)
        self.assertEqual(task.state, "01_in_progress")

    def test_waiting_with_empty_dependency_commands_does_not_bypass_cause(self):
        task = self._task()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            task.write({"state": "04_waiting_normal", "depend_on_ids": [Command.clear()]})
        self.assertEqual(task.state, "01_in_progress")

    def test_invalid_reference_rolls_back_external_cause_and_state(self):
        task = self._task()
        with self.assertRaises(AccessError), self.cr.savepoint():
            task.action_set_waiting(reason="Do not retain this partial cause", references=[
                {"model": "project.task", "id": 2147483647},
            ])
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.usl_waiting_reason_ids)

    def test_reader_cannot_change_waiting_causes(self):
        reader = new_test_user(self.env, login="waiting.reader@example.invalid", groups="base.group_user")
        task = self._task()
        task.action_set_waiting(reason="Synthetic reader boundary")
        readonly = task.with_user(reader)
        self.assertTrue(readonly.has_access("read"))
        self.assertFalse(readonly.has_access("write"))
        self.assertEqual(readonly.get_waiting_details()["tasks"][0]["causes"][0]["reason"], "Synthetic reader boundary")
        with self.assertRaises(AccessError):
            readonly.action_lift_waiting_cause(task.usl_waiting_reason_ids.id)
        self.assertFalse(task.usl_waiting_reason_ids.lifted)


    def test_new_task_can_be_created_with_its_external_cause_atomically(self):
        task = self.env["project.task"].with_user(self.writer).create({
            "name": "[QA] New explained waiting", "project_id": self.project.id,
            "state": "04_waiting_normal", "usl_waiting_reason_ids": [Command.create({
                "kind": "external", "reason": "Synthetic pending answer",
            })],
        })
        self.assertEqual(task.state, "04_waiting_normal")
        self.assertEqual(task.usl_waiting_reason_ids.reason, "Synthetic pending answer")
        self.assertEqual(task.get_waiting_details()["tasks"][0]["causes"][0]["reason"], "Synthetic pending answer")

    def test_serializable_context_cannot_move_or_mutate_another_tasks_cause(self):
        task, other = self._task(), self._task()
        task.action_set_waiting(reason="Owned by the first task")
        cause = task.usl_waiting_reason_ids
        with self.assertRaises(ValidationError), self.cr.savepoint():
            cause.with_context(usl_task_waiting_mutation={"inside": True, "task_ids": [other.id]}).write({"task_id": other.id})
        self.assertEqual(cause.task_id, task)
        self.assertEqual(task.state, "04_waiting_normal")
        self.assertEqual(other.state, "01_in_progress")

    def test_reference_details_action_does_not_return_private_mutation_context(self):
        task = self._task()
        task.action_set_waiting(references=[{"model": "project.project", "id": self.project.id}])
        action = task.action_open_waiting_reference(task.usl_waiting_reason_ids.id)
        self.assertEqual(action["res_model"], "project.project")
        self.assertEqual(action["res_id"], self.project.id)
        self.assertNotIn("usl_task_waiting_mutation", action["context"])
        self.assertNotIn("usl_task_waiting_target_check", action["context"])

    def test_read_projection_does_not_write_task_or_causes(self):
        task = self._task()
        task.action_set_waiting(reason="Pure read inspection")
        self.env.flush_all()
        before = task.read(["write_date", "write_uid"])
        cause_before = task.usl_waiting_reason_ids.read(["write_date", "write_uid", "lifted", "reason"])
        with patch.object(type(task), "write", side_effect=AssertionError("Projection wrote a task")), \
             patch.object(type(task), "_write", side_effect=AssertionError("Projection wrote a task tuple")), \
             patch.object(type(task.usl_waiting_reason_ids), "write", side_effect=AssertionError("Projection wrote a cause")):
            task.get_waiting_details()
            task.read(["usl_waiting_details"])
        self.env.flush_all()
        self.assertEqual(task.read(["write_date", "write_uid"]), before)
        self.assertEqual(task.usl_waiting_reason_ids.read(["write_date", "write_uid", "lifted", "reason"]), cause_before)


    def test_unreadable_reference_cannot_be_added_and_does_not_grant_access(self):
        private = self.env["project.project"].create({
            "name": "[QA] Unshared blocking project", "privacy_visibility": "invited_users",
        })
        self.assertFalse(private.with_user(self.writer).has_access("read"))
        task = self._task()
        with self.assertRaises(AccessError), self.cr.savepoint():
            task.action_set_waiting(references=[{"model": "project.project", "id": private.id}])
        self.assertFalse(task.usl_waiting_reason_ids)
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(private.with_user(self.writer).has_access("read"))

    def test_target_model_acl_is_required(self):
        target = self.env["ir.config_parameter"].create({"key": "qa.waiting.acl", "value": "synthetic"})
        self.assertFalse(target.with_user(self.writer).has_access("read"))
        task = self._task()
        with self.assertRaises(AccessError), self.cr.savepoint():
            task.action_set_waiting(references=[{"model": "ir.config_parameter", "id": target.id}])
        self.assertFalse(task.usl_waiting_reason_ids)

    def test_current_company_scope_applies_to_source_and_target(self):
        company = self.env["res.company"].create({"name": "[QA] Waiting second company"})
        target = self.env["project.project"].create({"name": "[QA] Other company evidence", "company_id": company.id})
        task = self._task(project_id=self.project.id)
        with self.assertRaises(AccessError), self.cr.savepoint():
            task.action_set_waiting(references=[{"model": "project.project", "id": target.id}])
        self.writer.company_ids = [Command.link(company.id)]
        allowed = task.with_context(allowed_company_ids=[self.env.company.id, company.id])
        allowed.action_set_waiting(references=[{"model": "project.project", "id": target.id}])
        self.assertEqual(allowed.get_waiting_details()["tasks"][0]["causes"][0]["reference"]["id"], target.id)
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        restricted = task.with_context(allowed_company_ids=[self.env.company.id])
        self.assertFalse(restricted.usl_waiting_reason_ids)
        self.assertEqual(restricted.get_waiting_details()["tasks"][0]["causes"][0]["kind"], "unavailable")
        self.assertFalse(target.with_user(self.writer).with_context(allowed_company_ids=[self.env.company.id]).has_access("read"))

    def test_hidden_native_task_dependency_is_not_exported_and_still_resolves(self):
        private = self.env["project.project"].create({
            "name": "[QA] Restricted dependency", "privacy_visibility": "invited_users",
        })
        private.message_subscribe(partner_ids=self.writer.partner_id.ids)
        blocker = self.env["project.task"].create({"name": "[QA] Hidden checklist", "project_id": private.id})
        task = self._task()
        task.action_set_waiting(references=[{"model": "project.task", "id": blocker.id}])
        private.message_unsubscribe(partner_ids=self.writer.partner_id.ids)
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        self.assertFalse(blocker.with_user(self.writer).has_access("read"))
        self.assertFalse(task.depend_on_ids)
        raw = task.read(["depend_on_ids", "usl_waiting_reason_ids", "usl_waiting_details"])[0]
        self.assertEqual(raw["depend_on_ids"], [])
        self.assertEqual(raw["usl_waiting_reason_ids"], [])
        self.assertNotIn(blocker.name, str(raw))
        self.assertEqual(task.state, "04_waiting_normal")
        blocker.state = "1_done"
        self.assertEqual(task.state, "01_in_progress")

    def test_reference_deletion_keeps_an_opaque_liftable_cause(self):
        target = self.env["project.project"].create({"name": "[QA] Disposable blocking project"})
        task = self._task()
        task.action_set_waiting(references=[{"model": "project.project", "id": target.id}])
        cause_id = task.usl_waiting_reason_ids.id
        target.unlink()
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        self.assertFalse(task.usl_waiting_reason_ids)
        self.assertEqual(task.get_waiting_details()["tasks"][0]["causes"][0]["kind"], "unavailable")
        task.action_lift_waiting_cause(cause_id)
        self.assertEqual(task.state, "01_in_progress")

    def test_cause_copy_does_not_copy_another_tasks_waiting_history(self):
        task = self._task()
        task.action_set_waiting(reason="Belongs to the original task")
        duplicate = task.copy({"name": "[QA] Independent copied task"})
        self.assertFalse(duplicate.usl_waiting_reason_ids)
        self.assertEqual(duplicate.state, "01_in_progress")

    def test_last_cause_reactivates_later_task_into_native_inbox(self):
        task = self._task()
        later = self.project.type_ids.filtered(lambda stage: stage.usl_reactivation_role == "later")
        inbox = self.project._get_default_task_stage()
        self.assertTrue(later)
        task.action_set_waiting(reason="Independent external answer")
        task.write({"stage_id": later.id})
        task.action_lift_waiting_cause(task.usl_waiting_reason_ids.id)
        self.assertEqual(task.state, "01_in_progress")
        self.assertEqual(task.stage_id, inbox)
        self.assertIn("unblocked", task.usl_return_reasons)

    def test_completed_task_is_not_reopened_when_its_last_cause_resolves(self):
        task = self._task()
        task.action_set_waiting(reason="Explicitly completed while waiting")
        cause = task.usl_waiting_reason_ids
        task.write({"state": "1_done"})
        task.action_lift_waiting_cause(cause.id)
        self.assertEqual(task.state, "1_done")


    def test_two_explicit_causes_for_one_task_keep_dependency_until_last_lift(self):
        task, blocker = self._task(), self._task(name="[QA] Shared native blocker")
        task.write({"usl_waiting_reason_ids": [Command.create({
            "kind": "reference", "target_model": "project.task", "target_id": blocker.id,
        }) for _index in range(2)]})
        first, second = task.usl_waiting_reason_ids
        task.action_lift_waiting_cause(first.id)
        self.assertEqual(task.state, "04_waiting_normal")
        self.assertEqual(task.depend_on_ids, blocker)
        task.action_lift_waiting_cause(second.id)
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.depend_on_ids)

    def test_repeating_the_same_reference_action_does_not_duplicate_the_cause(self):
        task, blocker = self._task(), self._task(name="[QA] Repeated native reference")
        references = [{"model": "project.task", "id": blocker.id}]
        task.action_set_waiting(references=references + references)
        task.action_set_waiting(references=references)
        self.assertEqual(len(task.usl_waiting_reason_ids), 1)
        self.assertEqual(task.depend_on_ids, blocker)


    def _agent(self, readonly=False):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent integration is covered in the full Distribution registry")
        group = self.env.ref("project.group_project_user")
        return self.env["usl.agent"].with_user(self.writer).create({
            "name": "[QA] Waiting collaborator", "purpose": "Verify the native task workflow and current owner scope",
            "company_id": self.env.company.id, "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(group.ids)],
            "read_only_group_ids": [Command.set(group.ids)] if readonly else [Command.clear()],
            "access_mode": "read_only" if readonly else "read_write",
        })

    def _managed_task(self, task, agent, method="action_set_waiting"):
        # Match the exact maintained JSON-2 entrypoint, including the native
        # task method's scoped internal sudo effects. No context dict can mint
        # this controller-owned carrier.
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        access = UslAgentJson2Controller._check_agent_call(
            agent=agent, model_name="project.task", method_name=method, kwargs={},
        )
        context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": self.env.company.ids, "tracking_disable": True},
            agent=agent, model_name="project.task", method_name=method, access=access,
        )
        return task.with_user(agent.user_id).with_context(context)

    def test_managed_writer_uses_native_causes_and_actor_attribution(self):
        agent = self._agent()
        task = self._task()
        actor_task = self._managed_task(task, agent)
        actor_task.action_set_waiting(reason="Synthetic delegated external answer")
        self.assertEqual(actor_task.state, "04_waiting_normal")
        self.assertEqual(actor_task.usl_waiting_reason_ids.create_uid, agent.user_id)
        self.assertTrue(actor_task.get_waiting_details()["tasks"][0]["can_change"])
        self._managed_task(task, agent, "action_lift_waiting_cause").action_lift_waiting_cause(actor_task.usl_waiting_reason_ids.id)
        self.assertEqual(actor_task.state, "01_in_progress")
        self.assertEqual(task.sudo().write_uid, agent.user_id)

    def test_managed_readonly_actor_cannot_lift_even_with_forged_context(self):
        agent = self._agent(readonly=True)
        task = self._task()
        task.action_set_waiting(reason="Synthetic read-only boundary")
        actor = task.with_user(agent.user_id).with_context(
            usl_task_waiting_mutation={"inside": True, "task_ids": task.ids}, sudo=True,
        )
        self.assertFalse(actor.get_waiting_details()["tasks"][0]["can_change"])
        with self.assertRaises(AccessError), self.cr.savepoint():
            actor.action_lift_waiting_cause(task.usl_waiting_reason_ids.id)
        self.assertEqual(task.state, "04_waiting_normal")
        self.assertFalse(task.usl_waiting_reason_ids.lifted)

    def test_managed_writer_follows_current_owner_reference_access(self):
        agent = self._agent()
        private = self.env["project.project"].create({
            "name": "[QA] Owner-authorized evidence", "privacy_visibility": "invited_users",
        })
        private.message_subscribe(partner_ids=self.writer.partner_id.ids)
        task = self._managed_task(self._task(), agent)
        task.action_set_waiting(references=[{"model": "project.project", "id": private.id}])
        identifier = task.usl_waiting_reason_ids.id
        private.message_unsubscribe(partner_ids=self.writer.partner_id.ids)
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        details = task.get_waiting_details()["tasks"][0]
        self.assertEqual(details["causes"][0]["kind"], "unavailable")
        self.assertFalse(task.usl_waiting_reason_ids)
        self._managed_task(task, agent, "action_lift_waiting_cause").action_lift_waiting_cause(identifier)
        self.assertEqual(task.state, "01_in_progress")
        with self.assertRaises(AccessError):
            self.env["project.task.waiting.reason"].with_user(agent.user_id).browse(identifier).read(["target_model", "target_id"])


    def test_public_blocked_status_does_not_read_an_inaccessible_task(self):
        private = self.env["project.project"].create({"name": "[QA] Private source", "privacy_visibility": "invited_users"})
        task = self.env["project.task"].create({"name": "[QA] Private task status", "project_id": private.id}).with_user(self.writer)
        self.assertFalse(task.has_access("read"))
        with self.assertRaises(AccessError):
            task.is_blocked_by_dependences()

    def test_lifting_an_explicit_task_cause_preserves_disabled_native_dependencies(self):
        project = self.env["project.project"].create({"name": "[QA] Native dependencies disabled", "allow_task_dependencies": False})
        first, unrelated = self._task(name="[QA] Explicit blocker"), self._task(name="[QA] Unchanged native relation")
        task = self._task(project_id=project.id)
        task.write({"depend_on_ids": [Command.link(unrelated.id)]})
        self.assertEqual(task.state, "01_in_progress")
        task.action_set_waiting(references=[{"model": "project.task", "id": first.id}])
        self.assertEqual(task.state, "04_waiting_normal")
        task.action_lift_waiting_cause(task.usl_waiting_reason_ids.id)
        self.assertEqual(task.state, "01_in_progress")
        self.assertEqual(task.depend_on_ids, unrelated)
        self.assertFalse(project.allow_task_dependencies)


    def test_portal_reads_only_native_shared_task_causes_without_new_write_access(self):
        portal = new_test_user(self.env, login="waiting.portal@example.invalid", groups="base.group_portal")
        self.project.privacy_visibility = "portal"
        task = self._task()
        task.sudo().message_subscribe(partner_ids=portal.partner_id.ids)
        private = self.env["project.project"].create({
            "name": "[QA] Internal supporting record", "privacy_visibility": "invited_users",
        })
        private.message_subscribe(partner_ids=self.writer.partner_id.ids)
        task.action_set_waiting(reason="Awaiting a synthetic reply", references=[{
            "model": "project.project", "id": private.id,
        }])
        public_cause = task.usl_waiting_reason_ids.filtered(lambda row: row.kind == "external")
        hidden_cause = task.usl_waiting_reason_ids - public_cause
        shared = task.with_user(portal)
        self.assertTrue(shared.has_access("read"))
        self.assertFalse(shared.has_access("write"))
        self.assertEqual(shared.read(["usl_waiting_reason_ids"])[0]["usl_waiting_reason_ids"], public_cause.ids)
        details = shared.get_waiting_details()["tasks"][0]
        self.assertFalse(details["can_change"])
        self.assertEqual(next(c for c in details["causes"] if c["id"] == hidden_cause.id)["kind"], "unavailable")
        with self.assertRaises(AccessError), self.cr.savepoint():
            public_cause.with_user(portal).write({"lifted": True})
        with self.assertRaises(AccessError):
            hidden_cause.with_user(portal).read(["target_model", "target_id"])
        task.sudo().message_unsubscribe(partner_ids=portal.partner_id.ids)
        self.env.invalidate_all()
        self.env.transaction.invalidate_access_cache()
        with self.assertRaises(AccessError):
            public_cause.with_user(portal).read(["reason"])


    def test_reference_picker_preserves_existing_agent_api_read_refusal(self):
        agent = self._agent()
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        task = self._task()
        task.action_set_waiting(references=[{"model": "project.project", "id": self.project.id}])
        actor = self._managed_task(task, agent)
        self.assertTrue(self.project.with_user(agent.user_id).has_access("read"))
        from odoo.addons.usl_access_control.controllers.json2 import AGENT_HIDDEN_API_MODELS
        # Simulate the existing exact controller decision on a permitted
        # synthetic business type, never obtain a prohibited object.
        with patch("odoo.addons.usl_access_control.controllers.json2.AGENT_HIDDEN_API_MODELS",
                   AGENT_HIDDEN_API_MODELS | {"project.project"}):
            with self.assertRaises(AccessError):
                UslAgentJson2Controller._check_agent_call(
                    agent=agent, model_name="project.project", method_name="read", kwargs={},
                )
            details = actor.get_waiting_details()["tasks"][0]
            self.assertEqual(details["causes"][0]["kind"], "unavailable")
            self.assertNotIn("reference", details["causes"][0])
            self.assertNotIn("project.project", [item["model"] for item in actor.get_waiting_reference_models()])
            with self.assertRaises((AccessError, ValidationError)), self.cr.savepoint():
                actor.search_waiting_references(model="project.project", query="Explained")
            with self.assertRaises((AccessError, ValidationError)), self.cr.savepoint():
                actor.action_set_waiting(references=[{"model": "project.project", "id": self.project.id}])
            self.assertEqual(task.get_waiting_details()["tasks"][0]["causes"][0]["reference"]["id"], self.project.id)
        self.assertEqual(actor.get_waiting_details()["tasks"][0]["causes"][0]["reference"]["id"], self.project.id)
