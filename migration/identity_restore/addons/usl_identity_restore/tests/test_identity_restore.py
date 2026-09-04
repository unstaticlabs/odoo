import os
from unittest.mock import patch

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.usl_identity_restore.models.restore import parse_saved_filter_domain


class TestSavedFilterDomainParser(TransactionCase):
    def test_dynamic_uid_is_preserved_without_evaluation(self):
        domain = parse_saved_filter_domain('[("user_id", "=", uid)]')

        self.assertEqual(repr(domain), "[('user_id', '=', uid)]")

    def test_source_expressions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported saved-filter syntax"):
            parse_saved_filter_domain("[('date', '=', context_today())]")


@tagged("post_install", "-at_install")
class TestIdentityRestore(TransactionCase):
    def test_source_company_partner_reuses_native_company_partner(self):
        run = self.env["usl.identity.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
            },
        )
        company = self.env["res.company"].create(
            {"name": "Restored Source Company"},
        )

        targets = run._company_partner_targets(
            [{"id": 71, "partner_id": 94}],
            {71: company},
        )

        self.assertEqual(targets, {94: company.partner_id})

    def test_valentin_home_uses_source_favorites_and_saved_views(self):
        if "usl.home.favorite" not in self.env.registry:
            self.skipTest("usl_home is not installed in this test registry")
        valentin = new_test_user(
            self.env,
            login="migration-home-valentin",
            groups="project.group_project_user",
        )
        project_names = [
            "Other favorite",
            "SBFH Vault",
            "GBC Ops",
            "USL admin",
            "SBFH admin",
        ]
        projects = self.env["project.project"].create([
            {
                "name": name,
                "favorite_user_ids": [Command.link(valentin.id)],
            }
            for name in project_names
        ])
        action = self.env.ref("project.action_view_my_task")
        filters = self.env["ir.filters"].create([
            {
                "name": "FY2526 Reconciliation",
                "model_id": "project.task",
                "domain": "[('priority', '=', '1')]",
                "context": "{}",
                "sort": "[]",
                "action_id": action.id,
                "user_ids": [Command.link(valentin.id)],
            },
            {
                "name": "Factures fournisseurs",
                "model_id": "project.task",
                "domain": "[]",
                "context": "{'group_by': []}",
                "sort": "[]",
                "action_id": action.id,
                "user_ids": [Command.link(valentin.id)],
            },
        ])
        run = self.env["usl.identity.restore.run"].create({
            "source_database": "test_source",
            "source_snapshot": "test_snapshot",
        })
        source = {
            "xmlids": [{
                "model": "res.users",
                "res_id": 2,
                "xmlid": "base.user_admin",
            }],
        }

        with patch.dict(
            os.environ,
            {"IDENTITY_MANAGER_TARGET_LOGIN": valentin.login},
        ):
            result = run._restore_valentin_home(
                source,
                {2: valentin},
                {14: filters[0], 6: filters[1]},
            )
            replay = run._restore_valentin_home(
                source,
                {2: valentin},
                {14: filters[0], 6: filters[1]},
            )

        favorites = self.env["usl.home.favorite"].sudo().search(
            [("user_id", "=", valentin.id)],
            order="sequence, id",
        )
        self.assertEqual(result, replay)
        self.assertEqual(len(favorites), result["favorite_count"])
        self.assertEqual(
            favorites[:5].mapped("name"),
            ["My Tasks", "USL admin", "SBFH admin", "SBFH Vault", "GBC Ops"],
        )
        self.assertNotIn(projects[0], projects.filtered(
            lambda project: project.id in result["project_ids"],
        ))
        self.assertEqual(
            favorites[-2:].mapped("filter_id"),
            filters,
        )
        settings = self.env["res.users.settings"].sudo()._find_or_create_for_user(
            valentin,
        )
        self.assertTrue(settings.usl_home_favorites_initialized)
        self.assertEqual(settings.usl_home_layout, result["layout"])
        self.assertEqual(
            valentin.action_id.id,
            self.env.ref("usl_home.action_usl_home").id,
        )

    def test_resume_inherits_completed_preferences_for_same_snapshot(self):
        runs = self.env["usl.identity.restore.run"]
        completed = {
            "filters": {"migrated": [6, 7], "target_ids": [22, 23]},
            "exports": {"native_recomputed": [1, 2]},
            "home": {"favorite_count": 7},
        }
        runs.create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "status": "passed",
                "statistics_json": {"preference_dispositions": completed},
            },
        )
        runs.create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
                "status": "passed",
                "statistics_json": {
                    "preference_dispositions": {"status": "deferred"},
                },
            },
        )
        current = runs.create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
            },
        )
        different_snapshot = runs.create(
            {
                "source_database": "test_source",
                "source_snapshot": "different_snapshot",
            },
        )

        self.assertEqual(
            current._completed_preference_dispositions(),
            completed,
        )
        self.assertFalse(
            different_snapshot._completed_preference_dispositions(),
        )

    def test_restored_user_does_not_keep_target_onboarding_todo(self):
        run = self.env["usl.identity.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "test_snapshot",
            },
        )
        tasks = self.env["project.task"].sudo().with_context(active_test=False)
        before = tasks.search_count([])
        partner = self.env["res.partner"].create(
            {
                "name": "Restored User",
                "email": "restored.user@example.com",
            },
        )

        user = run._create_restored_user(
            {
                "name": partner.name,
                "partner_id": partner.id,
                "login": partner.email,
                "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
            },
        )

        self.assertTrue(user.exists())
        self.assertEqual(tasks.search_count([]), before)
