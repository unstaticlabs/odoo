from datetime import datetime, timedelta
from pathlib import Path
from runpy import run_path

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestProjectTask(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        alias_domain = cls.env["mail.alias.domain"].create({
            "name": "todo-assignment.example.invalid",
        })
        cls.todo_alias = cls.env["mail.alias"].create({
            "alias_name": "todo",
            "alias_domain_id": alias_domain.id,
            "alias_model_id": cls.env["ir.model"]._get_id("project.task"),
        })
        cls.other_alias = cls.env["mail.alias"].create({
            "alias_name": "other-tasks",
            "alias_domain_id": alias_domain.id,
            "alias_model_id": cls.env["ir.model"]._get_id("project.task"),
        })
        cls.todo_sender = new_test_user(
            cls.env,
            login="todo.sender@example.invalid",
            email="todo.sender@example.invalid",
            groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )

    def _task_from_email(self, recipient, sender="External <outside@example.invalid>"):
        return self.env["project.task"].message_new({
            "subject": "Incoming personal task",
            "email_from": sender,
            "to": recipient,
        })

    def test_todo_email_assigns_internal_sender(self):
        task = self._task_from_email(
            self.todo_alias.alias_full_name,
            f"Todo Sender <{self.todo_sender.email}>",
        )

        self.assertFalse(task.project_id)
        self.assertEqual(task.user_ids, self.todo_sender)

    def test_todo_email_keeps_external_sender_unassigned(self):
        task = self._task_from_email(self.todo_alias.alias_full_name)

        self.assertFalse(task.project_id)
        self.assertFalse(task.user_ids)

    def test_other_projectless_alias_does_not_assign_sender(self):
        task = self._task_from_email(
            self.other_alias.alias_full_name,
            f"Todo Sender <{self.todo_sender.email}>",
        )

        self.assertFalse(task.project_id)
        self.assertFalse(task.user_ids)

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

    def test_task_form_uses_only_native_stage_duration_control(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        self.assertFalse(form_arch.xpath(
            "//section[contains(@class, 'o_usl_task_stage_duration')]",
        ))
        native_duration_controls = form_arch.xpath(
            "//field[@name='stage_id'][@widget='rotting_statusbar_duration']",
        )
        self.assertEqual(len(native_duration_controls), 1)

    def test_upgrade_merges_translated_duplicate_personal_stages(self):
        task_in_english_stage = self.env["project.task"].create({
            "name": "English personal-stage task",
            "user_ids": [Command.set(self.todo_sender.ids)],
        })
        task_in_french_stage = self.env["project.task"].create({
            "name": "French personal-stage task",
            "user_ids": [Command.set(self.todo_sender.ids)],
        })
        existing_stage = self.env["project.task.type"].search([
            ("user_id", "=", self.todo_sender.id),
            ("name", "=", "This Week"),
        ], limit=1)
        duplicate_stage = self.env["project.task.type"].create({
            "name": "Cette semaine",
            "sequence": existing_stage.sequence + 1,
            "user_id": self.todo_sender.id,
        })
        task_in_english_stage.with_user(
            self.todo_sender,
        ).personal_stage_type_id = existing_stage
        task_in_french_stage.with_user(
            self.todo_sender,
        ).personal_stage_type_id = duplicate_stage
        migration = run_path(
            Path(__file__).parents[1]
            / "migrations"
            / "saas~19.3.1.0.9"
            / "post-merge-default-personal-stages.py",
        )

        migration["migrate"](self.env.cr, "saas~19.3.1.0.8")
        self.env.invalidate_all()

        active_week_stages = self.env["project.task.type"].search([
            ("user_id", "=", self.todo_sender.id),
            ("id", "in", [existing_stage.id, duplicate_stage.id]),
        ])
        self.assertEqual(active_week_stages, existing_stage)
        self.assertEqual(
            task_in_english_stage.with_user(
                self.todo_sender,
            ).personal_stage_type_id,
            existing_stage,
        )
        self.assertEqual(
            task_in_french_stage.with_user(
                self.todo_sender,
            ).personal_stage_type_id,
            existing_stage,
        )
        self.assertEqual(
            existing_stage.with_context(lang="en_US").name,
            "This Week",
        )
        self.assertEqual(
            existing_stage.with_context(lang="fr_FR").name,
            "Cette semaine",
        )
        self.assertFalse(
            duplicate_stage.with_context(active_test=False).active,
        )

        migration["migrate"](self.env.cr, "saas~19.3.1.0.8")
        self.env.invalidate_all()
        self.assertEqual(
            self.env["project.task.type"].search_count([
                ("user_id", "=", self.todo_sender.id),
                ("id", "in", [existing_stage.id, duplicate_stage.id]),
            ]),
            1,
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
