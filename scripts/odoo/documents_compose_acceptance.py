"""Real-service acceptance checks for the Paperless-backed Documents product.

Run this only against an isolated synthetic Compose project. Unlike the module
test suite, this script deliberately uses the configured Paperless service and
persists QA documents. The outer runner is responsible for an outage exercise
and for database/media backup and restore.
"""

import base64
import hashlib
import os
import time
import uuid

from odoo.exceptions import AccessError


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def wait_for_operation(operation, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation.poll()
        env.cr.commit()
        if operation.state in ("archived", "duplicate", "failed"):
            return operation
        time.sleep(2)
    raise AssertionError(
        f"Paperless operation {operation.id} did not finish within {timeout}s"
    )


admin = env.ref("base.user_admin")
documents = env["usl.document"].with_user(admin)
client = documents._paperless()
marker = os.environ.get("USL_DOCUMENTS_ACCEPTANCE_ID") or uuid.uuid4().hex[:12]
compatibility = client.compatibility()
check(compatibility["api_version"] == "10", "Paperless API v10 contract")
check(
    compatibility["server_version"].startswith("3."),
    "Paperless qualified 3.x server",
)
policy = client.ensure_fail_closed_ingestion_policy()
check(
    policy["workflow_name"] == client.FAIL_CLOSED_WORKFLOW_NAME,
    "supported Workflow API enforces fail-closed ingestion ownership",
)

bill = env["account.move"].search(
    [("ref", "=", "USL-DOCS-QA-BILL"), ("move_type", "=", "in_invoice")],
    limit=1,
)
partner = env["res.partner"].search(
    [("ref", "=", "USL-DOCS-QA-SUPPLIER")], limit=1
)
check(bool(bill and partner), "synthetic bill and partner fixtures")

text = (
    f"USL Paperless acceptance {marker}\n"
    "Supplier invoice OCR-only phrase: heliotrope compliance evidence.\n"
).encode()
checksum = hashlib.sha256(text).hexdigest()
attachment_count = env["ir.attachment"].search_count(
    [("res_model", "=", bill._name), ("res_id", "=", bill.id)]
)
result = documents.upload_from_odoo(
    f"acceptance-{marker}.txt",
    base64.b64encode(text).decode(),
    "text/plain",
    res_model=bill._name,
    res_id=bill.id,
)
check(result["state"] in ("processing", "duplicate"), "Odoo upload accepted safely")
if result["state"] == "processing":
    operation = env["usl.document.operation"].browse(result["operation_id"])
    wait_for_operation(operation)
    check(operation.state == "archived", "asynchronous archival completed")
    document = operation.document_id
else:
    document = documents.browse(result["document_id"])
check(document.checksum == checksum, "received-original checksum retained")
check(
    env["ir.attachment"].search_count(
        [("res_model", "=", bill._name), ("res_id", "=", bill.id)]
    )
    == attachment_count,
    "Paperless upload did not duplicate binary in Odoo",
)
check(
    bool(
        env["usl.document.link"].search(
            [
                ("document_id", "=", document.id),
                ("res_model", "=", bill._name),
                ("res_id", "=", bill.id),
            ],
            limit=1,
        )
    ),
    "durable vendor-bill relationship created",
)

search_deadline = time.monotonic() + 180
search_result = {}
while time.monotonic() < search_deadline:
    search_result = documents.workspace_data(
        query="heliotrope compliance evidence",
        company_id=bill.company_id.id,
    )
    if any(item["id"] == document.id for item in search_result["documents"]):
        break
    time.sleep(2)
check(
    any(item["id"] == document.id for item in search_result["documents"]),
    "Paperless full-text content search surfaced in Odoo",
)

duplicate = documents.upload_from_odoo(
    f"duplicate-{marker}.txt",
    base64.b64encode(text).decode(),
    "text/plain",
    res_model=bill._name,
    res_id=bill.id,
)
check(duplicate["state"] == "duplicate", "identical upload detected")
check(
    duplicate.get("document_id") == document.id,
    "identical upload reused stable Paperless root",
)

second_link = document.link_to_record(partner._name, partner.id)
check(bool(second_link), "one archive root linked to a second business record")
check(
    env["usl.document.link"].search_count(
        [("document_id", "=", document.id), ("active", "=", True)]
    )
    >= 2,
    "one archived binary has multiple Odoo relationships",
)
document.unlink_from_record(partner._name, partner.id)
check(
    not env["usl.document.link"].search(
        [
            ("document_id", "=", document.id),
            ("res_model", "=", partner._name),
            ("res_id", "=", partner.id),
            ("active", "=", True),
        ]
    )
    and bool(document.exists()),
    "unlink removed only one relationship, not the archive",
)

download, _headers = client.download(document.paperless_id, original=True)
check(hashlib.sha256(download).hexdigest() == checksum, "original download integrity")
preview, preview_headers = client.preview(document.paperless_id)
check(bool(preview), "Paperless preview generated")
check(
    "content-type" in {key.lower() for key in preview_headers},
    "preview content type returned",
)

# Exercise the deliberate-copy policy for an Odoo-generated accounting output.
# The Odoo report attachment remains operationally authoritative in Odoo while
# Paperless retains the immutable archival copy and matching checksum.
generated_pdf, _ = (
    env["ir.actions.report"]
    .with_context(force_report_rendering=True)
    ._render_qweb_pdf("account.account_invoices", res_ids=bill.id)
)
generated_pdf += f"\n% USL Documents acceptance {marker}\n".encode()
generated_checksum = hashlib.sha256(generated_pdf).hexdigest()
generated_attachment = env["ir.attachment"].create({
    "name": f"final-accounting-output-{marker}.pdf",
    "datas": base64.b64encode(generated_pdf),
    "mimetype": "application/pdf",
    "res_model": bill._name,
    "res_id": bill.id,
})
generated_result = bill.action_archive_attachment(
    generated_attachment.id, source="odoo_generated"
)
if generated_result["state"] == "processing":
    generated_operation = env["usl.document.operation"].browse(
        generated_result["operation_id"]
    )
    wait_for_operation(generated_operation)
    check(
        generated_operation.state == "archived",
        "Odoo-generated accounting output archived",
    )
    generated_document = generated_operation.document_id
else:
    generated_document = documents.browse(generated_result["document_id"])
check(bool(generated_attachment.exists()), "Odoo operational report copy retained")
check(
    generated_document.source == "odoo_generated"
    and generated_document.checksum == generated_checksum,
    "generated output copies are explicitly related by source and checksum",
)

replacement = (
    f"USL replacement {marker}\n"
    "This is a later immutable version; the received original remains retained.\n"
).encode()
replacement_result = document.upload_new_version(
    f"acceptance-{marker}-v2.txt",
    base64.b64encode(replacement).decode(),
    "text/plain",
    f"Acceptance {marker} v2",
)
check(replacement_result["state"] == "processing", "replacement queued as version")
replacement_operation = env["usl.document.operation"].browse(
    replacement_result["operation_id"]
)
wait_for_operation(replacement_operation)
check(replacement_operation.state == "archived", "replacement version archived")
document.invalidate_recordset()
check(len(document.version_ids) >= 2, "structured version history synchronized")
check(
    env["usl.document.link"].search_count(
        [("document_id", "=", document.id), ("active", "=", True)]
    )
    >= 1,
    "business relationship stable across version replacement",
)

external = (
    f"External Paperless ingestion {marker}\n"
    "Needs Odoo company and business classification.\n"
).encode()
external_task_id = client.upload_multipart(
    external,
    f"external-{marker}.txt",
    "text/plain",
    title=f"External ingestion {marker}",
)
external_deadline = time.monotonic() + 180
external_task = None
while time.monotonic() < external_deadline:
    external_task = client.task(external_task_id)
    if str((external_task or {}).get("status", "")).lower() in (
        "success",
        "successful",
        "failure",
        "failed",
    ):
        break
    time.sleep(2)
check(
    str((external_task or {}).get("status", "")).lower()
    in ("success", "successful"),
    "external Paperless ingestion completed",
)
sync_result = documents.sync_from_paperless(full=True)
check(sync_result["complete"], "external ingestion reconciliation completed")
external_document = documents.search(
    [("name", "=", f"External ingestion {marker}")], limit=1
)
check(bool(external_document), "external document discovered automatically")
check(
    external_document.review_state == "needs_attention"
    and external_document.source == "paperless",
    "unclassified external document surfaced in Needs attention",
)

legal = (
    f"USL legal archive acceptance {marker}\n"
    "Synthetic retained contract evidence for restore validation.\n"
).encode()
legal_task_id = client.upload_multipart(
    legal,
    f"legal-contract-{marker}.txt",
    "text/plain",
    title=f"Legal contract {marker}",
)
legal_deadline = time.monotonic() + 180
legal_task = None
while time.monotonic() < legal_deadline:
    legal_task = client.task(legal_task_id)
    if str((legal_task or {}).get("status", "")).lower() in (
        "success",
        "successful",
        "failure",
        "failed",
    ):
        break
    time.sleep(2)
check(
    str((legal_task or {}).get("status", "")).lower()
    in ("success", "successful"),
    "legal contract ingestion completed",
)
legal_paperless_id = int(legal_task["related_document_ids"][0])
contract_type = client.ensure_document_type("Contract")
client.update_document_metadata(
    legal_paperless_id,
    {"title": f"Legal contract {marker}", "document_type": contract_type["id"]},
)
documents.sync_from_paperless(full=True)
legal_document = documents.search(
    [("paperless_id", "=", legal_paperless_id)], limit=1
)
legal_document.write({
    "company_id": env.company.id,
    "review_state": "classified",
})
project = env["project.project"].search(
    [("name", "=", "Synthetic Documents Project")], limit=1
)
legal_document.link_to_record(project._name, project.id)
legal_document.action_sync_permissions()
check(
    legal_document.document_type_name == "Contract"
    and legal_document.source == "paperless",
    "legal archive metadata synchronized from Paperless",
)
check(
    any(
        item["id"] == legal_document.id
        for item in documents.workspace_data(workspace="contracts")["documents"]
    ),
    "legal contract surfaced in the native Contracts workspace",
)

restricted = env["res.users"].search(
    [("login", "=", "documents-restricted")], limit=1
)
check(bool(restricted), "restricted multi-company user fixture")
try:
    document.with_user(restricted).check_access("read")
except AccessError:
    pass
else:
    raise AssertionError("restricted user accessed another company's document")
check(True, "multi-company document isolation enforced")

detail = documents.document_detail(document.id)
check(bool(detail["links"]), "detail exposes linked Odoo records")
check(len(detail["versions"]) >= 2, "detail exposes structured file versions")
check(
    detail["permission_sync_state"] == "synchronized",
    "Paperless object permissions synchronized before direct access",
)
manifest = documents.integrity_manifest(f"acceptance-{marker}")
check(not manifest["checksum_mismatches"], "cross-system checksums reconcile")
check(not manifest["orphaned_relationship_ids"], "no orphaned Odoo relationships")
check(
    document.paperless_id not in manifest["missing_document_ids"],
    "stable root is present in both systems",
)

env.cr.commit()
print(
    "DOCUMENTS_COMPOSE_ACCEPTANCE_OK",
    {
        "marker": marker,
        "paperless_version": compatibility["server_version"],
        "document_id": document.id,
        "paperless_id": document.paperless_id,
        "version_count": len(document.version_ids),
        "relationship_count": env["usl.document.link"].search_count(
            [("document_id", "=", document.id), ("active", "=", True)]
        ),
        "integrity_ok": manifest["integrity_ok"],
    },
)
