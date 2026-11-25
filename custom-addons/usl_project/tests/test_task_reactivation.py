from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestTaskReactivation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(
            cls.env,
            login="reactivation.a@example.invalid",
            groups="base.group_user,project.group_project_user",
        )
        cls.user_b = new_test_user(
            cls.env,
            login="reactivation.b@example.invalid",
            groups="base.group_user,project.group_project_user",
        )
        cls.project = cls.env["project.project"].create(
            {
                "name": "Reactivation project",
                "allow_task_dependencies": True,
            }
        )
        cls.stage_a, cls.stage_b = cls.env["project.task.type"].create(
            [
                {
                    "name": "Reactivation backlog",
                    "project_ids": [Command.link(cls.project.id)],
                },
                {
                    "name": "Reactivation active",
                    "project_ids": [Command.link(cls.project.id)],
                },
            ]
        )

    def _task(self, name, users=None, **values):
        return self.env["project.task"].create(
            {
                "name": name,
                "project_id": self.project.id,
                "stage_id": self.stage_a.id,
                "user_ids": [
                    Command.set((users or self.user_a).ids),
                ],
                **values,
            }
        )

    def _role_stage(self, user, role):
        return self.env["project.task.type"].sudo().search(
            [
                ("user_id", "=", user.id),
                ("usl_reactivation_role", "=", role),
            ],
            limit=1,
        )

    def _personal_link(self, task, user):
        return self.env["project.task.stage.personal"].sudo().search(
            [("task_id", "=", task.id), ("user_id", "=", user.id)],
            limit=1,
        )

    def _put_in_role(self, task, user, role):
        link = self._personal_link(task, user)
        link.stage_id = self._role_stage(user, role)
        return link

    def test_default_personal_stages_have_stable_roles(self):
        task = self._task("Default role task")

        stages = self.env["project.task.type"].sudo().search(
            [("user_id", "=", self.user_a.id)],
            order="sequence, id",
        )

        self.assertEqual(
            stages.filtered("usl_reactivation_role").mapped(
                "usl_reactivation_role"
            ),
            ["inbox", "later"],
        )
        self.assertEqual(self._personal_link(task, self.user_a).stage_id, stages[0])

    @freeze_time("2026-09-02 10:00:00")
    def test_hourly_cron_returns_unassigned_task_to_its_project_inbox(self):
        self.stage_a.usl_reactivation_role = "later"
        self.stage_b.usl_reactivation_role = "inbox"
        task = self._task(
            "Unassigned deferred task",
            user_ids=[Command.clear()],
            planned_date_begin=datetime(2026, 9, 2, 9),
            state="03_approved",
        )

        task._cron_usl_reactivate_later_tasks()

        self.assertEqual(task.stage_id, self.stage_b)
        self.assertEqual(task.state, "03_approved")
        self.assertEqual(task._cron_usl_reactivate_later_tasks(), 0)

    @freeze_time("2026-09-02 10:00:00")
    def test_shared_source_routes_each_project_to_its_own_inbox(self):
        other_project, unconfigured_project = self.env["project.project"].create(
            [{"name": "Other board"}, {"name": "Unconfigured board"}],
        )
        self.stage_a.write(
            {
                "usl_reactivation_role": "later",
                "project_ids": [Command.link(other_project.id), Command.link(unconfigured_project.id)],
            },
        )
        self.stage_b.usl_reactivation_role = "inbox"
        other_inbox = self.env["project.task.type"].create(
            {
                "name": "Team intake",
                "project_ids": [Command.link(other_project.id)],
                "usl_reactivation_role": "inbox",
            },
        )
        tasks = self.env["project.task"].union(
            self._task(
                "Deferred task",
                project_id=project.id,
                planned_date_begin=datetime(2026, 9, 2, 9),
            )
            for project in self.project | other_project | unconfigured_project
        )
        self.assertEqual(tasks._cron_usl_reactivate_later_tasks(), 2)
        self.assertEqual(tasks.mapped("stage_id"), self.stage_b | other_inbox | self.stage_a)

        other_inbox.project_ids = [Command.link(unconfigured_project.id)]
        self.stage_b.project_ids = [Command.link(unconfigured_project.id)]
        tasks._cron_usl_reactivate_later_tasks()
        self.assertEqual(tasks[2].stage_id, self.stage_a)

    @freeze_time("2026-09-02 10:00:00")
    def test_manual_drop_stays_later_until_a_real_trigger(self):
        self.stage_a.usl_reactivation_role = "later"
        self.stage_b.usl_reactivation_role = "inbox"
        task = self._task("Manual defer", stage_id=self.stage_b.id)
        personal_link = self._put_in_role(task, self.user_a, "later")
        task.write(
            {"stage_id": self.stage_a.id, "planned_date_begin": datetime(2026, 9, 2, 9)},
        )
        self.assertEqual(task.stage_id, self.stage_a)
        self.assertEqual(personal_link.stage_id, self._role_stage(self.user_a, "inbox"))

        self._put_in_role(task, self.user_a, "later")
        task.write({"planned_date_begin": task.planned_date_begin, "state": task.state})
        self.assertEqual(task.stage_id, self.stage_a)

        # Dates are still eligible for both boards, but a project move alone
        # must not trigger a second move in the personal pipeline.
        self.user_a.tz = "Pacific/Honolulu"
        self.project.company_id = self.env.company
        self.env.company.tz = "Europe/Paris"
        with freeze_time("2026-09-02 22:30:00"):
            task.write({"planned_date_begin": False, "date_deadline": datetime(2026, 9, 6, 12)})
        self.assertEqual(task.stage_id, self.stage_b)
        self.assertEqual(personal_link.stage_id, self._role_stage(self.user_a, "later"))

    def test_project_reactivation_on_reopening_dependency_and_subtasks(self):
        self.stage_a.usl_reactivation_role = "later"
        self.stage_b.usl_reactivation_role = "inbox"
        reopened = self._task("Reopened", state="1_canceled")
        blocker = self._task("Blocker", stage_id=self.stage_b.id)
        blocked = self._task("Blocked", depend_on_ids=[Command.link(blocker.id)])
        parent = self._task("Parent")
        child = self._task("Child", parent_id=parent.id, stage_id=self.stage_b.id)
        archived = self._task("Archived", active=False, state="1_done")

        reopened.state = "02_changes_requested"
        blocker.state = "1_done"
        child.state = "1_done"
        archived.state = "01_in_progress"

        self.assertEqual((reopened | blocked | parent).stage_id, self.stage_b)
        self.assertEqual(reopened.state, "02_changes_requested")
        self.assertEqual(archived.stage_id, self.stage_a)

    def test_project_deadline_follows_local_midnight_after_dst_transition(self):
        self.project.company_id = self.env.company
        self.env.company.tz = "Europe/Paris"
        self.stage_a.usl_reactivation_role = "later"
        self.stage_b.usl_reactivation_role = "inbox"
        blocker = self._task("Open dependency", stage_id=self.stage_b.id)
        task = self._task(
            "Deadline after the clock change",
            depend_on_ids=[Command.link(blocker.id)],
            date_deadline=datetime(2026, 4, 2, 12),
        )
        with freeze_time("2026-03-29 21:59:59"):
            task._cron_usl_reactivate_later_tasks()
        self.assertEqual(task.stage_id, self.stage_a)

        with freeze_time("2026-03-29 22:00:00"):
            task._cron_usl_reactivate_later_tasks()
        self.assertEqual(task.stage_id, self.stage_b)
        self.assertEqual(task.state, "04_waiting_normal")

    def test_project_association_cannot_take_ownership_of_a_personal_stage(self):
        manager = new_test_user(
            self.env,
            login="reactivation.manager@example.invalid",
            groups="base.group_user,project.group_project_manager",
        )
        personal = self._role_stage(manager, "later").with_user(manager)
        project = self.project.with_user(manager)
        with self.assertRaises(ValidationError):
            personal.project_ids = [Command.link(project.id)]
        with self.assertRaises(ValidationError):
            project.type_ids = [Command.link(personal.id)]
        with self.assertRaises(ValidationError):
            project.write({"type_ids": [personal.id]})
        with self.assertRaises(ValidationError):
            project.create({"name": "Other project", "type_ids": [Command.link(personal.id)]})
        self.assertEqual(personal.user_id, manager)

        quick_project_id, _name = project.name_create("Quick project")
        quick_stage = project.browse(quick_project_id).type_ids
        self.assertTrue(quick_stage)
        self.assertFalse(quick_stage.user_id)

    def test_stage_change_moves_only_later_assignees(self):
        task = self._task("Shared task", self.user_a | self.user_b)
        link_a = self._put_in_role(task, self.user_a, "later")
        user_b_stages = self.env["project.task.type"].sudo().search(
            [("user_id", "=", self.user_b.id)],
            order="sequence, id",
        )
        link_b = self._personal_link(task, self.user_b)
        link_b.stage_id = user_b_stages[1]

        task.stage_id = self.stage_b

        self.assertEqual(link_a.stage_id, self._role_stage(self.user_a, "inbox"))
        self.assertEqual(link_b.stage_id, user_b_stages[1])

    def test_closing_stays_later_and_reopening_moves_to_inbox(self):
        for closed_state in ("1_done", "1_canceled"):
            with self.subTest(closed_state=closed_state):
                task = self._task(f"Reopened task {closed_state}")
                link = self._put_in_role(task, self.user_a, "later")

                task.state = closed_state
                self.assertEqual(
                    link.stage_id,
                    self._role_stage(self.user_a, "later"),
                )

                task.state = "01_in_progress"
                self.assertEqual(
                    link.stage_id,
                    self._role_stage(self.user_a, "inbox"),
                )

    def test_open_status_change_reactivates_but_noop_does_not(self):
        task = self._task("Changed task status")
        link = self._put_in_role(task, self.user_a, "later")

        task.write({"state": task.state})
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

        task.state = "02_changes_requested"
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    def test_last_blocker_completion_and_dependency_removal_reactivate(self):
        blocker_a = self._task("Blocker A")
        blocker_b = self._task("Blocker B")
        task = self._task(
            "Blocked task",
            depend_on_ids=[Command.set((blocker_a | blocker_b).ids)],
        )
        link = self._put_in_role(task, self.user_a, "later")

        blocker_a.state = "1_done"
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

        blocker_b.state = "1_done"
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

        blocker_a.state = "01_in_progress"
        self._put_in_role(task, self.user_a, "later")
        task.depend_on_ids = [Command.clear()]
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    def test_all_direct_subtasks_complete_or_incomplete_one_archived(self):
        parent = self._task("Parent")
        child_a = self._task("Child A", parent_id=parent.id)
        child_b = self._task("Child B", parent_id=parent.id)
        link = self._put_in_role(parent, self.user_a, "later")

        child_a.state = "1_done"
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

        child_b.state = "1_done"
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

        child_b.state = "01_in_progress"
        self._put_in_role(parent, self.user_a, "later")
        child_b.active = False
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    def test_deleting_only_subtask_does_not_vacuously_complete_parent(self):
        parent = self._task("Empty parent")
        child = self._task("Only child", parent_id=parent.id)
        link = self._put_in_role(parent, self.user_a, "later")

        child.unlink()

        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

    def test_reparenting_or_deleting_last_incomplete_subtask_reactivates(self):
        for operation in ("reparent", "delete"):
            with self.subTest(operation=operation):
                parent = self._task(f"Parent {operation}")
                complete = self._task(
                    f"Complete child {operation}",
                    parent_id=parent.id,
                    state="1_done",
                )
                incomplete = self._task(
                    f"Incomplete child {operation}",
                    parent_id=parent.id,
                )
                link = self._put_in_role(parent, self.user_a, "later")

                if operation == "reparent":
                    other_parent = self._task("Other parent")
                    incomplete.parent_id = other_parent
                else:
                    incomplete.unlink()

                self.assertTrue(complete.exists())
                self.assertEqual(
                    link.stage_id,
                    self._role_stage(self.user_a, "inbox"),
                )

    @freeze_time("2026-09-02 10:00:00")
    def test_date_edits_and_hourly_cron_use_due_window(self):
        now = datetime(2026, 9, 2, 10)
        task = self._task(
            "Timed task",
            planned_date_begin=now + timedelta(hours=2),
            date_deadline=now + timedelta(days=5),
        )
        link = self._put_in_role(task, self.user_a, "later")

        self.assertFalse(
            task._usl_is_due_for_user(
                self.user_a,
                now=fields.Datetime.now(),
            )
        )
        task._cron_usl_reactivate_later_tasks()
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

        task.date_deadline = now + timedelta(days=3)
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

        self._put_in_role(task, self.user_a, "later")
        task.planned_date_begin = now - timedelta(minutes=1)
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

        self._put_in_role(task, self.user_a, "later")
        task.write({"planned_date_begin": task.planned_date_begin})
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "later"))

        task.write(
            {
                "planned_date_begin": now + timedelta(hours=2),
                "date_deadline": False,
            }
        )
        with freeze_time(now + timedelta(hours=2, minutes=1)):
            task._cron_usl_reactivate_later_tasks()
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    @freeze_time("2026-09-02 22:30:00")
    def test_deadline_window_uses_assignee_timezone(self):
        self.user_a.tz = "Pacific/Kiritimati"
        task = self._task(
            "Timezone task",
            date_deadline=datetime(2026, 9, 6, 9),
        )
        link = self._put_in_role(task, self.user_a, "later")

        self.assertTrue(
            task._usl_is_due_for_user(
                self.user_a,
                now=fields.Datetime.now(),
            )
        )
        task._cron_usl_reactivate_later_tasks()

        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    @freeze_time("2026-09-02 10:00:00")
    def test_cron_ignores_other_columns_closed_and_inactive_tasks(self):
        due = datetime(2026, 9, 2, 9)
        other_stage = self.env["project.task.type"].sudo().search(
            [
                ("user_id", "=", self.user_a.id),
                ("usl_reactivation_role", "=", False),
                ("fold", "=", False),
            ],
            order="sequence, id",
            limit=1,
        )
        ordinary_task = self._task("Ordinary personal column", planned_date_begin=due)
        ordinary_link = self._personal_link(ordinary_task, self.user_a)
        ordinary_link.stage_id = other_stage
        closed_task = self._task("Closed deferred task", planned_date_begin=due)
        closed_task.state = "1_done"
        closed_link = self._put_in_role(closed_task, self.user_a, "later")
        inactive_task = self._task("Inactive deferred task", planned_date_begin=due)
        inactive_link = self._put_in_role(inactive_task, self.user_a, "later")
        inactive_task.active = False

        self.env["project.task"]._cron_usl_reactivate_later_tasks()

        self.assertEqual(ordinary_link.stage_id, other_stage)
        self.assertEqual(closed_link.stage_id, self._role_stage(self.user_a, "later"))
        self.assertEqual(inactive_link.stage_id, self._role_stage(self.user_a, "later"))

    def test_reactivation_is_idempotent(self):
        task = self._task("Idempotent reactivation")
        link = self._put_in_role(task, self.user_a, "later")

        self.assertEqual(task._usl_reactivate_later_personal_stages(), 1)
        self.assertEqual(task._usl_reactivate_later_personal_stages(), 0)
        self.assertEqual(link.stage_id, self._role_stage(self.user_a, "inbox"))

    def test_ordinary_user_cannot_write_another_users_personal_stage(self):
        task = self._task("Private stage task", self.user_a | self.user_b)
        other_link = self._personal_link(task, self.user_b)

        with self.assertRaises(AccessError):
            other_link.with_user(self.user_a).write(
                {"stage_id": self._role_stage(self.user_b, "later").id}
            )
