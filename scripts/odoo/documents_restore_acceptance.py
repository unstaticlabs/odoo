"""Validate a restored Odoo/Paperless pair against real binaries and policy."""

import hashlib
import json
import os

from odoo.exceptions import AccessError


admin = env.ref("base.user_admin")
documents = env["usl.document"].with_user(admin)
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
try:
    evidence.with_user(restricted).check_access("read")
except AccessError:
    pass
else:
    raise AssertionError("restored multi-company restriction was lost")

preview, _headers = evidence._paperless().preview(evidence.paperless_id)
if not preview:
    raise AssertionError("restored Paperless preview is unavailable")

params = env["ir.config_parameter"].sudo()
params.set_str("usl_documents.backup_completion_status", "restored_and_verified")
params.set_str(
    "usl_documents.last_restore_test",
    os.environ.get("USL_BACKUP_ID", "synthetic-restore"),
)
manifest = documents.integrity_manifest(os.environ.get("USL_BACKUP_ID"))
if not manifest["integrity_ok"]:
    raise AssertionError(json.dumps(manifest, sort_keys=True))
env.cr.commit()
print(
    "DOCUMENTS_RESTORE_ACCEPTANCE_OK",
    {
        "documents": actual_documents,
        "relationships": actual_relationships,
        "paperless_id": evidence.paperless_id,
        "checksum": evidence.checksum,
        "preview_bytes": len(preview),
        "integrity_ok": manifest["integrity_ok"],
    },
)
