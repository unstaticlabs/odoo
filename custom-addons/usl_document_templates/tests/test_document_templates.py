import base64
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import RedirectWarning, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import BinaryBytes


PDF = b"%PDF-1.7\n%%EOF\n"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
RENDER_RESULT = {
    "pdf": PDF,
    "template_revision": "test-revision",
    "payload_sha256": "a" * 64,
    "renderer_version": "1.0.0",
}


@tagged("post_install", "-at_install", "usl_document_templates")
class TestDocumentTemplates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.france = cls.env.ref("base.fr")
        cls.company = cls.env.company
        cls.company.write(
            {
                "street": "1 rue de la Paix",
                "zip": "75001",
                "city": "Paris",
                "country_id": cls.france.id,
                "logo": BinaryBytes(PNG),
                "company_registry": "983982950",
                "vat": "FR48983982950",
                "usl_document_legal_form": "SAS",
                "usl_document_share_capital": 1000,
                "usl_document_rcs_city": "Paris",
            }
        )
        if "ape" in cls.company._fields:
            cls.company.ape = "6201Z"
        cls.recipient = cls.env["res.partner"].create(
            {
                "name": "Acme France",
                "street": "20 avenue Victor Hugo",
                "zip": "69002",
                "city": "Lyon",
                "country_id": cls.france.id,
                "lang": "fr_FR",
            }
        )

    def _letter(self):
        return self.env["usl.document.letter"].create(
            {
                "date": "2026-08-27",
                "recipient_id": self.recipient.id,
                "subject": "Confirmation officielle",
                "signatory_id": self.env.user.id,
                "signatory_title": "Présidence",
                "body": (
                    "<h2>Objet</h2><p>Nous confirmons les éléments convenus.</p>"
                    "<ul><li>Premier élément</li><li>Second élément</li></ul>"
                ),
            }
        )

    def test_company_readiness_and_content_addressed_logo(self):
        self.assertTrue(self.company.usl_document_identity_ready)
        payload, assets = self.company._usl_document_renderer_company_payload(
            "fr_FR"
        )
        self.assertEqual(payload["logo_asset"], assets[0]["sha256"])
        self.assertEqual(base64.b64decode(assets[0]["data"]), PNG)
        self.assertIn("RCS Paris", payload["legal_identity_lines"][1])

    def test_default_odoo_logo_resolves_to_governed_unstatic_wordmark(self):
        self.company.logo = self.env["res.company"]._get_logo()

        payload, assets = self.company._usl_document_renderer_company_payload(
            "fr_FR"
        )

        self.assertTrue(self.company.uses_default_logo)
        self.assertEqual(payload["builtin_logo"], "unstatic")
        self.assertIsNone(payload["logo_asset"])
        self.assertFalse(assets)

    def test_legal_identity_uses_document_locale_not_operator_language(self):
        english, _assets = self.company.with_context(
            lang="fr_FR"
        )._usl_document_renderer_company_payload("en_US")
        french, _assets = self.company.with_context(
            lang="en_US"
        )._usl_document_renderer_company_payload("fr_FR")

        self.assertIn("with share capital", english["legal_identity_lines"][0])
        self.assertIn("au capital de", french["legal_identity_lines"][0])

    def test_french_identity_distinguishes_siren_and_siret(self):
        self.company.company_registry = "98398295000021"
        payload, _assets = self.company._usl_document_renderer_company_payload(
            "fr_FR"
        )

        self.assertIn("RCS Paris 983 982 950", payload["legal_identity_lines"][1])
        self.assertIn("SIRET 983 982 950 00021", payload["legal_identity_lines"][1])
        self.assertIn("TVA intracommunautaire", payload["legal_identity_lines"][2])

    def test_preview_pack_uses_the_selected_document_locale(self):
        settings = self.env["res.config.settings"].create(
            {"company_id": self.company.id}
        )

        english = settings.with_context(lang="fr_FR")._preview_documents("en_US")
        french = settings.with_context(lang="en_US")._preview_documents("fr_FR")

        self.assertEqual(english["invoice.v1"]["due_date_label"], "Due:")
        self.assertEqual(french["invoice.v1"]["due_date_label"], "Échéance :")
        self.assertEqual(
            french["accounting_statement.v1"]["title"],
            "Aperçu du compte de résultat",
        )

    def test_governed_report_policy_is_consistent(self):
        template = self.env.ref(
            "usl_document_templates.template_official_letter_v1"
        )
        with self.assertRaises(ValidationError):
            self.env["ir.actions.report"].create(
                {
                    "name": "Invalid governed report",
                    "model": "usl.document.letter",
                    "report_name": "usl_document_templates.report_document_letter_placeholder",
                    "report_type": "qweb-html",
                    "usl_output_policy": "latex",
                    "usl_document_template_id": template.id,
                }
            )

    def test_health_rejects_revision_drift(self):
        parameters = self.env["ir.config_parameter"].sudo()
        parameters.set_str(
            "usl_document_templates.renderer_expected_revision",
            "expected",
        )
        renderer = self.env["usl.document.renderer"]
        with patch.object(
            type(renderer),
            "_request",
            return_value={
                "status": "ok",
                "template_revision": "unexpected",
            },
        ):
            with self.assertRaisesRegex(UserError, "revision mismatch"):
                renderer.health()

    def test_letter_finalization_is_immutable_and_correction_is_versioned(self):
        letter = self._letter()
        renderer = self.env["usl.document.renderer"]
        with patch.object(
            type(renderer),
            "render",
            return_value=RENDER_RESULT,
        ):
            letter.action_finalize()

        self.assertEqual(letter.state, "finalized")
        self.assertEqual(letter.finalized_snapshot["date"], "2026-08-27")
        self.assertEqual(letter.finalized_attachment_id.raw.content, PDF)
        self.assertEqual(
            letter.finalized_attachment_id.usl_document_payload_sha256,
            "a" * 64,
        )
        with self.assertRaisesRegex(UserError, "immutable"):
            letter.subject = "Changed after finalization"

        correction_action = letter.action_create_correction()
        correction = self.env["usl.document.letter"].browse(
            correction_action["res_id"]
        )
        self.assertEqual(correction.state, "draft")
        self.assertEqual(correction.version, 2)
        self.assertEqual(correction.supersedes_id, letter)
        self.assertNotEqual(correction.reference, letter.reference)

    def test_letter_lifecycle_cannot_be_forged_through_write(self):
        letter = self._letter()
        with self.assertRaisesRegex(UserError, "managed by the correspondence workflow"):
            letter.write({"state": "finalized"})
        with self.assertRaisesRegex(UserError, "managed by the correspondence workflow"):
            letter.write({"finalized_snapshot": {"forged": True}})

    def test_letter_never_silently_falls_back_when_renderer_is_disabled(self):
        letter = self._letter()
        self.company.usl_document_renderer_enabled = False
        with self.assertRaisesRegex(RedirectWarning, "renderer is disabled"):
            letter.action_finalize()

    def test_snapshot_date_drives_rendered_correction(self):
        letter = self._letter()
        snapshot = letter._current_snapshot()
        letter.date = fields.Date.from_string("2026-09-01")
        payload = letter._document_payload_from_snapshot(snapshot)
        self.assertEqual(payload["date"], "2026-08-27")

    def test_unknown_rich_text_block_is_rejected(self):
        letter = self._letter()
        letter.body = "<blockquote>Unsupported official content.</blockquote>"
        with self.assertRaisesRegex(ValidationError, "unsupported"):
            letter._body_blocks()

    def test_letter_requires_complete_recipient_identity(self):
        letter = self._letter()
        self.recipient.street = False
        with self.assertRaisesRegex(ValidationError, "complete postal address"):
            letter._current_snapshot()

    def test_letter_rejects_cross_company_printed_attachment(self):
        other_company = self.env["res.company"].create({"name": "Other company"})
        attachment = self.env["ir.attachment"].create(
            {
                "name": "foreign-evidence.pdf",
                "raw": PDF,
                "mimetype": "application/pdf",
                "company_id": other_company.id,
            }
        )
        letter = self._letter()

        with self.assertRaisesRegex(UserError, "company inconsistencies"):
            letter.attachment_ids = attachment
