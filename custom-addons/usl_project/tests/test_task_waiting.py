from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

WAITING = "04_waiting_normal"


@tagged("usl_project", "post_install", "-at_install")
class TestTaskWaitingReason(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({"name": "[QA] Waiting project", "allow_task_dependencies": True})
        cls.supplier = cls.env["res.partner"].create({"name": "[QA] Synthetic supplier"})
        cls.manager = new_test_user(
            cls.env, login="waiting.manager", groups="base.group_user,project.group_project_manager",
            context={"no_reset_password": True},
        )
        cls.Task = cls.env["project.task"].with_context(mail_notrack=False, tracking_disable=False)

    def _task(self, **values):
        return self.env["project.task"].create({"name": "[QA] Task", "project_id": self.project.id, **values})

    def test_reason_makes_the_task_wait_and_clearing_it_resumes(self):
        task = self._task()
        self.assertEqual(task.state, "01_in_progress")
        task.usl_waiting_reason = "Supplier quote"
        self.assertEqual(task.state, WAITING)
        task.usl_waiting_reason = False
        self.assertEqual(task.state, "01_in_progress")

    def test_created_with_a_reason_waits(self):
        tasks = self.env["project.task"].create([
            {"name": "[QA] One", "project_id": self.project.id, "usl_waiting_reason": "Customer answer"},
            {"name": "[QA] Two", "project_id": self.project.id},
        ])
        self.assertEqual(tasks.mapped("state"), [WAITING, "01_in_progress"])

    def test_choosing_in_progress_clears_reason_and_partner(self):
        task = self._task(usl_waiting_reason="Supplier quote", usl_waiting_partner_id=self.supplier.id)
        self.assertEqual(task.state, WAITING)
        task.state = "01_in_progress"
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.usl_waiting_reason)
        self.assertFalse(task.usl_waiting_partner_id)

    def test_resume_in_one_write_for_a_mixed_batch(self):
        waiting = self._task(usl_waiting_reason="Supplier quote")
        free = self._task()
        (waiting | free).write({"state": "01_in_progress"})
        self.assertEqual((waiting | free).mapped("state"), ["01_in_progress"] * 2)
        self.assertEqual((waiting | free).mapped("usl_waiting_reason"), [False, False])

    def test_closing_a_waiting_task_drops_the_reason_and_it_stays_closed(self):
        task = self._task(usl_waiting_reason="Supplier quote")
        task.state = "1_done"
        self.assertEqual(task.state, "1_done")
        self.assertFalse(task.usl_waiting_reason)
        task.usl_waiting_reason = "Late answer"
        self.assertEqual(task.state, "1_done", "A reason never reopens a closed task")

    def test_stage_change_keeps_a_waiting_task_waiting(self):
        task = self._task(usl_waiting_reason="Supplier quote")
        stage = self.env["project.task.type"].create({"name": "[QA] Next", "project_ids": [Command.link(self.project.id)]})
        task.stage_id = stage
        self.assertEqual(task.state, WAITING)
        self.assertEqual(task.usl_waiting_reason, "Supplier quote")

    def test_native_dependencies_still_block_and_combine_with_a_reason(self):
        blocker = self._task(name="[QA] Blocker")
        task = self._task()
        task.depend_on_ids = blocker
        self.assertEqual(task.state, WAITING)
        self.assertFalse(task.usl_waiting_reason)
        task.usl_waiting_reason = "Supplier quote"
        blocker.state = "1_done"
        self.assertEqual(task.state, WAITING, "The external reason still blocks")
        task.usl_waiting_reason = False
        self.assertEqual(task.state, "01_in_progress")
        blocker.state = "01_in_progress"
        self.assertEqual(task.state, WAITING)
        task.usl_waiting_reason = "Supplier quote"
        task.usl_waiting_reason = False
        self.assertEqual(task.state, WAITING, "An open dependency outlives the cleared reason")

    def _tracking_values(self, task):
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.flush_all()
        return self.env["mail.tracking.value"].search([("mail_message_id.res_id", "=", task.id), ("mail_message_id.model", "=", "project.task")])

    def _tracked_fields(self, task):
        return self._tracking_values(task).field_id.mapped("name")

    def test_history_is_native_chatter_tracking(self):
        task = self.Task.create({"name": "[QA] Tracked", "project_id": self.project.id})
        self._tracking_values(task)  # settle creation first: later changes are logged as changes
        task.write({"usl_waiting_reason": "Supplier quote", "usl_waiting_partner_id": self.supplier.id})
        tracked = self._tracked_fields(task)
        self.assertIn("usl_waiting_reason", tracked)
        self.assertIn("usl_waiting_partner_id", tracked)
        task.state = "01_in_progress"
        cleared = self._tracking_values(task).filtered(
            lambda value: value.field_id.name == "usl_waiting_reason" and not value.new_value_char,
        )
        self.assertTrue(cleared, "Resuming is recorded as a cleared reason")

    def test_chosen_status_wins_over_a_reason_edited_in_the_same_save(self):
        task = self._task(usl_waiting_reason="Supplier quote", usl_waiting_partner_id=self.supplier.id)
        task.write({"state": "01_in_progress", "usl_waiting_reason": "Edited before resuming"})
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.usl_waiting_reason)
        self.assertFalse(task.usl_waiting_partner_id)
        stage = self.env["project.task.type"].create({"name": "[QA] Later", "project_ids": [Command.link(self.project.id)]})
        task.stage_id = stage
        self.assertEqual(task.state, "01_in_progress", "No hidden reason sends it back to Waiting")

    def test_disabling_task_dependencies_keeps_external_reasons(self):
        blocker = self._task(name="[QA] Blocker")
        by_task = self._task()
        by_task.depend_on_ids = blocker
        by_reason = self._task(usl_waiting_reason="Supplier quote", usl_waiting_partner_id=self.supplier.id)
        self.assertEqual((by_task | by_reason).mapped("state"), [WAITING, WAITING])
        self.project.allow_task_dependencies = False
        self.assertEqual(by_task.state, "01_in_progress", "Native reset still applies to dependency waits")
        self.assertEqual(by_reason.state, WAITING)
        self.assertEqual(by_reason.usl_waiting_reason, "Supplier quote")
        self.assertEqual(by_reason.usl_waiting_partner_id, self.supplier)

    def test_waiting_needs_a_reason_or_an_open_dependency(self):
        task = self._task()
        with self.assertRaises(ValidationError):
            task.state = WAITING
        with self.assertRaises(ValidationError):
            self._task(state=WAITING)
        blocker = self._task(name="[QA] Blocker")
        task.depend_on_ids = blocker
        self.assertEqual(task.state, WAITING)
        task.depend_on_ids = False
        self.assertEqual(task.state, "01_in_progress")

    def test_clearing_the_reason_by_hand_clears_the_contact(self):
        task = self._task(usl_waiting_reason="Supplier quote", usl_waiting_partner_id=self.supplier.id)
        task.usl_waiting_reason = False
        self.assertFalse(task.usl_waiting_partner_id)
        self.assertEqual(task.state, "01_in_progress")

    def test_portal_collaborator_can_move_a_waiting_task_to_done(self):
        project = self.env["project.project"].create({"name": "[QA] Shared project", "privacy_visibility": "portal"})
        portal = new_test_user(
            self.env, login="waiting.collaborator", groups="base.group_portal", context={"no_reset_password": True},
        )
        self.env["project.collaborator"].create({
            "project_id": project.id, "partner_id": portal.partner_id.id, "limited_access": False,
        })
        task = self.env["project.task"].create({
            "name": "[QA] Shared task", "project_id": project.id, "usl_waiting_reason": "Supplier quote",
            "usl_waiting_partner_id": self.supplier.id,
        })
        task.with_user(portal).write({"state": "1_done"})
        self.assertEqual(task.state, "1_done")
        self.assertFalse(task.usl_waiting_reason)
        self.assertFalse(task.usl_waiting_partner_id)

    def test_copy_does_not_carry_the_reason(self):
        task = self._task(usl_waiting_reason="Supplier quote", usl_waiting_partner_id=self.supplier.id)
        copy = task.copy()
        self.assertFalse(copy.usl_waiting_reason)
        self.assertFalse(copy.usl_waiting_partner_id)
        self.assertEqual(copy.state, "01_in_progress")

    def test_access_follows_native_task_rules(self):
        task = self._task(usl_waiting_reason="Supplier quote")
        reader = new_test_user(
            self.env, login="waiting.reader", groups="base.group_user", context={"no_reset_password": True},
        )
        portal = new_test_user(
            self.env, login="waiting.portal", groups="base.group_portal", context={"no_reset_password": True},
        )
        task.with_user(self.manager).usl_waiting_reason = "Edited by manager"
        self.assertEqual(task.usl_waiting_reason, "Edited by manager")
        with self.assertRaises(AccessError):
            task.with_user(reader).write({"usl_waiting_reason": "Not allowed"})
        with self.assertRaises(AccessError):
            task.with_user(portal).read(["usl_waiting_reason"])
        # A private task of someone else stays unreadable.
        private = self.env["project.task"].with_user(self.manager).create({"name": "[QA] Private", "usl_waiting_reason": "Private"})
        other = new_test_user(
            self.env, login="waiting.other", groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )
        with self.assertRaises(AccessError):
            private.with_user(other).read(["usl_waiting_reason"])

    def test_kanban_read_costs_the_same_for_many_waiting_tasks(self):
        specification = {
            "name": {}, "state": {}, "usl_waiting_reason": {},
            "usl_waiting_partner_id": {"fields": {"display_name": {}}},
        }

        def queries(limit):
            self.env.invalidate_all()
            self.env.cr.flush()
            before = self.env.cr.sql_log_count
            result = self.env["project.task"].with_user(self.manager).web_search_read(
                [("project_id", "=", self.project.id)], specification, limit=limit,
            )
            return self.env.cr.sql_log_count - before, result

        self.env["project.task"].create([
            {
                "name": f"[QA] Task {index}", "project_id": self.project.id,
                "usl_waiting_reason": f"Reason {index}" if index % 2 else False,
                "usl_waiting_partner_id": self.supplier.id if index % 4 == 1 else False,
            }
            for index in range(80)
        ])
        queries(5)  # warm caches
        small, _ = queries(5)
        large, result = queries(80)
        self.assertEqual(len(result["records"]), 80)
        self.assertEqual(large, small, "Query count must not grow with the number of waiting tasks")
        self.assertLessEqual(large, 8)
