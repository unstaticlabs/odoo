import os
from base64 import b64encode
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.pdf import PdfWriter

from ..services import ProviderError


class FakeProvider:
    def __init__(self, pdf):
        self.pdf = pdf
        self.request = None
        self.fields = []
        self.calls = []
        self.fail_create_once = False

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
        return self.pdf


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
        return self.env["sign.oca.request"].create(vals)

    def _provider_patch(self, provider):
        return patch.multiple(
            "odoo.addons.usl_sign.models.provider",
            get_provider=lambda *args, **kwargs: provider,
        )

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
            self.assertEqual(
                set(sign_request.evidence_ids.mapped("kind")),
                {"original", "signed", "audit_trail"},
            )
            with self.assertRaises(ValidationError):
                sign_request.evidence_ids.filtered(
                    lambda evidence: evidence.kind == "signed"
                ).write({"data": b64encode(b"replacement")})
            with self.assertRaises(ValidationError):
                sign_request.write({"data": b64encode(self.pdf + b"changed")})

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
