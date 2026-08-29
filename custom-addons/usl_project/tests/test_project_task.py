from datetime import datetime, timedelta

from odoo import Command
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
