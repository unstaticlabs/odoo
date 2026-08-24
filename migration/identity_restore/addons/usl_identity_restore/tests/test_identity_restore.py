from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIdentityRestore(TransactionCase):
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
