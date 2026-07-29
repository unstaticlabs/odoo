from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_pocketid_product")
class TestPocketIDProductProfiles(TransactionCase):
    def test_named_profiles_use_product_authorization_groups(self):
        provider = self.env.ref("usl_pocketid.provider_pocketid")
        provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_id": "offline-product-test",
                "auth_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
                "usl_oidc_issuer": "https://id.example.test",
                "usl_public_base_url": "https://odoo.example.test",
                "usl_required_group": "odoo-offline-test",
            },
        )
        company = self.env.company
        second_company = self.env["res.company"].create(
            {"name": "Pocket ID Isolated Company"},
        )
        administrator = {
            "login": "valentin.product@example.invalid",
            "name": "Valentin Product Test",
            "email": "valentin.product@example.invalid",
            "profile": "administrator",
            "companies": "all",
            "subject": "valentin-product-subject",
            "create_if_missing": True,
        }
        imported_valentin_partner = self.env["res.partner"].create(
            {
                "name": administrator["name"],
                "email": administrator["email"],
            },
        )
        collaborator = {
            "login": "roger.product@example.invalid",
            "name": "Roger Product Test",
            "email": "roger.product@example.invalid",
            "profile": "collaborator",
            "companies": [company.name],
            "subject": "roger-product-subject",
            "create_if_missing": True,
        }
        reviewer = {
            "login": "prosper.product@example.invalid",
            "name": "Prosper Product Test",
            "email": "prosper.product@example.invalid",
            "profile": "accountant_reviewer",
            "companies": [company.name],
            "subject": "prosper-product-subject",
            "create_if_missing": True,
        }
        configuration = [
            {
                "login": self.env.ref("base.user_admin").login,
                "profile": "break_glass",
                "companies": "all",
            },
            administrator,
            collaborator,
            reviewer,
        ]
        users = self.env["res.users"]
        first_summary = users._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="offline-break-glass-password",
            strict=True,
        )
        identity_count = self.env["usl.oidc.identity"].search_count([])
        second_summary = users._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="offline-break-glass-password",
            strict=True,
        )

        self.assertEqual(first_summary["configured_count"], 4)
        self.assertEqual(second_summary["configured_count"], 4)
        self.assertEqual(
            self.env["usl.oidc.identity"].search_count([]),
            identity_count,
        )
        self.assertEqual(identity_count, 3)

        valentin = users.search([("login", "=", administrator["login"])])
        self.assertTrue(valentin.usl_pocketid_access)
        self.assertEqual(valentin.partner_id, imported_valentin_partner)
        self.assertEqual(
            set(valentin.company_ids.ids),
            set((company | second_company).ids),
        )
        for group in (
            "base.group_system",
            "account.group_account_manager",
            "hr_expense.group_hr_expense_manager",
            "project.group_project_manager",
        ):
            self.assertTrue(valentin.has_group(group), group)

        roger = users.search([("login", "=", collaborator["login"])])
        self.assertTrue(roger.usl_pocketid_access)
        self.assertEqual(roger.company_ids, company)
        self.assertTrue(roger.has_group("project.group_project_user"))
        for group in (
            "base.group_system",
            "account.group_account_readonly",
            "hr.group_hr_user",
            "hr_expense.group_hr_expense_manager",
            "project.group_project_manager",
        ):
            self.assertFalse(roger.has_group(group), group)

        prosper = users.search([("login", "=", reviewer["login"])])
        self.assertTrue(prosper.usl_pocketid_access)
        self.assertEqual(prosper.company_ids, company)
        self.assertTrue(
            prosper.has_group(
                "rebuild_account_migration.group_rebuild_accountant_reviewer",
            ),
        )
        self.assertTrue(prosper.has_group("account.group_account_readonly"))
        self.assertFalse(prosper.has_group("account.group_account_user"))
        self.assertFalse(prosper.has_group("account.group_account_manager"))
        account_moves = self.env["account.move"].with_user(prosper)
        self.assertTrue(account_moves.has_access("read"))
        self.assertFalse(account_moves.has_access("write"))
        self.assertFalse(account_moves.has_access("create"))
        self.assertFalse(account_moves.has_access("unlink"))
        for model_name in (
            "account.payment",
            "account.bank.statement.line",
            "account.account.reconcile",
            "account.reconcile.model",
            "account.journal",
        ):
            protected_model = self.env[model_name].with_user(prosper)
            self.assertFalse(
                protected_model.has_access("write"),
                model_name,
            )
            self.assertFalse(
                protected_model.has_access("create"),
                model_name,
            )
            self.assertFalse(
                protected_model.has_access("unlink"),
                model_name,
            )
        with self.assertRaises(AccessError):
            valentin.with_user(prosper).write({"name": "Forbidden user edit"})

        break_glass = self.env.ref("base.user_admin")
        self.assertTrue(break_glass.usl_local_break_glass)
        self.assertFalse(break_glass.usl_pocketid_access)
        self.assertTrue(break_glass.has_group("base.group_system"))
