import base64
import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import patch

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.pdf import PdfWriter

from ..models.constants import INTERNAL_OPERATION, REQUEST_STATES, TRUST_LEVELS
from ..models.template import EDITOR_ROLE_COLORS
from ..services import (
    DSSClient,
    DSSRejectedError,
    DSSServiceError,
    DSSUnavailableError,
    StepCAClient,
    StepCAError,
    build_strong_binding,
    personal_certificate_subject,
    strong_challenge,
    validate_personal_csr,
    verify_strong_assertion,
)


def _certificate_der():
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "USL Sign test platform seal")],
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.DER)


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


def _pyhanko_valid(count=1):
    return {
        "engine": "pyHanko",
        "engine_version": "0.36.2",
        "status": "valid",
        "signature_count": count,
        "signatures": [
            {
                "field_name": f"Signature-{index + 1}",
                "intact": True,
                "cryptographically_valid": True,
                "certificate_chain": [
                    base64.b64encode(_certificate_der()).decode(),
                ],
            }
            for index in range(count)
        ],
    }


def _webauthn_assertion(*, challenge, rp_id, origin, flags=0x05, sign_count=1):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()
    credential_id = b"usl-sign-test-credential"
    public_key = cbor2.dumps(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: public_numbers.x.to_bytes(32, "big"),
            -3: public_numbers.y.to_bytes(32, "big"),
        },
    )
    client_data = json.dumps(
        {
            "type": "webauthn.get",
            "challenge": bytes_to_base64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode()
    authenticator_data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([flags])
        + sign_count.to_bytes(4, "big")
    )
    signature = private_key.sign(
        authenticator_data + hashlib.sha256(client_data).digest(),
        ec.ECDSA(hashes.SHA256()),
    )
    encoded_id = bytes_to_base64url(credential_id)
    return (
        {
            "id": encoded_id,
            "rawId": encoded_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(authenticator_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        },
        public_key,
    )


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
        cls.coordinator_user = new_test_user(
            cls.env,
            login="usl-sign-coordinator",
            groups="usl_sign.group_sign_user",
            company_id=cls.company.id,
        )
        cls.unrelated_user = new_test_user(
            cls.env,
            login="usl-sign-unrelated",
            groups="usl_sign.group_sign_user",
            company_id=cls.company.id,
        )
        cls.evidence_reviewer = new_test_user(
            cls.env,
            login="usl-sign-evidence-reviewer",
            groups="usl_sign.group_sign_evidence_reviewer",
            company_id=cls.company.id,
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="usl-sign-reviewer",
            groups="usl_sign.group_sign_identity_reviewer",
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
            "data": base64.b64encode(self.pdf),
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
                    "data": base64.b64encode(stream.getvalue()),
                },
            )

    def test_template_wizard_explains_trust_and_creates_a_reviewable_draft(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Guided Standard template",
                "filename": "guided.pdf",
                "data": base64.b64encode(self.pdf),
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
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": self.env.ref("usl_sign.field_date").id,
                "role_id": self.role_customer.id,
                "required": True,
                "page": 1,
                "position_x": 10,
                "position_y": 22,
                "width": 18,
                "height": 5,
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
        self.assertIn("reinforced evidence", wizard.journey_availability)
        action = wizard.generate()
        self.assertEqual(action["type"], "ir.actions.act_window")
        request = self.env["sign.oca.request"].browse(action["res_id"])
        self.assertEqual(request.state, "draft")
        self.assertEqual(request.requested_trust, "standard")
        self.assertEqual(request.template_version, 1)
        self.assertEqual(request.responsible_message, "<p>Please review and sign.</p>")
        self.assertEqual(len(request.document_ids), 1)
        self.assertEqual(request.document_ids.source_sha256, template.document_ids.source_sha256)
        self.assertEqual(
            {item["kind"] for item in request.signatory_data.values()},
            {"date", "signature"},
        )
        self.assertFalse(request.sent_at)

    def test_template_editor_exposes_typed_fields_and_stable_role_colors(self):
        self.assertEqual(
            EDITOR_ROLE_COLORS,
            (
                "#E86A8D", "#FCD12A", "#56AE64", "#3EA8F9", "#9E8DF9",
                "#D7794D", "#00B591", "#E53935", "#CF75CB", "#000000",
            ),
        )
        template = self.env["sign.oca.template"].create(
            {
                "name": "Typed editor template",
                "filename": "typed.pdf",
                "data": base64.b64encode(self.pdf),
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
        self.assertEqual(fields_by_name["Initials"]["technical_type"], "signature")
        self.assertEqual(fields_by_name["Name"]["default_value"], "name")
        self.assertGreater(fields_by_name["Signature"]["default_width"], 0)
        self.assertEqual(
            {role["id"]: role["color"] for role in first["roles"]},
            {role["id"]: role["color"] for role in second["roles"]},
        )
        self.assertTrue(all(role["color"].startswith("#") for role in first["roles"]))
        self.assertEqual(
            [role["color"] for role in first["roles"][:2]],
            ["#E86A8D", "#FCD12A"],
        )
        self.assertEqual(first["revision"], 1)
        self.assertFalse(first["readonly"])

    def test_template_editor_commands_are_revision_checked_and_idempotent(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Command editor template",
                "filename": "commands.pdf",
                "data": base64.b64encode(self.pdf),
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

    def test_template_editor_roles_can_be_added_and_removed_safely(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Role management template",
                "filename": "roles.pdf",
                "data": base64.b64encode(self.pdf),
                "company_id": self.company.id,
            },
        )
        template.get_info()
        operation = str(uuid.uuid4())
        manager_template = template.with_user(self.template_manager)
        added = manager_template.editor_apply_command(
            operation,
            1,
            {"action": "role_add", "values": {"name": "Legal reviewer"}},
        )
        self.assertEqual(added["status"], "ok")
        self.assertIn("Legal reviewer", [role["name"] for role in added["roles"]])
        self.assertEqual(
            manager_template.editor_apply_command(
                operation,
                1,
                {"action": "role_add", "values": {"name": "Legal reviewer"}},
            ),
            added,
        )
        removed = manager_template.editor_apply_command(
            str(uuid.uuid4()),
            2,
            {"action": "role_remove", "role_id": added["role_id"]},
        )
        self.assertNotIn("Legal reviewer", [role["name"] for role in removed["roles"]])

        created = manager_template.editor_apply_command(
            str(uuid.uuid4()),
            3,
            {
                "action": "create",
                "values": {
                    "field_id": self.text_field.id,
                    "role_id": self.role_customer.id,
                    "page": 1,
                    "position_x": 10,
                    "position_y": 10,
                    "width": 20,
                    "height": 5,
                },
            },
        )
        self.assertEqual(created["status"], "ok")
        with self.assertRaisesRegex(ValidationError, "Reassign or delete"):
            manager_template.editor_apply_command(
                str(uuid.uuid4()),
                4,
                {"action": "role_remove", "role_id": self.role_customer.id},
            )

    def test_template_editor_rejects_missing_role_and_out_of_page_geometry(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Validated editor template",
                "filename": "validated.pdf",
                "data": base64.b64encode(self.pdf),
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

    def test_public_signer_info_infers_typed_metadata_from_frozen_layout(self):
        request = self._ready(self._request())
        request._freeze_document()
        signer_info = request.signer_ids.get_info()
        item = signer_info["items"]["1"]
        self.assertEqual(item["kind"], "signer_name")
        self.assertEqual(item["field_type"], "text")
        self.assertEqual(item["technical_type"], "text")
        self.assertEqual(item["default_value"], "name")

    def test_template_editor_role_colors_follow_template_company_rules(self):
        other_company = self.env["res.company"].create({"name": "Other Sign Company"})
        template = self.env["sign.oca.template"].with_company(other_company).create(
            {
                "name": "Other-company template",
                "filename": "other.pdf",
                "data": base64.b64encode(self.pdf),
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

    def test_operational_and_proof_records_are_isolated_by_company(self):
        other_company = self.env["res.company"].create({"name": "Isolated Sign Company"})
        isolated_admin = new_test_user(
            self.env,
            login="usl-sign-isolated-admin",
            groups="usl_sign.group_sign_admin",
            company_id=self.company.id,
        )
        other_policy = self.env["usl.sign.policy"].create(
            {
                "name": "Other-company policy",
                "company_id": other_company.id,
                "recommendation": "standard",
                "reason": "Synthetic isolation policy.",
                "consequence": "Synthetic isolation consequence.",
            },
        )
        other_template = self.env["sign.oca.template"].create(
            {
                "name": "Other-company isolated template",
                "filename": "isolated.pdf",
                "data": base64.b64encode(self.pdf),
                "company_id": other_company.id,
            },
        )
        other_item = self.env["sign.oca.template.item"].create(
            {
                "template_id": other_template.id,
                "field_id": self.text_field.id,
                "role_id": self.role_customer.id,
                "required": True,
                "page": 1,
                "position_x": 10,
                "position_y": 20,
                "width": 25,
                "height": 5,
            },
        )
        other_template.get_info()
        other_role_mapping = other_template.editor_role_ids[:1]
        other_request = self._request(
            company_id=other_company.id,
            user_id=isolated_admin.id,
            policy_id=other_policy.id,
        )
        other_evidence = other_request._create_evidence(
            "snapshot",
            "isolated-snapshot.json",
            b"{}",
            mimetype="application/json",
        )
        other_validation = self.env["usl.sign.validation"].with_context(
            usl_sign_validation_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": other_request.id,
                "engine_version": "6.4",
                "expected_trust": "standard",
                "achieved_trust": "standard",
                "status": "valid",
                "signature_count": 1,
                "report_evidence_id": other_evidence.id,
            },
        )
        other_enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": isolated_admin.partner_id.id,
                "company_id": other_company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Synthetic company-isolation relationship",
            },
        )
        other_passkey = self.env["usl.sign.passkey"].with_context(
            usl_sign_passkey_registration=INTERNAL_OPERATION,
        ).create(
            {
                "enrollment_id": other_enrollment.id,
                "name": "Other-company passkey",
                "credential_id": "other-company-credential",
                "public_key": base64.b64encode(b"synthetic-public-key"),
            },
        )
        other_ceremony = self.env["usl.sign.ceremony"].sudo().create(
            {
                "request_id": other_request.id,
                "signer_id": other_request.signer_ids.id,
                "enrollment_id": other_enrollment.id,
                "passkey_id": other_passkey.id,
                "challenge": base64.b64encode(b"synthetic-challenge"),
                "challenge_sha256": "1" * 64,
                "document_sha256": "2" * 64,
                "consent_sha256": "3" * 64,
                "csr_sha256": "4" * 64,
                "public_key_sha256": "5" * 64,
                "csr_pem": "synthetic-csr",
                "binding_payload": {"format": "synthetic"},
                "expires_at": fields.Datetime.now() + timedelta(minutes=5),
            },
        )
        other_provider = self.env["usl.sign.external.provider"].create(
            {
                "name": "Other-company provider",
                "company_id": other_company.id,
                "territory": "EU",
                "mobile_url": "https://qualified.example.test/sign",
                "instructions": "Synthetic provider instructions.",
                "reviewed_on": fields.Date.today(),
            },
        )
        other_journey = self.env["usl.sign.external.journey"].with_context(
            usl_sign_external_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": other_request.id,
                "provider_id": other_provider.id,
                "frozen_sha256": "6" * 64,
                "signer_information": [
                    {
                        "name": self.partner_one.name,
                        "email": self.partner_one.email,
                        "role": self.role_customer.name,
                    },
                ],
            },
        )
        other_approval = self.env["usl.sign.approval"].create(
            {
                "name": "Other-company approval",
                "company_id": other_company.id,
                "record_ref": f"res.partner,{self.partner_one.id}",
                "requested_by_id": isolated_admin.id,
                "approver_ids": [(6, 0, [isolated_admin.id])],
            },
        )
        other_manifest = self.env["usl.sign.daily.manifest"].with_context(
            usl_sign_daily_manifest_build=INTERNAL_OPERATION,
        ).create(
            {
                "company_id": other_company.id,
                "manifest_date": fields.Date.today(),
                "state": "signed",
                "payload": base64.b64encode(b"{}"),
                "payload_sha256": hashlib.sha256(b"{}").hexdigest(),
                "event_count": 0,
                "request_count": 0,
            },
        )

        isolated_records = [
            other_policy,
            other_template,
            other_item,
            other_role_mapping,
            other_request,
            other_request.signer_ids,
            other_request.document_ids,
            other_request.event_ids,
            other_evidence,
            other_validation,
            other_enrollment,
            other_passkey,
            other_ceremony,
            other_provider,
            other_journey,
            other_approval,
            other_approval.event_ids,
            other_manifest,
        ]
        for record in isolated_records:
            self.assertTrue(record, f"Missing isolation fixture for {record._name}")
            visible = (
                self.env[record._name]
                .with_user(isolated_admin)
                .with_context(allowed_company_ids=[self.company.id])
                .search_count([("id", "in", record.ids)])
            )
            self.assertFalse(visible, f"{record._name} crossed the company boundary")

    def test_approval_events_are_visible_only_to_same_company_participants(self):
        approval = self.env["usl.sign.approval"].create(
            {
                "name": "Participant-scoped approval",
                "company_id": self.company.id,
                "record_ref": f"res.partner,{self.partner_one.id}",
                "requested_by_id": self.sign_user.id,
                "approver_ids": [(6, 0, [self.coordinator_user.id])],
            },
        )
        event = approval.event_ids
        self.assertTrue(
            self.env[event._name]
            .with_user(self.coordinator_user)
            .search_count([("id", "=", event.id)]),
        )
        self.assertFalse(
            self.env[event._name]
            .with_user(self.unrelated_user)
            .search_count([("id", "=", event.id)]),
        )

    def test_business_record_picker_excludes_technical_registry_models(self):
        available = dict(self.env["sign.oca.request"]._sign_business_record_models())
        self.assertIn("res.partner", available)
        self.assertNotIn("ir.model", available)
        self.assertFalse(any(model.startswith("usl.sign") for model in available))
        self.assertFalse(any(model.startswith("sign.oca") for model in available))

    def test_named_coordinator_can_prepare_but_not_control_or_send(self):
        request = self._request(
            coordinator_ids=[(6, 0, [self.coordinator_user.id])],
        )
        coordinated = request.with_user(self.coordinator_user)
        coordinated.write({"responsible_message": "Coordinator prepared the request."})
        self.assertEqual(request.responsible_message, "Coordinator prepared the request.")
        coordinated.action_mark_ready()
        self.assertEqual(request.state, "ready")
        with self.assertRaises(AccessError):
            coordinated.write({"coordinator_ids": [(6, 0, [])]})
        with self.assertRaises(AccessError):
            coordinated.write({"requested_trust": "strong_personal"})
        with self.assertRaises(AccessError):
            coordinated.action_send()
        with self.assertRaises(AccessError):
            coordinated.cancel()
        self.assertFalse(
            self.env["sign.oca.request"]
            .with_user(self.unrelated_user)
            .search_count([("id", "=", request.id)]),
        )

    def test_signer_visibility_does_not_grant_draft_preparation_rights(self):
        signer_user = new_test_user(
            self.env,
            login="usl-sign-draft-participant",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        request = self._request(
            partners=[signer_user.partner_id],
            coordinator_ids=[(6, 0, [self.coordinator_user.id])],
        )
        signer = request.signer_ids
        document = request.document_ids
        participant_request = request.with_user(signer_user)
        participant_signer_model = self.env["sign.oca.request.signer"].with_user(
            signer_user,
        )
        participant_document_model = self.env["usl.sign.request.document"].with_user(
            signer_user,
        )

        self.assertTrue(participant_request.exists())
        with self.assertRaises(AccessError):
            participant_request.write({"responsible_message": "Changed by signer"})
        with self.assertRaises(AccessError):
            participant_signer_model.create(
                {
                    "request_id": request.id,
                    "partner_id": self.partner_two.id,
                    "role_id": self.role_employee.id,
                },
            )
        with self.assertRaises(AccessError):
            signer.with_user(signer_user).write({"sequence": 20})
        with self.assertRaises(AccessError):
            signer.with_user(signer_user).unlink()
        with self.assertRaises(AccessError):
            participant_document_model.create(
                {
                    "request_id": request.id,
                    "name": "Injected annex",
                    "filename": "injected.pdf",
                    "data": base64.b64encode(self.pdf),
                },
            )
        with self.assertRaises(AccessError):
            document.with_user(signer_user).write({"name": "Changed by signer"})
        with self.assertRaises(AccessError):
            document.with_user(signer_user).unlink()

        coordinator_document = participant_document_model.with_user(
            self.coordinator_user,
        ).create(
            {
                "request_id": request.id,
                "name": "Coordinator annex",
                "filename": "coordinator-annex.pdf",
                "data": base64.b64encode(self.pdf),
                "is_annex": True,
            },
        )
        coordinator_signer = participant_signer_model.with_user(
            self.coordinator_user,
        ).create(
            {
                "request_id": request.id,
                "partner_id": self.partner_two.id,
                "role_id": self.role_employee.id,
                "sequence": 20,
            },
        )
        coordinator_signer.with_user(self.coordinator_user).write({"sequence": 30})
        coordinator_signer.with_user(self.coordinator_user).unlink()
        coordinator_document.with_user(self.coordinator_user).unlink()

    def test_signer_identity_does_not_leak_across_commercial_partner_contacts(self):
        organization = self.env["res.partner"].create(
            {"name": "Shared Signer Organization", "is_company": True},
        )
        assigned_user = new_test_user(
            self.env,
            login="usl-sign-assigned-contact",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        sibling_user = new_test_user(
            self.env,
            login="usl-sign-sibling-contact",
            groups="usl_sign.group_sign_user",
            company_id=self.company.id,
        )
        assigned_user.partner_id.parent_id = organization
        sibling_user.partner_id.parent_id = organization
        self.assertEqual(
            assigned_user.partner_id.commercial_partner_id,
            sibling_user.partner_id.commercial_partner_id,
        )

        request = self._request(partners=[assigned_user.partner_id])
        signer = request.signer_ids
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"state": "sent"},
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "notified"},
        )
        request._append_event("identity_isolation_test", signer=signer)
        evidence = (
            self.env["usl.sign.evidence"]
            .with_context(usl_sign_evidence_create=INTERNAL_OPERATION)
            .create(
                {
                    "request_id": request.id,
                    "signer_id": signer.id,
                    "kind": "consent",
                    "name": "identity-isolation.json",
                    "data": base64.b64encode(b'{"consent":true}'),
                    "mimetype": "application/json",
                },
            )
        )
        validation = (
            self.env["usl.sign.validation"]
            .with_context(usl_sign_validation_create=INTERNAL_OPERATION)
            .create(
                {
                    "request_id": request.id,
                    "engine_version": "6.4",
                    "expected_trust": "standard",
                    "achieved_trust": "standard",
                    "status": "valid",
                },
            )
        )
        enrollment = self.env["usl.sign.enrollment"].with_user(self.reviewer).create(
            {
                "partner_id": assigned_user.partner_id.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Identity isolation fixture",
                "policy_version": "2026.2",
            },
        )

        assigned_request = self.env["sign.oca.request"].with_user(assigned_user)
        assigned_signer = self.env["sign.oca.request.signer"].with_user(assigned_user)
        self.assertTrue(assigned_request.search_count([("id", "=", request.id)]))
        self.assertTrue(assigned_signer.search_count([("id", "=", signer.id)]))
        self.assertTrue(
            self.env["usl.sign.request.document"]
            .with_user(assigned_user)
            .search_count([("request_id", "=", request.id)]),
        )
        self.assertTrue(
            self.env["usl.sign.event"]
            .with_user(assigned_user)
            .search_count([("request_id", "=", request.id)]),
        )
        self.assertTrue(
            self.env["usl.sign.evidence"]
            .with_user(assigned_user)
            .search_count([("id", "=", evidence.id)]),
        )
        self.assertTrue(
            self.env["usl.sign.validation"]
            .with_user(assigned_user)
            .search_count([("id", "=", validation.id)]),
        )
        self.assertTrue(
            self.env["usl.sign.enrollment"]
            .with_user(assigned_user)
            .search_count([("id", "=", enrollment.id)]),
        )
        assigned_landing = (
            self.env["usl.sign.workspace"].with_user(assigned_user).get_landing()
        )
        self.assertIn(
            signer.id,
            [item["id"] for item in assigned_landing["sections"]["sign_now"]["items"]],
        )
        self.assertTrue(signer.with_user(assigned_user).is_allow_signature)
        self.assertEqual(request.with_user(assigned_user).signer_id, signer)

        sibling_request = self.env["sign.oca.request"].with_user(sibling_user)
        sibling_signer = self.env["sign.oca.request.signer"].with_user(sibling_user)
        self.assertFalse(sibling_request.search_count([("id", "=", request.id)]))
        self.assertFalse(sibling_signer.search_count([("id", "=", signer.id)]))
        self.assertFalse(
            self.env["usl.sign.request.document"]
            .with_user(sibling_user)
            .search_count([("request_id", "=", request.id)]),
        )
        self.assertFalse(
            self.env["usl.sign.event"]
            .with_user(sibling_user)
            .search_count([("request_id", "=", request.id)]),
        )
        self.assertFalse(
            self.env["usl.sign.evidence"]
            .with_user(sibling_user)
            .search_count([("id", "=", evidence.id)]),
        )
        self.assertFalse(
            self.env["usl.sign.validation"]
            .with_user(sibling_user)
            .search_count([("id", "=", validation.id)]),
        )
        self.assertFalse(
            self.env["usl.sign.enrollment"]
            .with_user(sibling_user)
            .search_count([("id", "=", enrollment.id)]),
        )
        sibling_landing = (
            self.env["usl.sign.workspace"].with_user(sibling_user).get_landing()
        )
        self.assertNotIn(
            signer.id,
            [item["id"] for item in sibling_landing["sections"]["sign_now"]["items"]],
        )
        with self.assertRaises(AccessError):
            signer.with_user(sibling_user).sign()

        owner_request = self._request(
            partners=[assigned_user.partner_id],
            user_id=sibling_user.id,
        )
        self.assertFalse(owner_request.with_user(sibling_user).signer_id)

    def test_workspace_routes_work_without_exposing_signing_secrets(self):
        request = self._request(
            coordinator_ids=[(6, 0, [self.coordinator_user.id])],
        )
        approval = self.env["usl.sign.approval"].create(
            {
                "name": "Routine decision",
                "record_ref": f"res.partner,{self.partner_one.id}",
                "approver_ids": [(6, 0, [self.coordinator_user.id])],
            },
        )
        landing = (
            self.env["usl.sign.workspace"]
            .with_user(self.coordinator_user)
            .get_landing()
        )
        self.assertTrue(landing["can_start"])
        self.assertIn(
            request.id,
            [item["id"] for item in landing["sections"]["prepare"]["items"]],
        )
        self.assertIn(
            approval.id,
            [item["id"] for item in landing["sections"]["decide"]["items"]],
        )
        serialized = json.dumps(landing)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("sha256", serialized)

    def test_completed_library_enforces_validation_proof_and_archive_gate(self):
        request = self._request(
            coordinator_ids=[(6, 0, [self.coordinator_user.id])],
        )
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {"state": "completed"},
        )
        workspace = self.env["usl.sign.workspace"].with_user(self.coordinator_user)
        self.assertFalse(workspace.get_library("completed")["items"])
        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
            {
                "achieved_trust": "standard",
                "validation_status": "valid",
                "evidence_status": "complete",
                "archive_status": "archived",
                "final_data": base64.b64encode(self.pdf),
                "final_filename": "routine-final.pdf",
                "completion_certificate": base64.b64encode(self.pdf),
                "completion_filename": "routine-certificate.pdf",
                "dossier_data": base64.b64encode(self.pdf),
                "dossier_filename": "routine-dossier.pdf",
                "completed_at": fields.Datetime.now(),
            },
        )
        library = workspace.get_library("completed")
        self.assertEqual([item["id"] for item in library["items"]], [request.id])
        self.assertTrue(library["items"][0]["final_url"])
        self.assertTrue(library["items"][0]["certificate_url"])
        self.assertTrue(library["items"][0]["dossier_url"])

    def test_start_flow_routes_decisions_and_document_signatures(self):
        decision = self.env["usl.sign.start"].create(
            {
                "request_type": "decision",
                "name": "Approve routine supplier choice",
                "record_ref": f"res.partner,{self.partner_one.id}",
                "approver_ids": [(6, 0, [self.sign_user.id])],
            },
        )
        decision_action = decision.action_continue()
        approval = self.env["usl.sign.approval"].browse(decision_action["res_id"])
        self.assertEqual(approval.name, decision.name)
        self.assertEqual(approval.approver_ids, self.sign_user)

        upload = self.env["usl.sign.start"].create(
            {
                "request_type": "signature",
                "signature_source": "upload",
                "name": "One-off routine agreement",
                "document_data": base64.b64encode(self.pdf),
                "document_filename": "routine.pdf",
            },
        )
        signature_action = upload.action_continue()
        request = self.env["sign.oca.request"].browse(signature_action["res_id"])
        self.assertEqual(request.state, "draft")
        self.assertEqual(request.name, upload.name)

        template = self.env["sign.oca.template"].create(
            {
                "name": "Routine agreement template",
                "filename": "routine-template.pdf",
                "data": base64.b64encode(self.pdf),
                "company_id": self.company.id,
                "policy_id": self.policy.id,
            },
        )
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": self.env.ref("sign_oca.sign_field_signature").id,
                "role_id": self.role_customer.id,
                "required": True,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 25,
                "height": 5,
            },
        )
        template.action_mark_ready()
        template_start = self.env["usl.sign.start"].create(
            {
                "request_type": "signature",
                "signature_source": "template",
                "name": "Camille routine agreement",
                "template_id": template.id,
                "record_ref": f"res.partner,{self.partner_one.id}",
            },
        )
        template_action = template_start.action_continue()
        wizard = self.env["sign.oca.template.generate"].with_context(
            **template_action["context"],
        ).create(
            {
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
            },
        )
        self.assertEqual(wizard.template_id, template)
        self.assertEqual(wizard.request_name, template_start.name)
        self.assertEqual(wizard.record_ref, self.partner_one)

    def test_sign_navigation_uses_only_the_four_journey_workspaces(self):
        root = self.env.ref("sign_oca.sign_oca_root_menu")
        self.assertEqual(root.action, self.env.ref("usl_sign.sign_landing_action"))
        top_level = self.env["ir.ui.menu"].search(
            [("parent_id", "=", root.id), ("active", "=", True)],
            order="sequence, id",
        )
        self.assertEqual(
            top_level.mapped("name"),
            ["Library", "Open Requests", "My Signatures", "Configuration"],
        )
        my_signatures = self.env.ref("usl_sign.my_signatures_action")
        self.assertEqual(my_signatures.res_model, "sign.oca.request.signer")

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

    def test_reminder_decline_expiration_and_cancellation_are_controlled(self):
        reminder_request = self._request()
        reminder_request.write({"max_reminders": 1, "reminder_days": 0})
        reminder_request.action_mark_ready()
        reminder_request.action_send()
        reminder_request.action_send_reminder()
        self.assertEqual(reminder_request.reminder_count, 1)
        self.assertEqual(reminder_request.signer_ids.reminder_count, 1)
        self.assertTrue(
            reminder_request.event_ids.filtered(
                lambda event: event.event_type == "reminder_sent",
            ),
        )
        with self.assertRaisesRegex(ValidationError, "reminder limit"):
            reminder_request.action_send_reminder()

        declined_request = self._request()
        declined_request.action_mark_ready()
        declined_request.action_send()
        declined_signer = declined_request.signer_ids
        with self.assertRaises(AccessError):
            declined_signer.action_decline("Forged by requester")
        link_token = declined_signer._issue_access_token()
        session_token = declined_signer._exchange_access_token(link_token)
        declined_signer.action_decline(
            "The agreement is not acceptable.",
            access_token=session_token,
        )
        self.assertEqual(declined_request.state, "declined")
        self.assertEqual(declined_signer.state, "declined")
        self.assertTrue(declined_signer.access_revoked)
        self.assertEqual(
            len(
                declined_request.evidence_ids.filtered(
                    lambda row: row.kind == "decline",
                ),
            ),
            1,
        )

        expired_request = self._request()
        expired_request.action_mark_ready()
        expired_request.action_send()
        expired_request._expire_request()
        self.assertEqual(expired_request.state, "expired")
        self.assertEqual(expired_request.signer_ids.state, "expired")
        self.assertTrue(expired_request.signer_ids.access_revoked)
        self.assertEqual(
            len(
                expired_request.evidence_ids.filtered(
                    lambda row: row.kind == "expiration",
                ),
            ),
            1,
        )

        cancelled_request = self._request()
        cancelled_request.action_mark_ready()
        cancelled_request.action_send()
        cancelled_request.cancel()
        self.assertEqual(cancelled_request.state, "cancelled")
        self.assertEqual(cancelled_request.signer_ids.state, "cancelled")
        self.assertTrue(cancelled_request.signer_ids.access_revoked)
        self.assertEqual(
            len(
                cancelled_request.evidence_ids.filtered(
                    lambda row: row.kind == "cancellation",
                ),
            ),
            1,
        )

    def test_policy_recommendation_and_authorized_override(self):
        request = self._request(
            risk_level="material",
            signer_type="recurring",
            coordinator_ids=[(6, 0, [self.override_user.id])],
        )
        self.assertEqual(request.recommended_trust, "strong_personal")
        request.requested_trust = "standard"
        request.override_reason = "Signer is not yet enrolled; routine fallback approved."
        with self.assertRaises(AccessError):
            request.with_user(self.sign_user).action_mark_ready()
        request.with_user(self.override_user).action_mark_ready()
        self.assertEqual(request.state, "ready")

    def test_internal_decision_guides_to_attributable_odoo_approval(self):
        request = self._request(
            document_category="internal_decision", requires_signed_pdf=False,
        )
        self.assertTrue(request.approval_recommended)
        with self.assertRaisesRegex(ValidationError, "Request a business decision"):
            request.action_mark_ready()

    def test_logged_in_signer_opens_authenticated_ceremony_in_current_tab(self):
        request = self._ready(
            self._request(partners=[self.sign_user.partner_id]),
        )
        request.action_send()
        signer = request.signer_ids.with_user(self.sign_user)
        action = signer.sign()
        self.assertEqual(action["target"], "self")
        self.assertEqual(action["url"], f"/sign/user/{signer.id}")

    def test_freeze_is_deterministic_and_sent_content_is_immutable(self):
        request = self._ready(self._request())
        request._freeze_document()
        self.assertEqual(
            request.original_sha256,
            hashlib.sha256(base64.b64decode(request.original_data)).hexdigest(),
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
            request.write({"data": base64.b64encode(self.pdf + b"changed")})
        with self.assertRaises(ValidationError):
            request.signer_ids.write({"role_id": self.role_employee.id})

    def test_multiple_documents_and_annexes_freeze_in_order_with_individual_hashes(self):
        request = self._ready(self._request())
        annex = self.env["usl.sign.request.document"].create(
            {
                "request_id": request.id,
                "sequence": 20,
                "is_annex": True,
                "name": "Routine agreement annex",
                "filename": "routine-agreement-annex.pdf",
                "data": base64.b64encode(self.pdf),
            },
        )
        request._freeze_document()

        self.assertEqual(len(request.page_map), 2)
        self.assertEqual(
            [(row["page_start"], row["page_end"]) for row in request.page_map],
            [(1, 1), (2, 2)],
        )
        self.assertFalse(request.page_map[0]["annex"])
        self.assertTrue(request.page_map[1]["annex"])
        self.assertEqual(request.page_map[1]["sha256"], annex.source_sha256)
        source_evidence = request.evidence_ids.filtered(
            lambda evidence: evidence.kind == "source",
        )
        self.assertEqual(len(source_evidence), 2)
        self.assertEqual(
            request.original_sha256,
            hashlib.sha256(base64.b64decode(request.original_data)).hexdigest(),
        )

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
                    "data": base64.b64encode(b"forged"),
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
                document_sha256=first.get_info(access_token="first-session")[
                    "document_sha256"
                ],
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

    def test_unordered_standard_signers_are_bound_to_the_reviewed_pdf_revision(self):
        request = self._ready(
            self._request(
                partners=[self.partner_one, self.partner_two],
                roles=[self.role_customer, self.role_employee],
                signing_order=False,
            ),
        )
        with patch.object(type(request.signer_ids), "_send_signer_invitation", return_value=True):
            request.action_send()
        first, second = request.signer_ids.sorted(lambda row: (row.sequence, row.id))
        tokens = {first.id: "first-session", second.id: "second-session"}
        for signer in (first, second):
            signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                {
                    "state": "viewed",
                    "session_token_sha256": hashlib.sha256(
                        tokens[signer.id].encode(),
                    ).hexdigest(),
                    "session_expires_at": fields.Datetime.now()
                    + timedelta(minutes=5),
                },
            )
        first_revision = first.get_info(access_token=tokens[first.id])["document_sha256"]
        second_stale_revision = second.get_info(access_token=tokens[second.id])[
            "document_sha256"
        ]
        self.assertEqual(first_revision, second_stale_revision)

        with patch.object(type(second), "_send_signer_invitation", return_value=True):
            first.action_sign(
                self._items(request, first.role_id, "Camille Signer"),
                access_token=tokens[first.id],
                document_sha256=first_revision,
                consent=True,
            )
        signed_by_first = base64.b64decode(request.data)
        resulting_revision = hashlib.sha256(signed_by_first).hexdigest()
        self.assertNotEqual(resulting_revision, first_revision)

        with self.assertRaisesRegex(ValidationError, "changed after you reviewed"):
            second.action_sign(
                self._items(request, second.role_id, "Morgan Signer"),
                access_token=tokens[second.id],
                document_sha256=second_stale_revision,
                consent=True,
            )
        self.assertEqual(base64.b64decode(request.data), signed_by_first)
        self.assertFalse(second.signed_on)
        self.assertEqual(
            second.get_info(access_token=tokens[second.id])["document_sha256"],
            resulting_revision,
        )
        consent = request.evidence_ids.filtered(
            lambda row: row.kind == "consent" and row.signer_id == first,
        )
        consent_payload = json.loads(base64.b64decode(consent.data))
        self.assertEqual(
            consent_payload["reviewed_document_sha256"],
            first_revision,
        )
        self.assertEqual(
            consent_payload["signed_document_sha256"],
            resulting_revision,
        )
        self.assertRegex(consent_payload["field_values_sha256"], r"^[0-9a-f]{64}$")

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

    def test_missing_embedded_certificate_chain_never_completes(self):
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
            "engine": "pyHanko",
            "engine_version": "0.36.2",
            "status": "valid",
            "signature_count": 1,
            "signatures": [
                {
                    "field_name": "Signature-1",
                    "intact": True,
                    "cryptographically_valid": True,
                    "certificate_chain": [],
                },
            ],
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
        self.assertEqual(
            request.event_ids[-1:].event_type,
            "certificate_evidence_incomplete",
        )

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
                "data": base64.b64encode(self.pdf),
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
                "data": base64.b64encode(self.pdf),
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
                "imported_pdf": base64.b64encode(self.pdf),
                "imported_filename": "signed.pdf",
                "proof_package": base64.b64encode(b"provider proof"),
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
        submitted = request.evidence_ids.filtered(
            lambda evidence: evidence.name == "signed.pdf",
        )
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted.kind, "external")
        self.assertEqual(submitted.sha256, hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(submitted.metadata["trust"], "unverified_input")

        action = request.action_create_replacement()
        replacement = self.env["sign.oca.request"].browse(action["res_id"])
        self.assertEqual(replacement.state, "draft")
        self.assertEqual(replacement.requested_trust, "qualified_external")
        self.assertEqual(replacement.external_provider_id, provider)
        self.assertEqual(
            replacement.signer_ids.mapped("partner_id"),
            request.signer_ids.mapped("partner_id"),
        )
        self.assertEqual(len(replacement.document_ids), len(request.document_ids))
        self.assertEqual(
            replacement.document_ids.mapped("source_sha256"),
            request.document_ids.mapped("source_sha256"),
        )
        self.assertEqual(replacement.signatory_data, request.frozen_layout)
        self.assertFalse(replacement.original_data)
        self.assertFalse(replacement.final_data)
        self.assertFalse(replacement.validation_ids)
        self.assertFalse(replacement.evidence_ids)
        self.assertFalse(replacement.external_journey_id)
        with self.assertRaises(AccessError):
            request.with_user(self.coordinator_user).action_create_replacement()

    def test_external_import_rejects_insufficient_trust_without_downgrade(self):
        provider = self.env["usl.sign.external.provider"].create(
            {
                "name": "Insufficient trust provider",
                "territory": "EU",
                "mobile_url": "https://provider.example.test/mobile",
                "instructions": "Return a qualified signature and its proof package.",
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
                "imported_pdf": base64.b64encode(self.pdf),
                "imported_filename": "signed.pdf",
                "proof_package": base64.b64encode(b"provider proof"),
                "proof_filename": "proof.zip",
                "state": "imported",
                "imported_sha256": hashlib.sha256(self.pdf).hexdigest(),
                "imported_at": fields.Datetime.now(),
            },
        )
        request._transition("signed_to_import", "external_document_imported")

        class InsufficientDSS(FakeDSS):
            expected_signers = None

            @classmethod
            def validate(cls, document, expected_level, expected_signers=None):
                cls.expected_signers = expected_signers
                result = super().validate(document, expected_level, expected_signers)
                result.update(
                    {
                        "achievedTrust": "standard",
                        "summary": "The imported signature is not qualified.",
                    },
                )
                return result

        with patch.object(
            type(request), "_sign_dss_client", return_value=InsufficientDSS(),
        ):
            self.assertFalse(request.action_validate_external())

        self.assertEqual(InsufficientDSS.expected_signers, [self.partner_one.name])
        self.assertEqual(request.state, "validation_failed")
        self.assertEqual(request.requested_trust, "qualified_external")
        self.assertEqual(request.validation_ids[-1].achieved_trust, "standard")
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
            len(request.event_ids.filtered(lambda event: event.event_type == "external_document_exported")),
            1,
        )
        import_action = journey.action_open_import()
        self.assertEqual(import_action["res_model"], "usl.sign.external.import.wizard")
        wizard = self.env["usl.sign.external.import.wizard"].create(
            {
                "journey_id": journey.id,
                "signed_pdf": base64.b64encode(self.pdf),
                "signed_filename": "qualified-signed.pdf",
                "proof_package": base64.b64encode(b"external proof package"),
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
                "imported_pdf": base64.b64encode(self.pdf),
                "imported_filename": "signed.pdf",
                "proof_package": base64.b64encode(b"provider proof"),
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
        archived = self.env["usl.document"].sudo().create(
            {
                "name": "Clean Sign evidence dossier",
                "paperless_id": 990001,
                "company_id": self.company.id,
                "confidentiality": "private",
                "availability_state": "available",
                "source": "odoo_generated",
            },
        )
        upload = patch.object(
            type(self.env["usl.document"]),
            "upload_from_odoo",
            side_effect=[
                RuntimeError("synthetic Paperless outage"),
                {
                    "state": "duplicate",
                    "document_id": archived.id,
                    "message": "Checksum-identical dossier reused.",
                },
            ],
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
            certificate_evidence = request.evidence_ids.filtered(
                lambda evidence: evidence.kind == "certificate",
            )
            self.assertEqual(len(certificate_evidence), 1)
            certificate_payload = json.loads(
                base64.b64decode(certificate_evidence.data),
            )
            self.assertEqual(
                certificate_payload["format"],
                "usl-sign-pdf-certificate-chains-v1",
            )
            self.assertEqual(len(certificate_payload["signatures"]), 1)
            self.assertRegex(
                certificate_payload["signatures"][0]["certificate_chain"][0]["sha256"],
                r"^[0-9a-f]{64}$",
            )
            stale_operation = self.env["usl.document.operation"].sudo().create(
                {
                    "name": "stale-sign-archive.pdf",
                    "state": "processing",
                    "checksum": request.final_sha256,
                    "mime_type": "application/pdf",
                    "company_id": self.company.id,
                    "confidentiality": "private",
                    "paperless_task_id": "stale-sign-task",
                    "res_model": request._name,
                    "res_id": request.id,
                    "source": "odoo_generated",
                },
            )
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {"archive_operation_id": stale_operation.id},
            )
            request.action_retry_archive()
            request._reconcile_archive()
            notify.assert_called_once()
            self.assertTrue(notify.call_args.kwargs["attachment_ids"])
        self.assertEqual(request.state, "completed")
        self.assertEqual(request.archive_status, "archived")
        self.assertFalse(request.archive_operation_id)
        self.assertEqual(request.archive_document_id, archived)
        self.assertTrue(request.completed_at)
        self.assertEqual(request.evidence_status, "complete")
        self.assertFalse(request.last_error)
        self.assertFalse(request.recovery_action)
        self.assertEqual(
            len(request.event_ids.filtered(
                lambda event: event.event_type == "completed_dossier_queued",
            )),
            1,
        )

    def test_oca_final_document_delivery_defaults_to_enabled(self):
        defaults = self.env["res.company"].default_get(
            ["sign_oca_send_sign_request_copy"],
        )
        self.assertTrue(defaults["sign_oca_send_sign_request_copy"])

    def test_strong_csr_is_personal_p256_and_signer_bound(self):
        subject = personal_certificate_subject(self.partner_one.name)
        private_key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .sign(private_key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()

        details = validate_personal_csr(csr_pem, subject)

        self.assertEqual(details["csr_sha256"], hashlib.sha256(csr_pem.encode()).hexdigest())
        self.assertEqual(len(details["public_key_sha256"]), 64)
        with self.assertRaises(ValidationError):
            validate_personal_csr(csr_pem, personal_certificate_subject("Another Signer"))

        wrong_curve_key = ec.generate_private_key(ec.SECP384R1())
        wrong_curve_csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .sign(wrong_curve_key, hashes.SHA384())
            .public_bytes(serialization.Encoding.PEM)
            .decode()
        )
        with self.assertRaises(ValidationError):
            validate_personal_csr(wrong_curve_csr, subject)

    def test_strong_challenge_binds_document_consent_identity_key_and_policy(self):
        values = {
            "request_id": 10,
            "signer_id": 20,
            "enrollment_id": 30,
            "role_id": 40,
            "original_sha256": "1" * 64,
            "document_sha256": "2" * 64,
            "consent_sha256": "3" * 64,
            "csr_sha256": "4" * 64,
            "public_key_sha256": "5" * 64,
            "policy_sha256": "6" * 64,
            "policy_version": "2026-08",
            "nonce": "one-use-nonce",
            "expires_at": "2026-08-06 12:05:00",
        }
        binding = build_strong_binding(**values)
        challenge = strong_challenge(binding)

        self.assertEqual(binding["format"], "usl-strong-challenge-v1")
        self.assertEqual(len(challenge), 32)
        for field_name in (
            "request_id",
            "signer_id",
            "enrollment_id",
            "role_id",
            "original_sha256",
            "document_sha256",
            "consent_sha256",
            "csr_sha256",
            "public_key_sha256",
            "policy_sha256",
            "policy_version",
            "nonce",
            "expires_at",
        ):
            changed = dict(values)
            changed[field_name] = f"different-{field_name}"
            self.assertNotEqual(challenge, strong_challenge(build_strong_binding(**changed)))

    def test_only_one_live_strong_ceremony_can_exist_per_signer(self):
        sign_request = self._request(requested_trust="strong_personal")
        signer = sign_request.signer_ids
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": signer.partner_id.id,
                "company_id": sign_request.company_id.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Strong single-flight test",
            },
        )
        ceremony_values = {
            "request_id": sign_request.id,
            "signer_id": signer.id,
            "enrollment_id": enrollment.id,
            "challenge": base64.b64encode(b"single-flight-challenge"),
            "challenge_sha256": "1" * 64,
            "document_sha256": "2" * 64,
            "consent_sha256": "3" * 64,
            "csr_sha256": "4" * 64,
            "public_key_sha256": "5" * 64,
            "csr_pem": "single-flight-csr",
            "binding_payload": {"format": "single-flight-test"},
            "expires_at": fields.Datetime.now() + timedelta(minutes=5),
        }
        first = self.env["usl.sign.ceremony"].sudo().create(ceremony_values)
        self.env.cr.execute(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = current_schema()
               AND indexname = 'usl_sign_ceremony_active_signer_unique'
            """,
        )
        index_definition = self.env.cr.fetchone()[0]
        self.assertIn("UNIQUE INDEX", index_definition)
        self.assertIn("(signer_id)", index_definition)
        self.assertIn("state", index_definition)
        self.assertIn("authorized", index_definition)
        self.assertIn("challenge", index_definition)
        duplicate_values = dict(
            ceremony_values,
            challenge=base64.b64encode(b"duplicate-challenge"),
            challenge_sha256="6" * 64,
        )
        first.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {"state": "failed", "failure_code": "superseded_by_new_challenge"},
        )
        first.flush_recordset(["state", "failure_code"])
        replacement = self.env["usl.sign.ceremony"].sudo().create(duplicate_values)
        self.assertEqual(replacement.state, "challenge")

    def test_strong_webauthn_assertion_fails_closed_for_replay_and_binding_changes(self):
        rp_id = "sign.example.test"
        origin = "https://sign.example.test"
        binding = build_strong_binding(
            request_id=10,
            signer_id=20,
            enrollment_id=30,
            role_id=40,
            original_sha256="1" * 64,
            document_sha256="2" * 64,
            consent_sha256="3" * 64,
            csr_sha256="4" * 64,
            public_key_sha256="5" * 64,
            policy_sha256="6" * 64,
            policy_version="2026-08",
            nonce="one-use-nonce",
            expires_at="2026-08-06 12:05:00",
        )
        challenge = strong_challenge(binding)
        credential, public_key = _webauthn_assertion(
            challenge=challenge,
            rp_id=rp_id,
            origin=origin,
        )

        verification = verify_strong_assertion(
            credential=credential,
            challenge=challenge,
            rp_id=rp_id,
            origin=origin,
            credential_public_key=public_key,
            current_sign_count=0,
        )
        self.assertTrue(verification.user_verified)
        self.assertEqual(verification.new_sign_count, 1)

        changed_document = dict(binding, document_sha256="9" * 64)
        invalid_expectations = (
            {"challenge": strong_challenge(changed_document)},
            {"origin": "https://attacker.example.test"},
            {"rp_id": "other.example.test"},
            {"current_sign_count": 1},
        )
        base = {
            "credential": credential,
            "challenge": challenge,
            "rp_id": rp_id,
            "origin": origin,
            "credential_public_key": public_key,
            "current_sign_count": 0,
        }
        for overrides in invalid_expectations:
            with self.assertRaises(InvalidAuthenticationResponse):
                verify_strong_assertion(**(base | overrides))

        no_uv_credential, no_uv_public_key = _webauthn_assertion(
            challenge=challenge,
            rp_id=rp_id,
            origin=origin,
            flags=0x01,
        )
        with self.assertRaises(InvalidAuthenticationResponse):
            verify_strong_assertion(
                credential=no_uv_credential,
                challenge=challenge,
                rp_id=rp_id,
                origin=origin,
                credential_public_key=no_uv_public_key,
                current_sign_count=0,
            )

    def test_passkey_enrolment_revocation_and_reenrolment_are_controlled(self):
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner_one.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Contractor file C-001",
                "policy_version": "2026.1",
            },
        )
        enrollment.with_user(self.reviewer).action_confirm_identity()
        passkey = self.env["usl.sign.passkey"].with_context(
            usl_sign_passkey_registration=INTERNAL_OPERATION,
        ).create(
            {
                "enrollment_id": enrollment.id,
                "name": "Primary passkey",
                "credential_id": "credential-one",
                "public_key": base64.b64encode(b"credential-public-key"),
            },
        )
        enrollment.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {"state": "active"},
        )
        with self.assertRaises(AccessError):
            passkey.write({"state": "lost"})
        enrollment.with_user(self.reviewer).action_revoke("Passkey reported lost.")
        self.assertEqual(enrollment.state, "revoked")
        self.assertEqual(passkey.state, "revoked")
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
        self.assertEqual(replacement.state, "pending_review")
        with self.assertRaises(AccessError):
            enrollment.unlink()

    def test_approval_decisions_and_events_are_attributable_and_immutable(self):
        approval = self.env["usl.sign.approval"].create(
            {
                "name": "Approve routine internal decision",
                "record_ref": f"res.partner,{self.partner_one.id}",
                "approver_ids": [(6, 0, [self.sign_user.id])],
                "policy_version": "2026.1",
            },
        )
        approval.with_user(self.sign_user).action_reject("Business owner declined.")
        self.assertEqual(approval.state, "rejected")
        self.assertEqual(approval.decision_by_id, self.sign_user)
        self.assertEqual(approval.event_ids.mapped("event_type"), ["requested", "rejected"])
        with self.assertRaises(AccessError):
            approval.event_ids[-1].write({"reason": "changed"})
        with self.assertRaises(AccessError):
            approval.unlink()

    def test_daily_event_head_manifest_is_signed_and_immutable(self):
        request = self._request()
        request._append_event("daily_manifest_test", payload={"case": "clean"})
        with patch(
            "odoo.addons.usl_sign.models.daily_manifest.DSSClient",
            return_value=FakeDSS(),
        ):
            manifest = self.env["usl.sign.daily.manifest"].build_for_day(
                self.company, fields.Date.today(),
            )
        self.assertEqual(manifest.state, "signed")
        self.assertGreaterEqual(manifest.event_count, 2)
        raw = base64.b64decode(manifest.payload)
        self.assertEqual(manifest.payload_sha256, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(AccessError):
            manifest.write({"event_count": 0})
        with self.assertRaises(AccessError):
            manifest.unlink()

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

    def test_step_ca_health_requires_trusted_https_and_a_healthy_response(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "ok"}

        client = StepCAClient(base_url="https://ca.example.test", timeout=7)
        client.ca_bundle = "/run/test/root.crt"
        with patch(
            "odoo.addons.usl_sign.services.step_ca.requests.get",
            return_value=Response(),
        ) as request:
            self.assertEqual(client.health(), {"status": "ok"})
        request.assert_called_once_with(
            "https://ca.example.test/health",
            timeout=7,
            verify="/run/test/root.crt",
        )

        plaintext = StepCAClient(base_url="http://ca.example.test")
        plaintext.ca_bundle = "/run/test/root.crt"
        with self.assertRaisesRegex(StepCAError, "trusted HTTPS"):
            plaintext.health()

        with patch(
            "odoo.addons.usl_sign.services.step_ca.requests.get",
            return_value=type(
                "UnhealthyResponse",
                (),
                {
                    "raise_for_status": staticmethod(lambda: None),
                    "json": staticmethod(lambda: {"status": "degraded"}),
                },
            )(),
        ), self.assertRaisesRegex(StepCAError, "unhealthy response"):
            client.health()
