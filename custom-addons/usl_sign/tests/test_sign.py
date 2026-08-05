import os
from base64 import b64encode
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.pdf import PdfWriter

from ..services import ProviderError
from ..services.yousign import YousignClient


class FakeProvider:
    def __init__(self, pdf):
        self.pdf = pdf
        self.request = None
        self.fields = []
        self.calls = []
        self.fail_create_once = False
        self.fail_audit = False
        self.audit_level = "Simple electronic signature"

    def create_request(self, payload):
        self.calls.append(("create_request", payload))
        self.request = {
            "id": "request-1",
            "external_id": payload["external_id"],
            "status": "draft",
            "documents": [],
            "signers": [],
        }
        if self.fail_create_once:
            self.fail_create_once = False
            raise ProviderError(
                "The signature provider could not be reached. Reconcile before retrying.",
                retryable=True,
                uncertain=True,
            )
        return deepcopy(self.request)

    def recover_request(self, external_id):
        self.calls.append(("recover_request", external_id))
        if self.request and self.request["external_id"] == external_id:
            return deepcopy(self.request)
        return None

    def upload_document(self, request_id, filename, content, initials=None):
        self.calls.append(("upload_document", request_id, filename, initials))
        assert content == self.pdf
        document = {"id": "document-1", "nature": "signable_document"}
        self.request["documents"] = [document]
        return deepcopy(document)

    def add_signer(self, request_id, payload):
        self.calls.append(("add_signer", request_id, payload))
        signer = {
            "id": f"signer-{len(self.request['signers']) + 1}",
            "status": "initiated",
            "info": payload["info"],
            "signature_level": payload["signature_level"],
            "signature_authentication_mode": payload.get(
                "signature_authentication_mode"
            ),
        }
        self.request["signers"].append(signer)
        return deepcopy(signer)

    def add_field(self, request_id, document_id, payload):
        self.calls.append(("add_field", request_id, document_id, payload))
        field = {"id": f"field-{len(self.fields) + 1}", **payload}
        self.fields.append(field)
        return deepcopy(field)

    def list_fields(self, request_id, document_id):
        self.calls.append(("list_fields", request_id, document_id))
        return deepcopy(self.fields)

    def activate(self, request_id):
        self.calls.append(("activate", request_id))
        self.request["status"] = "ongoing"
        for signer in self.request["signers"]:
            signer.update(
                {
                    "status": "notified",
                    "signature_link": f"https://sandbox.example/{signer['id']}",
                    "signature_link_expiration_date": "2030-01-01 00:00:00",
                }
            )
        return deepcopy(self.request)

    def get_request(self, request_id):
        self.calls.append(("get_request", request_id))
        return deepcopy(self.request)

    def cancel(self, request_id):
        self.calls.append(("cancel", request_id))
        self.request["status"] = "canceled"

    def download_document(self, request_id, document_id):
        self.calls.append(("download_document", request_id, document_id))
        return self.pdf

    def download_audit_trail(self, request_id, signer_id):
        self.calls.append(("download_audit_trail", request_id, signer_id))
        if self.fail_audit:
            raise ProviderError("Audit evidence is temporarily unavailable.", retryable=True)
        return (
            "{"
            f'"electronic_signature_level":{{"level":"{self.audit_level}"}},'
            '"authentication":{"mode":"otp_by_email"},'
            f'"signer":{{"id":"{signer_id}"}}'
            "}"
        ).encode()


@tagged("post_install", "-at_install")
class TestUslSign(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(stream)
        cls.pdf = stream.getvalue()
        cls.company = cls.env.company
        cls.company.sign_provider_enabled = True
        cls.policy = cls.env["usl.sign.policy"].search(
            [
                ("company_id", "=", cls.company.id),
                ("assurance_level", "=", "standard"),
            ],
            limit=1,
        )
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        cls.signature_field = cls.env.ref("sign_oca.sign_field_signature")
        cls.partner = cls.env["res.partner"].create(
            {"name": "Camille Signer", "email": "camille@example.test"}
        )

    def _request(self, **extra):
        vals = {
            "name": "Employment agreement",
            "data": b64encode(self.pdf),
            "filename": "agreement.pdf",
            "company_id": self.company.id,
            "policy_id": self.policy.id,
            "user_id": self.env.user.id,
            "signatory_data": {
                "1": {
                    "field_id": self.signature_field.id,
                    "role_id": self.role.id,
                    "page": 1,
                    "position_x": 60,
                    "position_y": 75,
                    "width": 20,
                    "height": 5,
                    "required": True,
                }
            },
            "signer_ids": [
                (
                    0,
                    0,
                    {"partner_id": self.partner.id, "role_id": self.role.id},
                )
            ],
        }
        vals.update(extra)
        return self.env["sign.oca.request"].create(vals).with_context(
            usl_sign_skip_provider_ready=True
        )

    def _provider_patch(self, provider):
        return patch.multiple(
            "odoo.addons.usl_sign.models.provider",
            get_provider=lambda *args, **kwargs: provider,
        )

    def _template(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Public acknowledgement",
                "data": b64encode(self.pdf),
                "filename": "acknowledgement.pdf",
                "company_id": self.company.id,
                "policy_id": self.policy.id,
            }
        )
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": self.signature_field.id,
                "role_id": self.role.id,
                "page": 1,
                "position_x": 60,
                "position_y": 75,
                "width": 20,
                "height": 5,
                "required": True,
            }
        )
        template.preparation_status = "ready"
        return template

    def test_full_provider_lifecycle_and_immutable_evidence(self):
        sign_request = self._request()
        provider = FakeProvider(self.pdf)
        with patch.dict(
            os.environ, {"USL_YOUSIGN_SANDBOX_API_KEY": "test-key"}
        ), self._provider_patch(provider):
            sign_request.action_mark_ready()
            sign_request.action_send()
            self.assertEqual(sign_request.state, "sent")
            self.assertEqual(sign_request.provider_transaction_id, "request-1")
            self.assertEqual(sign_request.provider_document_id, "document-1")
            self.assertEqual(sign_request.signer_ids.state, "notified")
            self.assertTrue(sign_request.signer_ids.access_token)
            self.assertTrue(sign_request.signer_ids.invitation_sent_at)
            self.assertEqual(len(sign_request.provider_field_map), 1)
            self.assertEqual(sign_request.evidence_ids.mapped("kind"), ["original"])

            provider.request["status"] = "done"
            provider.request["signers"][0].update(
                {"status": "signed", "signed_at": "2026-08-05 08:00:00"}
            )
            sign_request.action_reconcile()
            self.assertEqual(sign_request.state, "completed")
            self.assertEqual(sign_request.achieved_assurance, "standard")
            self.assertEqual(sign_request.evidence_status, "available")
            self.assertTrue(sign_request.retention_until)
            self.assertEqual(sign_request.retention_status, "active")
            self.assertEqual(
                set(sign_request.evidence_ids.mapped("kind")),
                {"original", "signed", "audit_trail"},
            )
            audit = sign_request.evidence_ids.filtered(
                lambda evidence: evidence.kind == "audit_trail"
            )
            self.assertEqual(audit.mimetype, "application/json")
            self.assertTrue(audit.name.endswith(".json"))
            with self.assertRaises(ValidationError):
                sign_request.evidence_ids.filtered(
                    lambda evidence: evidence.kind == "signed"
                ).write({"data": b64encode(b"replacement")})
            with self.assertRaises(ValidationError):
                sign_request.write({"data": b64encode(self.pdf + b"changed")})

    def test_yousign_client_uses_signer_audit_json_endpoint(self):
        client = YousignClient(
            {"base_url": "https://api.example.test/v3", "api_key": "test-key"},
            error_class=ProviderError,
        )
        with patch.object(
            client,
            "_request",
            return_value={"signer": {"name": "Élodie", "id": "signer-1"}},
        ) as request_call:
            content = client.download_audit_trail("request-1", "signer-1")

        request_call.assert_called_once_with(
            "GET", "/signature_requests/request-1/signers/signer-1/audit_trails"
        )
        self.assertEqual(
            content,
            b'{"signer":{"id":"signer-1","name":"\xc3\x89lodie"}}',
        )

    def test_sms_authentication_uses_odoo_19_partner_phone(self):
        self.assertNotIn("mobile", self.env["res.partner"]._fields)
        self.partner.phone = "+33612345678"
        verified_policy = self.env["usl.sign.policy"].search(
            [
                ("company_id", "=", self.company.id),
                ("assurance_level", "=", "verified"),
            ],
            limit=1,
        )
        sign_request = self._request(policy_id=verified_policy.id)
        sign_request.action_mark_ready()
        payload = sign_request._signer_payload(sign_request.signer_ids)
        self.assertEqual(payload["info"]["phone_number"], "+33612345678")

    def test_uncertain_create_recovers_without_duplicate(self):
        sign_request = self._request()
        provider = FakeProvider(self.pdf)
        provider.fail_create_once = True
        with patch.dict(
            os.environ, {"USL_YOUSIGN_SANDBOX_API_KEY": "test-key"}
        ), self._provider_patch(provider):
            sign_request.action_send()
            self.assertEqual(sign_request.state, "action_required")
            self.assertFalse(sign_request.provider_transaction_id)
            sign_request.action_send()
            self.assertEqual(sign_request.state, "sent")
            creates = [call for call in provider.calls if call[0] == "create_request"]
            self.assertEqual(len(creates), 1)

    def test_provider_done_waits_for_complete_evidence(self):
        sign_request = self._request()
        provider = FakeProvider(self.pdf)
        provider.fail_audit = True
        with patch.dict(
            os.environ, {"USL_YOUSIGN_SANDBOX_API_KEY": "test-key"}
        ), self._provider_patch(provider):
            sign_request.action_send()
            provider.request["status"] = "done"
            provider.request["signers"][0]["status"] = "signed"
            sign_request.action_reconcile()
        self.assertEqual(sign_request.state, "action_required")
        self.assertEqual(sign_request.evidence_status, "missing")
        self.assertTrue(sign_request.final_data)
        self.assertIn("full evidence package", sign_request.last_error)
        self.assertIn("Refresh provider status", sign_request.recovery_action)
        with self.assertRaisesRegex(ValidationError, "already has a provider transaction"):
            sign_request.action_send()

    def test_achieved_assurance_requires_provider_audit_evidence(self):
        sign_request = self._request()
        provider = FakeProvider(self.pdf)
        provider.audit_level = "Unknown signature level"
        with patch.dict(
            os.environ, {"USL_YOUSIGN_SANDBOX_API_KEY": "test-key"}
        ), self._provider_patch(provider):
            sign_request.action_send()
            provider.request["status"] = "done"
            provider.request["signers"][0]["status"] = "signed"
            sign_request.action_reconcile()
        self.assertEqual(sign_request.state, "action_required")
        self.assertFalse(sign_request.achieved_assurance)
        self.assertEqual(sign_request.evidence_status, "missing")

    def test_reminders_are_due_once_and_capped(self):
        sign_request = self._request(
            state="sent",
            sent_at="2026-08-01 00:00:00",
            reminder_days=1,
            max_reminders=2,
        )
        signer = sign_request.signer_ids
        signer.write({"state": "notified"})
        signer._portal_ensure_token()
        sign_request._send_due_reminders()
        self.assertEqual(sign_request.reminder_count, 1)
        self.assertEqual(signer.reminder_count, 1)
        sign_request._send_due_reminders()
        self.assertEqual(sign_request.reminder_count, 1)
        sign_request.action_send_reminder()
        self.assertEqual(sign_request.reminder_count, 2)
        with self.assertRaises(ValidationError):
            sign_request.action_send_reminder()

    def test_public_submission_is_independent_and_idempotent(self):
        template = self._template()
        provider = FakeProvider(self.pdf)
        environment = {
            "USL_YOUSIGN_SANDBOX_API_KEY": "test-key",
            "USL_YOUSIGN_SANDBOX_WEBHOOK_SECRET": "test-secret",
        }
        with patch.dict(os.environ, environment), self._provider_patch(provider):
            self.company.invalidate_recordset(
                [
                    "sign_yousign_configured",
                    "sign_yousign_webhook_configured",
                    "sign_provider_ready",
                ]
            )
            template.action_enable_public_link()
            submission = self.env["usl.sign.public.submission"]._create_submission(
                template,
                {"name": "Public Signer", "email": "public@example.test"},
                "a" * 40,
                "source-hash",
            )
            duplicate = self.env["usl.sign.public.submission"]._create_submission(
                template,
                {"name": "Duplicate", "email": "duplicate@example.test"},
                "a" * 40,
                "source-hash",
            )
            self.assertEqual(submission, duplicate)
            self.assertEqual(
                self.env["sign.oca.request"].search_count(
                    [("template_id", "=", template.id)]
                ),
                1,
            )
            submission._process_pending()
        self.assertEqual(submission.state, "sent")
        self.assertEqual(submission.request_id.state, "sent")

    def test_decline_and_expiration_preserve_terminal_evidence(self):
        sign_request = self._request(
            provider_transaction_id="request-terminal",
            provider_environment="sandbox",
            state="sent",
        )
        signer = sign_request.signer_ids
        signer.write(
            {
                "provider_signer_id": "signer-terminal",
                "state": "notified",
                "access_token": "secure-token",
            }
        )
        sign_request._apply_provider_event(
            {
                "event_id": "decline-event",
                "event_name": "signer.declined",
                "event_time": "1785916800",
                "data": {
                    "signer": {
                        "id": "signer-terminal",
                        "signature_request_id": "request-terminal",
                        "decline_reason": "Terms not accepted",
                    }
                },
            }
        )
        self.assertEqual(sign_request.state, "declined")
        self.assertEqual(signer.decline_reason, "Terms not accepted")
        self.assertTrue(signer.access_revoked)
        self.assertIn("decline", sign_request.evidence_ids.mapped("kind"))

        expiring = self._request(
            state="sent", expires_at="2026-08-01 00:00:00"
        )
        expiring.signer_ids.write({"state": "notified"})
        expiring._expire_request()
        self.assertEqual(expiring.state, "expired")
        self.assertEqual(expiring.signer_ids.state, "expired")
        self.assertIn("expiration", expiring.evidence_ids.mapped("kind"))

    def test_pdf_validation_and_rotated_page_coordinates(self):
        malformed = self._request(data=b64encode(b"not a pdf"))
        with self.assertRaisesRegex(ValidationError, "not a readable PDF"):
            malformed._validate_source_pdf()

        encrypted_stream = BytesIO()
        encrypted_writer = PdfWriter()
        encrypted_writer.add_blank_page(width=595, height=842)
        encrypted_writer.encrypt("secret")
        encrypted_writer.write(encrypted_stream)
        encrypted = self._request(data=b64encode(encrypted_stream.getvalue()))
        with self.assertRaisesRegex(ValidationError, "encrypted"):
            encrypted._validate_source_pdf()

        rotated_stream = BytesIO()
        rotated_writer = PdfWriter()
        rotated_writer.add_blank_page(width=595, height=842)
        rotated_page = rotated_writer.add_blank_page(width=595, height=842)
        rotated_page.rotate(90)
        rotated_writer.write(rotated_stream)
        rotated = self._request(data=b64encode(rotated_stream.getvalue()))
        rotated.with_context(usl_sign_freeze=True).write(
            {"original_data": rotated.data}
        )
        self.assertEqual(rotated._pdf_page_metrics(), [(595.0, 842.0), (842.0, 595.0)])
        payload = rotated._field_payload(
            {
                "field_id": self.signature_field.id,
                "page": 2,
                "position_x": 50,
                "position_y": 50,
                "width": 20,
                "height": 10,
                "required": True,
            },
            rotated.signer_ids,
            rotated._pdf_page_metrics(),
        )
        self.assertEqual(payload["page"], 2)
        self.assertLessEqual(payload["x"] + payload["width"], 842)
        self.assertLessEqual(payload["y"] + payload["height"], 595)

    def test_event_idempotency_and_out_of_order_protection(self):
        sign_request = self._request(
            provider_transaction_id="request-1",
            provider_environment="sandbox",
            state="sent",
        )
        signer = sign_request.signer_ids
        signer.provider_signer_id = "signer-1"
        done = {
            "event_id": "event-new",
            "event_name": "signer.done",
            "event_time": "1785916800",
            "sandbox": True,
            "data": {
                "signer": {
                    "id": "signer-1",
                    "signature_request_id": "request-1",
                    "signature_level": "electronic_signature",
                }
            },
        }
        sign_request._apply_provider_event(done)
        sign_request._apply_provider_event(done)
        self.assertEqual(signer.state, "signed")
        self.assertEqual(
            self.env["usl.sign.provider.event"].search_count(
                [("event_id", "=", "event-new")]
            ),
            1,
        )
        old = deepcopy(done)
        old.update(
            {
                "event_id": "event-old",
                "event_name": "signer.link_opened",
                "event_time": "1785916700",
            }
        )
        event = sign_request._apply_provider_event(old)
        self.assertEqual(event.status, "ignored")
        self.assertEqual(signer.state, "signed")

    def test_signer_event_order_is_independent_per_signer(self):
        second_partner = self.env["res.partner"].create(
            {"name": "Second Signer", "email": "second@example.test"}
        )
        sign_request = self._request(
            provider_transaction_id="request-streams",
            provider_environment="sandbox",
            state="sent",
        )
        first = sign_request.signer_ids
        first.provider_signer_id = "signer-first"
        second = self.env["sign.oca.request.signer"].create(
            {
                "request_id": sign_request.id,
                "partner_id": second_partner.id,
                "role_id": self.role.id,
                "provider_signer_id": "signer-second",
            }
        )
        for event_id, event_time, provider_signer_id in (
            ("first-later", "1785916800", first.provider_signer_id),
            ("second-earlier", "1785916700", second.provider_signer_id),
        ):
            event = sign_request._apply_provider_event(
                {
                    "event_id": event_id,
                    "event_name": "signer.done",
                    "event_time": event_time,
                    "data": {
                        "signer": {
                            "id": provider_signer_id,
                            "signature_request_id": "request-streams",
                            "signature_level": "electronic_signature",
                        }
                    },
                }
            )
            self.assertEqual(event.status, "processed")
        self.assertEqual(first.state, "signed")
        self.assertEqual(second.state, "signed")

    def test_identity_failure_is_durable_and_actionable(self):
        sign_request = self._request(
            provider_transaction_id="request-identity",
            provider_environment="sandbox",
            state="sent",
        )
        signer = sign_request.signer_ids
        signer.provider_signer_id = "signer-identity"
        event = sign_request._apply_provider_event(
            {
                "event_id": "identity-failed",
                "event_name": "signer.identification_failed",
                "event_time": "1785916800",
                "data": {
                    "signer": {
                        "id": "signer-identity",
                        "signature_request_id": "request-identity",
                        "signature_level": "advanced_electronic_signature",
                    }
                },
            }
        )
        self.assertEqual(event.status, "processed")
        self.assertEqual(signer.state, "error")
        self.assertEqual(sign_request.state, "action_required")
        self.assertTrue(sign_request.last_error)
        self.assertTrue(sign_request.recovery_action)

    def test_secure_token_and_terminal_state_guards(self):
        sign_request = self._request(state="sent")
        signer = sign_request.signer_ids
        signer._portal_ensure_token()
        self.assertTrue(signer._check_secure_access(signer.access_token))
        with self.assertRaises(AccessError):
            signer._check_secure_access("wrong-token")
        sign_request.with_context(usl_sign_transition=True).state = "cancelled"
        with self.assertRaises(AccessError):
            signer._check_secure_access(signer.access_token)
        with self.assertRaises(ValidationError):
            sign_request.write({"state": "draft"})

    def test_odoo_19_configuration_urls_and_historical_boundary(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "web.base.url", "https://sign.example.test"
        )
        template = self._template()
        template.action_enable_public_link()
        self.assertEqual(
            template.public_url,
            f"https://sign.example.test/sign/public/{template.public_access_token}",
        )
        self.company.invalidate_recordset(["sign_yousign_webhook_url"])
        self.assertEqual(
            self.company.sign_yousign_webhook_url,
            f"https://sign.example.test/sign/webhooks/yousign/{self.company.id}",
        )

        historical = self._request(
            historical=True,
            state="completed",
            provider_code="odoo_online",
            authentication_method=False,
            achieved_assurance=False,
            expires_at=False,
        )
        self.assertFalse(historical.expires_at)
        with self.assertRaisesRegex(ValidationError, "read-only evidence"):
            historical.write({"name": "Changed historical record"})

    def test_request_record_boundary(self):
        owner = new_test_user(
            self.env,
            login="sign-owner",
            groups="base.group_user,usl_sign.group_sign_user",
        )
        outsider = new_test_user(
            self.env,
            login="sign-outsider",
            groups="base.group_user,usl_sign.group_sign_user",
        )
        sign_request = self._request(user_id=owner.id)
        self.assertIn(
            sign_request,
            self.env["sign.oca.request"].with_user(owner).search(
                [("id", "=", sign_request.id)]
            ),
        )
        self.assertNotIn(
            sign_request,
            self.env["sign.oca.request"].with_user(outsider).search(
                [("id", "=", sign_request.id)]
            ),
        )

    def test_company_and_reviewer_security_boundaries(self):
        other_company = self.env["res.company"].create({"name": "Other Sign Company"})
        other_policy = self.env["usl.sign.policy"].search(
            [
                ("company_id", "=", other_company.id),
                ("assurance_level", "=", "standard"),
            ],
            limit=1,
        )
        other_request = self._request(
            company_id=other_company.id,
            policy_id=other_policy.id,
        )
        company_admin = new_test_user(
            self.env,
            login="sign-company-admin",
            groups="base.group_user,usl_sign.group_sign_admin",
            company_id=self.company.id,
            company_ids=[(6, 0, self.company.ids)],
        )
        self.assertFalse(
            self.env["sign.oca.request"]
            .with_user(company_admin)
            .search([("id", "=", other_request.id)])
        )

        completed = self._request(state="completed")
        reviewer = new_test_user(
            self.env,
            login="sign-evidence-reviewer",
            groups="base.group_user,usl_sign.group_sign_reviewer",
            company_id=self.company.id,
            company_ids=[(6, 0, self.company.ids)],
        )
        reviewer_request = completed.with_user(reviewer)
        self.assertTrue(reviewer_request.exists())
        with self.assertRaises(AccessError):
            reviewer_request.write({"name": "Reviewer mutation"})

    def test_template_readiness_versioning_and_geometry(self):
        unprepared = self.env["sign.oca.template"].create(
            {
                "name": "Unprepared",
                "data": b64encode(self.pdf),
                "filename": "unprepared.pdf",
                "company_id": self.company.id,
                "policy_id": self.policy.id,
            }
        )
        with self.assertRaisesRegex(ValidationError, "at least one field"):
            unprepared.preparation_status = "ready"
        with self.assertRaisesRegex(ValidationError, "mark this template ready"):
            unprepared.action_enable_public_link()

        template = self._template()
        initial_version = template.version
        sign_request = self._request(template_id=template.id)
        self.assertEqual(sign_request.template_version, initial_version)
        template.reminder_days += 1
        self.assertEqual(template.preparation_status, "review_required")
        self.assertGreater(template.version, initial_version)
        self.assertEqual(sign_request.template_version, initial_version)

        invalid = self._request()
        layout = deepcopy(invalid.signatory_data)
        layout["1"].update({"position_x": 95, "width": 10})
        invalid.signatory_data = layout
        with self.assertRaisesRegex(ValidationError, "outside the PDF page"):
            invalid.action_mark_ready()

    def test_sign_administrator_has_scoped_configuration(self):
        other_company = self.env["res.company"].create(
            {"name": "Restricted Configuration Company"}
        )
        administrator = new_test_user(
            self.env,
            login="sign-configuration-admin",
            groups="base.group_user,usl_sign.group_sign_admin",
            company_id=self.company.id,
            company_ids=[(6, 0, self.company.ids)],
        )
        configuration_model = self.env["usl.sign.configuration"].with_user(
            administrator
        )
        configuration = configuration_model.create(
            {
                "company_id": self.company.id,
                "provider_enabled": True,
                "environment": "sandbox",
                "workspace_id": "workspace-test",
                "deliver_completed_to_signers": True,
                "evidence_retention_years": 12,
            }
        )
        with patch.dict(
            os.environ,
            {
                "USL_YOUSIGN_SANDBOX_API_KEY": "test-key",
                "USL_YOUSIGN_SANDBOX_WEBHOOK_SECRET": "test-secret",
            },
        ):
            configuration.invalidate_recordset()
            self.assertTrue(configuration.provider_ready)
        configuration.action_save()
        self.company.invalidate_recordset()
        self.assertEqual(self.company.sign_yousign_workspace_id, "workspace-test")
        self.assertTrue(self.company.sign_deliver_completed_to_signers)
        self.assertEqual(self.company.sign_evidence_retention_years, 12)

        unauthorized = configuration_model.create(
            {
                "company_id": other_company.id,
                "environment": "sandbox",
                "evidence_retention_years": 10,
            }
        )
        with self.assertRaises(AccessError):
            unauthorized.action_save()

        ordinary_user = new_test_user(
            self.env,
            login="sign-configuration-user",
            groups="base.group_user,usl_sign.group_sign_user",
        )
        with self.assertRaises(AccessError):
            self.env["usl.sign.configuration"].with_user(ordinary_user).create(
                {
                    "company_id": self.company.id,
                    "environment": "sandbox",
                    "evidence_retention_years": 10,
                }
            )

    def test_partial_cancellation_preserves_completed_signer_history(self):
        second_partner = self.env["res.partner"].create(
            {"name": "Pending Signer", "email": "pending@example.test"}
        )
        sign_request = self._request()
        first = sign_request.signer_ids
        second = self.env["sign.oca.request.signer"].create(
            {
                "request_id": sign_request.id,
                "partner_id": second_partner.id,
                "role_id": self.role.id,
            }
        )
        provider = FakeProvider(self.pdf)
        with patch.dict(
            os.environ, {"USL_YOUSIGN_SANDBOX_API_KEY": "test-key"}
        ), self._provider_patch(provider):
            sign_request.action_send()
            first.write({"state": "signed", "signed_on": "2026-08-05 08:00:00"})
            sign_request.with_context(usl_sign_transition=True).state = "partial"
            sign_request.cancel()
        self.assertEqual(sign_request.state, "cancelled")
        self.assertEqual(first.state, "signed")
        self.assertEqual(second.state, "cancelled")
        self.assertTrue(sign_request.evidence_ids.filtered(lambda row: row.kind == "cancellation"))

    def test_pocketid_profiles_include_scoped_sign_roles(self):
        definitions = self.env["res.users"]._usl_pocketid_profile_definitions()
        self.assertIn(
            "usl_sign.group_sign_admin", definitions["administrator"]["groups"]
        )
        self.assertIn(
            "usl_sign.group_sign_admin", definitions["break_glass"]["groups"]
        )
        self.assertIn(
            "usl_sign.group_sign_user", definitions["collaborator"]["groups"]
        )


@tagged("post_install", "-at_install")
class TestUslSignPublicPage(HttpCase):
    def test_unavailable_public_link_keeps_actionable_explanation(self):
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(stream)
        company = self.env.company
        company.sign_provider_enabled = False
        policy = self.env["usl.sign.policy"].search(
            [
                ("company_id", "=", company.id),
                ("assurance_level", "=", "standard"),
            ],
            limit=1,
        )
        role = self.env.ref("sign_oca.sign_role_customer")
        template = self.env["sign.oca.template"].create(
            {
                "name": "Public explanation rendering",
                "data": b64encode(stream.getvalue()),
                "filename": "public-explanation.pdf",
                "company_id": company.id,
                "policy_id": policy.id,
            }
        )
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": self.env.ref("sign_oca.sign_field_signature").id,
                "role_id": role.id,
                "page": 1,
                "position_x": 60,
                "position_y": 75,
                "width": 20,
                "height": 5,
                "required": True,
            }
        )
        template.preparation_status = "ready"
        template.action_enable_public_link()

        response = self.url_open(f"/sign/public/{template.public_access_token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Signing is temporarily unavailable. Please try again later.",
            response.text,
        )

    def test_oca_source_pdf_route_uses_odoo_19_stream_api(self):
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(stream)
        pdf = stream.getvalue()
        company = self.env.company
        policy = self.env["usl.sign.policy"].search(
            [("company_id", "=", company.id), ("is_default", "=", True)], limit=1
        )
        partner = self.env["res.partner"].create(
            {"name": "Source route signer", "email": "source-route@example.test"}
        )
        sign_request = self.env["sign.oca.request"].create(
            {
                "name": "Source PDF route",
                "data": b64encode(pdf),
                "filename": "source-route.pdf",
                "company_id": company.id,
                "policy_id": policy.id,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": partner.id,
                            "role_id": self.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        signer = sign_request.signer_ids
        signer._portal_ensure_token()

        response = self.url_open(
            f"/sign_oca/content/{signer.id}/{signer.access_token}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, pdf)
