import hashlib
from copy import deepcopy
from datetime import datetime, timedelta

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("usl_project_restore", "post_install", "-at_install")
class TestProjectRestore(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source_company_id = 9001
        cls.source_partner_id = 9002
        cls.source_user_id = 9003
        cls.source_stage_id = 9004
        cls.source_historical_stage_id = 8999
        cls.source_project_id = 9005
        cls.source_blocker_id = 9006
        cls.source_task_id = 9007
        cls.source_update_id = 9008
        cls.source_message_id = 9009
        cls.source_attachment_id = 9010
        cls.source_tracking_id = 9011
        cls.source_follower_id = 9012
        cls.source_activity_id = 9013
        cls.source_activity_type_id = 9014
        cls.source_message_subtype_id = 9015
        cls.source_alias_id = 9016

    def _payload(self):
        start = datetime(2026, 8, 10, 9)
        blocker_deadline = start + timedelta(days=2)
        rows = {
            "companies": [
                {"id": self.source_company_id, "name": self.env.company.name},
            ],
            "partners": [
                {
                    "id": self.source_partner_id,
                    "name": "Project Restore Test User",
                    "email": "project.restore.test@example.com",
                    "active": True,
                    "is_company": False,
                    "company_id": self.source_company_id,
                },
            ],
            "users": [
                {
                    "id": self.source_user_id,
                    "login": "project.restore.test@example.com",
                    "active": True,
                    "share": False,
                    "partner_id": self.source_partner_id,
                    "company_ids": [self.source_company_id],
                    "project_group_xmlids": ["project.group_project_user"],
                },
            ],
            "project_stages": [],
            "task_stages": [
                {
                    "id": self.source_stage_id,
                    "sequence": 1,
                    "color": 0,
                    "user_id": None,
                    "name": {"en_US": "Restored Stage", "fr_FR": "Étape restaurée"},
                    "active": True,
                    "fold": False,
                    "auto_validation_state": False,
                    "rotting_threshold_days": 0,
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                },
            ],
            "historical_task_stage_ids": [self.source_historical_stage_id],
            "tags": [],
            "projects": [
                {
                    "id": self.source_project_id,
                    "account_id": None,
                    "sequence": 10,
                    "partner_id": None,
                    "company_id": self.source_company_id,
                    "color": 3,
                    "user_id": self.source_user_id,
                    "stage_id": None,
                    "last_update_id": self.source_update_id,
                    "access_token": "restored-project-token",
                    "privacy_visibility": "followers",
                    "last_update_status": "at_risk",
                    "date_start": None,
                    "date": None,
                    "name": {"en_US": "Restored Private Project"},
                    "label_tasks": {"en_US": "Tasks"},
                    "task_properties_definition": {},
                    "description": "<p>Source description</p>",
                    "active": True,
                    "allow_task_dependencies": True,
                    "allow_milestones": False,
                    "allow_recurring_tasks": False,
                    "is_template": False,
                    "date_last_stage_update": start,
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                    "documents_folder_id": None,
                    "alias_id": self.source_alias_id,
                    "alias_name": "restore-private",
                    "alias_contact": "everyone",
                },
            ],
            "recurrences": [],
            "milestones": [],
            "tasks": [
                self._task_row(
                    self.source_blocker_id,
                    "Blocking task",
                    start=None,
                    deadline=blocker_deadline,
                ),
                self._task_row(
                    self.source_task_id,
                    "Planned task",
                    start=start,
                    deadline=start + timedelta(days=4),
                ),
            ],
            "updates": [
                {
                    "id": self.source_update_id,
                    "progress": 40,
                    "user_id": self.source_user_id,
                    "project_id": self.source_project_id,
                    "task_count": 2,
                    "closed_task_count": 0,
                    "name": "Operational review",
                    "status": "at_risk",
                    "date": start.date(),
                    "description": "<p>Dependencies need attention.</p>",
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                },
            ],
            "project_task_stage_rel": [
                {
                    "project_id": self.source_project_id,
                    "type_id": self.source_stage_id,
                },
            ],
            "project_tag_rel": [],
            "project_favorite_rel": [],
            "task_user_rel": [
                {
                    "task_id": self.source_task_id,
                    "user_id": self.source_user_id,
                },
            ],
            "task_tag_rel": [],
            "dependencies": [
                {
                    "task_id": self.source_task_id,
                    "depends_on_id": self.source_blocker_id,
                },
            ],
            "messages": [
                {
                    "id": self.source_message_id,
                    "parent_id": None,
                    "res_id": self.source_task_id,
                    "record_company_id": self.source_company_id,
                    "subtype_id": self.source_message_subtype_id,
                    "mail_activity_type_id": None,
                    "author_id": self.source_partner_id,
                    "subject": "Source decision",
                    "model": "project.task",
                    "message_type": "comment",
                    "email_from": "project.restore.test@example.com",
                    "message_id": "<source-decision@example.com>",
                    "reply_to": None,
                    "body": "<p>Keep the historical evidence.</p>",
                    "is_internal": False,
                    "date": start,
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                },
            ],
            "message_attachment_rel": [
                {
                    "message_id": self.source_message_id,
                    "attachment_id": self.source_attachment_id,
                },
            ],
            "message_partner_rel": [
                {
                    "message_id": self.source_message_id,
                    "partner_id": self.source_partner_id,
                },
            ],
            "tracking_values": [
                {
                    "id": self.source_tracking_id,
                    "mail_message_id": self.source_message_id,
                    "field_model": "project.task",
                    "field_name": "priority",
                    "old_value_integer": 0,
                    "new_value_integer": 0,
                    "old_value_char": "0",
                    "new_value_char": "1",
                    "old_value_text": None,
                    "new_value_text": None,
                    "old_value_datetime": None,
                    "new_value_datetime": None,
                    "old_value_float": 0.0,
                    "new_value_float": 0.0,
                    "field_info": {},
                },
            ],
            "attachments": [
                {
                    "id": self.source_attachment_id,
                    "res_id": self.source_task_id,
                    "name": "source-evidence.txt",
                    "res_model": "project.task",
                    "type": "binary",
                    "url": None,
                    "store_fname": None,
                    "checksum": hashlib.sha1(b"source evidence").hexdigest(),
                    "mimetype": "text/plain",
                    "description": "Source evidence",
                    "public": False,
                    "db_datas": b"source evidence",
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                },
            ],
            "followers": [
                {
                    "id": self.source_follower_id,
                    "res_id": self.source_task_id,
                    "partner_id": self.source_partner_id,
                    "res_model": "project.task",
                },
            ],
            "follower_subtype_rel": [
                {
                    "follower_id": self.source_follower_id,
                    "subtype_id": self.source_message_subtype_id,
                },
            ],
            "activities": [
                {
                    "id": self.source_activity_id,
                    "res_id": self.source_task_id,
                    "activity_type_id": self.source_activity_type_id,
                    "user_id": self.source_user_id,
                    "res_model": "project.task",
                    "summary": "Follow up source decision",
                    "date_deadline": start.date(),
                    "date_done": None,
                    "note": "<p>Preserved activity.</p>",
                    "feedback": None,
                    "automated": False,
                    "active": True,
                    "create_uid": self.source_user_id,
                    "write_uid": self.source_user_id,
                    "create_date": start,
                    "write_date": start,
                },
            ],
            "activity_types": [
                {
                    "id": self.source_activity_type_id,
                    "name": {"en_US": "To-Do"},
                    "active": True,
                    "category": "default",
                    "summary": None,
                    "icon": "fa-tasks",
                    "decoration_type": "warning",
                    "delay_count": 0,
                    "delay_unit": "days",
                    "delay_from": "current_date",
                },
            ],
            "xmlids": {
                (
                    "mail.activity.type",
                    self.source_activity_type_id,
                ): "mail.mail_activity_data_todo",
                (
                    "mail.message.subtype",
                    self.source_message_subtype_id,
                ): "mail.mt_note",
            },
            "empty_documents_folder_count": 0,
            "project_document_count": 0,
            "project_collaborator_count": 0,
            "project_sales_link_count": 0,
            "linked_expense_ids": [],
        }
        rows["tasks"][1]["parent_id"] = self.source_blocker_id
        list_count_keys = {
            "companies": "companies",
            "partners": "partners",
            "users": "users",
            "projects": "projects",
            "tasks": "tasks",
            "project_stages": "project_stages",
            "task_stages": "task_stages",
            "tags": "tags",
            "activity_types": "activity_types",
            "milestones": "milestones",
            "recurrences": "recurrences",
            "updates": "updates",
            "project_task_stage_links": "project_task_stage_rel",
            "project_tag_links": "project_tag_rel",
            "project_favorite_links": "project_favorite_rel",
            "task_user_links": "task_user_rel",
            "task_tag_links": "task_tag_rel",
            "dependencies": "dependencies",
            "messages": "messages",
            "message_recipient_links": "message_partner_rel",
            "message_attachment_links": "message_attachment_rel",
            "tracking_values": "tracking_values",
            "followers": "followers",
            "follower_subtype_links": "follower_subtype_rel",
            "activities": "activities",
            "attachments": "attachments",
        }
        rows["counts"] = {
            name: len(rows[key])
            for name, key in list_count_keys.items()
        }
        rows["counts"].update(
            {
                "task_parent_links": 1,
                "task_milestone_links": 0,
                "task_recurrence_links": 0,
                "project_aliases": 1,
                "project_alias_names": 1,
                "project_analytic_links": 0,
                "linked_expenses": 0,
                "message_parent_links": 0,
            },
        )
        return rows

    def _task_row(self, source_id, name, *, start, deadline):
        audit_date = datetime(2026, 8, 1, 10)
        return {
            "id": source_id,
            "sequence": 10,
            "stage_id": self.source_stage_id,
            "project_id": self.source_project_id,
            "partner_id": None,
            "company_id": self.source_company_id,
            "color": 0,
            "displayed_image_id": None,
            "parent_id": None,
            "milestone_id": None,
            "recurrence_id": None,
            "access_token": f"token-{source_id}",
            "name": name,
            "priority": "1",
            "state": "01_in_progress",
            "email_from": None,
            "html_field_history": {},
            "duration_tracking": {
                "d": audit_date.strftime("%Y-%m-%d %H:%M:%S"),
                "s": self.source_stage_id,
                str(self.source_historical_stage_id): 45,
                str(self.source_stage_id): 120,
            },
            "task_properties": {},
            "description": "<p>Restored task</p>",
            "active": True,
            "recurring_task": False,
            "is_template": False,
            "date_end": None,
            "date_assign": audit_date,
            "date_deadline": deadline,
            "date_last_stage_update": audit_date,
            "allocated_hours": 4.0,
            "planned_date_begin": start,
            "create_uid": self.source_user_id,
            "write_uid": self.source_user_id,
            "create_date": audit_date,
            "write_date": audit_date,
        }

    def _run(self, payload):
        run = self.env["usl.project.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "target_database": self.env.cr.dbname,
            },
        )
        run.restore_from_payload(payload, filestore="/tmp")
        return run

    def test_restore_removes_only_untouched_native_onboarding_todos(self):
        user = self.env.user
        localized = user.with_context(lang=user.lang or self.env.user.lang)
        body = localized.env["ir.qweb"]._render(
            "project_todo.todo_user_onboarding",
            {"object": user},
            minimal_qcontext=True,
            raise_if_not_found=False,
        )
        values = {
            "name": localized.env._("Welcome %s!", user.name),
            "description": body,
            "user_ids": [Command.set(user.ids)],
        }
        generated = (
            self.env["project.task"]
            .sudo()
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(values)
        )
        edited = generated.copy({"description": "<p>Kept by the user</p>"})

        self._run(self._payload())

        self.assertFalse(generated.exists())
        self.assertTrue(edited.exists())
        self.assertEqual(edited.description, "<p>Kept by the user</p>")

    def test_project_documents_are_delegated_to_documents_archive(self):
        payload = self._payload()
        payload["project_document_count"] = 1

        run = self._run(payload)

        self.assertEqual(run.status, "passed", run.issue_ids.mapped("description"))
        exclusions = run.statistics_json["deliberate_exclusions"]
        self.assertEqual(exclusions["source_project_documents"], 1)
        self.assertIn(
            "Documents archive after Projects restoration",
            exclusions["source_project_documents_disposition"],
        )

    def test_restore_preserves_relationships_and_is_idempotent(self):
        payload = self._payload()
        mail_count = self.env["mail.mail"].sudo().search_count([])
        notification_count = self.env["mail.notification"].sudo().search_count([])
        first = self._run(deepcopy(payload))
        self.assertEqual(
            first.status,
            "passed",
            first.issue_ids.mapped("description"),
        )
        projects = self.env["project.project"].with_context(
            active_test=False,
        ).search(
            [
                ("rebuild_source_model", "=", "project.project"),
                ("rebuild_source_id", "=", self.source_project_id),
            ],
        )
        tasks = self.env["project.task"].with_context(active_test=False).search(
            [
                ("rebuild_source_model", "=", "project.task"),
                (
                    "rebuild_source_id",
                    "in",
                    [self.source_blocker_id, self.source_task_id],
                ),
            ],
        )
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(projects.id, self.source_project_id)
        self.assertEqual(
            set(tasks.ids),
            {self.source_blocker_id, self.source_task_id},
        )
        restored_stage = self.env["project.task.type"].with_context(
            active_test=False,
        ).browse(self.source_stage_id)
        historical_stage = self.env["project.task.type"].with_context(
            active_test=False,
        ).browse(self.source_historical_stage_id)
        self.assertEqual(restored_stage.rebuild_source_id, self.source_stage_id)
        self.assertEqual(restored_stage.id, self.source_stage_id)
        self.assertTrue(historical_stage.exists())
        self.assertFalse(historical_stage.active)
        self.assertEqual(
            historical_stage.rebuild_source_model,
            "project.task.type.history",
        )
        planned = tasks.filtered(
            lambda task: task.rebuild_source_id == self.source_task_id,
        )
        blocker = tasks.filtered(
            lambda task: task.rebuild_source_id == self.source_blocker_id,
        )
        self.assertEqual(planned.parent_id, blocker)
        self.assertEqual(planned.depend_on_ids, blocker)
        self.assertTrue(planned.usl_dependency_date_warning)
        self.assertEqual(planned.project_id.last_update_status, "at_risk")
        self.assertEqual(
            planned.project_id.alias_id.alias_name,
            "restore-private",
        )
        self.assertFalse(planned.date_end)
        source_duration = payload["tasks"][1]["duration_tracking"]
        self.assertEqual(planned.duration_tracking, source_duration)
        continuation_stage = self.env["project.task.type"].create(
            {"name": "Post-restore stage", "user_id": False},
        )
        self.assertGreater(continuation_stage.id, self.source_stage_id)
        planned.write({"stage_id": continuation_stage.id})
        transitioned_duration = deepcopy(planned.duration_tracking)
        self.assertEqual(transitioned_duration["s"], continuation_stage.id)
        self.assertEqual(
            transitioned_duration[str(self.source_historical_stage_id)],
            source_duration[str(self.source_historical_stage_id)],
        )
        self.assertGreaterEqual(
            transitioned_duration[str(self.source_stage_id)],
            source_duration[str(self.source_stage_id)],
        )
        self.assertNotEqual(transitioned_duration["d"], source_duration["d"])
        planned.write(
            {
                "state": "1_done",
                "date_end": datetime(2026, 8, 6, 10),
                "description": "<p>Target workflow continuation</p>",
            },
        )
        self.assertEqual(planned.state, "1_done")

        message_domain = [
            ("rebuild_source_model", "=", "mail.message"),
            ("rebuild_source_id", "=", self.source_message_id),
        ]
        attachment_domain = [
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_id", "=", self.source_attachment_id),
        ]
        activity_domain = [
            ("rebuild_source_model", "=", "mail.activity"),
            ("rebuild_source_id", "=", self.source_activity_id),
        ]
        tracking_domain = [
            ("rebuild_source_model", "=", "mail.tracking.value"),
            ("rebuild_source_id", "=", self.source_tracking_id),
        ]
        message = self.env["mail.message"].sudo().search(message_domain)
        attachment = self.env["ir.attachment"].sudo().search(attachment_domain)
        self.assertEqual(message.attachment_ids, attachment)
        self.assertEqual(bytes(attachment.raw), b"source evidence")
        self.assertEqual(message.tracking_value_ids.new_value_char, "1")
        self.assertEqual(
            self.env["mail.activity"].sudo().search_count(activity_domain),
            1,
        )
        self.assertEqual(
            self.env["mail.followers"].sudo().search_count(
                [
                    ("res_model", "=", "project.task"),
                    ("res_id", "=", planned.id),
                    ("partner_id", "=", message.author_id.id),
                ],
            ),
            1,
        )
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mail_count)
        self.assertEqual(
            self.env["mail.notification"].sudo().search_count([]),
            notification_count,
        )

        second = self._run(deepcopy(payload))
        self.assertEqual(second.status, "passed")
        planned.invalidate_recordset()
        self.assertEqual(planned.state, "1_done")
        self.assertTrue(planned.date_end)
        self.assertIn("Target workflow continuation", planned.description)
        self.assertEqual(planned.stage_id, continuation_stage)
        self.assertEqual(planned.duration_tracking, transitioned_duration)
        self.assertEqual(
            self.env["project.project"].with_context(
                active_test=False,
            ).search_count(
                [
                    ("rebuild_source_model", "=", "project.project"),
                    ("rebuild_source_id", "=", self.source_project_id),
                ],
            ),
            1,
        )
        self.assertEqual(
            self.env["project.task"].with_context(
                active_test=False,
            ).search_count(
                [
                    ("rebuild_source_model", "=", "project.task"),
                    (
                        "rebuild_source_id",
                        "in",
                        [self.source_blocker_id, self.source_task_id],
                    ),
                ],
            ),
            2,
        )
        self.assertEqual(
            self.env["mail.message"].sudo().search_count(message_domain),
            1,
        )
        self.assertEqual(
            self.env["ir.attachment"].sudo().search_count(attachment_domain),
            1,
        )
        self.assertEqual(
            self.env["mail.activity"].sudo().search_count(activity_domain),
            1,
        )
        self.assertEqual(
            self.env["mail.tracking.value"].sudo().search_count(
                tracking_domain,
            ),
            1,
        )
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mail_count)
        self.assertEqual(
            self.env["mail.notification"].sudo().search_count([]),
            notification_count,
        )

        next_project = self.env["project.project"].create(
            {"name": "Post-restore project"},
        )
        next_task = self.env["project.task"].create(
            {"name": "Post-restore task"},
        )
        self.assertGreater(next_project.id, self.source_project_id)
        self.assertGreater(next_task.id, self.source_task_id)

    def test_source_primary_id_collisions_fail_closed(self):
        target_project = self.env["project.project"].create(
            {"name": "Occupied project identity"},
        )
        target_task = self.env["project.task"].create(
            {"name": "Occupied task identity"},
        )
        run = self.env["usl.project.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "target_database": self.env.cr.dbname,
            },
        )
        with self.env.cr.savepoint(), self.assertRaisesRegex(
            RuntimeError,
            "Online ID.*is occupied",
        ):
            run._acquire_preserved_primary_id_control(
                {
                    "projects": [{"id": target_project.id}],
                    "tasks": [{"id": target_task.id}],
                    "task_stages": [],
                    "historical_task_stage_ids": [],
                },
            )

    def test_task_stage_ids_replace_only_unowned_target_fixtures(self):
        live_fixture, historical_fixture = self.env["project.task.type"].create(
            [
                {"name": "Generated live collision", "user_id": False},
                {"name": "Generated history collision", "user_id": False},
            ],
        )
        payload = self._payload()
        payload["task_stages"][0]["id"] = live_fixture.id
        payload["historical_task_stage_ids"] = [historical_fixture.id]
        payload["project_task_stage_rel"][0]["type_id"] = live_fixture.id
        for task in payload["tasks"]:
            task["stage_id"] = live_fixture.id
            task["duration_tracking"] = {
                "d": task["duration_tracking"]["d"],
                "s": live_fixture.id,
                str(historical_fixture.id): 45,
                str(live_fixture.id): 120,
            }

        run = self._run(payload)

        self.assertEqual(run.status, "passed", run.issue_ids.mapped("description"))
        restored_live = self.env["project.task.type"].with_context(
            active_test=False,
        ).browse(live_fixture.id)
        restored_historical = self.env["project.task.type"].with_context(
            active_test=False,
        ).browse(historical_fixture.id)
        self.assertEqual(restored_live.name, "Restored Stage")
        self.assertEqual(restored_live.rebuild_source_id, live_fixture.id)
        self.assertFalse(restored_historical.active)
        self.assertEqual(
            restored_historical.rebuild_source_model,
            "project.task.type.history",
        )

    def test_referenced_target_stage_collision_fails_closed(self):
        project = self.env["project.project"].create(
            {"name": "Target-owned project"},
        )
        stage = self.env["project.task.type"].create(
            {
                "name": "Occupied stage identity",
                "user_id": False,
                "project_ids": [Command.set(project.ids)],
            },
        )
        run = self.env["usl.project.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "target_database": self.env.cr.dbname,
            },
        )
        with self.env.cr.savepoint(), self.assertRaisesRegex(
            RuntimeError,
            "still own.*projects",
        ):
            run._acquire_preserved_primary_id_control(
                {
                    "projects": [],
                    "tasks": [],
                    "task_stages": [{"id": stage.id}],
                    "historical_task_stage_ids": [],
                },
            )

    def test_closed_recurrence_does_not_generate_target_only_task(self):
        payload = self._payload()
        recurrence_id = 9020
        payload["recurrences"] = [
            {
                "id": recurrence_id,
                "repeat_interval": 1,
                "repeat_unit": "month",
                "repeat_type": "forever",
                "repeat_until": None,
                "create_uid": self.source_user_id,
                "write_uid": self.source_user_id,
                "create_date": datetime(2026, 8, 1, 10),
                "write_date": datetime(2026, 8, 1, 10),
            },
        ]
        payload["tasks"][0].update(
            {
                "recurrence_id": recurrence_id,
                "recurring_task": True,
                "state": "1_done",
            },
        )
        payload["counts"].update(
            {
                "recurrences": 1,
                "task_recurrence_links": 1,
            },
        )

        first = self._run(deepcopy(payload))
        self.assertEqual(
            first.status,
            "passed",
            first.issue_ids.mapped("description"),
        )
        source_tasks = self.env["project.task"].with_context(
            active_test=False,
        ).search(
            [
                ("rebuild_source_model", "=", "project.task"),
                ("rebuild_source_snapshot", "=", "test_snapshot"),
            ],
        )
        self.assertEqual(len(source_tasks), 2)
        recurring = source_tasks.filtered("recurring_task")
        self.assertEqual(len(recurring), 1)
        self.assertEqual(recurring.recurrence_id.task_ids, recurring)

        second = self._run(deepcopy(payload))
        self.assertEqual(second.status, "passed")
        self.assertEqual(recurring.recurrence_id.task_ids, recurring)

    def test_private_project_respects_native_record_rules(self):
        payload = self._payload()
        self._run(payload)
        project = self.env["project.project"].search(
            [
                ("rebuild_source_model", "=", "project.project"),
                ("rebuild_source_id", "=", self.source_project_id),
            ],
        )
        outsider = self.env["res.users"].create(
            {
                "name": "Project Outsider",
                "login": "project.outsider@example.com",
                "email": "project.outsider@example.com",
                "group_ids": [
                    Command.link(
                        self.env.ref("project.group_project_user").id,
                    ),
                ],
            },
        )
        visible = (
            self.env["project.project"]
            .with_user(outsider)
            .search([("id", "=", project.id)])
        )
        self.assertFalse(visible)
        project.write({"privacy_visibility": "portal"})
        self.assertEqual(
            self.env["project.project"]
            .with_user(outsider)
            .search([("id", "=", project.id)]),
            project,
        )

    def test_project_company_boundary_uses_native_rules(self):
        other_company = self.env["res.company"].create(
            {"name": "Project Restore Other Company"},
        )
        cross_company_user = self.env["res.users"].create(
            {
                "name": "Cross Company Project User",
                "login": "cross.company.project@example.com",
                "email": "cross.company.project@example.com",
                "company_id": other_company.id,
                "company_ids": [Command.set([other_company.id])],
                "group_ids": [
                    Command.link(
                        self.env.ref("project.group_project_user").id,
                    ),
                ],
            },
        )
        company_project = self.env["project.project"].create(
            {
                "name": "Company Restricted Project",
                "company_id": self.env.company.id,
                "privacy_visibility": "employees",
            },
        )
        visible = (
            self.env["project.project"]
            .with_user(cross_company_user)
            .with_context(allowed_company_ids=[other_company.id])
            .search([("id", "=", company_project.id)])
        )
        self.assertFalse(visible)

    def test_user_login_disambiguates_shared_partner_email(self):
        shared_email = "shared.project.identity@example.com"
        system_user = self.env.ref("base.user_root")
        company_partner = self.env.company.partner_id
        company_partner.email = shared_email
        source_company_partner_id = 9101
        source_system_partner_id = 9102
        run = self.env["usl.project.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "target_database": self.env.cr.dbname,
            },
        )

        _companies, partners, _users = (
            run._restore_partners_companies_users(
                {
                    "companies": [
                        {
                            "id": self.source_company_id,
                            "name": self.env.company.name,
                        },
                    ],
                    "partners": [
                        {
                            "id": source_company_partner_id,
                            "name": company_partner.name,
                            "email": shared_email,
                            "active": True,
                            "is_company": True,
                            "company_id": None,
                        },
                        {
                            "id": source_system_partner_id,
                            "name": system_user.partner_id.name,
                            "email": shared_email,
                            "active": False,
                            "is_company": False,
                            "company_id": self.source_company_id,
                        },
                    ],
                    "users": [
                        {
                            "id": self.source_user_id,
                            "login": system_user.login,
                            "active": False,
                            "share": False,
                            "partner_id": source_system_partner_id,
                            "company_ids": [self.source_company_id],
                            "project_group_xmlids": [],
                        },
                    ],
                },
                "test_snapshot",
            )
        )

        self.assertEqual(
            partners[source_company_partner_id],
            company_partner,
        )
        self.assertEqual(
            partners[source_system_partner_id],
            system_user.partner_id,
        )
        self.assertNotEqual(
            partners[source_company_partner_id],
            partners[source_system_partner_id],
        )

    def test_partner_name_disambiguates_shared_email_without_user(self):
        shared_email = "shared.project.author@example.com"
        company_partner = self.env.company.partner_id
        system_partner = self.env.ref("base.partner_root")
        company_partner.email = shared_email
        source_company_partner_id = 9201
        source_system_partner_id = 9202
        run = self.env["usl.project.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "target_database": self.env.cr.dbname,
            },
        )
        company_partner.write(
            run._trace_values(
                "res.partner",
                source_company_partner_id,
                "accounting_snapshot",
            ),
        )

        _companies, partners, _users = (
            run._restore_partners_companies_users(
                {
                    "companies": [
                        {
                            "id": self.source_company_id,
                            "name": self.env.company.name,
                        },
                    ],
                    "partners": [
                        {
                            "id": source_company_partner_id,
                            "name": company_partner.name,
                            "email": shared_email,
                            "active": True,
                            "is_company": True,
                            "company_id": None,
                        },
                        {
                            "id": source_system_partner_id,
                            "name": system_partner.name,
                            "email": shared_email,
                            "active": False,
                            "is_company": False,
                            "company_id": self.source_company_id,
                        },
                    ],
                    "users": [],
                },
                "test_snapshot",
            )
        )

        self.assertEqual(
            partners[source_company_partner_id],
            company_partner,
        )
        self.assertEqual(
            partners[source_system_partner_id],
            system_partner,
        )
        self.assertEqual(
            company_partner.rebuild_source_id,
            source_company_partner_id,
        )
        self.assertEqual(
            system_partner.rebuild_source_id,
            source_system_partner_id,
        )
