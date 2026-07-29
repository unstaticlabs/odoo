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
                    "alias_id": None,
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
        }
        count_keys = (
            "projects",
            "tasks",
            "project_stages",
            "task_stages",
            "tags",
            "milestones",
            "recurrences",
            "updates",
            "dependencies",
            "messages",
            "tracking_values",
            "followers",
            "activities",
            "attachments",
        )
        rows["counts"] = {key: len(rows[key]) for key in count_keys}
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

    def test_restore_preserves_relationships_and_is_idempotent(self):
        payload = self._payload()
        first = self._run(deepcopy(payload))
        self.assertEqual(first.status, "passed")
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
        planned = tasks.filtered(
            lambda task: task.rebuild_source_id == self.source_task_id,
        )
        blocker = tasks.filtered(
            lambda task: task.rebuild_source_id == self.source_blocker_id,
        )
        self.assertEqual(planned.depend_on_ids, blocker)
        self.assertTrue(planned.usl_dependency_date_warning)
        self.assertEqual(planned.project_id.last_update_status, "at_risk")
        self.assertFalse(planned.date_end)

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
        self.assertEqual(attachment.raw, b"source evidence")
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

        second = self._run(deepcopy(payload))
        self.assertEqual(second.status, "passed")
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
