import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.usl_pocketid.exceptions import PocketIDAccessDenied
from odoo.addons.usl_sign.models.constants import (
    INTERNAL_OPERATION,
    SIGN_RESULT_SESSION_KEY,
)
from odoo.addons.usl_sign.services import (
    DSSClient,
    DSSServiceError,
    StepCAClient,
    StepCAError,
    field_content,
    field_value,
)

_logger = logging.getLogger(__name__)
_TRANSACTION_TTL = 300
_SESSION_TRANSACTIONS = "usl_sign_pocketid_transactions"
_SESSION_ENROLMENTS = "usl_sign_pocketid_enrolments"
_SESSION_ENROLLMENT_FAILURES = "usl_sign_pocketid_enrollment_failures"
_SESSION_COMPLETIONS = "usl_sign_strong_completions"


def _base64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _personal_certificate_subject(signer):
    # This prefix is enforced by the pinned step-ca certificate template and
    # classified by DSS as a local personal certificate.  Keep it stable and
    # test it as one contract across the browser CSR, CA, and validator.
    return f"USL Sign Personal: {signer.partner_id.name}".replace(",", " ")


def _public_failure_code(error):
    if isinstance(error, PocketIDAccessDenied):
        return "pocket_id_rejected"
    if isinstance(error, StepCAError):
        return "certificate_service"
    if isinstance(error, DSSServiceError):
        return "signature_service"
    if isinstance(error, AccessError):
        return "identity_check"
    return "request_invalid"


def _fresh_passkey_claims_summary(claims, *, transaction_created, now=None):
    authentication_methods = claims.get("amr", [])
    auth_time = claims.get("auth_time")
    current_time = int(time.time()) if now is None else int(now)
    if (
        not isinstance(authentication_methods, list)
        or "phr" not in authentication_methods
        or "otp" in authentication_methods
        or not isinstance(auth_time, int | float)
        or int(auth_time) < int(transaction_created)
        or int(auth_time) > current_time + 60
    ):
        msg = "A fresh Pocket ID passkey interaction is required."
        raise AccessError(msg)
    return {
        key: claims.get(key)
        for key in (
            "iss",
            "sub",
            "aud",
            "azp",
            "name",
            "preferred_username",
            "email",
            "email_verified",
            "groups",
            "amr",
            "auth_time",
            "iat",
            "exp",
            "nonce",
        )
        if key in claims
    }


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
                "Permissions-Policy": "publickey-credentials-get=(), publickey-credentials-create=()",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )
        return response

    def _lock_ceremony(self, ceremony_id, expected_state):
        request.env.cr.execute(
            "SELECT state FROM usl_sign_ceremony WHERE id = %s FOR UPDATE",
            [ceremony_id],
        )
        row = request.env.cr.fetchone()
        if not row or row[0] != expected_state:
            msg = "The strong-signature ceremony was already used or is unavailable."
            raise ValidationError(
                msg,
            )
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

    def _pocket_client(self):
        return request.env["auth.oauth.provider"].sudo()._usl_pocketid_sign_configuration()

    def _callback_context(self, transaction):
        purpose = (transaction or {}).get("purpose")
        company = request.env["res.company"]
        if purpose == "enrollment" and transaction.get("enrollment_id"):
            enrollment = request.env["usl.sign.enrollment"].sudo().browse(
                transaction["enrollment_id"],
            ).exists()
            company = enrollment.company_id
        elif purpose == "strong_signature" and transaction.get("ceremony_id"):
            ceremony = request.env["usl.sign.ceremony"].sudo().browse(
                transaction["ceremony_id"],
            ).exists()
            company = ceremony.request_id.company_id
        return {
            "callback_purpose": purpose,
            "company_name": company.name if company else False,
        }

    def _create_oidc_transaction(self, *, purpose, nonce, values):
        configuration = self._pocket_client()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
        now = int(time.time())
        transactions = dict(request.session.get(_SESSION_TRANSACTIONS, {}))
        transactions = {
            key: transaction
            for key, transaction in transactions.items()
            if int(transaction.get("expires_unix", 0)) >= now
        }
        transactions[state] = {
            "state": state,
            "purpose": purpose,
            "nonce": nonce,
            "code_verifier": verifier,
            "created_unix": now,
            "expires_unix": now + _TRANSACTION_TTL,
            **values,
        }
        request.session[_SESSION_TRANSACTIONS] = transactions
        parameters = {
            "client_id": configuration.client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "max_age": "0",
            "nonce": nonce,
            "prompt": "login",
            "redirect_uri": configuration.redirect_uri,
            "response_type": "code",
            "scope": configuration.scopes,
            "state": state,
        }
        return state, f"{configuration.authorization_endpoint}?{urlencode(parameters)}"

    def _consume_oidc_transaction(self, state):
        transactions = dict(request.session.get(_SESSION_TRANSACTIONS, {}))
        transaction = transactions.pop(state, None)
        request.session[_SESSION_TRANSACTIONS] = transactions
        if (
            not transaction
            or int(transaction.get("expires_unix", 0)) < int(time.time())
            or not secrets.compare_digest(state, str(transaction.get("state", "")))
        ):
            msg = "This Pocket ID authorization is invalid or expired."
            raise AccessError(msg)
        return transaction

    def _validated_pocket_callback(self, transaction, code):
        client = request.env["auth.oauth.provider"].sudo()
        configuration = client._usl_pocketid_sign_configuration()
        access_token, id_token = client._usl_pocketid_exchange_code_for_client(
            configuration,
            code=code,
            code_verifier=transaction["code_verifier"],
        )
        claims, keys = client._usl_pocketid_validate_id_token_for_client(
            configuration,
            id_token=id_token,
            access_token=access_token,
            nonce=transaction["nonce"],
        )
        summary = _fresh_passkey_claims_summary(
            claims,
            transaction_created=transaction["created_unix"],
        )
        return configuration, claims, summary, keys, id_token

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
        failures = dict(request.session.get(_SESSION_ENROLLMENT_FAILURES, {}))
        failures.pop(str(enrollment.id), None)
        request.session[_SESSION_ENROLLMENT_FAILURES] = failures
        nonce = _base64url(secrets.token_bytes(32))
        _state, authorization_url = self._create_oidc_transaction(
            purpose="enrollment",
            nonce=nonce,
            values={"enrollment_id": enrollment.id},
        )
        return {"authorization_url": authorization_url, "expires_in": _TRANSACTION_TTL}

    @http.route(
        "/sign/enroll/<int:enrollment_id>/<string:token>/status",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def enrollment_status(self, enrollment_id, token):
        enrollment = request.env["usl.sign.enrollment"].sudo().browse(enrollment_id).exists()
        completed = set(request.session.get(_SESSION_ENROLMENTS, []))
        failure = request.session.get(_SESSION_ENROLLMENT_FAILURES, {}).get(
            str(enrollment_id),
        )
        if not enrollment:
            msg = "This enrolment is unavailable."
            raise AccessError(msg)
        if failure and int(failure.get("expires_unix", 0)) >= int(time.time()):
            return {"state": "failed", "failure_code": failure.get("code")}
        if enrollment.id not in completed:
            if enrollment.state != "pending_pocket":
                msg = "This enrolment is unavailable."
                raise AccessError(msg)
            enrollment._check_invitation(token)
        return {
            "state": enrollment.state,
            "display_name": enrollment.pocket_display_name,
            "subject_fingerprint": enrollment.pocket_subject_fingerprint,
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
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
            common_names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        except (TypeError, ValueError) as error:
            msg = "The browser produced an invalid certificate request."
            raise ValidationError(
                msg,
            ) from error
        public_key = csr.public_key()
        if (
            not csr.is_signature_valid
            or not isinstance(public_key, ec.EllipticCurvePublicKey)
            or not isinstance(public_key.curve, ec.SECP256R1)
            or len(common_names) != 1
            or common_names[0].value != _personal_certificate_subject(signer)
        ):
            msg = "The certificate request is not the expected signer-bound P-256 request."
            raise ValidationError(
                msg,
            )
        enrollment = signer._active_enrollment()
        if not enrollment or not enrollment.pocket_subject:
            msg = "Complete Pocket ID strong-signer enrolment first."
            raise ValidationError(msg)
        # Serialize attempts for this signer before creating any one-use key or
        # certificate state. Expired abandoned attempts are closed here so a
        # refresh can recover without an administrator; a genuinely live tab
        # remains protected from being silently replaced by another tab.
        request.env.cr.execute(
            "SELECT id FROM sign_oca_request_signer WHERE id = %s FOR UPDATE",
            [signer.id],
        )
        live_ceremonies = request.env["usl.sign.ceremony"].sudo().search(
            [
                ("signer_id", "=", signer.id),
                ("state", "in", ["challenge", "authorizing", "authorized"]),
            ],
        )
        expired = live_ceremonies.filtered(
            lambda row: row.expires_at < fields.Datetime.now(),
        )
        expired.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {"state": "expired", "failure_code": "authorization_timeout"},
        )
        if live_ceremonies - expired:
            msg = "A protected signing attempt is already open for this document."
            raise ValidationError(
                msg,
            )
        document = field_content(signer.request_id.data)
        document_sha256 = hashlib.sha256(document).hexdigest()
        csr_sha256 = hashlib.sha256(csr_pem.encode()).hexdigest()
        public_key_sha256 = hashlib.sha256(
            public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ),
        ).hexdigest()
        consent_sha256 = hashlib.sha256(
            signer.request_id.consent_text_snapshot.encode(),
        ).hexdigest()
        policy_sha256 = hashlib.sha256(
            _canonical_json(
                {
                    "version": signer.request_id.policy_version,
                    "snapshot": signer.request_id.policy_snapshot,
                },
            ),
        ).hexdigest()
        expiry = fields.Datetime.now() + timedelta(minutes=5)
        binding = {
            "format": "usl-strong-pocketid-binding-v1",
            "request_id": signer.request_id.id,
            "signer_id": signer.id,
            "enrollment_id": enrollment.id,
            "role_id": signer.role_id.id,
            "original_sha256": signer.request_id.original_sha256,
            "document_sha256": document_sha256,
            "consent_sha256": consent_sha256,
            "csr_sha256": csr_sha256,
            "public_key_sha256": public_key_sha256,
            "policy_sha256": policy_sha256,
            "policy_version": signer.request_id.policy_version,
            "nonce": secrets.token_urlsafe(24),
            "expires_at": fields.Datetime.to_string(expiry),
        }
        binding_digest = hashlib.sha256(_canonical_json(binding)).digest()
        oidc_nonce = _base64url(binding_digest)
        ceremony = request.env["usl.sign.ceremony"].sudo().create(
            {
                "request_id": signer.request_id.id,
                "signer_id": signer.id,
                "enrollment_id": enrollment.id,
                "challenge": field_value(binding_digest),
                "challenge_sha256": binding_digest.hex(),
                "document_sha256": document_sha256,
                "consent_sha256": consent_sha256,
                "csr_sha256": csr_sha256,
                "public_key_sha256": public_key_sha256,
                "csr_pem": csr_pem,
                "binding_payload": binding,
                "expires_at": expiry,
                "oidc_nonce": oidc_nonce,
            },
        )
        state, authorization_url = self._create_oidc_transaction(
            purpose="strong_signature",
            nonce=oidc_nonce,
            values={"ceremony_id": ceremony.id},
        )
        ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "authorizing",
                "oidc_state_sha256": hashlib.sha256(state.encode()).hexdigest(),
            },
        )
        return {
            "ceremony_id": ceremony.id,
            "authorization_url": authorization_url,
            "expires_in": _TRANSACTION_TTL,
        }

    def _authorize_ceremony(self, ceremony, configuration, claims, summary, keys, id_token):
        signer = ceremony.signer_id
        enrollment = ceremony.enrollment_id
        current_document_hash = hashlib.sha256(
            field_content(signer.request_id.data),
        ).hexdigest()
        if (
            ceremony.expires_at < fields.Datetime.now()
            or current_document_hash != ceremony.document_sha256
            or enrollment.state != "active"
            or enrollment.pocket_issuer != configuration.issuer
            or not secrets.compare_digest(enrollment.pocket_subject, claims["sub"])
        ):
            msg = "The signer identity or document binding no longer matches."
            raise AccessError(msg)
        try:
            issued = StepCAClient().issue(
                ceremony.csr_pem,
                subject=_personal_certificate_subject(signer),
                binding={
                    "usl_signer": signer.id,
                    "usl_enrollment": enrollment.id,
                    "usl_request": signer.request_id.id,
                    "usl_role": signer.role_id.id,
                    "usl_document_sha256": ceremony.document_sha256,
                    "usl_policy_sha256": ceremony.binding_payload["policy_sha256"],
                    "usl_public_key_sha256": ceremony.public_key_sha256,
                },
            )
            issued_certificate = x509.load_pem_x509_certificate(issued["certificate"].encode())
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
                f"urn:usl:enrollment:{enrollment.id}",
                f"urn:usl:request:{signer.request_id.id}",
                f"urn:usl:role:{signer.role_id.id}",
                f"urn:sha256:{ceremony.document_sha256}",
                f"urn:usl:policy-sha256:{ceremony.binding_payload['policy_sha256']}",
                f"urn:usl:public-key-sha256:{ceremony.public_key_sha256}",
            }
            now_utc = fields.Datetime.now().replace(tzinfo=UTC)
            key_usage = issued_certificate.extensions.get_extension_for_class(x509.KeyUsage).value
            if (
                len(certificate_common_names) != 1
                or certificate_common_names[0].value != _personal_certificate_subject(signer)
                or certificate_public_key_sha256 != ceremony.public_key_sha256
                or certificate_sans != expected_sans
                or not key_usage.digital_signature
                or issued_certificate.not_valid_before_utc > now_utc + timedelta(minutes=1)
                or issued_certificate.not_valid_after_utc <= now_utc
                or issued_certificate.not_valid_after_utc
                - issued_certificate.not_valid_before_utc
                > timedelta(minutes=11)
            ):
                msg = "The issued personal certificate does not satisfy the ceremony binding."
                raise StepCAError(
                    msg,
                )
            data_to_sign = DSSClient().data_to_sign(
                field_content(signer.request_id.data),
                issued["certificate"],
                certificate_chain=issued["chain"],
                request_reference=f"USL-STRONG-{signer.request_id.id}-{signer.id}",
                timestamp=signer.request_id.company_id.sign_rfc3161_enabled,
            )
        except (x509.ExtensionNotFound, TypeError, ValueError) as error:
            msg = "The local certificate authority returned an invalid certificate."
            raise StepCAError(
                msg,
            ) from error
        raw_to_sign = base64.b64decode(data_to_sign["dataToSign"], validate=True)
        auth_time = datetime.fromtimestamp(int(claims["auth_time"]), tz=UTC).replace(tzinfo=None)
        ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
            {
                "state": "authorized",
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
                "data_to_sign": data_to_sign["dataToSign"],
                "data_to_sign_sha256": hashlib.sha256(raw_to_sign).hexdigest(),
                "dss_signing_context": data_to_sign["signingContext"],
                "oidc_issuer": configuration.issuer,
                "oidc_subject": claims["sub"],
                "oidc_auth_time": auth_time,
                "oidc_claims_summary": summary,
                "oidc_discovery_snapshot": configuration.discovery_snapshot,
                "oidc_jwks_snapshot": {"keys": keys},
                "oidc_validation_result": {
                    "status": "valid_fresh_passkey",
                    "issuer": configuration.issuer,
                    "audience": configuration.client_id,
                    "required_group": configuration.required_group,
                    "authentication_method": "phr",
                    "nonce_sha256": hashlib.sha256(
                        ceremony.oidc_nonce.encode(),
                    ).hexdigest(),
                    "validated_at": fields.Datetime.to_string(fields.Datetime.now()),
                },
                "oidc_id_token": id_token,
            },
        )
        enrollment.with_context(usl_sign_enrollment_transition=INTERNAL_OPERATION).write(
            {
                "pocket_last_authorized_at": fields.Datetime.now(),
                "pocket_authentication_method": "phr",
            },
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {"state": "authorized"},
        )
        signer.request_id._append_event(
            "strong_signature_authorized",
            signer=signer,
            authentication_method="pocket_id_passkey",
            payload={
                "ceremony_id": ceremony.id,
                "binding_sha256": ceremony.challenge_sha256,
                "document_sha256": ceremony.document_sha256,
                "csr_sha256": ceremony.csr_sha256,
                "pocket_subject_fingerprint": enrollment.pocket_subject_fingerprint,
            },
        )

    @http.route(
        "/sign/pocketid/callback",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
    )
    def pocketid_callback(self, state=None, code=None, error=None, **_params):
        transaction = None
        try:
            if not state:
                msg = "Pocket ID authorization was cancelled or denied."
                raise AccessError(msg)  # noqa: TRY301 - normalized by the safe callback page
            transaction = self._consume_oidc_transaction(state)
            if error or not code:
                msg = "Pocket ID authorization was cancelled or denied."
                raise AccessError(msg)  # noqa: TRY301 - normalized by the safe callback page
            configuration, claims, summary, keys, id_token = self._validated_pocket_callback(
                transaction,
                code,
            )
            if transaction["purpose"] == "enrollment":
                enrollment = self._enrollment_for_callback(transaction["enrollment_id"])
                enrollment._bind_pocket_identity(issuer=configuration.issuer, claims=claims)
                completed = set(request.session.get(_SESSION_ENROLMENTS, [])[-19:])
                completed.add(enrollment.id)
                request.session[_SESSION_ENROLMENTS] = sorted(completed)
            elif transaction["purpose"] == "strong_signature":
                ceremony = self._lock_ceremony(transaction["ceremony_id"], "authorizing")
                self._authorize_ceremony(
                    ceremony,
                    configuration,
                    claims,
                    summary,
                    keys,
                    id_token,
                )
            else:
                msg = "The Pocket ID authorization purpose is invalid."
                raise AccessError(msg)  # noqa: TRY301 - normalized by the safe callback page
        except (AccessError, ValidationError, PocketIDAccessDenied, StepCAError, DSSServiceError) as exc:
            _logger.warning("Pocket ID Sign authorization failed: %s", type(exc).__name__)
            failure_code = _public_failure_code(exc)
            if transaction and transaction.get("ceremony_id"):
                ceremony = request.env["usl.sign.ceremony"].sudo().browse(
                    transaction["ceremony_id"],
                ).exists()
                if ceremony and ceremony.state == "authorizing":
                    ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                        {"state": "failed", "failure_code": failure_code},
                    )
            elif transaction and transaction.get("enrollment_id"):
                failures = dict(request.session.get(_SESSION_ENROLLMENT_FAILURES, {}))
                failures[str(transaction["enrollment_id"])] = {
                    "code": failure_code,
                    "expires_unix": int(time.time()) + _TRANSACTION_TTL,
                }
                request.session[_SESSION_ENROLLMENT_FAILURES] = failures
            return self._secure_page(
                "usl_sign.pocketid_callback_result",
                {
                    "successful": False,
                    "failure_code": failure_code,
                    **self._callback_context(transaction),
                },
            )
        return self._secure_page(
            "usl_sign.pocketid_callback_result",
            {"successful": True, **self._callback_context(transaction)},
        )

    def _enrollment_for_callback(self, enrollment_id):
        request.env.cr.execute(
            "SELECT state FROM usl_sign_enrollment WHERE id = %s FOR UPDATE",
            [enrollment_id],
        )
        row = request.env.cr.fetchone()
        if not row or row[0] != "pending_pocket":
            msg = "This enrolment is no longer awaiting Pocket ID."
            raise AccessError(msg)
        enrollment = request.env["usl.sign.enrollment"].sudo().browse(enrollment_id)
        enrollment.invalidate_recordset()
        return enrollment

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/status",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_status(self, signer_id, token, ceremony_id):
        completion = request.session.get(_SESSION_COMPLETIONS, {}).get(str(ceremony_id), {})
        if (
            completion.get("signer_id") == signer_id
            and int(completion.get("expires_unix", 0)) >= int(time.time())
        ):
            return {"state": "completed", "redirect": completion["redirect"]}
        signer = self._signer(signer_id, token)
        ceremony = request.env["usl.sign.ceremony"].sudo().browse(ceremony_id).exists()
        if not ceremony or ceremony.signer_id != signer:
            msg = "This strong-signature ceremony is unavailable."
            raise AccessError(msg)
        if ceremony.state in {"challenge", "authorizing"} and ceremony.expires_at < fields.Datetime.now():
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {"state": "expired", "failure_code": "authorization_timeout"},
            )
        result = {"state": ceremony.state, "failure_code": ceremony.failure_code or None}
        if ceremony.state == "authorized":
            result["data_to_sign"] = ceremony.data_to_sign
        return result

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/cancel",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_cancel(self, signer_id, token, ceremony_id):
        signer = self._signer(signer_id, token)
        request.env.cr.execute(
            "SELECT state FROM usl_sign_ceremony WHERE id = %s FOR UPDATE",
            [ceremony_id],
        )
        row = request.env.cr.fetchone()
        ceremony = request.env["usl.sign.ceremony"].sudo().browse(ceremony_id).exists()
        if not row or not ceremony or ceremony.signer_id != signer:
            msg = "This signing attempt is unavailable."
            raise AccessError(msg)
        if row[0] in {"challenge", "authorizing", "authorized"}:
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {
                    "state": "revoked",
                    "failure_code": "signer_restarted",
                    "data_to_sign": False,
                    "dss_signing_context": False,
                },
            )
            signer.request_id._append_event(
                "strong_signature_attempt_cancelled",
                signer=signer,
                authentication_method="pocket_id_passkey",
                payload={"ceremony_id": ceremony.id},
            )
        return {"state": ceremony.state}

    @http.route(
        "/sign/strong/<int:signer_id>/<string:token>/finalize",
        type="jsonrpc",
        auth="public",
        csrf=False,
    )
    def strong_finalize(self, signer_id, token, ceremony_id, signature):
        signer = self._signer(signer_id, token)
        ceremony = self._lock_ceremony(ceremony_id, "authorized").exists()
        enrollment = signer._active_enrollment()
        if (
            not ceremony
            or ceremony.signer_id != signer
            or ceremony.expires_at < fields.Datetime.now()
            or ceremony.document_sha256
            != hashlib.sha256(field_content(signer.request_id.data)).hexdigest()
            or not enrollment
            or enrollment != ceremony.enrollment_id
            or enrollment.pocket_subject != ceremony.oidc_subject
        ):
            msg = "The strong-signature authorization is no longer valid."
            raise ValidationError(msg)
        signature_bytes = base64.b64decode(signature, validate=True)
        try:
            embedded = DSSClient().embed_signature(
                field_content(signer.request_id.data),
                ceremony.certificate_pem,
                signature_bytes,
                request_reference=f"USL-STRONG-{signer.request_id.id}-{signer.id}",
                signing_context=ceremony.dss_signing_context,
            )
            signed_pdf = base64.b64decode(embedded["document"], validate=True)
            validation = DSSClient().validate(signed_pdf, expected_level="strong_personal")
            if validation.get("status") != "valid" or validation.get(
                "achievedTrust",
            ) != "strong_personal":
                msg = "DSS rejected the personal PAdES signature."
                raise DSSServiceError(msg)  # noqa: TRY301 - handled by fail-closed cleanup below
            signer.request_id._store_dss_reports(validation)
        except DSSServiceError as error:
            ceremony.with_context(usl_sign_ceremony_transition=INTERNAL_OPERATION).write(
                {
                    "state": "failed",
                    "failure_code": type(error).__name__,
                    "data_to_sign": False,
                    "dss_signing_context": False,
                },
            )
            raise UserError(str(error)) from error
        digest = hashlib.sha256(signed_pdf).hexdigest()
        now = fields.Datetime.now()
        signer.request_id.with_context(usl_sign_working_pdf=INTERNAL_OPERATION).write(
            {"data": field_value(signed_pdf), "current_hash": digest},
        )
        signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
            {
                "state": "signed",
                "signed_on": now,
                "signature_hash": digest,
                "signed_document_sha256": digest,
                "authentication_method": "pocket_id_passkey",
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
                "data_to_sign": False,
                "dss_signing_context": False,
                "pades_level": embedded.get("padesLevel") or ceremony.pades_level,
            },
        )
        signer.request_id._create_evidence(
            "authentication",
            f"{signer.request_id.name}-{signer.id}-pocket-id-token.jwt",
            ceremony.oidc_id_token.encode(),
            mimetype="application/jwt",
            signer=signer,
            metadata={
                "ceremony_id": ceremony.id,
                "issuer": ceremony.oidc_issuer,
                "subject_fingerprint": enrollment.pocket_subject_fingerprint,
                "auth_time": fields.Datetime.to_string(ceremony.oidc_auth_time),
                "claims": ceremony.oidc_claims_summary,
                "discovery": ceremony.oidc_discovery_snapshot,
                "jwks": ceremony.oidc_jwks_snapshot,
                "validation": ceremony.oidc_validation_result,
            },
        )
        signer.request_id._create_evidence(
            "certificate",
            f"{signer.request_id.name}-{signer.id}-certificate-chain.pem",
            (
                ceremony.certificate_pem
                + "\n"
                + "\n".join(ceremony.certificate_chain or [])
            ).encode(),
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
            _canonical_json(ceremony.binding_payload),
            mimetype="application/json",
            signer=signer,
        )
        signer.request_id._append_event(
            "strong_personal_signature_applied",
            signer=signer,
            authentication_method="pocket_id_passkey",
            payload={"ceremony_id": ceremony.id, "document_sha256": digest},
        )
        signer._close_internal_signing_activities()
        signer._activate_next_signer_or_finish()
        redirect = "/sign/result/success"
        completions = {
            key: value
            for key, value in request.session.get(_SESSION_COMPLETIONS, {}).items()
            if int(value.get("expires_unix", 0)) >= int(time.time())
        }
        completions[str(ceremony.id)] = {
            "signer_id": signer.id,
            "expires_unix": int(time.time()) + 300,
            "redirect": redirect,
        }
        request.session[_SESSION_COMPLETIONS] = completions
        request.session[SIGN_RESULT_SESSION_KEY] = {
            "status": "success",
            "company_id": signer.request_id.company_id.id,
            "request_name": signer.request_id.name,
            "request_id": signer.request_id.id,
        }
        return {"ok": True, "redirect": redirect}
