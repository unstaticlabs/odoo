"""Verifying a reviewed file must not depend on being able to parse it."""

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_b2c_restore import private_evidence

CSV_EVIDENCE = "medusa-sold-items-2026-09-06.csv"


@tagged("post_install", "-at_install")
class TestPinnedEvidence(BaseCase):
    def test_a_reviewed_file_that_is_not_json_still_reports_its_availability(self):
        """`available` answers about the digest, so it must never parse."""
        mounted = private_evidence.available(CSV_EVIDENCE)
        self.assertIsInstance(mounted, bool)
        if not mounted:
            self.skipTest("The pinned evidence is not mounted here.")
        self.assertTrue(private_evidence.read(CSV_EVIDENCE))

    def test_a_file_outside_the_reviewed_set_is_refused(self):
        self.assertFalse(private_evidence.available("not-reviewed.json"))
        with self.assertRaises(private_evidence.MissingEvidenceError):
            private_evidence.read("not-reviewed.json")

    def test_every_pinned_file_is_readable_when_the_package_is_mounted(self):
        """A pin that no accessor can honour is a pin that was never checked."""
        if not private_evidence.available(CSV_EVIDENCE):
            self.skipTest("The pinned evidence is not mounted here.")
        for name in private_evidence.PINNED_EVIDENCE:
            with self.subTest(name=name):
                self.assertTrue(private_evidence.read(name))
