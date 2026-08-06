"""Verify every completion gate after the browser Strong ceremony."""

import base64
import hashlib
import json
import os
import time


request_id = int(os.environ.get("USL_SIGN_ACCEPTANCE_REQUEST_ID", "0"))
if request_id <= 0:
    raise RuntimeError("A Strong acceptance request ID is required")
sign_request = env["sign.oca.request"].browse(request_id).exists()
if not sign_request:
    raise RuntimeError("The Strong acceptance request no longer exists")

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
validation_record = sign_request.validation_ids.sorted("id")[-1:]
required_evidence = {"authentication", "certificate", "consent", "validation"}
available_evidence = set(sign_request.evidence_ids.mapped("kind"))
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
        and validation_record.report_evidence_id
    ),
    "evidence_complete": sign_request.evidence_status == "complete",
    "archive_confirmed": sign_request.archive_status == "archived",
    "archive_linked": bool(sign_request.archive_document_id),
    "signer_signed": all(signer.state == "signed" for signer in sign_request.signer_ids),
    "ceremony_completed": bool(ceremony and ceremony.state == "completed"),
    "ceremony_secret_cleared": bool(
        ceremony and not ceremony.data_to_sign and not ceremony.dss_signing_context
    ),
    "oidc_validated": bool(
        ceremony
        and ceremony.oidc_validation_result.get("status") == "valid_fresh_passkey"
    ),
    "required_evidence": required_evidence.issubset(available_evidence),
}
for name, passed in checks.items():
    if not passed:
        failures.append(name)
if failures:
    raise RuntimeError("Strong acceptance checks failed: " + ", ".join(failures))

final_pdf = base64.b64decode(sign_request.final_data)
validation = sign_request._sign_dss_client().validate(
    final_pdf,
    expected_level="strong_personal",
)
if validation.get("status") != "valid" or validation.get("achievedTrust") != "strong_personal":
    raise RuntimeError("Independent final DSS validation rejected the Strong PDF")

payload = {
    "archive_document_id": sign_request.archive_document_id.id,
    "archive_status": sign_request.archive_status,
    "ceremony_id": ceremony.id,
    "checks": checks,
    "evidence_kinds": sorted(available_evidence),
    "final_sha256": hashlib.sha256(final_pdf).hexdigest(),
    "request_id": sign_request.id,
    "state": sign_request.state,
    "validation_engine": validation_record.engine,
    "validation_status": validation.get("status"),
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
