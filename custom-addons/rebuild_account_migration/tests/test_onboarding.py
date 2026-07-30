from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestAccountingOnboarding(TransactionCase):
    def test_standard_invoicing_tour_is_consumed_for_administrator(self):
        administrator = self.env.ref("base.user_admin")
        account_tour = self.env.ref("account.account_tour")

        self.assertIn(administrator, account_tour.user_consumed_ids)

        administrator.sudo().tour_enabled = True
        current_tour = (
            self.env["web_tour.tour"]
            .with_user(administrator)
            .get_current_tour()
        )
        self.assertNotEqual(
            current_tour and current_tour["name"],
            account_tour.name,
        )
