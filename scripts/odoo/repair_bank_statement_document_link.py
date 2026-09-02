"""Repair one accepted bank statement whose exact PDF has competing links.

Run through ``odoo shell`` with the five ``USL_BANK_REPAIR_*`` environment
variables set. The script is assertion-heavy and idempotent; it never deletes
a Documents record or a retained attachment.
"""

import os


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


statement_id = int(required("USL_BANK_REPAIR_STATEMENT_ID"))
source_file_id = int(required("USL_BANK_REPAIR_SOURCE_FILE_ID"))
document_id = int(required("USL_BANK_REPAIR_DOCUMENT_ID"))
version_id = required("USL_BANK_REPAIR_VERSION_ID")
expected_sha256 = required("USL_BANK_REPAIR_SHA256")

statement = env["account.bank.statement"].sudo().browse(statement_id).exists()
source_file = env["account.bank.ingestion.file"].sudo().browse(source_file_id).exists()
document = env["usl.document"].sudo().browse(document_id).exists()
if not statement or not source_file or not document:
    raise RuntimeError("The statement, retained source file, or document is missing")
if statement.accepted_evidence_id != source_file:
    raise RuntimeError("The retained source file is not the accepted statement evidence")
if source_file.sha256 != expected_sha256:
    raise RuntimeError("The retained source checksum differs from the approved repair")

version = document.version_ids.filtered(
    lambda item: (
        item.paperless_version_id == version_id
        and item.checksum == expected_sha256
    ),
)[:1]
if not version:
    raise RuntimeError("The selected Documents version does not match the retained PDF")

Link = env["usl.document.link"].sudo()
correct_link = Link.search(
    [
        ("document_id", "=", document.id),
        ("res_model", "=", statement._name),
        ("res_id", "=", statement.id),
        ("active", "=", True),
    ],
    limit=1,
)
if not correct_link or correct_link.version_id != version_id:
    raise RuntimeError("The exact Documents version is not actively linked to the statement")

competing_links = Link.search(
    [
        ("res_model", "=", statement._name),
        ("res_id", "=", statement.id),
        ("active", "=", True),
        ("id", "!=", correct_link.id),
    ],
)
if competing_links:
    competing_links.write({"active": False})

source_file._pin_paperless_version(document)
statement.message_post(
    body=(
        "Documents evidence repaired: the exact retained statement PDF was "
        f"pinned to Documents version {version_id}; {len(competing_links)} "
        "conflicting link(s) were deactivated without deleting their documents."
    ),
)
if source_file._paperless_integrity_error():
    raise RuntimeError(source_file._paperless_integrity_error())

env.cr.commit()
print(
    "bank_statement_document_repair=passed "
    f"statement={statement.id} source_file={source_file.id} "
    f"document={document.id} version={version_id} "
    f"deactivated_links={len(competing_links)}"
)
