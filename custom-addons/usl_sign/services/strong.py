import hashlib
import json

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from webauthn import verify_authentication_response

from odoo.exceptions import ValidationError


def personal_certificate_subject(signer_name):
    return f"USL Sign Personal: {signer_name}".replace(",", " ")


def validate_personal_csr(csr_pem, expected_common_name):
    """Return the signer-bound P-256 CSR and its public-key digest."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        common_names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    except (AttributeError, TypeError, ValueError) as error:
        msg = "The browser produced an invalid certificate request."
        raise ValidationError(msg) from error
    public_key = csr.public_key()
    if (
        not csr.is_signature_valid
        or not isinstance(public_key, ec.EllipticCurvePublicKey)
        or not isinstance(public_key.curve, ec.SECP256R1)
        or len(common_names) != 1
        or common_names[0].value != expected_common_name
    ):
        msg = "The certificate request is not the expected signer-bound P-256 request."
        raise ValidationError(msg)
    public_key_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "csr": csr,
        "public_key": public_key,
        "csr_sha256": hashlib.sha256(csr_pem.encode()).hexdigest(),
        "public_key_sha256": hashlib.sha256(public_key_der).hexdigest(),
    }


def build_strong_binding(
    *,
    request_id,
    signer_id,
    enrollment_id,
    role_id,
    original_sha256,
    document_sha256,
    consent_sha256,
    csr_sha256,
    public_key_sha256,
    policy_sha256,
    policy_version,
    nonce,
    expires_at,
):
    return {
        "format": "usl-strong-challenge-v1",
        "request_id": request_id,
        "signer_id": signer_id,
        "enrollment_id": enrollment_id,
        "role_id": role_id,
        "original_sha256": original_sha256,
        "document_sha256": document_sha256,
        "consent_sha256": consent_sha256,
        "csr_sha256": csr_sha256,
        "public_key_sha256": public_key_sha256,
        "policy_sha256": policy_sha256,
        "policy_version": policy_version,
        "nonce": nonce,
        "expires_at": expires_at,
    }


def strong_challenge(binding):
    payload = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).digest()


def verify_strong_assertion(
    *, credential, challenge, rp_id, origin, credential_public_key, current_sign_count,
):
    """Verify the exact ceremony assertion and require authenticator user verification."""
    return verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=credential_public_key,
        credential_current_sign_count=current_sign_count,
        require_user_verification=True,
    )
