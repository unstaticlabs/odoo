# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is the test result.
"""Verify every completion gate after the browser Strong ceremony."""

import base64
import hashlib
import json
import os
import time
from datetime import timedelta

from odoo.addons.usl_sign.services import field_content

request_id = int(os.environ.get("USL_SIGN_ACCEPTANCE_REQUEST_ID", "0"))
if request_id <= 0:
    msg = "A Strong acceptance request ID is required"
    raise RuntimeError(msg)
sign_request = env["sign.oca.request"].browse(request_id).exists()
if not sign_request:
    msg = "The Strong acceptance request no longer exists"
    raise RuntimeError(msg)

for _attempt in range(30):
    sign_request.invalidate_recordset()
    sign_request._reconcile_archive()
    env.cr.commit()
    sign_request.invalidate_recordset()
    if sign_request.state in {"completed", "action_required", "validation_failed"}:
        break
    time.sleep(1)

ceremony = env["usl.sign.ceremony"].search(
    [("request_id", "=", sign_request.id)],
    order="id desc",
    limit=1,
)
if not ceremony:
    msg = "The Strong acceptance ceremony is missing"
    raise RuntimeError(msg)
validation_record = sign_request.validation_ids.sorted("id")[-1:]
required_evidence = {"authentication", "certificate", "consent", "validation"}
available_evidence = set(sign_request.evidence_ids.mapped("kind"))
binding_bytes = json.dumps(
    ceremony.binding_payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode()
binding_digest = hashlib.sha256(binding_bytes).digest()
expected_oidc_nonce = base64.urlsafe_b64encode(binding_digest).rstrip(b"=").decode()
event_head = sign_request.event_ids.verify_chain()
auth_time = ceremony.oidc_auth_time
auth_upper_bound = ceremony.authorized_at + timedelta(seconds=60)
certificate_lifetime = ceremony.certificate_not_after - ceremony.certificate_issued_at
failures = []
checks = {
    "request_completed": sign_request.state == "completed",
    "requested_trust": sign_request.requested_trust == "strong_personal",
    "achieved_trust": sign_request.achieved_trust == "strong_personal",
    "validation_valid": sign_request.validation_status == "valid",
    "dss_validation_record": bool(
        validation_record
        and validation_record.engine == "EU DSS"
        and validation_record.engine_version == "6.4"
        and validation_record.status == "valid"
        and validation_record.achieved_trust == "strong_personal"
        and validation_record.report_evidence_id,
    ),
    "evidence_complete": sign_request.evidence_status == "complete",
    "archive_confirmed": sign_request.archive_status == "archived",
    "archive_linked": bool(sign_request.archive_document_id),
    "signer_signed": all(signer.state == "signed" for signer in sign_request.signer_ids),
    "ceremony_completed": bool(ceremony and ceremony.state == "completed"),
    "ceremony_secret_cleared": bool(
        ceremony and not ceremony.data_to_sign and not ceremony.dss_signing_context,
    ),
    "oidc_validated": bool(
        ceremony
        and ceremony.oidc_validation_result.get("status") == "valid_fresh_passkey",
    ),
    "amr_is_fresh_passkey": ceremony.oidc_claims_summary.get("amr") == ["phr"],
    "auth_time_is_fresh": bool(
        auth_time
        and int(ceremony.create_date.timestamp())
        <= int(auth_time.timestamp())
        <= int(auth_upper_bound.timestamp()),
    ),
    "binding_digest_matches": bool(
        ceremony.challenge_sha256 == binding_digest.hex()
        and ceremony.oidc_nonce == expected_oidc_nonce,
    ),
    "document_binding_matches": bool(
        ceremony.binding_payload.get("document_sha256") == ceremony.document_sha256
        and ceremony.binding_payload.get("csr_sha256") == ceremony.csr_sha256
        and ceremony.binding_payload.get("public_key_sha256")
        == ceremony.public_key_sha256
        and ceremony.binding_payload.get("consent_sha256") == ceremony.consent_sha256,
    ),
    "certificate_short_lived": bool(
        timedelta(0) < certificate_lifetime <= timedelta(minutes=10, seconds=5)
        and ceremony.certificate_not_after > ceremony.authorized_at,
    ),
    "signed_oidc_token_preserved": bool(ceremony.oidc_id_token),
    "event_chain_valid": bool(event_head),
    "signed_manifest_present": bool(
        sign_request.evidence_ids.filtered(lambda evidence: evidence.kind == "manifest"),
    ),
    "required_evidence": required_evidence.issubset(available_evidence),
}
for name, passed in checks.items():
    if not passed:
        failures.append(name)
if failures:
    raise RuntimeError("Strong acceptance checks failed: " + ", ".join(failures))

final_pdf = field_content(sign_request.final_data)
validation = sign_request._sign_dss_client().validate(
    final_pdf,
    expected_level="strong_personal",
)
if validation.get("status") != "valid" or validation.get("achievedTrust") != "strong_personal":
    msg = "Independent final DSS validation rejected the Strong PDF"
    raise RuntimeError(msg)

payload = {
    "archive_document_id": sign_request.archive_document_id.id,
    "archive_status": sign_request.archive_status,
    "ceremony_id": ceremony.id,
    "checks": checks,
    "evidence_kinds": sorted(available_evidence),
    "event_chain_head": event_head.event_hash,
    "final_sha256": hashlib.sha256(final_pdf).hexdigest(),
    "fresh_authentication": {
        "amr": ceremony.oidc_claims_summary.get("amr"),
        "auth_time": str(ceremony.oidc_auth_time),
        "binding_sha256": ceremony.challenge_sha256,
        "certificate_lifetime_seconds": int(certificate_lifetime.total_seconds()),
        "csr_sha256": ceremony.csr_sha256,
        "document_sha256": ceremony.document_sha256,
        "pades_level": ceremony.pades_level,
    },
    "request_id": sign_request.id,
    "state": sign_request.state,
    "validation_engine": validation_record.engine,
    "validation_status": validation.get("status"),
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
