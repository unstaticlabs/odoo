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

ceremonies = env["usl.sign.ceremony"].search(
    [("request_id", "=", sign_request.id)],
    order="id",
)
if len(ceremonies) != 2:
    msg = "The two Strong acceptance ceremonies are missing"
    raise RuntimeError(msg)
validation_record = sign_request.validation_ids.sorted("id")[-1:]
required_evidence = {"authentication", "certificate", "consent", "validation"}
available_evidence = set(sign_request.evidence_ids.mapped("kind"))
event_head = sign_request.event_ids.verify_chain()
signers = sign_request.signer_ids.sorted(lambda signer: (signer.sequence, signer.id))
certificate_serials = ceremonies.mapped("certificate_serial")


def valid_ceremony(ceremony):
    binding_bytes = json.dumps(
        ceremony.binding_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    binding_digest = hashlib.sha256(binding_bytes).digest()
    expected_oidc_nonce = base64.urlsafe_b64encode(binding_digest).rstrip(b"=").decode()
    auth_time = ceremony.oidc_auth_time
    auth_upper_bound = ceremony.authorized_at + timedelta(seconds=60)
    certificate_lifetime = ceremony.certificate_not_after - ceremony.certificate_issued_at
    return {
        "completed": ceremony.state == "completed",
        "secret_cleared": not any(
            [
                ceremony.candidate_data,
                ceremony.candidate_layout,
                ceremony.data_to_sign,
                ceremony.dss_signing_context,
            ],
        ),
        "oidc_validated": ceremony.oidc_validation_result.get("status")
        == "valid_fresh_passkey",
        "fresh_passkey": ceremony.oidc_claims_summary.get("amr") == ["phr"],
        "fresh_auth_time": bool(
            auth_time
            and int(ceremony.create_date.timestamp())
            <= int(auth_time.timestamp())
            <= int(auth_upper_bound.timestamp())
        ),
        "binding_digest": bool(
            ceremony.challenge_sha256 == binding_digest.hex()
            and ceremony.oidc_nonce == expected_oidc_nonce
        ),
        "document_binding": bool(
            ceremony.binding_payload.get("document_sha256") == ceremony.document_sha256
            and ceremony.binding_payload.get("base_document_sha256")
            == ceremony.base_document_sha256
            and ceremony.binding_payload.get("csr_sha256") == ceremony.csr_sha256
            and ceremony.binding_payload.get("public_key_sha256")
            == ceremony.public_key_sha256
            and ceremony.binding_payload.get("consent_sha256")
            == ceremony.consent_sha256
        ),
        "short_lived_certificate": bool(
            timedelta(0) < certificate_lifetime <= timedelta(minutes=10, seconds=5)
            and ceremony.certificate_not_after > ceremony.authorized_at
        ),
        "signed_oidc_token": bool(ceremony.oidc_id_token),
    }


ceremony_checks = [valid_ceremony(ceremony) for ceremony in ceremonies]
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
    "both_archives_linked": bool(
        sign_request.archive_document_id and sign_request.archive_dossier_document_id,
    ),
    "two_signers_signed": len(signers) == 2
    and all(signer.state == "signed" for signer in signers),
    "ceremonies_completed": all(row["completed"] for row in ceremony_checks),
    "ceremony_secrets_cleared": all(row["secret_cleared"] for row in ceremony_checks),
    "all_oidc_validated": all(row["oidc_validated"] for row in ceremony_checks),
    "all_amr_is_fresh_passkey": all(row["fresh_passkey"] for row in ceremony_checks),
    "all_auth_times_are_fresh": all(row["fresh_auth_time"] for row in ceremony_checks),
    "all_binding_digests_match": all(row["binding_digest"] for row in ceremony_checks),
    "all_document_bindings_match": all(row["document_binding"] for row in ceremony_checks),
    "certificates_short_lived": all(
        row["short_lived_certificate"] for row in ceremony_checks
    ),
    "signed_oidc_tokens_preserved": all(
        row["signed_oidc_token"] for row in ceremony_checks
    ),
    "distinct_personal_certificates": len(certificate_serials) == 2
    and len(set(certificate_serials)) == 2
    and signers.mapped("certificate_serial") == certificate_serials,
    "incremental_revision_chain": ceremonies[1].base_document_sha256
    == signers[0].signed_document_sha256,
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
cross_validation = sign_request._sign_dss_client().cross_validate(final_pdf)
expected_signature_count = len(signers) + 1
if (
    validation.get("signatureCount") != expected_signature_count
    or cross_validation.get("status") != "valid"
    or cross_validation.get("signature_count") != expected_signature_count
):
    msg = "The final Strong PDF does not contain two personal revisions and one platform seal"
    raise RuntimeError(msg)

manifest_wrapper = json.loads(field_content(sign_request.evidence_manifest))
manifest_payload = json.loads(base64.b64decode(manifest_wrapper["manifest"]))
if manifest_payload.get("format") != "usl-sign-evidence-manifest-v2":
    msg = "The Strong acceptance dossier is not using evidence manifest v2"
    raise RuntimeError(msg)

payload = {
    "archive_document_id": sign_request.archive_document_id.id,
    "archive_status": sign_request.archive_status,
    "ceremony_ids": ceremonies.ids,
    "checks": checks,
    "evidence_kinds": sorted(available_evidence),
    "event_chain_head": event_head.event_hash,
    "final_sha256": hashlib.sha256(final_pdf).hexdigest(),
    "manifest_format": manifest_payload["format"],
    "personal_certificate_serials": certificate_serials,
    "request_id": sign_request.id,
    "signature_count": expected_signature_count,
    "signatures": [
        {
            "certificate_subject": row.get("certificate_subject"),
            "docmdp_ok": row.get("docmdp_ok"),
            "intact": row.get("intact"),
            "valid": row.get("valid"),
        }
        for row in cross_validation.get("signatures", [])
    ],
    "state": sign_request.state,
    "validation_engine": validation_record.engine,
    "validation_status": validation.get("status"),
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
