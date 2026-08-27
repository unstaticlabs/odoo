from odoo import Command
from odoo.tests.common import TransactionCase, tagged

from ..models.restore import parse_saved_filter_domain


class TestSavedFilterDomainParser(TransactionCase):
    def test_dynamic_uid_is_preserved_without_evaluation(self):
        domain = parse_saved_filter_domain('[("user_id", "=", uid)]')

        self.assertEqual(repr(domain), "[('user_id', '=', uid)]")

    def test_source_expressions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported saved-filter syntax"):
            parse_saved_filter_domain("[('date', '=', context_today())]")


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
