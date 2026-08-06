import base64
import hashlib
import json
import secrets
from datetime import UTC, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..models.constants import INTERNAL_OPERATION
from ..services import (
    DSSClient,
    DSSServiceError,
    StepCAClient,
    StepCAError,
    build_strong_binding,
    personal_certificate_subject,
    strong_challenge,
    validate_personal_csr,
    verify_strong_assertion,
)


def _json_options(options):
    return json.loads(options_to_json(options))


def _personal_certificate_subject(signer):
    return personal_certificate_subject(signer.partner_id.name)


class StrongSignController(http.Controller):
    _CSP = (
        "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
        "connect-src 'self'; frame-src 'self'; worker-src 'self'; img-src 'self' data:; "
        "base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'"
    )

    def _secure_page(self, xmlid, values):
        response = request.render(xmlid, values)
        response.headers.update(
            {
                "Cache-Control": "no-store, max-age=0",
                "Content-Security-Policy": self._CSP,
                "Cross-Origin-Opener-Policy": "same-origin",
                "Permissions-Policy": "publickey-credentials-get=(self), publickey-credentials-create=(self)",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )
        return response

    def _origin(self, company):
        origin = (request.httprequest.headers.get("Origin") or "").rstrip("/")
        if origin not in company._sign_allowed_origins():
            msg = "This browser origin is not allowed for passkeys."
            raise AccessError(msg)
        return origin

    def _lock_ceremony(self, ceremony_id, expected_state):
        request.env.cr.execute(
            "SELECT state FROM usl_sign_ceremony WHERE id = %s FOR UPDATE",
            [ceremony_id],
        )
        row = request.env.cr.fetchone()
        if not row or row[0] != expected_state:
            msg = "The strong-signature ceremony was already used or is unavailable."
            raise ValidationError(msg)
        ceremony = request.env["usl.sign.ceremony"].sudo().browse(ceremony_id)
        ceremony.invalidate_recordset()
        return ceremony

    def _enrollment(self, enrollment_id, token):
        enrollment = request.env["usl.sign.enrollment"].sudo().browse(enrollment_id).exists()
        if not enrollment:
            msg = "The enrolment does not exist."
            raise AccessError(msg)
        enrollment._check_invitation(token)
        return enrollment

    def _signer(self, signer_id, token):
        signer = request.env["sign.oca.request.signer"].sudo().browse(signer_id).exists()
        if not signer:
            msg = "The signer does not exist."
            raise AccessError(msg)
        signer._check_token(token, session=True)
        return signer

    def _lock_signer(self, signer, token, *, allowed_states):
        """Serialize a personal-signature operation on its PDF and signer."""
        request.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id = %s FOR UPDATE",
            [signer.request_id.id],
        )
        request.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [signer.id],
        )
        signer.request_id.invalidate_recordset(["data", "current_hash", "state"])
        signer.invalidate_recordset(
            [
                "access_revoked",
                "session_token_sha256",
                "session_expires_at",
                "signed_on",
                "state",
            ],
        )
        signer._check_token(token, session=True)
        if signer.state not in allowed_states:
            msg = "This personal-signature step is no longer available."
            raise ValidationError(msg)
        return signer

    @http.route(
        "/sign/enroll/<int:enrollment_id>/<string:token>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def enrollment_page(self, enrollment_id, token):
        try:
            enrollment = self._enrollment(enrollment_id, token)
        except AccessError:
            return request.render("usl_sign.portal_sign_unavailable")
        return self._secure_page(
            "usl_sign.strong_enrollment_page",
            {"enrollment": enrollment, "enrollment_token": token},
        )

    @http.route(
        "/sign/enroll/<int:enrollment_id>/<string:token>/begin",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def enrollment_begin(self, enrollment_id, token):
        enrollment = self._enrollment(enrollment_id, token)
        challenge = secrets.token_bytes(32)
        credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id))
            for key in enrollment.passkey_ids.filtered(lambda item: item.state == "active")
        ]
        options = generate_registration_options(
            rp_id=enrollment.company_id.sign_webauthn_rp_id,
            rp_name=enrollment.company_id.name,
            user_id=f"usl-sign:{enrollment.id}:{enrollment.partner_id.id}".encode(),
            user_name=enrollment.partner_id.email or f"partner-{enrollment.partner_id.id}",
            user_display_name=enrollment.partner_id.name,
            challenge=challenge,
            exclude_credentials=credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        enrollment.sudo().with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "registration_challenge": base64.b64encode(challenge),
                "registration_challenge_expires_at": fields.Datetime.now()
                + timedelta(minutes=5),
            },
        )
        return _json_options(options)

    @http.route(
        "/sign/enroll/<int:enrollment_id>/<string:token>/complete",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def enrollment_complete(self, enrollment_id, token, credential, name, transports=None):
        enrollment = self._enrollment(enrollment_id, token)
        request.env.cr.execute(
            "SELECT id FROM usl_sign_enrollment WHERE id = %s FOR UPDATE",
            [enrollment.id],
        )
        enrollment.invalidate_recordset(
            ["registration_challenge", "registration_challenge_expires_at"],
        )
        if (
            not enrollment.registration_challenge
            or enrollment.registration_challenge_expires_at < fields.Datetime.now()
        ):
            msg = "The passkey registration challenge expired."
            raise ValidationError(msg)
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64.b64decode(enrollment.registration_challenge),
            expected_rp_id=enrollment.company_id.sign_webauthn_rp_id,
            expected_origin=self._origin(enrollment.company_id),
            require_user_verification=True,
        )
        credential_id = bytes_to_base64url(verification.credential_id)
        passkey = request.env["usl.sign.passkey"].sudo().with_context(
            usl_sign_passkey_registration=INTERNAL_OPERATION,
        ).create(
            {
                "enrollment_id": enrollment.id,
                "name": (name or "Passkey").strip(),
                "credential_id": credential_id,
                "public_key": base64.b64encode(verification.credential_public_key),
                "sign_count": verification.sign_count,
                "aaguid": str(verification.aaguid),
                "transports": transports or [],
                "device_type": str(verification.credential_device_type),
                "backed_up": bool(verification.credential_backed_up),
            },
        )
        enrollment.sudo().with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "state": "active",
                "registration_challenge": False,
                "registration_challenge_expires_at": False,
            },
        )
        return {
            "ok": True,
            "passkey_id": passkey.id,
            "recovery_ready": enrollment.recovery_ready,
        }

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/begin",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_begin(self, signer_id, token, csr_pem, consent):
        signer = self._signer(signer_id, token)
        if signer.request_id.requested_trust != "strong_personal":
            msg = "This is not a strong personal signature request."
            raise ValidationError(msg)
        if not consent:
            msg = "Explicit electronic-signature consent is required."
            raise ValidationError(msg)
        csr_details = validate_personal_csr(
            csr_pem,
            _personal_certificate_subject(signer),
        )
        enrollment = signer._active_enrollment()
        if not enrollment:
            msg = "Complete strong-signer enrolment first."
            raise ValidationError(msg)
        signer = self._lock_signer(
            signer,
            token,
            allowed_states={"notified", "viewed"},
        )
        active_ceremonies = request.env["usl.sign.ceremony"].sudo().search(
            [
                ("signer_id", "=", signer.id),
                ("state", "in", ["challenge", "authorized"]),
            ],
        )
        if active_ceremonies.filtered(lambda ceremony: ceremony.state == "authorized"):
            msg = "A personal-signature authorization is already in progress."
            raise ValidationError(msg)
        for ceremony in active_ceremonies:
            ceremony.with_context(
                usl_sign_ceremony_transition=INTERNAL_OPERATION,
            ).write(
                {
                    "state": "failed",
                    "failure_code": "superseded_by_new_challenge",
                    "dss_signing_context": False,
                },
            )
            signer.request_id._append_event(
                "strong_ceremony_superseded",
                signer=signer,
                authentication_method="passkey",
                payload={"ceremony_id": ceremony.id},
            )
        active_ceremonies.flush_recordset(
            ["state", "failure_code", "dss_signing_context"],
        )
        document = base64.b64decode(signer.request_id.data)
        document_sha256 = hashlib.sha256(document).hexdigest()
        csr_sha256 = csr_details["csr_sha256"]
        public_key_sha256 = csr_details["public_key_sha256"]
        consent_text = signer.request_id.consent_text_snapshot
        consent_sha256 = hashlib.sha256(consent_text.encode()).hexdigest()
        policy_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "version": signer.request_id.policy_version,
                    "snapshot": signer.request_id.policy_snapshot,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode(),
        ).hexdigest()
        binding = build_strong_binding(
            request_id=signer.request_id.id,
            signer_id=signer.id,
            enrollment_id=enrollment.id,
            role_id=signer.role_id.id,
            original_sha256=signer.request_id.original_sha256,
            document_sha256=document_sha256,
            consent_sha256=consent_sha256,
            csr_sha256=csr_sha256,
            public_key_sha256=public_key_sha256,
            policy_sha256=policy_sha256,
            policy_version=signer.request_id.policy_version,
            nonce=secrets.token_urlsafe(24),
            expires_at=fields.Datetime.to_string(
                fields.Datetime.now() + timedelta(minutes=5),
            ),
        )
        challenge = strong_challenge(binding)
        options = generate_authentication_options(
            rp_id=signer.request_id.company_id.sign_webauthn_rp_id,
            challenge=challenge,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id))
                for key in enrollment.passkey_ids.filtered(
                    lambda item: item.state == "active",
                )
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        ceremony = request.env["usl.sign.ceremony"].sudo().create(
            {
                "request_id": signer.request_id.id,
                "signer_id": signer.id,
                "enrollment_id": enrollment.id,
                "challenge": base64.b64encode(challenge),
                "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
                "document_sha256": document_sha256,
                "consent_sha256": consent_sha256,
                "csr_sha256": csr_sha256,
                "public_key_sha256": public_key_sha256,
                "csr_pem": csr_pem,
                "binding_payload": binding,
                "expires_at": fields.Datetime.now() + timedelta(minutes=5),
            },
        )
        return {"ceremony_id": ceremony.id, "options": _json_options(options)}

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/authorize",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_authorize(self, signer_id, token, ceremony_id, credential):
        signer = self._signer(signer_id, token)
        signer = self._lock_signer(
            signer,
            token,
            allowed_states={"notified", "viewed"},
        )
        ceremony = self._lock_ceremony(ceremony_id, "challenge").exists()
        if (
            not ceremony
            or ceremony.signer_id != signer
            or ceremony.state != "challenge"
            or ceremony.expires_at < fields.Datetime.now()
            or ceremony.document_sha256
            != hashlib.sha256(base64.b64decode(signer.request_id.data)).hexdigest()
        ):
            msg = "The strong-signature challenge is invalid or expired."
            raise ValidationError(msg)
        credential_id = credential.get("id") or credential.get("rawId")
        passkey = ceremony.enrollment_id.passkey_ids.filtered(
            lambda key: key.state == "active" and key.credential_id == credential_id,
        )[:1]
        if not passkey:
            msg = "The passkey is not active for this signer."
            raise AccessError(msg)
        verification = verify_strong_assertion(
            credential=credential,
            challenge=base64.b64decode(ceremony.challenge),
            rp_id=signer.request_id.company_id.sign_webauthn_rp_id,
            origin=self._origin(signer.request_id.company_id),
            credential_public_key=base64.b64decode(passkey.public_key),
            current_sign_count=passkey.sign_count,
        )
        passkey.with_context(usl_sign_passkey_use=INTERNAL_OPERATION).write(
            {
                "sign_count": verification.new_sign_count,
                "last_used_at": fields.Datetime.now(),
                "device_type": str(verification.credential_device_type),
                "backed_up": bool(verification.credential_backed_up),
            },
        )
        try:
            issued = StepCAClient().issue(
                ceremony.csr_pem,
                subject=_personal_certificate_subject(signer),
                binding={
                    "usl_signer": signer.id,
                    "usl_enrollment": ceremony.enrollment_id.id,
                    "usl_request": signer.request_id.id,
                    "usl_role": signer.role_id.id,
                    "usl_document_sha256": ceremony.document_sha256,
                    "usl_policy_sha256": ceremony.binding_payload["policy_sha256"],
                    "usl_public_key_sha256": ceremony.public_key_sha256,
                },
            )
            issued_certificate = x509.load_pem_x509_certificate(
                issued["certificate"].encode(),
            )
            certificate_public_key_sha256 = hashlib.sha256(
                issued_certificate.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
            ).hexdigest()
            certificate_common_names = issued_certificate.subject.get_attributes_for_oid(
                NameOID.COMMON_NAME,
            )
            certificate_sans = set(
                issued_certificate.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName,
                ).value.get_values_for_type(x509.UniformResourceIdentifier),
            )
            expected_sans = {
                f"urn:usl:signer:{signer.id}",
                f"urn:usl:enrollment:{ceremony.enrollment_id.id}",
                f"urn:usl:request:{signer.request_id.id}",
                f"urn:usl:role:{signer.role_id.id}",
                f"urn:sha256:{ceremony.document_sha256}",
                f"urn:usl:policy-sha256:{ceremony.binding_payload['policy_sha256']}",
                f"urn:usl:public-key-sha256:{ceremony.public_key_sha256}",
            }
            now_utc = fields.Datetime.now().replace(tzinfo=UTC)
            key_usage = issued_certificate.extensions.get_extension_for_class(
                x509.KeyUsage,
            ).value
            if (
                len(certificate_common_names) != 1
                or certificate_common_names[0].value != _personal_certificate_subject(signer)
                or certificate_public_key_sha256 != ceremony.public_key_sha256
                or certificate_sans != expected_sans
                or not key_usage.digital_signature
                or issued_certificate.not_valid_before_utc > now_utc + timedelta(minutes=1)
                or issued_certificate.not_valid_after_utc <= now_utc
                or issued_certificate.not_valid_after_utc - issued_certificate.not_valid_before_utc
                > timedelta(minutes=11)
            ):
                msg = "The issued personal certificate does not satisfy the ceremony binding."
                raise StepCAError(  # noqa: TRY301 - normalized to a safe ceremony failure below
                    msg,
                )
            data_to_sign = DSSClient().data_to_sign(
                base64.b64decode(signer.request_id.data),
                issued["certificate"],
                certificate_chain=issued["chain"],
                request_reference=f"USL-STRONG-{signer.request_id.id}-{signer.id}",
                timestamp=signer.request_id.company_id.sign_rfc3161_enabled,
            )
        except (x509.ExtensionNotFound, TypeError, ValueError) as error:
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {"state": "failed", "failure_code": "invalid_ca_certificate"},
            )
            msg = "The local certificate authority returned an invalid certificate."
            raise UserError(msg) from error
        except (StepCAError, DSSServiceError) as error:
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {"state": "failed", "failure_code": type(error).__name__},
            )
            raise UserError(str(error)) from error
        raw_to_sign = base64.b64decode(data_to_sign["dataToSign"])
        ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "authorized",
                "passkey_id": passkey.id,
                "authorized_at": fields.Datetime.now(),
                "certificate_pem": issued["certificate"],
                "certificate_chain": issued["chain"],
                "certificate_serial": format(issued_certificate.serial_number, "x"),
                "certificate_issued_at": issued_certificate.not_valid_before_utc.replace(
                    tzinfo=None,
                ),
                "certificate_not_after": issued_certificate.not_valid_after_utc.replace(
                    tzinfo=None,
                ),
                "issuance_receipt": issued["receipt"],
                "pades_level": data_to_sign["padesLevel"],
                "data_to_sign_sha256": hashlib.sha256(raw_to_sign).hexdigest(),
                "dss_signing_context": data_to_sign["signingContext"],
            },
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write({"state": "authorized"})
        signer.request_id._append_event(
            "strong_signature_authorized",
            signer=signer,
            authentication_method="passkey",
            payload={
                "ceremony_id": ceremony.id,
                "challenge_sha256": ceremony.challenge_sha256,
                "document_sha256": ceremony.document_sha256,
                "csr_sha256": ceremony.csr_sha256,
                "passkey_id": passkey.id,
            },
        )
        return {
            "data_to_sign": data_to_sign["dataToSign"],
        }

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/finalize",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_finalize(self, signer_id, token, ceremony_id, signature):
        signer = self._signer(signer_id, token)
        signer = self._lock_signer(
            signer,
            token,
            allowed_states={"authorized"},
        )
        ceremony = self._lock_ceremony(ceremony_id, "authorized").exists()
        if (
            not ceremony
            or ceremony.signer_id != signer
            or ceremony.state != "authorized"
            or ceremony.expires_at < fields.Datetime.now()
            or ceremony.document_sha256
            != hashlib.sha256(base64.b64decode(signer.request_id.data)).hexdigest()
            or not ceremony.passkey_id
            or ceremony.passkey_id.state != "active"
        ):
            msg = "The strong-signature authorization is no longer valid."
            raise ValidationError(msg)
        signature_bytes = base64.b64decode(signature)
        try:
            embedded = DSSClient().embed_signature(
                base64.b64decode(signer.request_id.data),
                ceremony.certificate_pem,
                signature_bytes,
                request_reference=f"USL-STRONG-{signer.request_id.id}-{signer.id}",
                signing_context=ceremony.dss_signing_context,
            )
            signed_pdf = base64.b64decode(embedded["document"])
            validation = DSSClient().validate(signed_pdf, expected_level="strong_personal")
            if validation.get("status") != "valid" or validation.get(
                "achievedTrust",
            ) != "strong_personal":
                msg = "DSS rejected the personal PAdES signature."
                raise DSSServiceError(msg)  # noqa: TRY301 - normalized to a safe ceremony failure below
            signer.request_id._store_dss_reports(validation)
        except DSSServiceError as error:
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {
                    "state": "failed",
                    "failure_code": type(error).__name__,
                    "dss_signing_context": False,
                },
            )
            raise UserError(str(error)) from error
        digest = hashlib.sha256(signed_pdf).hexdigest()
        now = fields.Datetime.now()
        signer.request_id.with_context(usl_sign_working_pdf=INTERNAL_OPERATION).write(
            {
                "data": base64.b64encode(signed_pdf),
                "current_hash": digest,
            },
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "state": "signed",
                "signed_on": now,
                "signature_hash": digest,
                "signed_document_sha256": digest,
                "authentication_method": "passkey",
                "certificate_serial": ceremony.certificate_serial,
                "consent_text": signer.request_id.consent_text_snapshot,
                "consent_version": "1",
                "consented_at": now,
                "access_revoked": True,
                "session_token_sha256": False,
                "session_expires_at": False,
            },
        )
        ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "completed",
                "completed_at": now,
                "dss_signing_context": False,
                "pades_level": embedded.get("padesLevel") or ceremony.pades_level,
            },
        )
        signer.request_id._create_evidence(
            "certificate",
            f"{signer.request_id.name}-{signer.id}-certificate-chain.pem",
            (ceremony.certificate_pem + "\n" + "\n".join(ceremony.certificate_chain or [])).encode(),
            mimetype="application/x-pem-file",
            signer=signer,
            metadata={
                "ceremony_id": ceremony.id,
                "csr_sha256": ceremony.csr_sha256,
                "public_key_sha256": ceremony.public_key_sha256,
                "certificate_serial": ceremony.certificate_serial,
                "certificate_not_after": fields.Datetime.to_string(
                    ceremony.certificate_not_after,
                ),
                "issuance_receipt": ceremony.issuance_receipt,
                "pades_level": ceremony.pades_level,
            },
        )
        signer.request_id._create_evidence(
            "consent",
            f"{signer.request_id.name}-{signer.id}-strong-consent.json",
            json.dumps(ceremony.binding_payload, sort_keys=True).encode(),
            mimetype="application/json",
            signer=signer,
        )
        signer.request_id._append_event(
            "strong_personal_signature_applied",
            signer=signer,
            authentication_method="passkey",
            payload={"ceremony_id": ceremony.id, "document_sha256": digest},
        )
        signer._activate_next_signer_or_finish()
        return {"ok": True, "redirect": "/sign/result/success"}
