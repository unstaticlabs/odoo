from datetime import timedelta

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
            }
        )

    def _task(self, name, user=None, **values):
        return self.env["project.task"].create(
            {
                "name": name,
                "project_id": self.project.id,
                "stage_id": self.stage.id,
                "user_ids": [Command.link((user or self.project_user).id)],
                **values,
            }
        )

    def test_new_internal_user_receives_home_action(self):
        user = new_test_user(
            self.env,
            login="usl-home-default-action",
            groups="base.group_user",
        )
        self.assertEqual(user.action_id.id, self.env.ref("usl_home.action_usl_home").id)

    def test_layout_is_validated_and_isolated(self):
        Settings = self.env["res.users.settings"]
        mine = Settings._find_or_create_for_user(self.project_user)
        other = Settings._find_or_create_for_user(self.other_user)
        normalized = mine.with_user(self.project_user).set_usl_home_layout(
            {
                "version": 99,
                "order": ["favorites", "favorites", "unknown"],
                "hidden": ["activities", "unknown", "activities"],
            }
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
                }
            )
            expected.append(activity)
        result = self.env["usl.home.service"].with_user(
            self.project_user
        ).get_activities()
        self.assertEqual(len(result["items"]), 5)
        expected_ids = [activity.id for activity in sorted(
            expected, key=lambda activity: (activity.date_deadline, activity.id)
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
            self.project_user
        ).get_my_tasks()
        self.assertEqual(result["stages"][0]["name"], self.stage.name)
        self.assertGreaterEqual(result["signals"]["overdue"], 1)
        self.assertGreaterEqual(result["signals"]["due_soon"], 1)
        self.assertGreaterEqual(result["signals"]["waiting"], 1)
        self.assertGreaterEqual(result["signals"]["changes_requested"], 1)

    def test_ai_attention_uses_runtime_tags_and_assignment(self):
        tags = {
            name: self.env["project.tags"].create({"name": name})
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
            self.project_user
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
            }
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
            }
        )
        removed_task.unlink()
        summary = self.env["usl.home.service"].with_user(
            self.other_user
        )._favorite_summary(inaccessible.with_user(self.other_user))
        self.assertFalse(summary["available"])
        self.assertEqual(summary["name"], "Destination unavailable")

    def test_provider_favorite_resolves_native_my_tasks_action(self):
        favorite = self.env["usl.home.favorite"].with_user(self.project_user).create(
            {
                "name": "My Tasks",
                "target_type": "provider",
                "provider_key": "my_tasks",
            }
        )
        result = self.env["usl.home.service"].with_user(
            self.project_user
        ).resolve_favorite(favorite.id)
        self.assertTrue(result["available"])
        self.assertEqual(result["action"]["res_model"], "project.task")
