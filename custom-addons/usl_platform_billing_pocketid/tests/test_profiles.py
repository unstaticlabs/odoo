from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_platform_billing_pocketid")
class TestPlatformBillingPocketIDProfiles(TransactionCase):
    def test_only_governed_administrators_receive_platform_management(self):
        definitions = self.env[
            "res.users"
        ]._usl_pocketid_profile_definitions()
        platform_group = (
            "usl_platform_billing.group_platform_billing_manager"
        )

        self.assertIn(platform_group, definitions["administrator"]["groups"])
        self.assertIn(platform_group, definitions["break_glass"]["groups"])
        for profile in (
            "collaborator",
            "portal",
            "historical",
            "decision",
        ):
            self.assertNotIn(
                platform_group,
                definitions[profile]["groups"] or (),
                profile,
            )
