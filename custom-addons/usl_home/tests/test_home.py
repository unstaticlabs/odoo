from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_home")
class TestUslHome(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_user = new_test_user(
            cls.env,
            login="usl-home-project-user",
            groups="project.group_project_user",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="usl-home-restricted-user",
            groups="base.group_user",
        )
        cls.project = cls.env["project.project"].create({"name": "Home test project"})
        cls.stage = cls.env["project.task.type"].create(
            {
                "name": "Home In Progress",
                "project_ids": [Command.link(cls.project.id)],
            },
        )

    def _task(self, name, user=None, **values):
        return self.env["project.task"].create(
            {
                "name": name,
                "project_id": self.project.id,
                "stage_id": self.stage.id,
                "user_ids": [Command.link((user or self.project_user).id)],
                **values,
            },
        )

    def test_new_internal_user_receives_home_action(self):
        user = new_test_user(
            self.env,
            login="usl-home-default-action",
            groups="base.group_user",
        )
        self.assertEqual(user.action_id.id, self.env.ref("usl_home.action_usl_home").id)

        explicit_action = self.env.ref("project.action_view_my_task")
        explicitly_configured = new_test_user(
            self.env,
            login="usl-home-explicit-action",
            groups="project.group_project_user",
            action_id=explicit_action.id,
        )
        self.assertEqual(explicitly_configured.action_id.id, explicit_action.id)

    def test_layout_is_validated_and_isolated(self):
        Settings = self.env["res.users.settings"]
        mine = Settings._find_or_create_for_user(self.project_user)
        other = Settings._find_or_create_for_user(self.other_user)
        normalized = mine.with_user(self.project_user).set_usl_home_layout(
            {
                "version": 99,
                "order": ["favorites", "favorites", "unknown"],
                "hidden": ["activities", "unknown", "activities"],
            },
        )
        self.assertEqual(normalized["version"], 1)
        self.assertEqual(normalized["order"][0], "favorites")
        self.assertNotIn("unknown", normalized["order"])
        self.assertEqual(normalized["hidden"], ["activities"])
        self.assertNotEqual(other.usl_home_layout, mine.usl_home_layout)

    def test_activities_are_accessible_limited_and_deterministic(self):
        today = fields.Date.today()
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        expected = []
        for index, offset in enumerate([-4, -2, 0, 0, 1, 2, 3], start=1):
            task = self._task(f"Activity target {index}")
            activity = self.env["mail.activity"].create(
                {
                    "activity_type_id": activity_type.id,
                    "summary": f"Attention {index}",
                    "date_deadline": today + timedelta(days=offset),
                    "user_id": self.project_user.id,
                    "res_model_id": self.env["ir.model"]._get_id("project.task"),
                    "res_id": task.id,
                },
            )
            expected.append(activity)
        result = self.env["usl.home.service"].with_user(
            self.project_user,
        ).get_activities()
        self.assertEqual(len(result["items"]), 5)
        expected_ids = [activity.id for activity in sorted(
            expected, key=lambda activity: (activity.date_deadline, activity.id),
        )[:5]]
        self.assertEqual([item["id"] for item in result["items"]], expected_ids)
        self.assertEqual(result["items"][0]["bucket"], "overdue")
        self.assertEqual(result["items"][2]["bucket"], "today")

    def test_my_tasks_uses_actual_stages_and_attention_states(self):
        today = fields.Date.today()
        self._task("Overdue", date_deadline=today - timedelta(days=1))
        self._task("Due soon", date_deadline=today + timedelta(days=2))
        self._task("Waiting", state="04_waiting_normal")
        self._task("Changes", state="02_changes_requested")
        result = self.env["usl.home.service"].with_user(
            self.project_user,
        ).get_my_tasks()
        self.assertEqual(result["stages"][0]["name"], self.stage.name)
        self.assertGreaterEqual(result["signals"]["overdue"], 1)
        self.assertGreaterEqual(result["signals"]["due_soon"], 1)
        self.assertGreaterEqual(result["signals"]["waiting"], 1)
        self.assertGreaterEqual(result["signals"]["changes_requested"], 1)

    def test_project_widget_and_aggregate_are_omitted_without_access(self):
        service = self.env["usl.home.service"].with_user(self.other_user)
        # Community grants project.task read access broadly to internal users;
        # emulate a distribution/profile that genuinely removes that ACL and
        # protect the permission-sensitive branch explicitly.
        with patch.object(type(service), "_model_is_readable", return_value=False):
            self.assertNotIn("my_tasks", service._available_widgets())
            with self.assertRaises(AccessError):
                service.get_my_tasks()

    def test_ai_attention_uses_runtime_tags_and_assignment(self):
        tags = {
            name: self.env["project.tags"].search([("name", "=", name)], limit=1)
            or self.env["project.tags"].create({"name": name})
            for name in ("Agent Ready", "Agent Failed", "Needs Human", "Blocked")
        }
        failed = self._task(
            "Failed agent work",
            tag_ids=[Command.set([tags["Agent Failed"].id])],
        )
        review = self._task(
            "Human handoff",
            tag_ids=[Command.set([tags["Needs Human"].id])],
        )
        self._task(
            "Ready only",
            tag_ids=[Command.set([tags["Agent Ready"].id])],
        )
        self._task(
            "Other user's failure",
            user_ids=[Command.set([self.other_user.id])],
            tag_ids=[Command.set([tags["Agent Failed"].id])],
        )
        result = self.env["usl.home.service"].with_user(
            self.project_user,
        ).get_ai_attention()
        self.assertEqual([item["id"] for item in result["items"]], [failed.id, review.id])
        self.assertEqual([item["status"] for item in result["items"]], ["failed", "review"])

    def test_favorites_are_private_and_unavailable_targets_are_generic(self):
        favorite = self.env["usl.home.favorite"].with_user(self.project_user).create(
            {
                "name": "Private task destination",
                "target_type": "record",
                "res_model": "project.task",
                "res_id": self._task("Private favorite").id,
            },
        )
        visible = self.env["usl.home.favorite"].with_user(self.project_user).search([])
        hidden = self.env["usl.home.favorite"].with_user(self.other_user).search([])
        self.assertIn(favorite, visible)
        self.assertNotIn(favorite, hidden)
        with self.assertRaises(AccessError):
            favorite.with_user(self.other_user).write({"name": "Cross-user edit"})

        removed_task = self._task("Removed protected target")
        inaccessible = self.env["usl.home.favorite"].sudo().create(
            {
                "user_id": self.other_user.id,
                "name": "Protected record label",
                "target_type": "record",
                "res_model": "project.task",
                "res_id": removed_task.id,
            },
        )
        removed_task.unlink()
        summary = self.env["usl.home.service"].with_user(
            self.other_user,
        )._favorite_summary(inaccessible.with_user(self.other_user))
        self.assertFalse(summary["available"])
        self.assertEqual(summary["name"], "Destination unavailable")
        self.assertFalse(summary["kind_label"])
        self.assertEqual(summary["icon"], "destination")

    def test_provider_favorite_resolves_native_my_tasks_action(self):
        favorite = self.env["usl.home.favorite"].with_user(self.project_user).create(
            {
                "name": "My Tasks",
                "target_type": "provider",
                "provider_key": "my_tasks",
            },
        )
        result = self.env["usl.home.service"].with_user(
            self.project_user,
        ).resolve_favorite(favorite.id)
        self.assertTrue(result["available"])
        self.assertEqual(result["action"]["res_model"], "project.task")
        summary = self.env["usl.home.service"].with_user(
            self.project_user,
        )._favorite_summary(favorite)
        self.assertEqual(summary["kind_label"], "Project")
        self.assertEqual(summary["icon"], "tasks")

    def test_saved_view_reconstructs_exact_native_action_and_is_user_private(self):
        action = self.env.ref("project.action_view_my_task")
        favorite = self.env["usl.home.favorite"].with_user(self.project_user).create(
            {
                "name": "My focused tasks",
                "target_type": "view",
                "action_id": action.id,
                "action_xmlid": "project.action_view_my_task",
                "view_mode": "kanban,list",
                "domain_json": [["priority", "=", "1"]],
                "context_json": {"search_default_open_tasks": 1},
                "group_by_json": ["stage_id"],
                "order_by_json": [{"name": "date_deadline", "asc": True}],
            },
        )
        result = self.env["usl.home.service"].with_user(
            self.project_user,
        ).resolve_favorite(favorite.id)
        self.assertTrue(result["available"])
        self.assertEqual(result["action"]["domain"], [["priority", "=", "1"]])
        self.assertEqual(result["action"]["view_mode"], "kanban,list")
        self.assertEqual(result["action"]["context"]["group_by"], ["stage_id"])
        self.assertEqual(
            result["action"]["context"]["orderedBy"],
            [{"name": "date_deadline", "asc": True}],
        )
        self.assertFalse(
            self.env["usl.home.service"].with_user(self.other_user).resolve_favorite(
                favorite.id,
            )["available"],
        )

    def test_accounting_widget_uses_only_the_active_company_and_hides_from_restricted_user(self):
        if "rebuild.account.overview" not in self.env.registry:
            self.skipTest("Accounting overview is not installed in the minimal dependency graph")
        other_company = self.env["res.company"].create({"name": "Home accounting company"})
        accounting_user = new_test_user(
            self.env,
            login="usl-home-accounting-user",
            groups="account.group_account_manager",
            company_id=self.env.company.id,
            company_ids=[Command.set((self.env.company | other_company).ids)],
        )
        service = self.env["usl.home.service"].with_user(accounting_user)
        first = service.with_company(self.env.company).get_accounting_alerts()
        second = service.with_company(other_company).get_accounting_alerts()
        self.assertEqual(first["company"]["id"], self.env.company.id)
        self.assertEqual(second["company"]["id"], other_company.id)
        self.assertNotEqual(first["company"]["id"], second["company"]["id"])

        restricted_service = self.env["usl.home.service"].with_user(self.other_user)
        self.assertNotIn("accounting", restricted_service._available_widgets())
        with self.assertRaises(AccessError):
            restricted_service.get_accounting_alerts()
