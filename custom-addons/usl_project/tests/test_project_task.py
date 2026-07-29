from datetime import datetime, timedelta

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestProjectTask(TransactionCase):
    def test_planned_start_warns_only_for_open_late_blocker(self):
        start = datetime(2026, 8, 10, 9)
        blocker = self.env["project.task"].create(
            {
                "name": "Blocking task",
                "date_deadline": start + timedelta(days=2),
            },
        )
        task = self.env["project.task"].create(
            {
                "name": "Planned task",
                "planned_date_begin": start,
                "depend_on_ids": [Command.link(blocker.id)],
            },
        )

        self.assertTrue(task.usl_dependency_date_warning)
        blocker.state = "1_done"
        self.assertFalse(task.usl_dependency_date_warning)

    def test_product_models_have_no_project_restore_provenance(self):
        forbidden_fields = {
            "rebuild_source_database",
            "rebuild_source_model",
            "rebuild_source_id",
            "rebuild_source_snapshot",
            "rebuild_import_status",
            "rebuild_import_note",
            "usl_source_task_properties",
            "usl_source_task_properties_definition",
        }

        for model_name in (
            "project.project",
            "project.task",
            "project.project.stage",
            "project.task.type",
            "project.tags",
            "project.milestone",
            "project.task.recurrence",
            "project.update",
            "mail.message",
            "mail.activity",
            "mail.activity.type",
            "mail.tracking.value",
            "mail.alias",
            "mail.followers",
        ):
            self.assertFalse(
                forbidden_fields & set(self.env[model_name]._fields),
                f"{model_name} exposes migration-only provenance fields.",
            )
