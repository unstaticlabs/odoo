import base64
import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.pdf import PdfWriter

from ..controllers.strong import StrongSignController, _personal_certificate_subject
from ..models.constants import INTERNAL_OPERATION, REQUEST_STATES, TRUST_LEVELS
from ..services import (
    DSSClient,
    DSSRejectedError,
    DSSServiceError,
    DSSUnavailableError,
    OpenTimestampsUnavailableError,
    field_content,
    field_value,
)


class FakeDSS:
    def __init__(self, *, revision_matches=True):
        self.revision_match = revision_matches

    @staticmethod
    def seal(document, **kwargs):
        del kwargs
        return {"document": base64.b64encode(document).decode()}

    @staticmethod
    def validate(document, expected_level, expected_signers=None):
        del document, expected_signers
        return {
            "status": "valid",
            "achievedTrust": expected_level,
            "engineVersion": "6.4",
            "signatureCount": 1,
            "summary": "DSS validation passed.",
            "reports": {"simple": "<SimpleReport><Status>valid</Status></SimpleReport>"},
            "certificates": [],
            "timestamps": [],
            "revocation": {},
        }

    @staticmethod
    def sign_manifest(manifest):
        return {
            "manifestSha256": hashlib.sha256(manifest).hexdigest(),
            "signature": base64.b64encode(b"detached-signature").decode(),
            "signatureAlgorithm": "ECDSA_SHA256",
            "certificateChain": [base64.b64encode(b"certificate").decode()],
        }

    @staticmethod
    def build_dossier(**kwargs):
        del kwargs
        return {"document": base64.b64encode(_pdf()).decode()}

    @staticmethod
    def validate_pdfa(document):
        del document
        return {
            "compliant": True,
            "engine": "veraPDF",
            "engineVersion": "1.30.2",
            "profile": "PDF/A-3b",
            "report": {"compliant": True},
        }

    @staticmethod
    def cross_validate(document):
        del document
        return _pyhanko_valid()

    def revision_matches(self, original, signed):
        del original, signed
        return {"matches": self.revision_match}


def _pdf(pages=1):
    stream = BytesIO()
    writer = PdfWriter()
    for _index in range(pages):
        writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()


@lru_cache(maxsize=1)
def _test_certificate_der():
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "USL Sign test certificate")],
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .sign(key, hashes.SHA256())
    )
    return base64.b64encode(
        certificate.public_bytes(serialization.Encoding.DER),
    ).decode()


def _pyhanko_valid(count=1):
    return {
        "engine": "pyHanko",
        "engine_version": "0.36.2",
        "status": "valid",
        "signature_count": count,
        "signatures": [
            {
                "intact": True,
                "cryptographically_valid": True,
                "field_name": f"USL-Test-Signature-{index + 1}",
                "certificate_chain": [_test_certificate_der()],
            }
            for index in range(count)
        ],
    }


@tagged("post_install", "-at_install")
class TestCleanUslSign(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.email = "sign@example.test"
        cls.pdf = _pdf()
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        cls.text_field = cls.env.ref("sign_oca.sign_field_name")
        cls.partner_one = cls.env["res.partner"].create(
            {"name": "Camille Signer", "email": "camille@example.test"},
        )
        cls.partner_two = cls.env["res.partner"].create(
            {"name": "Morgan Signer", "email": "morgan@example.test"},
        )
        cls.sign_user = new_test_user(
            cls.env,
            login="usl-sign-user",
            groups="usl_sign.group_sign_user",
            company_id=cls.company.id,
        )
        cls.override_user = new_test_user(
            cls.env,
            login="usl-sign-override",
            groups="usl_sign.group_sign_trust_override",
            company_id=cls.company.id,
        )
        cls.template_manager = new_test_user(
            cls.env,
            login="usl-sign-template-manager",
            groups="usl_sign.group_sign_template_manager",
            company_id=cls.company.id,
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="usl-sign-reviewer",
            groups="usl_sign.group_sign_identity_reviewer",
            company_id=cls.company.id,
        )
        cls.evidence_reviewer = new_test_user(
            cls.env,
            login="usl-sign-evidence-reviewer",
            groups="usl_sign.group_sign_evidence_reviewer",
            company_id=cls.company.id,
        )
        cls.sign_admin = new_test_user(
            cls.env,
            login="usl-sign-admin",
            groups="usl_sign.group_sign_admin",
            company_id=cls.company.id,
        )
        cls.policy = cls.env.ref("usl_sign.policy_routine_standard")

    def _request(self, *, partners=None, roles=None, **values):
        partners = partners or [self.partner_one]
        roles = roles or [self.role_customer]
        layout = {}
        for index, role in enumerate(roles, start=1):
            layout[str(index)] = {
                "id": index,
                "field_id": self.text_field.id,
                "field_type": self.text_field.field_type,
                "required": True,
                "name": self.text_field.name,
                "role_id": role.id,
                "page": 1,
                "position_x": 10 + index,
                "position_y": 20 + index,
                "width": 25,
                "height": 5,
                "value": False,
                "default_value": self.text_field.default_value,
                "placeholder": "",
            }
        request_values = {
            "name": "Clean Sign request",
            "data": field_value(self.pdf),
            "filename": "clean-request.pdf",
            "company_id": self.company.id,
            "user_id": self.env.user.id,
            "policy_id": self.policy.id,
            "signatory_data": layout,
            "signer_ids": [
                (
                    0,
                    0,
                    {
                        "partner_id": partner.id,
                        "role_id": role.id,
                        "sequence": index * 10,
                    },
                )
                for index, (partner, role) in enumerate(zip(partners, roles), start=1)
            ],
        }
        request_values.update(values)
        return self.env["sign.oca.request"].create(request_values)

    @staticmethod
    def _items(request, role, value):
        items = json.loads(json.dumps(request.frozen_layout))
        for item in items.values():
            if int(item["role_id"]) == role.id:
                item["value"] = value
        return items

    def _ready(self, request):
        request.action_mark_ready()
        return request

    def test_only_final_trust_and_lifecycle_vocabulary_is_registered(self):
        self.assertEqual(
            [key for key, _label in TRUST_LEVELS],
            ["standard", "strong_personal", "qualified_external"],
        )

        self.assertEqual(
            [label for _key, label in TRUST_LEVELS],
            [
                "Standard electronic signature with reinforced evidence.",
                "Strong personal signature — designed for advanced-signature requirements.",
                "Qualified external signature.",
            ],
        )
        self.assertEqual(
            [key for key, _label in REQUEST_STATES],
            [
                "draft",
                "ready",
                "sent",
                "viewed",
                "partial",
                "waiting_enrollment",
                "waiting_external",
                "signed_to_import",
                "validating",
                "completed",
                "evidence_incomplete",
                "validation_failed",
                "declined",
                "expired",
                "cancelled",
                "action_required",
            ],
        )
        state_values = dict(self.env["sign.oca.request"]._fields["state"].selection)
        self.assertEqual(set(state_values), {key for key, _label in REQUEST_STATES})

    def test_pdf_without_page_dimensions_is_rejected_before_freezing(self):
        stream = BytesIO()
        writer = PdfWriter()
        page = writer.add_blank_page(width=595, height=842)
        del page["/MediaBox"]
        writer.write(stream)
        request = self._request()
        with self.assertRaisesRegex(ValidationError, "valid page dimensions"):
            self.env["usl.sign.request.document"].create(
                {
                    "request_id": request.id,
                    "name": "Malformed PDF",
                    "filename": "malformed.pdf",
                    "data": field_value(stream.getvalue()),
                },
            )

    def test_template_wizard_explains_trust_and_creates_a_reviewable_draft(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Guided Standard template",
                "filename": "guided.pdf",
                "data": field_value(self.pdf),
                "company_id": self.company.id,
                "policy_id": self.policy.id,
            },
        )
        signature = self.env.ref("sign_oca.sign_field_signature")
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": signature.id,
                "role_id": self.role_customer.id,
                "required": True,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 25,
                "height": 8,
            },
        )
        template.action_mark_ready()
        wizard = self.env["sign.oca.template.generate"].create(
            {
                "template_id": template.id,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "role_id": self.role_customer.id,
                            "partner_id": self.partner_one.id,
                        },
                    ),
                ],
                "message": "Please review and sign.",
            },
        )
        wizard._refresh_usl_journey()
        self.assertEqual(wizard.recommended_trust, "standard")
        self.assertEqual(wizard.requested_trust, "standard")
        self.assertEqual(
            wizard.journey_availability,
            "Ready. Each signer receives a private link to review and sign.",
        )
        action = wizard.generate()
        self.assertEqual(action["type"], "ir.actions.act_window")
        request = self.env["sign.oca.request"].browse(action["res_id"])
        self.assertEqual(request.state, "draft")
        self.assertEqual(request.requested_trust, "standard")
        self.assertEqual(request.template_version, 1)
        self.assertEqual(request.responsible_message, "<p>Please review and sign.</p>")
        self.assertEqual(len(request.document_ids), 1)
        self.assertEqual(request.document_ids.source_sha256, template.document_ids.source_sha256)
        self.assertFalse(request.sent_at)

    def test_template_editor_exposes_typed_fields_and_stable_role_colors(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Typed editor template",
                "filename": "typed.pdf",
                "data": field_value(self.pdf),
                "company_id": self.company.id,
            },
        )
        first = template.get_info()
        second = template.get_info()
        fields_by_name = {field["name"]: field for field in first["fields"]}
        self.assertEqual(fields_by_name["Signature"]["kind"], "signature")
        self.assertEqual(fields_by_name["Email"]["kind"], "email")
        self.assertEqual(fields_by_name["Phone"]["kind"], "phone")
        self.assertEqual(fields_by_name["Check"]["kind"], "checkbox")
        self.assertEqual(fields_by_name["Initials"]["field_type"], "signature")
        self.assertGreater(fields_by_name["Signature"]["default_width"], 0)
        self.assertEqual(
            {role["id"]: role["color"] for role in first["roles"]},
            {role["id"]: role["color"] for role in second["roles"]},
        )
        self.assertTrue(all(role["color"].startswith("#") for role in first["roles"]))
        self.assertEqual(first["revision"], 1)
        self.assertFalse(first["readonly"])

    def test_template_editor_commands_are_revision_checked_and_idempotent(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Command editor template",
                "filename": "commands.pdf",
                "data": field_value(self.pdf),
                "company_id": self.company.id,
            },
        )
        operation = str(uuid.uuid4())
        command = {
            "action": "create",
            "values": {
                "field_id": self.text_field.id,
                "role_id": self.role_employee.id,
                "page": 1,
                "position_x": 10,
                "position_y": 12,
                "width": 24,
                "height": 5,
            },
        }
        created = template.editor_apply_command(operation, 1, command)
        duplicate = template.editor_apply_command(operation, 1, command)
        self.assertEqual(created, duplicate)
        self.assertEqual(len(template.item_ids), 1)
        self.assertEqual(template.item_ids.role_id, self.role_employee)
        self.assertEqual(template.editor_revision, 2)
        conflict = template.editor_apply_command(str(uuid.uuid4()), 1, command)
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(template.editor_revision, 2)

        updated = template.editor_apply_command(
            str(uuid.uuid4()),
            2,
            {
                "action": "update",
                "item_id": created["item"]["id"],
                "values": {"role_id": self.role_customer.id, "required": True},
            },
        )
        self.assertEqual(updated["item"]["role_id"], self.role_customer.id)
        self.assertTrue(updated["item"]["required"])

    def test_template_editor_rejects_missing_role_and_out_of_page_geometry(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Validated editor template",
                "filename": "validated.pdf",
                "data": field_value(self.pdf),
                "company_id": self.company.id,
            },
        )
        with self.assertRaisesRegex(ValidationError, "field type and a signer"):
            template.editor_apply_command(
                str(uuid.uuid4()),
                1,
                {"action": "create", "values": {"field_id": self.text_field.id}},
            )
        with self.assertRaisesRegex(ValidationError, "inside its PDF page"):
            template.editor_apply_command(
                str(uuid.uuid4()),
                1,
                {
                    "action": "create",
                    "values": {
                        "field_id": self.text_field.id,
                        "role_id": self.role_customer.id,
                        "page": 1,
                        "position_x": 95,
                        "position_y": 10,
                        "width": 20,
                        "height": 5,
                    },
                },
            )
        with self.assertRaisesRegex(ValidationError, "page does not exist"):
            template.editor_apply_command(
                str(uuid.uuid4()),
                1,
                {
                    "action": "create",
                    "values": {
                        "field_id": self.text_field.id,
                        "role_id": self.role_customer.id,
                        "page": 2,
                        "position_x": 10,
                        "position_y": 10,
                        "width": 20,
                        "height": 5,
                    },
                },
            )

    def test_request_editor_uses_assigned_signer_names_and_explicit_roles(self):
        request = self._request(
            partners=[self.partner_one, self.partner_two],
            roles=[self.role_customer, self.role_employee],
        )
        info = request.get_info()
        roles = {role["id"]: role for role in info["roles"]}
        self.assertEqual(roles[self.role_customer.id]["signer_name"], self.partner_one.name)
        self.assertEqual(roles[self.role_employee.id]["signer_name"], self.partner_two.name)
        result = request.editor_apply_command(
            str(uuid.uuid4()),
            1,
            {
                "action": "create",
                "values": {
                    "field_id": self.text_field.id,
                    "role_id": self.role_employee.id,
                    "page": 1,
                    "position_x": 35,
                    "position_y": 40,
                    "width": 24,
                    "height": 5,
                },
            },
        )
        self.assertEqual(result["item"]["role_id"], self.role_employee.id)
        self.assertEqual(result["item"]["tabindex"], 1)
        self.assertEqual(request.editor_revision, 2)

    def test_template_editor_role_colors_follow_template_company_rules(self):
        other_company = self.env["res.company"].create({"name": "Other Sign Company"})
        template = self.env["sign.oca.template"].with_company(other_company).create(
            {
                "name": "Other-company template",
                "filename": "other.pdf",
                "data": field_value(self.pdf),
                "company_id": other_company.id,
            },
        )
        template.get_info()
        mappings = template.editor_role_ids
        self.assertTrue(mappings)
        self.assertFalse(
            self.env["usl.sign.template.role"].with_user(self.template_manager).search(
                [("id", "in", mappings.ids)],
            ),
        )

    def test_business_record_picker_excludes_technical_registry_models(self):
        available = dict(self.env["sign.oca.request"]._sign_business_record_models())
        self.assertIn("res.partner", available)
        self.assertNotIn("ir.model", available)
        self.assertFalse(any(model.startswith("usl.sign") for model in available))
        self.assertFalse(any(model.startswith("sign.oca") for model in available))

    def test_invitation_is_queued_and_failed_delivery_recovers(self):
        request = self._ready(self._request())
        request.action_send()
        signer = request.signer_ids
        first_hash = signer.access_token_sha256
        first_mail = signer.invitation_mail_id
        self.assertEqual(request.state, "sent")
        self.assertEqual(signer.state, "notified")
        self.assertEqual(signer.invitation_delivery_state, "queued")
        self.assertEqual(first_mail.state, "outgoing")
        self.assertFalse(signer.access_token)
        first_mail.write(
            {"state": "exception", "failure_reason": "Synthetic SMTP outage"},
        )
        self.env["sign.oca.request"]._cron_sign_operations()
        self.assertEqual(request.state, "action_required")
        self.assertEqual(signer.invitation_delivery_state, "failed")
        self.assertIn("mail configuration", request.recovery_action)
        request.action_retry_validation()
        self.assertEqual(request.state, "sent")
        self.assertFalse(first_mail.exists())
        self.assertEqual(signer.invitation_delivery_state, "queued")
        self.assertNotEqual(signer.access_token_sha256, first_hash)
        self.assertFalse(request.last_error)
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"signed_on": fields.Datetime.now(), "state": "signed"},
        )
        self.assertEqual(signer.invitation_delivery_state, "resolved")

    def test_internal_signer_uses_odoo_when_email_delivery_fails(self):
        internal_signer = new_test_user(
            self.env,
            login="usl-sign-mail-fallback",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        internal_signer.partner_id.email = "odoo-fallback@example.test"
        request = self._ready(
            self._request(partners=[internal_signer.partner_id]),
        )
        request.with_context(
            usl_sign_share_confirmed=INTERNAL_OPERATION,
        ).action_send()
        signer = request.signer_ids
        activity_type = self.env.ref("usl_sign.mail_activity_type_sign_document")
        activity_domain = [
            ("activity_type_id", "=", activity_type.id),
            ("res_model", "=", signer._name),
            ("res_id", "=", signer.id),
            ("user_id", "=", internal_signer.id),
            ("active", "=", True),
        ]
        activity = self.env["mail.activity"].search(activity_domain)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.summary, "Review and sign: Clean Sign request")
        self.assertEqual(activity.date_deadline, fields.Date.to_date(request.expires_at))
        self.assertEqual(
            signer.with_user(internal_signer).sign()["url"],
            f"/sign/user/{signer.id}",
        )

        signer._send_signer_invitation(force=True, reminder=True)
        self.assertEqual(self.env["mail.activity"].search_count(activity_domain), 1)
        signer.invitation_mail_id.write(
            {"state": "exception", "failure_reason": "Synthetic SMTP outage"},
        )

        self.env["sign.oca.request"]._cron_sign_operations()

        self.assertEqual(request.state, "sent")
        self.assertEqual(signer.invitation_delivery_state, "available_in_odoo")
        self.assertTrue(signer.invitation_fallback_at)
        self.assertTrue(
            request.event_ids.filtered(
                lambda event: event.event_type == "invitation_available_in_odoo",
            ),
        )
        self.assertTrue(signer.with_user(internal_signer).is_allow_signature)

        activity.unlink()
        self.env["sign.oca.request"]._cron_sign_operations()
        self.assertEqual(self.env["mail.activity"].search_count(activity_domain), 1)
        request.cancel()
        self.assertFalse(self.env["mail.activity"].search_count(activity_domain))

    def test_share_confirmation_grants_internal_signer_access_and_activity(self):
        internal_signer = new_test_user(
            self.env,
            login="usl-sign-invited-colleague",
            groups="base.group_user",
            company_id=self.company.id,
        )
        internal_signer.partner_id.email = "invited-colleague@example.test"
        sign_group = self.env.ref("usl_sign.group_sign_user")
        self.assertNotIn(sign_group, internal_signer.all_group_ids)
        request = self._ready(
            self._request(partners=[internal_signer.partner_id]),
        )

        confirmation_action = request.action_send()
        self.assertEqual(confirmation_action["res_model"], "usl.sign.share.confirm")
        self.env["usl.sign.share.confirm"].browse(
            confirmation_action["res_id"],
        ).action_confirm()

        self.assertIn(sign_group, internal_signer.all_group_ids)
        signer = request.signer_ids
        self.assertTrue(signer.with_user(internal_signer).is_allow_signature)
        self.assertEqual(
            self.env["mail.activity"].search_count(
                [
                    (
                        "activity_type_id",
                        "=",
                        self.env.ref("usl_sign.mail_activity_type_sign_document").id,
                    ),
                    ("res_model", "=", signer._name),
                    ("res_id", "=", signer.id),
                    ("user_id", "=", internal_signer.id),
                    ("active", "=", True),
                ],
            ),
            1,
        )

    def test_identity_setup_email_copy_review_and_request_resume(self):
        self.company.email = "identity-review@example.test"
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner_one.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Partner record 2026-014",
                "policy_version": "2026.1",
            },
        )
        copy_action = enrollment.with_user(self.reviewer).action_copy_invitation()
        self.assertEqual(copy_action["res_model"], "usl.sign.enrollment.invitation")
        self.assertNotEqual(copy_action["type"], "ir.actions.act_url")
        copy_link = copy_action["context"]["default_invitation_url"]
        self.assertIn(f"/sign/enroll/{enrollment.id}/", copy_link)
        first_hash = enrollment.invitation_token_sha256

        sent = enrollment.with_user(self.reviewer).action_send_invitation()
        self.assertEqual(sent["tag"], "display_notification")
        self.assertTrue(enrollment.invitation_sent_at)
        self.assertEqual(enrollment.invitation_delivery_state, "queued")
        self.assertNotEqual(enrollment.invitation_token_sha256, first_hash)
        self.assertIn(
            f"/sign/enroll/{enrollment.id}/",
            str(enrollment.sudo().invitation_mail_id.body_html),
        )

        sign_request = self._ready(
            self._request(
                partners=[self.partner_one],
                policy_id=self.env.ref(
                    "usl_sign.policy_material_recurring_strong",
                ).id,
                document_category="commercial",
                signer_type="recurring",
                risk_level="material",
                requested_trust="strong_personal",
            ),
        )
        sign_request.action_send()
        self.assertEqual(sign_request.state, "waiting_enrollment")

        enrollment._bind_pocket_identity(
            issuer="https://id.example.test",
            claims={"sub": "review-and-resume", "name": self.partner_one.name},
        )
        self.assertEqual(enrollment.state, "pending_review")
        self.assertTrue(enrollment.activity_ids)
        enrollment.with_user(self.reviewer).action_confirm_identity()

        self.assertEqual(enrollment.state, "active")
        self.assertFalse(enrollment.activity_ids)
        self.assertEqual(sign_request.state, "sent")
        self.assertEqual(
            _personal_certificate_subject(sign_request.signer_ids),
            f"USL Sign Personal: {self.partner_one.name}",
        )

    def test_identity_review_reference_rejects_empty_values(self):
        with self.assertRaises(ValidationError):
            self.env["usl.sign.enrollment"].create(
                {
                    "partner_id": self.partner_one.id,
                    "company_id": self.company.id,
                    "relationship_basis": "employee",
                    "relationship_reference": "   ",
                    "policy_version": "2026.1",
                },
            )

    def test_policy_recommendation_and_authorized_override(self):
        request = self._request(risk_level="material", signer_type="recurring")
        self.assertEqual(request.recommended_trust, "strong_personal")
        request.requested_trust = "standard"
        request.override_reason = "Signer is not yet enrolled; routine fallback approved."
        with self.assertRaises(AccessError):
            request.with_user(self.sign_user).action_mark_ready()
        request.coordinator_ids = self.override_user
        request.with_user(self.override_user).action_mark_ready()
        self.assertEqual(request.state, "ready")

    def test_every_sign_request_requires_a_signed_document(self):
        request = self._request(
            document_category="internal_decision", requires_signed_pdf=False,
        )
        self.assertFalse(request.approval_recommended)
        with self.assertRaisesRegex(ValidationError, "must produce a signed PDF"):
            request.action_mark_ready()

    def test_freeze_is_deterministic_and_sent_content_is_immutable(self):
        request = self._ready(self._request())
        request._freeze_document()
        self.assertEqual(
            request.original_sha256,
            hashlib.sha256(field_content(request.original_data)).hexdigest(),
        )
        self.assertEqual(request.page_map[0]["sha256"], request.document_ids.source_sha256)
        self.assertEqual(
            set(request.evidence_ids.mapped("kind")), {"source", "frozen"},
        )
        self.assertEqual(request.policy_snapshot["version"], request.policy_version)
        self.assertEqual(request.signer_snapshot[0]["partner_id"], self.partner_one.id)
        self.assertTrue(request.consent_text_snapshot)
        request._transition("sent", "request_sent")
        with self.assertRaises(ValidationError):
            request.write({"data": field_value(self.pdf + b"changed")})
        with self.assertRaises(ValidationError):
            request.signer_ids.write({"role_id": self.role_employee.id})

    def test_event_chain_is_append_only_and_detectable(self):
        request = self._request()
        request._append_event("test_event", payload={"value": 1})
        events = request.event_ids.sorted("sequence")
        self.assertEqual(events.mapped("sequence"), list(range(1, len(events) + 1)))
        for previous, current in itertools.pairwise(events):
            self.assertEqual(current.previous_hash, previous.event_hash)
            self.assertEqual(
                current.event_hash,
                hashlib.sha256(
                    f"{previous.event_hash}:{current.payload_sha256}".encode(),
                ).hexdigest(),
            )
        self.assertEqual(events.verify_chain(), events[-1])
        with self.assertRaises(AccessError):
            events[-1].write({"event_type": "tampered"})
        with self.assertRaises(AccessError):
            events[-1].unlink()

    def test_rpc_context_boole_cannot_forge_internal_operations(self):
        request = self._request()
        signer = request.signer_ids
        with self.assertRaisesRegex(ValidationError, "lifecycle action"):
            request.with_context(usl_sign_transition=True).write({"state": "ready"})
        with self.assertRaisesRegex(ValidationError, "controlled signer action"):
            signer.with_context(usl_sign_signer_transition=True).write(
                {"session_token_sha256": hashlib.sha256(b"forged").hexdigest()},
            )
        with self.assertRaisesRegex(AccessError, "controlled evidence"):
            self.env["usl.sign.evidence"].with_context(
                usl_sign_evidence_create=True,
            ).create(
                {
                    "request_id": request.id,
                    "kind": "validation",
                    "name": "forged.json",
                    "data": field_value(b"forged"),
                    "mimetype": "application/json",
                },
            )
        with self.assertRaisesRegex(AccessError, "controlled request action"):
            self.env["usl.sign.external.journey"].with_context(
                usl_sign_external_create=True,
            ).create(
                {
                    "request_id": request.id,
                    "provider_id": self.env["usl.sign.external.provider"].create(
                        {
                            "name": "Context-forgery test provider",
                            "territory": "EU",
                            "mobile_url": "https://provider.example.test",
                            "instructions": "Test only.",
                            "reviewed_on": fields.Date.today(),
                        },
                    ).id,
                    "frozen_sha256": "0" * 64,
                    "signer_information": [],
                },
            )

    def test_raw_pocket_token_is_restricted_to_evidence_reviewers(self):
        request = self._request(user_id=self.sign_user.id)
        public_evidence = request._create_evidence(
            "consent",
            "consent.json",
            b'{"consent":true}',
            mimetype="application/json",
        )
        authentication_evidence = request._create_evidence(
            "authentication",
            "pocket-id-token.jwt",
            b"signed.identity.token",
            mimetype="application/jwt",
        )
        visible_to_sign_user = self.env["usl.sign.evidence"].with_user(
            self.sign_user,
        ).search([("id", "in", (public_evidence | authentication_evidence).ids)])
        self.assertEqual(visible_to_sign_user, public_evidence)
        visible_to_reviewer = self.env["usl.sign.evidence"].with_user(
            self.evidence_reviewer,
        ).search([("id", "in", (public_evidence | authentication_evidence).ids)])
        self.assertEqual(visible_to_reviewer, public_evidence | authentication_evidence)

    def test_signing_link_is_hash_only_one_time_and_revocable(self):
        request = self._ready(self._request())
        request._freeze_document()
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"sent_at": fields.Datetime.now(), "authentication_method": "secure_link"},
        )
        request._transition("sent", "request_sent")
        signer = request.signer_ids
        token = signer._issue_access_token()
        self.assertFalse(signer.access_token)
        self.assertNotEqual(token, signer.access_token_sha256)
        session = signer._exchange_access_token(token)
        self.assertFalse(signer.access_token_sha256)
        with self.assertRaises(AccessError):
            signer._exchange_access_token(token)
        signer._check_token(session, session=True)
        public_info = signer.get_info(access_token=session)
        self.assertEqual(public_info["company_name"], self.company.name)
        self.assertEqual(public_info["signer_role_name"], self.role_customer.name)
        self.assertNotIn("access_token", public_info)
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"access_revoked": True},
        )
        with self.assertRaises(AccessError):
            signer._check_token(session, session=True)
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"signed_on": fields.Datetime.now(), "state": "signed"},
        )
        with self.assertRaisesRegex(AccessError, "invalid, expired, or revoked"):
            signer._exchange_access_token(token)

    def test_signing_link_exchange_is_rate_limited(self):
        request = self._ready(self._request())
        request._freeze_document()
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"sent_at": fields.Datetime.now(), "authentication_method": "secure_link"},
        )
        request._transition("sent", "request_sent")
        signer = request.signer_ids
        valid_token = signer._issue_access_token()
        for _attempt in range(5):
            try:
                signer._exchange_access_token("invalid-token")
            except AccessError:
                pass
            else:
                self.fail("An invalid invitation token was accepted.")
        self.assertGreater(signer.access_blocked_until, fields.Datetime.now())
        self.assertEqual(signer.access_failure_count, 5)
        self.assertEqual(
            request.event_ids.filtered(
                lambda event: event.event_type == "signing_link_rejected",
            )[-1].payload["payload"],
            {"attempt_count": 5, "blocked": True},
        )
        with self.assertRaisesRegex(AccessError, "temporarily unavailable"):
            signer._exchange_access_token(valid_token)

    def test_email_otp_is_hash_only_one_time_and_rate_limited(self):
        self.policy.default_authentication = "email_otp"
        request = self._ready(self._request())
        request._freeze_document()
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {
                "sent_at": fields.Datetime.now(),
                "authentication_method": "email_otp",
            },
        )
        request._transition("sent", "request_sent")
        signer = request.signer_ids
        invitation = signer._issue_access_token()
        with (
            patch("odoo.addons.usl_sign.models.request.secrets.randbelow", return_value=42),
            patch.object(type(signer), "_send_ephemeral_email", return_value=True),
        ):
            exchange = signer._exchange_access_token(invitation)
        self.assertEqual(exchange["otp_required"], True)
        self.assertFalse(signer.access_token_sha256)
        self.assertFalse(signer.session_token_sha256)
        self.assertNotEqual(signer.otp_exchange_token_sha256, exchange["exchange_token"])
        self.assertNotEqual(signer.email_otp_sha256, "00000042")
        with self.assertRaisesRegex(AccessError, "incorrect"):
            signer._verify_email_otp(exchange["exchange_token"], "00000041")
        session = signer._verify_email_otp(exchange["exchange_token"], "00000042")
        signer._check_token(session, session=True)
        self.assertFalse(signer.email_otp_sha256)
        self.assertFalse(signer.otp_exchange_token_sha256)
        with self.assertRaisesRegex(AccessError, "invalid or expired"):
            signer._verify_email_otp(exchange["exchange_token"], "00000042")

    def test_ordered_signers_and_frozen_geometry(self):
        request = self._ready(
            self._request(
                partners=[self.partner_one, self.partner_two],
                roles=[self.role_customer, self.role_employee],
                signing_order=True,
            ),
        )
        with patch.object(type(request.signer_ids), "_send_signer_invitation", return_value=True):
            request.action_send()
        first, second = request.signer_ids.sorted(lambda row: (row.sequence, row.id))
        first.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "state": "viewed",
                "session_token_sha256": hashlib.sha256(b"first-session").hexdigest(),
                "session_expires_at": fields.Datetime.now() + timedelta(minutes=5),
            },
        )
        with self.assertRaises(AccessError):
            second._check_token("not-issued", session=True)
        items = self._items(request, first.role_id, "Camille Signer")
        own_item = next(
            item for item in items.values() if int(item["role_id"]) == first.role_id.id
        )
        own_item.update({"page": 999, "position_x": 99, "width": 1})
        with patch.object(type(first), "_activate_next_signer_or_finish", return_value=True):
            first.action_sign(
                items,
                access_token="first-session",
                document_sha256=hashlib.sha256(
                    field_content(request.data),
                ).hexdigest(),
                consent=True,
            )
        persisted = next(
            item
            for item in request.signatory_data.values()
            if int(item["role_id"]) == first.role_id.id
        )
        frozen = next(
            item
            for item in request.frozen_layout.values()
            if int(item["role_id"]) == first.role_id.id
        )
        self.assertEqual(persisted["value"], "Camille Signer")
        self.assertEqual(persisted["page"], frozen["page"])
        self.assertEqual(persisted["position_x"], frozen["position_x"])
        self.assertEqual(persisted["width"], frozen["width"])

    def test_cross_validation_disagreement_never_completes(self):
        request = self._ready(self._request())
        request._freeze_document()
        request._transition("sent", "request_sent")
        request._transition("validating", "validation_started")
        request.signer_ids.with_context(
            usl_sign_signer_transition=INTERNAL_OPERATION,
        ).write(
            {
                "state": "signed",
                "signed_on": fields.Datetime.now(),
                "authentication_method": "secure_link",
                "consent_text": request.consent_text_snapshot,
                "consent_version": "2026.1",
                "consented_at": fields.Datetime.now(),
                "signed_document_sha256": hashlib.sha256(self.pdf).hexdigest(),
            },
        )
        dss = FakeDSS()
        dss.cross_validate = lambda document: {
            "status": "invalid",
            "signature_count": 0,
        }
        with patch.object(type(request), "_sign_dss_client", return_value=dss):
            result = request._complete_validated_document(
                self.pdf,
                FakeDSS.validate(self.pdf, "standard"),
            )
        self.assertFalse(result)
        self.assertEqual(request.state, "action_required")
        self.assertEqual(request.validation_status, "indeterminate")
        self.assertFalse(request.final_data)
        self.assertIn("pyhanko", " ".join(request.evidence_ids.mapped("name")).lower())

    def test_validation_without_achieved_trust_fails_closed(self):
        request = self._ready(self._request())
        request._freeze_document()
        request._transition("sent", "request_sent")
        request._transition("validating", "validation_started")
        validation = FakeDSS.validate(self.pdf, "standard")
        validation.pop("achievedTrust")
        with patch.object(type(request), "_sign_dss_client", return_value=FakeDSS()):
            self.assertFalse(request._complete_validated_document(self.pdf, validation))
        self.assertEqual(request.state, "validation_failed")
        self.assertEqual(request.validation_status, "invalid")
        self.assertFalse(request.achieved_trust)
        self.assertFalse(request.final_data)

    def test_preparation_rejects_fields_outside_the_pdf_page(self):
        request = self._request()
        layout = json.loads(json.dumps(request.signatory_data))
        next(iter(layout.values())).update({"position_x": 90, "width": 20})
        request.signatory_data = layout
        with self.assertRaisesRegex(ValidationError, "outside the PDF page"):
            request.action_mark_ready()

    def test_published_template_configure_creates_a_clean_new_version(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Versioned clean template",
                "data": field_value(self.pdf),
                "filename": "versioned.pdf",
                "company_id": self.company.id,
                "policy_id": self.policy.id,
            },
        )
        signature = self.env.ref("sign_oca.sign_field_signature")
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": signature.id,
                "role_id": self.role_customer.id,
                "required": True,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 25,
                "height": 8,
            },
        )
        template.action_mark_ready()
        with self.assertRaisesRegex(ValidationError, "cannot be deleted"):
            template.unlink()
        action = template.configure()
        self.assertEqual(action["type"], "ir.actions.act_window")
        new_template = self.env["sign.oca.template"].browse(action["res_id"])
        self.assertEqual(new_template.version, 2)
        self.assertEqual(new_template.previous_version_id, template)
        self.assertEqual(new_template.preparation_status, "draft")
        self.assertFalse(template.active)
        self.assertEqual(len(new_template.item_ids), 1)
        self.assertEqual(len(new_template.document_ids), 1)
        self.assertEqual(
            new_template.configure()["tag"],
            "usl_sign_template_configure",
        )

    def test_template_hash_compute_ignores_web_binary_size_context(self):
        template = self.env["sign.oca.template"].with_context(bin_size=True).create(
            {
                "name": "Browser upload hash regression",
                "filename": "browser-upload.pdf",
                "data": field_value(self.pdf),
            },
        )
        self.assertEqual(
            template.document_sha256,
            hashlib.sha256(self.pdf).hexdigest(),
        )

    def test_request_editor_uses_a_reload_safe_client_action(self):
        action = self._request().configure()
        self.assertEqual(action["tag"], "usl_sign_request_configure")

    def test_external_import_rejects_a_different_revision(self):
        provider = self.env["usl.sign.external.provider"].create(
            {
                "name": "Configurable qualified provider",
                "territory": "EU",
                "mobile_url": "https://provider.example.test",
                "instructions": "Download, sign and import.",
                "reviewed_on": fields.Date.today(),
            },
        )
        request = self._request(
            requested_trust="qualified_external",
            external_provider_id=provider.id,
            risk_level="maximum",
            formal_qes_required=True,
        )
        request.action_mark_ready()
        request.action_send()
        journey = request.external_journey_id
        journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
            {
                "imported_pdf": field_value(self.pdf),
                "imported_filename": "signed.pdf",
                "proof_package": field_value(b"provider proof"),
                "proof_filename": "proof.bin",
                "state": "imported",
                "imported_sha256": hashlib.sha256(self.pdf).hexdigest(),
                "imported_at": fields.Datetime.now(),
            },
        )
        request._transition("signed_to_import", "external_document_imported")
        with patch.object(type(request), "_sign_dss_client", return_value=FakeDSS(revision_matches=False)):
            self.assertFalse(request.action_validate_external())
        self.assertEqual(request.state, "validation_failed")
        self.assertEqual(journey.state, "rejected")
        self.assertFalse(request.completed_at)

    def test_external_provider_requires_a_safe_https_journey(self):
        with self.assertRaisesRegex(ValidationError, "HTTPS URL"):
            self.env["usl.sign.external.provider"].create(
                {
                    "name": "Unsafe provider configuration",
                    "territory": "EU",
                    "mobile_url": "http://provider.example.test/sign",
                    "instructions": "This record must be rejected.",
                    "reviewed_on": fields.Date.today(),
                },
            )

    def test_external_journey_exposes_export_instructions_and_import_gate(self):
        provider = self.env["usl.sign.external.provider"].create(
            {
                "name": "Reviewed qualified provider",
                "territory": "EU",
                "mobile_url": "https://provider.example.test/mobile",
                "instructions": "Download the frozen PDF and return every proof file.",
                "reviewed_on": fields.Date.today(),
            },
        )
        request = self._request(
            requested_trust="qualified_external",
            external_provider_id=provider.id,
            risk_level="maximum",
            formal_qes_required=True,
        )
        request.action_mark_ready()
        request.action_send()
        journey = request.external_journey_id

        self.assertEqual(request.state, "waiting_external")
        self.assertEqual(journey.state, "waiting")
        self.assertEqual(journey.next_step, "Download the exact document to sign.")
        self.assertTrue(journey.signer_summary.startswith("1. Camille Signer"))
        self.assertEqual(
            journey.action_open_details()["views"][0][0],
            self.env.ref("usl_sign.sign_external_journey_form").id,
        )
        self.assertEqual(
            journey.action_open_provider()["url"],
            "https://provider.example.test/mobile",
        )
        provider.mobile_url = "https://provider.example.test/new-catalog-url"
        self.assertEqual(
            journey.action_open_provider()["url"],
            "https://provider.example.test/mobile",
        )
        first_export = journey.action_export()
        first_exported_at = journey.exported_at
        second_export = journey.action_export()
        self.assertEqual(first_export["target"], "download")
        self.assertEqual(second_export["url"], first_export["url"])
        self.assertEqual(journey.exported_at, first_exported_at)
        self.assertEqual(
            journey.next_step,
            "Complete the signature with the provider, then upload the result.",
        )
        self.assertEqual(
            len(request.event_ids.filtered(lambda event: event.event_type == "external_document_exported")),
            1,
        )
        import_action = journey.action_open_import()
        self.assertEqual(import_action["res_model"], "usl.sign.external.import.wizard")
        wizard = self.env["usl.sign.external.import.wizard"].create(
            {
                "journey_id": journey.id,
                "signed_pdf": field_value(self.pdf),
                "signed_filename": "qualified-signed.pdf",
                "proof_package": field_value(b"external proof package"),
                "proof_filename": "qualified-proof.zip",
            },
        )
        result = wizard.action_import()
        self.assertEqual(request.state, "signed_to_import")
        self.assertEqual(journey.state, "imported")
        self.assertEqual(result["res_id"], request.id)
        self.assertFalse(request.completed_at)

    def test_external_validation_outage_is_retryable_not_a_rejection(self):
        provider = self.env["usl.sign.external.provider"].create(
            {
                "name": "Retryable qualified provider",
                "territory": "EU",
                "mobile_url": "https://provider.example.test/mobile",
                "instructions": "Return the signed PDF and proof package.",
                "reviewed_on": fields.Date.today(),
            },
        )
        request = self._request(
            requested_trust="qualified_external",
            external_provider_id=provider.id,
            risk_level="maximum",
            formal_qes_required=True,
        )
        request.action_mark_ready()
        request.action_send()
        journey = request.external_journey_id
        journey.with_context(usl_sign_external_transition=INTERNAL_OPERATION).write(
            {
                "imported_pdf": field_value(self.pdf),
                "imported_filename": "signed.pdf",
                "proof_package": field_value(b"provider proof"),
                "proof_filename": "proof.zip",
                "state": "imported",
                "imported_sha256": hashlib.sha256(self.pdf).hexdigest(),
                "imported_at": fields.Datetime.now(),
            },
        )
        request._transition("signed_to_import", "external_document_imported")

        class OfflineDSS:
            @staticmethod
            def revision_matches(*args, **kwargs):
                del args, kwargs
                msg = "DSS unavailable"
                raise DSSServiceError(msg)

        with patch.object(
            type(request), "_sign_dss_client", return_value=OfflineDSS(),
        ):
            self.assertFalse(request.action_validate_external())
        self.assertEqual(request.state, "action_required")
        self.assertEqual(request.validation_status, "indeterminate")
        self.assertEqual(journey.state, "imported")
        self.assertFalse(journey.rejection_reason)
        with patch.object(
            type(request), "action_validate_external", autospec=True, return_value=True,
        ) as retry_validation:
            request.action_retry_validation()
        retry_validation.assert_called_once()
        self.assertEqual(request.state, "signed_to_import")

    def test_completion_waits_for_archive_and_recovers_idempotently(self):
        self.company.sign_oca_send_sign_request_copy = True
        request = self._ready(self._request())
        request._freeze_document()
        request._transition("sent", "request_sent")
        request._transition("validating", "validation_started")
        request.signer_ids.with_context(
            usl_sign_signer_transition=INTERNAL_OPERATION,
        ).write(
            {
                "state": "signed",
                "signed_on": fields.Datetime.now(),
                "authentication_method": "secure_link",
                "consent_text": request.consent_text_snapshot,
                "consent_version": "2026.1",
                "consented_at": fields.Datetime.now(),
                "signed_document_sha256": hashlib.sha256(self.pdf).hexdigest(),
            },
        )
        archived_signed = self.env["usl.document"].sudo().create(
            {
                "name": "Clean Sign signed document",
                "paperless_id": 990001,
                "company_id": self.company.id,
                "confidentiality": "private",
                "availability_state": "available",
                "source": "odoo_generated",
            },
        )
        archived_dossier = self.env["usl.document"].sudo().create(
            {
                "name": "Clean Sign proof package",
                "paperless_id": 990002,
                "company_id": self.company.id,
                "confidentiality": "private",
                "availability_state": "available",
                "source": "odoo_generated",
            },
        )
        archive_results = iter(
            [
                RuntimeError("synthetic Paperless outage"),
                {
                    "state": "duplicate",
                    "document_id": archived_signed.id,
                    "message": "Checksum-identical signed document reused.",
                },
                {
                    "state": "duplicate",
                    "document_id": archived_dossier.id,
                    "message": "Checksum-identical proof package reused.",
                },
            ],
        )
        archive_call_users = []
        archive_uploads = []

        def archive_upload(documents, *args, **kwargs):
            del kwargs
            archive_call_users.append(documents.env.user.id)
            archive_uploads.append((args[0], args[1]))
            result = next(archive_results)
            if isinstance(result, Exception):
                raise result
            return result

        upload = patch.object(
            type(self.env["usl.document"]),
            "upload_from_odoo",
            autospec=True,
            side_effect=archive_upload,
        )
        with (
            patch.object(type(request), "_sign_dss_client", return_value=FakeDSS()),
            upload,
            patch.object(
                type(self.env["mail.thread"]), "message_notify", autospec=True,
            ) as notify,
        ):
            validation = request._complete_validated_document(
                self.pdf, FakeDSS.validate(self.pdf, "standard"),
            )
            self.assertTrue(validation)
            self.assertEqual(request.state, "evidence_incomplete")
            self.assertEqual(request.archive_status, "failed")
            self.assertFalse(request.completed_at)
            signed_evidence = request.evidence_ids.filtered(
                lambda evidence: evidence.kind == "signed",
            )
            self.assertEqual(len(signed_evidence), 1)
            self.assertEqual(signed_evidence.sha256, request.final_sha256)
            request.action_retry_archive()
            request._reconcile_archive()
            notify.assert_called_once()
            self.assertTrue(notify.call_args.kwargs["attachment_ids"])
        self.assertEqual(request.state, "completed")
        self.assertEqual(request.archive_status, "archived")
        self.assertEqual(request.archive_document_id, archived_signed)
        self.assertEqual(request.archive_dossier_document_id, archived_dossier)
        self.assertTrue(request.dossier_filename.endswith("-proof-package.pdf"))
        self.assertEqual(
            set(archive_call_users),
            {self.env.ref("base.user_root").id},
        )
        self.assertTrue(
            all(isinstance(payload, str) for _filename, payload in archive_uploads),
        )
        signed_uploads = [
            payload
            for filename, payload in archive_uploads
            if filename == request.final_filename
        ]
        dossier_uploads = [
            payload
            for filename, payload in archive_uploads
            if filename == request.dossier_filename
        ]
        self.assertEqual(len(signed_uploads), 2)
        self.assertEqual(len(dossier_uploads), 1)
        self.assertTrue(
            all(
                base64.b64decode(payload, validate=True)
                == field_content(request.final_data)
                for payload in signed_uploads
            ),
        )
        self.assertEqual(
            base64.b64decode(dossier_uploads[0], validate=True),
            field_content(request.dossier_data),
        )
        self.assertTrue(request.completed_at)
        self.assertEqual(request.evidence_status, "complete")
        self.assertEqual(request.daily_timestamp_status, "scheduled")
        self.assertEqual(request.state, "completed")
        preview = request.preview()
        self.assertEqual(preview["type"], "ir.actions.act_url")
        self.assertIn("/final_data/", preview["url"])
        self.assertNotEqual(preview.get("tag"), "sign_oca_preview")
        self.assertEqual(
            len(request.event_ids.filtered(
                lambda event: event.event_type == "completed_dossier_queued",
            )),
            1,
        )

        class Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                tomorrow = datetime.now(UTC) + timedelta(days=1)
                return tomorrow if tz else tomorrow.replace(tzinfo=None)

        with (
            patch("odoo.addons.usl_sign.models.daily_manifest.datetime", Tomorrow),
            patch(
                "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
                return_value=FakeDSS(),
            ),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                fields.Date.today(),
            )
        entry = manifest.entry_ids.filtered(lambda item: item.request_id == request)
        self.assertEqual(entry.final_sha256, request.final_sha256)
        self.assertTrue(entry.dossier_sha256)
        self.assertTrue(entry.completion_event_hash)
        self.assertEqual(request.daily_timestamp_status, "scheduled")
        self.assertEqual(request.state, "completed")

    def test_proof_package_embeds_the_signed_pdf_as_its_primary_artifact(self):
        request = self._request()
        request.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "final_data": field_value(self.pdf),
                "final_filename": "Routine-agreement-signed.pdf",
                "final_sha256": hashlib.sha256(self.pdf).hexdigest(),
                "achieved_trust": "standard",
            },
        )
        captured = {}

        class CapturingDSS(FakeDSS):
            @staticmethod
            def build_dossier(**kwargs):
                captured.update(kwargs)
                return {"document": base64.b64encode(_pdf()).decode()}

        with patch.object(
            type(request), "_sign_dss_client", return_value=CapturingDSS(),
        ):
            request._build_dossier_pdf(b"signed manifest")

        signed_artifact = next(
            artifact
            for artifact in captured["artifacts"]
            if artifact["name"] == "final-Routine-agreement-signed.pdf"
        )
        self.assertEqual(signed_artifact["content"], self.pdf)
        self.assertEqual(signed_artifact["mimetype"], "application/pdf")
        self.assertEqual(signed_artifact["relationship"], "Data")
        self.assertIn(
            "Signed PDF embedded in this package: final-Routine-agreement-signed.pdf",
            captured["summary"],
        )

    def test_oca_final_document_delivery_defaults_to_enabled(self):
        defaults = self.env["res.company"].default_get(
            ["sign_oca_send_sign_request_copy"],
        )
        self.assertTrue(defaults["sign_oca_send_sign_request_copy"])

    def test_pocket_enrolment_revocation_and_reenrolment_are_controlled(self):
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner_one.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Contractor file C-001",
                "policy_version": "2026.1",
            },
        )
        enrollment._bind_pocket_identity(
            issuer="https://id.example.test",
            claims={
                "sub": "immutable-pocket-subject",
                "name": "Camille Signer",
                "email": "camille@example.test",
            },
        )
        self.assertEqual(enrollment.state, "pending_review")
        self.assertEqual(enrollment.pocket_subject, "immutable-pocket-subject")
        enrollment.with_user(self.reviewer).action_confirm_identity()
        self.assertEqual(enrollment.state, "active")
        enrollment.with_user(self.reviewer).action_revoke("Pocket identity access revoked.")
        self.assertEqual(enrollment.state, "revoked")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT state FROM usl_sign_enrollment WHERE id = %s", [enrollment.id],
        )
        self.assertEqual(self.env.cr.fetchone()[0], "revoked")
        replacement = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner_one.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Re-reviewed contractor file C-001",
                "policy_version": "2026.2",
            },
        )
        self.assertEqual(replacement.state, "pending_pocket")
        with self.assertRaises(AccessError):
            enrollment.unlink()

    def test_signer_can_cancel_and_retry_a_live_strong_ceremony(self):
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner_one.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Controlled cancellation fixture",
                "policy_version": "2026.1",
            },
        )
        enrollment._bind_pocket_identity(
            issuer="https://id.example.test",
            claims={"sub": "cancel-fixture-subject", "name": self.partner_one.name},
        )
        enrollment.with_user(self.reviewer).action_confirm_identity()
        sign_request = self._request(
            policy_id=self.env.ref("usl_sign.policy_material_recurring_strong").id,
            document_category="commercial",
            signer_type="recurring",
            risk_level="material",
            requested_trust="strong_personal",
        )
        sign_request.action_mark_ready()
        sign_request.action_send()
        signer = sign_request.signer_ids
        invitation_token = signer._issue_access_token()
        session_token = signer._exchange_access_token(invitation_token)
        ceremony = self.env["usl.sign.ceremony"].create(
            {
                "request_id": sign_request.id,
                "signer_id": signer.id,
                "enrollment_id": enrollment.id,
                "challenge": field_value(b"binding"),
                "challenge_sha256": hashlib.sha256(b"binding").hexdigest(),
                "document_sha256": hashlib.sha256(self.pdf).hexdigest(),
                "consent_sha256": hashlib.sha256(b"consent").hexdigest(),
                "csr_sha256": hashlib.sha256(b"csr").hexdigest(),
                "public_key_sha256": hashlib.sha256(b"public").hexdigest(),
                "csr_pem": "fixture-csr",
                "binding_payload": {"fixture": True},
                "expires_at": fields.Datetime.now() + timedelta(minutes=5),
            },
        )
        ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "authorizing",
                "data_to_sign": "one-use-data",
                "dss_signing_context": "one-use-context",
            },
        )
        fake_request = SimpleNamespace(env=self.env, session={})
        with patch(
            "odoo.addons.usl_sign.controllers.strong.request",
            fake_request,
        ):
            result = StrongSignController().strong_cancel(
                signer.id,
                session_token,
                ceremony.id,
            )
        ceremony.invalidate_recordset()
        self.assertEqual(result["state"], "revoked")
        self.assertEqual(ceremony.state, "revoked")
        self.assertFalse(ceremony.data_to_sign)
        self.assertFalse(ceremony.dss_signing_context)
        self.assertEqual(ceremony.failure_code, "signer_restarted")
        self.assertEqual(
            sign_request.event_ids[-1].event_type,
            "strong_signature_attempt_cancelled",
        )

    def test_decisions_are_not_loaded_into_the_product_registry(self):
        self.assertNotIn("usl.sign.approval", self.env.registry)
        self.assertNotIn("usl.sign.approval.event", self.env.registry)
        self.assertFalse(
            self.env.ref("usl_sign.completed_decisions_action", raise_if_not_found=False),
        )

    def test_document_navigation_and_retrieval_views_match_the_product_boundary(self):
        expected = [
            ("usl_sign.sign_dashboard_menu", "Sign Dashboard", "sign_oca.sign_oca_root_menu"),
            ("usl_sign.request_signature_menu", "Request Signature", "sign_oca.sign_oca_root_menu"),
            ("usl_sign.request_signature_templates_menu", "Templates", "usl_sign.request_signature_menu"),
            ("usl_sign.request_signature_open_menu", "Open Requests", "usl_sign.request_signature_menu"),
            ("usl_sign.request_signature_completed_menu", "Completed", "usl_sign.request_signature_menu"),
            ("usl_sign.my_signatures_menu", "My Signatures", "sign_oca.sign_oca_root_menu"),
        ]
        for xml_id, name, parent_xml_id in expected:
            menu = self.env.ref(xml_id)
            self.assertTrue(menu.active)
            self.assertEqual(menu.name, name)
            self.assertEqual(menu.parent_id, self.env.ref(parent_xml_id))
        self.assertFalse(
            self.env.ref("usl_sign.sign_library_menu", raise_if_not_found=False),
        )
        self.assertNotIn("request_type", self.env["usl.sign.start"]._fields)
        landing = self.env["usl.sign.workspace"].with_user(self.sign_user).get_landing()
        self.assertEqual(
            set(landing["sections"]),
            {"sign_now", "prepare", "issues", "waiting", "completed"},
        )

        my_signatures = self.env.ref("usl_sign.my_signatures_action")
        self.assertNotIn("search_default", my_signatures.context or "")
        search_arch = self.env.ref("usl_sign.my_signature_search_usl").arch
        for filter_name in [
            "to_sign",
            "waiting_turn",
            "signed_by_me",
            "completed",
            "closed",
            "group_sender",
            "group_due",
        ]:
            self.assertIn(f'name="{filter_name}"', search_arch)

        completed_action = self.env.ref("usl_sign.completed_documents_action")
        self.assertIn("managed_by_current_user", completed_action.domain)

    def test_internal_signer_dashboard_and_result_do_not_expose_other_signer_rows(self):
        internal_signer = new_test_user(
            self.env,
            login="usl-sign-invited-user",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        internal_signer.partner_id.email = "invited@example.test"
        request = self._request(
            partners=[internal_signer.partner_id, self.partner_two],
            roles=[self.role_customer, self.role_employee],
            user_id=self.sign_user.id,
        )
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"state": "sent"},
        )
        own_signer, other_signer = request.sudo().signer_ids.sorted("sequence")
        own_signer.with_context(
            usl_sign_signer_transition=INTERNAL_OPERATION,
        ).write({"state": "notified"})

        participant_request = request.with_user(internal_signer)
        self.assertTrue(own_signer.with_user(internal_signer).is_allow_signature)
        self.assertEqual(participant_request.signer_progress, "0 of 2 signed")
        self.assertEqual(participant_request.next_step, "Waiting for 2 signers.")
        landing = self.env["usl.sign.workspace"].with_user(
            internal_signer,
        ).get_landing()
        self.assertEqual(landing["sections"]["sign_now"]["count"], 1)
        self.assertEqual(
            landing["sections"]["sign_now"]["items"][0]["progress"],
            "0 of 2 signed",
        )
        with self.assertRaises(AccessError):
            other_signer.with_user(internal_signer).read(["state"])

        own_signer.with_context(
            usl_sign_signer_transition=INTERNAL_OPERATION,
        ).write({"state": "signed", "signed_on": fields.Datetime.now()})
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {
                "state": "completed",
                "validation_status": "valid",
                "evidence_status": "complete",
                "archive_status": "archived",
                "final_data": field_value(self.pdf),
                "final_filename": "signed.pdf",
                "completion_certificate": field_value(self.pdf),
                "completion_filename": "certificate.pdf",
                "dossier_data": field_value(self.pdf),
                "dossier_filename": "proof.pdf",
                "completed_at": fields.Datetime.now(),
            },
        )
        result_action = own_signer.with_user(internal_signer).action_open_request()
        self.assertEqual(
            result_action["views"][0][0],
            self.env.ref("usl_sign.sign_request_signer_result_form").id,
        )
        self.assertEqual(participant_request.preview()["type"], "ir.actions.act_url")
        completed_landing = self.env["usl.sign.workspace"].with_user(
            internal_signer,
        ).get_landing()
        completed_item = completed_landing["sections"]["completed"]["items"][0]
        self.assertEqual(completed_item["action"]["method"], "action_open_request")
        self.assertFalse(
            self.env["sign.oca.request"].with_user(internal_signer).search(
                [
                    ("id", "=", request.id),
                    ("managed_by_current_user", "=", True),
                ],
            ),
        )
        self.assertEqual(
            self.env["sign.oca.request"].with_user(self.sign_user).search(
                [
                    ("id", "=", request.id),
                    ("managed_by_current_user", "=", True),
                ],
            ),
            request,
        )

    def test_send_confirms_backend_access_for_invited_odoo_users(self):
        internal_signer = new_test_user(
            self.env,
            login="usl-sign-share-confirm-user",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        internal_signer.partner_id.email = "share-confirm@example.test"
        request = self._ready(
            self._request(
                partners=[internal_signer.partner_id],
                user_id=self.sign_user.id,
            ),
        )
        action = request.with_user(self.sign_user).action_send(
            message="Please review this agreement.",
        )
        self.assertEqual(action["res_model"], "usl.sign.share.confirm")
        self.assertEqual(request.state, "ready")
        wizard = self.env["usl.sign.share.confirm"].with_user(
            self.sign_user,
        ).browse(action["res_id"])
        self.assertEqual(wizard.recipient_names, internal_signer.name)
        self.assertEqual(wizard.recipient_count, 1)
        with patch.object(
            type(request.signer_ids),
            "_send_signer_invitation",
            return_value=True,
        ):
            self.assertTrue(wizard.action_confirm())
        request.invalidate_recordset()
        self.assertEqual(request.state, "sent")
        self.assertEqual(request.responsible_message, "Please review this agreement.")

    def test_one_off_upload_starts_with_a_signer_and_opens_field_placement(self):
        start = self.env["usl.sign.start"].with_user(self.sign_user).create(
            {
                "document_data": field_value(self.pdf),
                "document_filename": "routine_agreement.pdf",
                "signer_partner_id": self.partner_one.id,
                "message": "Please review and sign.",
            },
        )
        self.assertEqual(start.signature_source, "upload")
        action = start.action_continue()
        request = self.env["sign.oca.request"].search(
            [("name", "=", "routine agreement"), ("user_id", "=", self.sign_user.id)],
            limit=1,
        )
        self.assertTrue(request)
        self.assertEqual(action["tag"], "usl_sign_request_configure")
        self.assertEqual(request.requested_trust, "standard")
        self.assertEqual(request.signer_ids.partner_id, self.partner_one)
        self.assertEqual(request.signer_ids.role_id, self.role_customer)
        self.assertEqual(request.responsible_message, "Please review and sign.")

    def test_my_signatures_uses_human_statuses_and_clear_actions(self):
        request = self._request(user_id=self.sign_user.id)
        signer = request.signer_ids
        self.assertEqual(signer.personal_status, "Not sent yet")

        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"state": "sent"},
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "notified"},
        )
        signer.invalidate_recordset(["state", "is_allow_signature", "personal_status"])
        self.assertEqual(signer.personal_status, "Ready to sign")

        view_arch = self.env.ref("usl_sign.my_signature_list_usl").arch
        self.assertIn('name="personal_status"', view_arch)
        self.assertIn('string="Review and sign"', view_arch)
        self.assertIn('string="View result"', view_arch)
        self.assertNotIn('string="Your signature"', view_arch)

        role_action = self.env.ref("sign_oca.sign_oca_role_act_window")
        role_menu = self.env.ref("sign_oca.sign_oca_role_menu")
        self.assertEqual(role_action.name, "Signing Roles")
        self.assertEqual(role_menu.name, "Signing Roles")

    def test_request_status_summary_never_calls_a_failed_or_closed_request_done(self):
        request = self._request(
            user_id=self.sign_user.id,
            record_ref=f"res.partner,{self.partner_one.id}",
        )
        summary = self.env["sign.oca.request"].with_user(
            self.sign_user,
        ).get_business_record_summary("res.partner", self.partner_one.id)
        self.assertEqual(summary["state_label"], "Draft")
        self.assertEqual(summary["requested_trust"], "Standard")
        expected = {
            "ready": "Ready to send",
            "waiting_enrollment": "Waiting for identity setup",
            "waiting_external": "With external provider",
            "signed_to_import": "Ready to check",
            "validating": "Checking result",
            "evidence_incomplete": "Final storage needs attention",
            "action_required": "Needs attention",
            "validation_failed": "Result rejected",
            "completed": "Completed",
            "declined": "Declined",
            "expired": "Expired",
            "cancelled": "Cancelled",
        }
        for state, label in expected.items():
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {"state": state},
            )
            self.assertEqual(request.lifecycle_stage_label, label)
            self.assertNotEqual(request.lifecycle_stage_label, "Done")

    def test_signing_method_is_changed_through_a_focused_permission_gate(self):
        request = self._request(user_id=self.sign_user.id)
        action = request.with_user(self.sign_user).action_open_signing_method()
        self.assertEqual(action["res_model"], "usl.sign.request.method")
        method = self.env["usl.sign.request.method"].with_user(self.sign_user).create(
            {"request_id": request.id},
        )
        self.assertEqual(method.requested_trust, "standard")
        method.requested_trust = "strong_personal"
        method.override_reason = "This agreement needs a known signer."
        with self.assertRaisesRegex(AccessError, "permission to override"):
            method.action_apply()

    def test_configuration_guidance_uses_business_facing_role_and_capability_copy(self):
        role_labels = dict(
            self.env["sign.oca.role"]._fields[
                "partner_selection_policy"
            ]._description_selection(self.env),
        )
        self.assertEqual(role_labels["empty"], "Choose for each request")
        self.assertEqual(role_labels["default"], "Preselect one person")
        self.assertEqual(role_labels["expression"], "Use the linked business record")
        self.assertFalse(self.env.ref("sign_oca.sign_oca_field_menu").active)
        self.assertEqual(
            self.env.ref("usl_sign.sign_service_status_action").name,
            "System Status",
        )
        health = self.env["usl.sign.service.health"]._ensure_company(self.company)
        self.assertTrue(all(health.mapped("purpose")))
        self.assertTrue(all(health.mapped("checks")))
        self.assertTrue(health.filtered(lambda row: row.code == "rfc3161").is_optional)
        standard = health.filtered(lambda row: row.code == "standard")
        standard.with_context(usl_sign_health_write=INTERNAL_OPERATION).write(
            {"name": "Old technical label", "sequence": 999},
        )
        refreshed = self.env["usl.sign.service.health"]._ensure_company(self.company)
        standard = refreshed.filtered(lambda row: row.code == "standard")
        self.assertEqual(standard.name, "Standard documents")
        self.assertEqual(standard.sequence, 10)

    def test_service_status_reports_ready_missing_and_partial_capabilities(self):
        health = self.env["usl.sign.service.health"]._ensure_company(self.company)
        checked = health.filtered(lambda row: row.code in {"standard", "strong", "qualified"})
        model_type = type(health)
        with (
            patch.object(
                model_type,
                "_dss",
                return_value={"engineVersion": "6.4", "qualifiedTrustReady": True},
            ),
            patch.object(
                model_type,
                "_paperless",
                return_value={"server_version": "2.18"},
            ),
            patch.object(model_type, "_pocket", return_value={"fresh": True}),
            patch.object(model_type, "_step_ca", return_value={"status": "UP"}),
        ):
            checked.with_user(self.sign_admin)._refresh_checks()
        self.assertEqual(set(checked.mapped("status")), {"ready"})
        self.assertTrue(all(checked.mapped("checked_at")))

        standard = health.filtered(lambda row: row.code == "standard")
        with (
            patch.object(model_type, "_dss", return_value={"engineVersion": "6.4"}),
            patch.object(model_type, "_paperless", return_value=False),
        ):
            standard.with_user(self.sign_admin)._refresh_checks()
        self.assertEqual(standard.status, "not_configured")
        self.assertEqual(standard.diagnostic_code, "paperless_not_configured")

        qualified = health.filtered(lambda row: row.code == "qualified")
        with (
            patch.object(
                model_type,
                "_dss",
                return_value={"engineVersion": "6.4", "qualifiedTrustReady": False},
            ),
            patch.object(
                model_type,
                "_paperless",
                return_value={"server_version": "2.18"},
            ),
        ):
            qualified.with_user(self.sign_admin)._refresh_checks()
        self.assertEqual(qualified.status, "action_required")
        self.assertEqual(qualified.diagnostic_code, "qualified_trust_unavailable")

    def test_service_status_fails_closed_for_stale_cron_and_other_company(self):
        health_model = self.env["usl.sign.service.health"]
        health = health_model._ensure_company(self.company)
        daily = health.filtered(lambda row: row.code == "daily_proof")
        manifest_cron = self.env.ref("usl_sign.ir_cron_sign_daily_event_heads")
        manifest_cron.active = False
        model_type = type(health)
        with (
            patch.object(model_type, "_dss", return_value={"engineVersion": "6.4"}),
            patch.object(
                model_type,
                "_paperless",
                return_value={"server_version": "2.18"},
            ),
        ):
            daily.with_user(self.sign_admin)._refresh_checks()
        self.assertEqual(daily.status, "action_required")
        self.assertEqual(daily.diagnostic_code, "daily_proof_cron_unhealthy")

        other_company = self.env["res.company"].create({"name": "Other Sign Company"})
        other = health_model._ensure_company(other_company)[0]
        self.assertFalse(
            health_model.with_user(self.sign_admin).search(
                [("company_id", "=", other_company.id)],
            ),
        )
        with self.assertRaises(AccessError):
            other.with_user(self.sign_admin).action_refresh()

    def test_template_upload_creates_one_atomic_multi_pdf_envelope(self):
        manager_templates = self.env["sign.oca.template"].with_user(
            self.template_manager,
        )
        operation_uuid = str(uuid.uuid4())
        action = manager_templates.create_from_documents(
            [
                {"name": "Routine Agreement.pdf", "data": base64.b64encode(self.pdf)},
                {"name": "Annex.pdf", "data": base64.b64encode(_pdf(2))},
            ],
            operation_uuid,
        )
        template = self.env["sign.oca.template"].search(
            [("upload_operation_uuid", "=", operation_uuid)],
        )
        self.assertEqual(len(template), 1)
        self.assertEqual(len(template.document_ids), 2)
        self.assertEqual(template.document_ids.mapped("is_annex"), [False, True])
        self.assertEqual(action["tag"], "usl_sign_template_configure")
        duplicate_action = manager_templates.create_from_documents(
            [{"name": "Ignored.pdf", "data": base64.b64encode(self.pdf)}],
            operation_uuid,
        )
        self.assertEqual(duplicate_action["tag"], "usl_sign_template_configure")
        self.assertEqual(
            self.env["sign.oca.template"].search_count(
                [("upload_operation_uuid", "=", operation_uuid)],
            ),
            1,
        )

    def test_template_upload_rejects_whole_envelope_when_one_pdf_is_invalid(self):
        operation_uuid = str(uuid.uuid4())
        before = self.env["sign.oca.template"].search_count([])
        with self.assertRaises(ValidationError):
            self.env["sign.oca.template"].with_user(
                self.template_manager,
            ).create_from_documents(
                [
                    {"name": "Good.pdf", "data": base64.b64encode(self.pdf)},
                    {"name": "Broken.pdf", "data": base64.b64encode(b"not-a-pdf")},
                ],
                operation_uuid,
            )
        self.assertEqual(self.env["sign.oca.template"].search_count([]), before)

    def test_daily_event_head_manifest_is_signed_and_immutable(self):
        request = self._request()
        first_day = fields.Date.today() - timedelta(days=2)
        second_day = fields.Date.today() - timedelta(days=1)
        request._append_event(
            "daily_manifest_test",
            payload={"case": "first"},
            occurred_at=datetime.combine(first_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                first_day,
            )
        self.assertEqual(manifest.state, "signed")
        self.assertEqual(manifest.event_count, 1)
        self.assertEqual(manifest.entry_ids.request_id, request)
        self.assertEqual(
            manifest.entry_ids.chain_head_hash,
            manifest.entry_ids.event_hash,
        )
        raw = field_content(manifest.payload)
        self.assertEqual(manifest.payload_sha256, hashlib.sha256(raw).hexdigest())

        request._append_event(
            "daily_manifest_test",
            payload={"case": "second"},
            occurred_at=datetime.combine(second_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            next_manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                second_day,
            )
        self.assertEqual(next_manifest.previous_manifest_id, manifest)
        self.assertEqual(
            next_manifest.previous_manifest_sha256,
            manifest.signed_envelope_sha256,
        )
        with self.assertRaises(AccessError):
            manifest.write({"event_count": 0})
        with self.assertRaises(AccessError):
            manifest.entry_ids.write({"event_hash": "tampered"})
        with self.assertRaises(AccessError):
            manifest.unlink()

    def test_daily_manifest_cron_catches_up_closed_utc_days_without_gaps(self):
        request = self._request()
        first_day = fields.Date.today() - timedelta(days=3)
        closed_day = fields.Date.today() - timedelta(days=1)
        request._append_event(
            "daily_manifest_catchup",
            occurred_at=datetime.combine(first_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            self.env["usl.sign.daily.manifest"]._cron_build_daily_manifests()
        manifests = self.env["usl.sign.daily.manifest"].search(
            [
                ("company_id", "=", self.company.id),
                ("manifest_date", ">=", first_day),
                ("manifest_date", "<=", closed_day),
            ],
            order="manifest_date",
        )
        self.assertEqual(manifests.mapped("manifest_date"), [
            first_day,
            first_day + timedelta(days=1),
            closed_day,
        ])
        self.assertFalse(manifests[1].event_count)
        self.assertEqual(manifests[1].previous_manifest_id, manifests[0])
        self.assertEqual(manifests[2].previous_manifest_id, manifests[1])
        with self.assertRaises(ValidationError):
            self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                fields.Date.today(),
            )

    def test_opentimestamps_submission_retry_reuses_persisted_nonce(self):
        first_day = fields.Date.today() - timedelta(days=1)
        request = self._request()
        request._append_event(
            "timestamp_retry_test",
            occurred_at=datetime.combine(first_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                first_day,
            )

        class IntermittentClient:
            def __init__(self):
                self.nonces = []

            def submit(self, document, *, nonce):
                del document
                self.nonces.append(nonce)
                if len(self.nonces) == 1:
                    msg = "Synthetic calendar outage"
                    raise OpenTimestampsUnavailableError(msg)
                return {"receipt": b"pending-ots-receipt"}

        client = IntermittentClient()
        try:
            manifest._process_opentimestamps(client)
        except OpenTimestampsUnavailableError:
            pass
        else:
            self.fail("The synthetic calendar outage must escape for cron handling.")
        manifest.invalidate_recordset(["submission_nonce"])
        self.assertTrue(manifest.submission_nonce)
        manifest._process_opentimestamps(client)
        self.assertEqual(client.nonces[0], client.nonces[1])
        self.assertEqual(manifest.anchoring_status, "pending")
        self.assertTrue(manifest.initial_receipt_id)
        with self.assertRaises(AccessError):
            manifest.initial_receipt_id.write({"sha256": "tampered"})

    def test_opentimestamps_poll_persists_rfc3339_block_time_as_odoo_utc(self):
        first_day = fields.Date.today() - timedelta(days=1)
        request = self._request()
        request._append_event(
            "timestamp_block_time_test",
            occurred_at=datetime.combine(first_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                first_day,
            )

        class PollingClient:
            @staticmethod
            def submit(document, *, nonce):
                del document, nonce
                return {"receipt": b"initial-receipt"}

            @staticmethod
            def upgrade(receipt, document):
                del receipt, document
                return {
                    "receipt": b"upgraded-receipt",
                    "bitcoin_attestations": [{"height": 962_325}],
                }

            @staticmethod
            def verify(receipt, document):
                del receipt, document
                return {
                    "status": "pending",
                    "bitcoin_block_height": 962_325,
                    "bitcoin_block_hash": "00" * 32,
                    "bitcoin_block_time": "2026-08-13T19:31:19+00:00",
                    "confirmations": 2,
                }

        manifest._process_opentimestamps(PollingClient())
        manifest._process_opentimestamps(PollingClient())
        self.assertEqual(manifest.anchoring_status, "pending")
        self.assertEqual(manifest.bitcoin_confirmations, 2)
        self.assertEqual(
            manifest.bitcoin_block_time,
            datetime(2026, 8, 13, 19, 31, 19),
        )
        self.assertFalse(manifest.failure_code)

    def test_daily_timestamp_dossier_archive_reuses_checksum_identical_document(self):
        first_day = fields.Date.today() - timedelta(days=1)
        request = self._request()
        request._append_event(
            "timestamp_archive_test",
            occurred_at=datetime.combine(first_day, datetime.min.time())
            + timedelta(hours=12),
        )
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                first_day,
            )
        archived = self.env["usl.document"].sudo().create(
            {
                "name": "Daily timestamp proof dossier",
                "paperless_id": 990002,
                "company_id": self.company.id,
                "confidentiality": "private",
                "availability_state": "available",
                "source": "odoo_generated",
            },
        )
        dossier = _pdf()
        manifest._operational_write(
            {
                "proof_dossier": field_value(dossier),
                "proof_dossier_sha256": hashlib.sha256(dossier).hexdigest(),
            },
        )
        with patch.object(
            type(self.env["usl.document"]),
            "upload_from_odoo",
            return_value={
                "state": "duplicate",
                "document_id": archived.id,
                "message": "Checksum-identical dossier reused.",
            },
        ) as upload:
            self.assertTrue(manifest._archive_timestamp_dossier())
        archived_payload = upload.call_args.args[1]
        self.assertIsInstance(archived_payload, str)
        self.assertEqual(base64.b64decode(archived_payload, validate=True), dossier)
        self.assertEqual(manifest.archive_status, "archived")
        self.assertEqual(manifest.archive_document_id, archived)

    def test_confirmed_archived_daily_proof_leaves_the_cron_queue(self):
        first_day = fields.Date.today() - timedelta(days=1)
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company,
                first_day,
            )
        manifest._operational_write(
            {
                "anchoring_status": "confirmed",
                "confirmed_at": fields.Datetime.now(),
                "verification_report": field_value(b"{}"),
                "verification_report_sha256": hashlib.sha256(b"{}").hexdigest(),
                "archive_status": "archived",
            },
        )
        with patch.object(
            type(manifest),
            "_process_opentimestamps",
            autospec=True,
        ) as process:
            self.env["usl.sign.daily.manifest"]._cron_process_opentimestamps()
        process.assert_not_called()

    def test_service_failure_is_actionable_and_does_not_complete(self):
        request = self._ready(self._request())
        request._freeze_document()
        request._transition("sent", "request_sent")

        class OfflineDSS:
            @staticmethod
            def seal(*args, **kwargs):
                del args, kwargs
                msg = "offline"
                raise DSSServiceError(msg)

        with patch.object(type(request), "_sign_dss_client", return_value=OfflineDSS()):
            self.assertFalse(request._start_final_validation())
        self.assertEqual(request.state, "action_required")
        self.assertFalse(request.completed_at)
        self.assertTrue(request.recovery_action)

    def test_dss_client_distinguishes_rejection_from_outage(self):
        class Response:
            def __init__(self, status_code, message):
                self.status_code = status_code
                self.message = message

            def json(self):
                return {"ok": False, "error": self.message}

        class Session:
            def __init__(self, response):
                self.response = response

            def post(self, *args, **kwargs):
                del args, kwargs
                return self.response

        def client(response):
            result = DSSClient(
                base_url="https://dss.example.test", session=Session(response),
            )
            result.client_cert = "/run/test/client.crt"
            result.client_key = "/run/test/client.key"
            result.ca_bundle = "/run/test/root.crt"
            return result

        with self.assertRaisesRegex(DSSRejectedError, "no signature revision"):
            client(Response(422, "The PDF contains no signature revision.")).health()
        with self.assertRaisesRegex(DSSUnavailableError, "temporarily unavailable"):
            client(Response(503, "The service is temporarily unavailable.")).health()
