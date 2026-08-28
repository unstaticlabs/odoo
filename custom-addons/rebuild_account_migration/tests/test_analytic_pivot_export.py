from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

COMPANY_PAYLOAD = {
    "name": "USL Test",
    "builtin_logo": "unstatic",
    "primary_color": "#714B67",
    "footer_label": "USL Test · Comptabilité",
    "legal_identity_lines": ["USL Test"],
}
RENDER_RESULT = {
    "pdf": b"%PDF-test",
    "template_revision": "test-revision",
    "payload_sha256": "0" * 64,
    "renderer_version": "test",
}


@tagged("post_install", "-at_install", "accounting_reports")
class TestAnalyticPivotPdf(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].create({"name": "PDF plan"})
        cls.account = cls.env["account.analytic.account"].create({
            "name": "Professional services",
            "plan_id": plan.id,
            "company_id": cls.env.company.id,
        })
        cls.line = cls.env["account.analytic.line"].create({
            "name": "Analytic pivot evidence",
            "date": "2026-08-28",
            "account_id": cls.account.id,
            "company_id": cls.env.company.id,
            "amount": 1250.0,
        })

    def _payload(self, **overrides):
        return {
            "row_axes": ["account_id"],
            "column_axes": ["date:quarter"],
            "measures": ["amount"],
            "domain": [["id", "=", self.line.id]],
            "context": {"lang": "fr_FR", "tz": "Europe/Paris"},
            "order": {"measure": "amount", "direction": "desc"},
            "company_id": self.env.company.id,
            **overrides,
        }

    def test_snapshot_is_reaggregated_and_rendered_as_typed_matrix(self):
        renderer = self.env["usl.document.renderer"]
        with (
            patch.object(
                type(self.env.company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(
                type(renderer),
                "render",
                return_value=RENDER_RESULT,
            ) as render,
        ):
            result = self.env[
                "account.analytic.line"
            ]._usl_analytic_pivot_document(self._payload())

        self.assertEqual(result, RENDER_RESULT)
        template, _company, document = render.call_args.args[:3]
        self.assertEqual(template.key, "accounting_statement.v2")
        self.assertEqual(document["layout_variant"], "pivot")
        self.assertEqual(document["orientation"], "landscape")
        self.assertEqual(document["sections"][0]["rows"][-1]["role"], "total")
        self.assertIn("Professional services", document["sections"][0]["rows"][0]["values"]["label"])
        self.assertTrue(
            any("1 250,00" in value for value in document["sections"][0]["rows"][0]["values"].values()),
        )

    def test_unknown_axis_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["account.analytic.line"]._usl_validate_pivot_request(
                self._payload(row_axes=["create_uid"]),
            )

    def test_company_outside_allowed_scope_is_rejected(self):
        other = self.env["res.company"].create({"name": "Forbidden company"})
        with self.assertRaises(AccessError):
            self.env["account.analytic.line"].with_context(
                allowed_company_ids=self.env.company.ids,
            )._usl_validate_pivot_request(
                self._payload(company_id=other.id),
            )
