from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_home_tour")
class TestUslHomeTour(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login="usl-home-tour-user",
            password="home-tour",
            groups="project.group_project_user",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )

    def test_home_shell_and_personalization_tour(self):
        self.start_tour(
            "/odoo",
            "usl_home_core_journey",
            login=self.user.login,
        )
