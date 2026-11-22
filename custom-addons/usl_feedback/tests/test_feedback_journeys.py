import os
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_feedback_tour import FeedbackTourCommon


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestFeedbackJourneys(FeedbackTourCommon, JourneyCase):
    """The how-to for sending feedback, generated from the desktop journey.

    The fixture is the tour's own: an internal user with no other role and
    a synthetic message. The page it produces is committed under
    ``docs/users/how-to`` by ``make docs``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The fixture's generated name would show in the screenshots.
        cls.user.name = "Journey Reader"
        # The draft includes deployment details. Give the disposable release
        # the same identity whether the tour runs on a workstation or in CI.
        cls.enterClassContext(patch.dict(os.environ, {
            "USL_DEPLOYMENT_ENV": "development",
            "USL_RELEASE_COMMIT": cls.env["ir.config_parameter"].sudo().get_str("usl.release.commit"),
            "USL_GITOPS_COMMIT": "",
            "USL_DEPLOYMENT_GENERATION": "",
            "USL_RELEASE_MANIFEST_SHA256": "",
        }))
        # The assistant answers with its local, fixed scenarios when it is
        # enabled without a Gemini key, so the page shows a real reply and
        # nothing leaves the database.
        cls.env["ir.config_parameter"].sudo().set_str("usl_feedback.gemini_enabled", "True")

    def test_send_feedback_from_any_page(self):
        self.run_journey("usl_feedback_desktop_journey", "/odoo", login=self.user.login)
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.user.id)],
            limit=1,
        )
        expected = (
            "Le statut est difficile à comprendre après le rechargement."
            if self.journey_language == "fr"
            else "After I reload the page, the status is unclear."
        )
        self.assertEqual(task.name, expected)
        self.assertEqual(task.usl_feedback_agent_state, "triaged")

    def test_send_feedback_from_any_page_in_french(self):
        self.journey_language = "fr"
        self.test_send_feedback_from_any_page()
