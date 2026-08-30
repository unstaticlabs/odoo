from datetime import datetime, timedelta

from psycopg2.extras import Json

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestProjectTask(TransactionCase):
    def test_project_card_company_label_tracks_active_companies(self):
        second_company = self.env["res.company"].create({
            "name": "Second card company",
        })
        self.env.user.company_ids = [Command.link(second_company.id)]
        project = self.env["project.project"].create({
            "name": "Multi-company card project",
            "company_id": self.env.company.id,
        })

        single_company_project = project.with_context(
            allowed_company_ids=[self.env.company.id],
        )
        multi_company_project = project.with_context(
            allowed_company_ids=[self.env.company.id, second_company.id],
        )

        self.assertFalse(single_company_project.usl_show_company_on_card)
        self.assertTrue(multi_company_project.usl_show_company_on_card)

        arch = self.env.ref("project.view_project_kanban")._get_combined_arch()
        company_labels = arch.xpath(
            "//t[@t-name='card']//*[@t-if="
            "'record.usl_show_company_on_card.raw_value']",
        )
        self.assertEqual(len(company_labels), 1)
        self.assertTrue(company_labels[0].xpath(".//field[@name='company_id']"))

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

    def test_task_form_exposes_preserved_current_and_historical_stage_time(self):
        historical_stage, current_stage = self.env["project.task.type"].create([
            {"name": "Online archive", "active": False, "sequence": 1},
            {"name": "In progress", "sequence": 2},
        ])
        project = self.env["project.project"].create({
            "name": "Preserved stage history",
            "type_ids": [Command.set([current_stage.id])],
        })
        task = self.env["project.task"].create({
            "name": "Historical task",
            "project_id": project.id,
            "stage_id": current_stage.id,
        })
        ledger = {
            "d": fields.Datetime.to_string(
                fields.Datetime.now() - timedelta(minutes=65),
            ),
            "s": current_stage.id,
            str(historical_stage.id): 1_501,
            str(current_stage.id): 30,
        }
        task.flush_recordset(["duration_tracking"])
        self.env.cr.execute(
            "UPDATE project_task SET duration_tracking = %s WHERE id = %s",
            [Json(ledger), task.id],
        )
        task.invalidate_recordset([
            "duration_tracking",
            "usl_stage_duration_history",
        ])

        summary = str(task.usl_stage_duration_history)
        self.assertIn("Online archive", summary)
        self.assertIn("In progress", summary)
        self.assertIn("1 d 1 h 1 min", summary)
        self.assertIn("Historical stage", summary)
        self.assertIn("Current stage", summary)

        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        history_sections = form_arch.xpath(
            "//section[contains(@class, 'o_usl_task_stage_duration')]",
        )
        self.assertEqual(len(history_sections), 1)
        self.assertTrue(
            history_sections[0].xpath(
                ".//field[@name='usl_stage_duration_history'][@readonly='1']",
            ),
        )

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
            if model_name not in self.env.registry:
                continue
            self.assertFalse(
                forbidden_fields & set(self.env[model_name]._fields),
                f"{model_name} exposes migration-only provenance fields.",
            )
