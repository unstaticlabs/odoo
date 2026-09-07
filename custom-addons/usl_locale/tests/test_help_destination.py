import json

from odoo.tests import HttpCase, tagged

from odoo.addons.usl_locale.models.ir_http import USER_DOCS_URL


@tagged("post_install", "-at_install", "usl_locale")
class TestHelpDestination(HttpCase):
    def _session_info(self):
        """Return the session the web client is given, as it asks for it."""
        response = self.url_open(
            "/web/session/get_session_info",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]

    def test_help_opens_this_odoo_rather_than_a_price_list(self):
        """The user menu's Help entry is whatever support_url says it is."""
        self.authenticate("admin", "admin")

        support_url = self._session_info()["support_url"]

        self.assertEqual(support_url, USER_DOCS_URL)
        self.assertNotIn("odoo.com", support_url)

    def test_the_documentation_answers_where_help_sends_a_reader(self):
        """A Help link naming a page nobody serves is worse than none."""
        self.authenticate("admin", "admin")

        response = self.url_open(USER_DOCS_URL)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
