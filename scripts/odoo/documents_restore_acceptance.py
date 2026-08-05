"""Validate a restored Odoo/Paperless pair against real binaries and policy."""

import hashlib
import json
import os
from decimal import Decimal

from odoo.exceptions import AccessError


manager_group = env.ref("usl_documents.group_documents_manager")
manager = env["res.users"].sudo().search(
    [("active", "=", True), ("all_group_ids", "in", manager_group.id)],
    order="id",
    limit=1,
)
if not manager:
    raise AssertionError("restored database has no active Documents administrator")
documents = env["usl.document"].with_user(manager)
expected_documents = int(os.environ["USL_EXPECTED_DOCUMENT_COUNT"])
expected_relationships = int(os.environ["USL_EXPECTED_RELATIONSHIP_COUNT"])
actual_documents = documents.search_count([])
actual_relationships = env["usl.document.link"].search_count([("active", "=", True)])
if actual_documents != expected_documents:
    raise AssertionError(
        f"restored document count {actual_documents} != {expected_documents}"
    )
if actual_relationships != expected_relationships:
    raise AssertionError(
        f"restored relationship count {actual_relationships} != {expected_relationships}"
    )


def assert_sql_control(label, query, expected_environment, *, decimal=False):
    env.cr.execute(query)
    actual = env.cr.fetchone()[0]
    expected_raw = os.environ[expected_environment]
    if decimal:
        actual = Decimal(str(actual))
        expected = Decimal(expected_raw)
    else:
        expected = int(expected_raw)
    if actual != expected:
        raise AssertionError(f"restored {label} {actual} != {expected}")
    return actual


actual_companies = assert_sql_control(
    "company count",
    "SELECT count(*) FROM res_company",
    "USL_EXPECTED_COMPANY_COUNT",
)
actual_internal_users = assert_sql_control(
    "active internal user count",
    "SELECT count(*) FROM res_users WHERE active AND NOT share",
    "USL_EXPECTED_INTERNAL_USER_COUNT",
)
actual_moves = assert_sql_control(
    "move count",
    "SELECT count(*) FROM account_move",
    "USL_EXPECTED_MOVE_COUNT",
)
actual_move_lines = assert_sql_control(
    "move-line count",
    "SELECT count(*) FROM account_move_line",
    "USL_EXPECTED_MOVE_LINE_COUNT",
)
actual_posted_debit = assert_sql_control(
    "posted debit",
    """
        SELECT COALESCE(sum(line.debit), 0)
          FROM account_move_line line
          JOIN account_move move ON move.id = line.move_id
         WHERE move.state = 'posted'
    """,
    "USL_EXPECTED_POSTED_DEBIT",
    decimal=True,
)
actual_posted_credit = assert_sql_control(
    "posted credit",
    """
        SELECT COALESCE(sum(line.credit), 0)
          FROM account_move_line line
          JOIN account_move move ON move.id = line.move_id
         WHERE move.state = 'posted'
    """,
    "USL_EXPECTED_POSTED_CREDIT",
    decimal=True,
)
if actual_posted_debit != actual_posted_credit:
    raise AssertionError("restored posted accounting is not balanced")

params = env["ir.config_parameter"].sudo()
for environment_name, parameter_name in (
    ("USL_EXPECTED_RELEASE_COMMIT", "usl.release.commit"),
    ("USL_EXPECTED_RELEASE_IMAGE", "usl.release.image"),
):
    expected = os.environ.get(environment_name)
    if expected and params.get_str(parameter_name) != expected:
        raise AssertionError(f"restored {parameter_name} does not match the backup")
for live_guard in ("USL_EINVOICE_LIVE_ENABLED", "USL_EREPORTING_LIVE_ENABLED"):
    if os.environ.get(live_guard) != "0":
        raise AssertionError(f"restored environment does not disable {live_guard}")

evidence = documents.search(
    [
        ("checksum", "!=", False),
        ("link_ids.res_model", "=", "account.move"),
        ("link_ids.active", "=", True),
    ],
    order="id desc",
    limit=1,
)
if not evidence:
    raise AssertionError("restored archive has no representative accounting evidence")
content, _headers = evidence._paperless().download(
    evidence.paperless_id, original=True
)
restored_checksum = hashlib.sha256(content).hexdigest()
if restored_checksum != evidence.checksum:
    raise AssertionError(
        "restored current-original checksum mismatch "
        f"for Paperless {evidence.paperless_id}: "
        f"download={restored_checksum}, cache={evidence.checksum}"
    )
received_version = evidence.version_ids.filtered("is_received_original")[:1]
if not received_version:
    raise AssertionError("restored document has no received-original version")
received_content, _headers = evidence._paperless().download(
    evidence.paperless_id,
    version_id=received_version.paperless_version_id,
    original=True,
)
if hashlib.sha256(received_content).hexdigest() != received_version.checksum:
    raise AssertionError("restored received-original version checksum mismatch")

bill_link = evidence.link_ids.filtered(
    lambda item: item.active and item.res_model == "account.move"
)[:1]
if not bill_link or not env["account.move"].browse(bill_link.res_id).exists():
    raise AssertionError("restored accounting relationship does not resolve")

restricted = env["res.users"].search(
    [("login", "=", "documents-restricted")], limit=1
)
if restricted:
    try:
        evidence.with_user(restricted).check_access("read")
    except AccessError:
        pass
    else:
        raise AssertionError("restored multi-company restriction was lost")
elif os.environ.get("USL_RECOVERY_REQUIRE_RESTRICTED_FIXTURE", "1") == "1":
    raise AssertionError("restored restricted-user fixture is missing")

preview, _headers = evidence._paperless().preview(evidence.paperless_id)
if not preview:
    raise AssertionError("restored Paperless preview is unavailable")

source_integrity_ok = os.environ["USL_EXPECTED_SOURCE_INTEGRITY_OK"] == "1"
params.set_str(
    "usl_documents.backup_completion_status",
    (
        "restored_and_verified"
        if source_integrity_ok
        else "restored_with_known_permission_checkpoint"
    ),
)
params.set_str(
    "usl_documents.last_restore_test",
    os.environ.get("USL_BACKUP_ID", "synthetic-restore"),
)
manifest = documents.integrity_manifest(os.environ.get("USL_BACKUP_ID"))
expected_permission_failures = {
    int(value)
    for value in os.environ.get("USL_EXPECTED_PERMISSION_SYNC_FAILURES", "").split(",")
    if value
}
actual_permission_failures = set(manifest["permission_sync_failures"])
if actual_permission_failures != expected_permission_failures:
    raise AssertionError(
        "restored Paperless permission failures differ from the recovery point: "
        f"actual={sorted(actual_permission_failures)}, "
        f"expected={sorted(expected_permission_failures)}"
    )
for failure_key in (
    "missing_document_ids",
    "orphaned_relationship_ids",
    "checksum_mismatches",
    "unmirrored_paperless_ids",
):
    if manifest[failure_key]:
        raise AssertionError(
            f"restored archive has unexpected {failure_key}: {manifest[failure_key]}"
        )
if source_integrity_ok and not manifest["integrity_ok"]:
    raise AssertionError(json.dumps(manifest, sort_keys=True))
if not source_integrity_ok:
    if os.environ.get("USL_RECOVERY_ALLOW_SOURCE_INTEGRITY_FAILURE") != "1":
        raise AssertionError("source recovery point did not have clean integrity")
    if not expected_permission_failures:
        raise AssertionError("source integrity failure is not an identity checkpoint")
env.cr.commit()
print(
    "DOCUMENTS_RESTORE_ACCEPTANCE_OK",
    {
        "documents": actual_documents,
        "relationships": actual_relationships,
        "companies": actual_companies,
        "active_internal_users": actual_internal_users,
        "moves": actual_moves,
        "move_lines": actual_move_lines,
        "posted_debit": str(actual_posted_debit),
        "posted_credit": str(actual_posted_credit),
        "paperless_id": evidence.paperless_id,
        "checksum": evidence.checksum,
        "preview_bytes": len(preview),
        "integrity_ok": manifest["integrity_ok"],
        "source_integrity_ok": source_integrity_ok,
        "preserved_permission_failures": len(actual_permission_failures),
    },
)
