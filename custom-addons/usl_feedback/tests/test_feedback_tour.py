from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


class FeedbackTourCommon(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login=f"feedback-tour-{cls.__name__.lower()}",
            password="feedback-tour",
            groups="base.group_user",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.env["ir.config_parameter"].sudo().set_str(
            "usl.release.commit",
            "b" * 40,
        )


@tagged("post_install", "-at_install", "usl_feedback_tour")
class TestFeedbackDesktopTour(FeedbackTourCommon):
    browser_size = "1440x900"

    def test_desktop_feedback_tour(self):
        self.start_tour("/odoo", "usl_feedback_desktop_journey", login=self.user.login)
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.user.id)],
            limit=1,
        )
        self.assertEqual(task.name, "The desktop status is unclear after reload.")
        self.assertFalse(task.usl_feedback_context_included)


@tagged("post_install", "-at_install", "usl_feedback_tour", "mobile")
class TestFeedbackMobileTour(FeedbackTourCommon):
    browser_size = "390x844"

    def test_mobile_feedback_tour(self):
        self.start_tour("/odoo", "usl_feedback_mobile_journey", login=self.user.login)
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.user.id)],
            limit=1,
        )
        self.assertEqual(task.name, "The mobile status is unclear after reload.")
        self.assertFalse(task.usl_feedback_context_included)
