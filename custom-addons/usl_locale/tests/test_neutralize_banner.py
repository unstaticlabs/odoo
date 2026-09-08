import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_locale")
class TestNeutralizedBannerNamesTheRelease(TransactionCase):
    """A tester on staging must be able to tell which release they are on."""

    COMMIT = "0801ff5b7cf6a1b2c3d4e5f60718293a4b5c6d7e"

    def _set_neutralized(self, value):
        self.env["ir.config_parameter"].sudo().set_bool(
            "database.is_neutralized", value,
        )

    def test_banner_names_the_release_on_a_neutralized_database(self):
        self._set_neutralized(True)
        with patch.dict(os.environ, {"USL_RELEASE_COMMIT": self.COMMIT}):
            text = self.env["ir.http"]._usl_neutralize_banner_text(True)

        self.assertIn("neutralized", text)
        self.assertIn(self.COMMIT[:12], text)

    def test_a_database_that_is_not_neutralized_gets_no_banner(self):
        """Production is not neutralized, so no release is ever named there.

        The end-to-end guarantee is asserted through the rendering context
        below, which reads the real parameter; this pins the helper's own
        contract.
        """
        with patch.dict(os.environ, {"USL_RELEASE_COMMIT": self.COMMIT}):
            self.assertEqual(self.env["ir.http"]._usl_neutralize_banner_text(False), "")

    def test_an_untrusted_commit_leaves_the_stock_banner_alone(self):
        """Naming a release we cannot vouch for is worse than naming none."""
        self._set_neutralized(True)
        for value in ("", "unknown", "not-a-sha", "0801ff5", "0801ff5b7cf6" * 4):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"USL_RELEASE_COMMIT": value}):
                    self.assertEqual(
                        self.env["ir.http"]._usl_neutralize_banner_text(True), "",
                    )

    def test_rendering_context_carries_the_banner_only_when_there_is_one(self):
        # session_info() needs an HTTP request, which a TransactionCase has
        # not got; stub only that so the override's own contribution is what
        # this asserts.
        model = self.env["ir.http"]
        stub = patch.object(type(model), "session_info", return_value={})

        self._set_neutralized(True)
        with stub, patch.dict(os.environ, {"USL_RELEASE_COMMIT": self.COMMIT}):
            context = model.webclient_rendering_context()
        self.assertIn(self.COMMIT[:12], context["neutralize_banner_text"])
        # The stock keys must survive the override.
        self.assertIn("session_info", context)
        self.assertIn("color_scheme", context)

        self._set_neutralized(False)
        with stub:
            context = model.webclient_rendering_context()
        self.assertNotIn("neutralize_banner_text", context)
        self.assertIn("session_info", context)
