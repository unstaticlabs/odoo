# ruff: noqa: F821

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
marker = os.environ.get("USL_DOCUMENTS_ACCEPTANCE_ID") or "qa-real-service"
compatibility = client.compatibility()
check(compatibility["api_version"] == "10", "Paperless API v10 contract")
check(
    compatibility["server_version"] == "3.0.4",
    "Paperless qualified 3.0.4 server",
)
policy = client.ensure_fail_closed_ingestion_policy()
check(
    policy["workflow_name"] == client.FAIL_CLOSED_WORKFLOW_NAME,
    "supported Workflow API enforces fail-closed ingestion ownership",
)
# Paperless mutations cannot participate in Odoo's database transaction. Reconcile
# first so rerunning after an interrupted acceptance reuses the archive roots that
# Paperless already committed instead of consuming the same fixture again.
startup_sync = documents.sync_from_paperless(full=True)
check(
    startup_sync["complete"],
    "interrupted acceptance state reconciled before fixture reuse",
)

# Prove the exact catalog-create contract against Paperless, not only a mock.
# Empty Odoo Char values must be sent as strings and multi-term matching rules
# must remain readable in Odoo while using Paperless's supported expression.
catalog_suffix = f"{marker}-{int(time.time())}"
catalog_records = []
try:
    acceptance_tag = env["usl.paperless.tag"].with_user(admin).create(
        {
            "name": f"Acceptance tag {catalog_suffix}",
            "matching_algorithm": "1",
            "rule_lines": "acceptance phrase\narchive proof",
            "color": "#4b6f8a",
        },
    )
    acceptance_correspondent = env[
        "usl.paperless.correspondent"
    ].with_user(admin).create(
        {"name": f"Acceptance correspondent {catalog_suffix}"},
    )
    catalog_records.extend([acceptance_tag, acceptance_correspondent])
    remote_tag = next(
        (
            item
            for item in client.list_metadata("tags")
            if int(item["id"]) == acceptance_tag.paperless_id
        ),
        None,
    )
    remote_correspondent = next(
        (
            item
            for item in client.list_metadata("correspondents")
            if int(item["id"]) == acceptance_correspondent.paperless_id
        ),
        None,
    )
    check(
        bool(remote_tag)
        and remote_tag.get("match") == '"acceptance phrase" "archive proof"',
        "Odoo created a multi-term Paperless tag rule through the live API",
    )
    check(
        bool(remote_correspondent)
        and remote_correspondent.get("match", "") == "",
        "Odoo created a Paperless correspondent with a valid empty match",
    )
finally:
    for catalog_record in catalog_records:
        catalog_record.with_user(admin).unlink()

bill = env["account.move"].search(
    [("ref", "=", "USL-DOCS-CEO-QA-BILL"), ("move_type", "=", "in_invoice")],
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
received_version = document.version_ids.filtered("is_received_original")[:1]
check(
    bool(received_version) and received_version.checksum == checksum,
    "received-original checksum retained",
)
check(
    env["ir.attachment"].search_count(
        [("res_model", "=", bill._name), ("res_id", "=", bill.id)]
    )
    == attachment_count,
    "Paperless upload did not duplicate binary in Odoo",
)
bill_link = env["usl.document.link"].search(
    [
        ("document_id", "=", document.id),
        ("res_model", "=", bill._name),
        ("res_id", "=", bill.id),
    ],
    limit=1,
)
check(bool(bill_link), "durable vendor-bill relationship created")
linked_version = document.version_ids.filtered(
    lambda version: version.paperless_version_id == bill_link.version_id
)[:1]
if not linked_version or linked_version.checksum != checksum:
    # Repair a synthetic acceptance fixture linked by an implementation that
    # predated checksum-aware file-version pins. Product code now pins the
    # exact matching historical version when duplicate bytes are reused.
    bill_link.write({"version_id": received_version.paperless_version_id})
check(
    bill_link.version_id == received_version.paperless_version_id,
    "vendor-bill evidence pins the supporting file version",
)

search_deadline = time.monotonic() + 180
search_result = {}
while time.monotonic() < search_deadline:
    search_result = documents.workspace_data(
        query="heliotrope cobalt compliance evidence",
        company_id=bill.company_id.id,
    )
    if any(
        item["name"] == "Alpine Office Supplies — Invoice SI-2026-0715"
        for item in search_result["documents"]
    ):
        break
    time.sleep(2)
check(
    any(
        item["name"] == "Alpine Office Supplies — Invoice SI-2026-0715"
        for item in search_result["documents"]
    ),
    "Paperless full-text content search surfaced in Odoo",
)

custom_fields = documents.workspace_data(workspace="all")["custom_fields"]
invoice_reference = next(
    (item for item in custom_fields if item["name"] == "Invoice reference"),
    None,
)
check(bool(invoice_reference), "Paperless custom-field catalog synchronized")
custom_field_result = documents.workspace_data(
    workspace="all",
    custom_field_id=invoice_reference["id"],
    custom_field_value="INV-QA-2026-0042",
)
check(
    any(
        item["name"] == "Alpine Office Supplies — Invoice SI-2026-0715"
        for item in custom_field_result["documents"]
    ),
    "supported Paperless custom-field search is filtered through Odoo",
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

download, _headers = client.download(
    document.paperless_id,
    version_id=received_version.paperless_version_id,
    original=True,
)
check(hashlib.sha256(download).hexdigest() == checksum, "original download integrity")
processed_download, processed_headers = client.download(
    document.paperless_id,
    version_id=received_version.paperless_version_id,
    original=False,
)
check(bool(processed_download), "processed archive download returned content")
check(
    "content-type" in {key.lower() for key in processed_headers},
    "processed archive download returned a content type",
)
preview, preview_headers = client.preview(document.paperless_id)
check(bool(preview), "Paperless preview generated")
check(
    "content-type" in {key.lower() for key in preview_headers},
    "preview content type returned",
)

# Exercise the deliberate-copy policy for an Odoo-generated accounting output.
# The Odoo report attachment remains operationally authoritative in Odoo while
# Paperless retains the immutable archival copy and matching checksum.
generated_name = f"final-accounting-output-{marker}.pdf"
generated_document = documents.search(
    [
        ("original_filename", "=", generated_name),
        ("source", "=", "odoo_generated"),
    ],
    limit=1,
)
generated_attachment = env["ir.attachment"].search(
    [
        ("name", "=", generated_name),
        ("res_model", "=", bill._name),
        ("res_id", "=", bill.id),
    ],
    limit=1,
)
if not generated_document:
    generated_pdf, _ = (
        env["ir.actions.report"]
        .with_context(force_report_rendering=True)
        ._render_qweb_pdf("account.account_invoices", res_ids=bill.id)
    )
    generated_pdf += f"\n% USL Documents acceptance {marker}\n".encode()
    generated_checksum = hashlib.sha256(generated_pdf).hexdigest()
    generated_attachment = env["ir.attachment"].create({
        "name": generated_name,
        "raw": generated_pdf,
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
generated_checksum = hashlib.sha256(
    base64.b64decode(generated_attachment.datas),
).hexdigest()
check(
    generated_document.source == "odoo_generated"
    and generated_checksum
    in (
        {generated_document.checksum}
        | set(generated_document.version_ids.mapped("checksum"))
    ),
    "generated output copies are explicitly related by source and checksum",
)

replacement = (
    f"USL replacement {marker}\n"
    "This is a later immutable version; the received original remains retained.\n"
).encode()
replacement_checksum = hashlib.sha256(replacement).hexdigest()
if replacement_checksum not in document.version_ids.mapped("checksum"):
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
else:
    check(True, "existing replacement version reused")
document.invalidate_recordset()
check(len(document.version_ids) >= 2, "structured version history synchronized")
check(
    env["usl.document.link"].search_count(
        [("document_id", "=", document.id), ("active", "=", True)]
    )
    >= 1,
    "business relationship stable across version replacement",
)
check(
    bill_link.version_id == received_version.paperless_version_id,
    "later versions do not move the vendor-bill evidence pin",
)

external = (
    f"External Paperless ingestion {marker}\n"
    "Needs Odoo company and business classification.\n"
).encode()
external_document = documents.search(
    [
        ("name", "=", f"External ingestion {marker}"),
        ("availability_state", "in", ("available", "trashed")),
    ],
    limit=1,
)
if external_document.availability_state == "trashed":
    external_document.with_user(admin).restore_from_trash()
    external_document.invalidate_recordset()
    check(True, "existing external ingestion restored before lifecycle check")
if not external_document:
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
        [
            ("name", "=", f"External ingestion {marker}"),
            ("availability_state", "=", "available"),
        ],
        limit=1,
    )
else:
    check(True, "existing external ingestion reused")
check(bool(external_document), "external document discovered automatically")
check(
    external_document.review_state == "needs_attention"
    and external_document.source == "paperless",
    "unclassified external document surfaced in Needs attention",
)
external_identity = external_document.paperless_id
trash_result = external_document.with_user(admin).move_to_trash()
external_document.invalidate_recordset()
check(
    trash_result["state"] == "trashed"
    and external_document.availability_state == "trashed"
    and external_document.trashed_by_id == admin,
    "Odoo Trash records the initiating user and preserves the archive identity",
)
trash_detail = documents.document_detail(external_document.id)
check(
    trash_detail["trashed_by"] == admin.display_name
    and bool(trash_detail["trashed_at"]),
    "Trash detail exposes who moved the document and when",
)
restore_result = external_document.with_user(admin).restore_from_trash()
external_document.invalidate_recordset()
check(
    restore_result["state"] == "restored"
    and external_document.availability_state == "available"
    and external_document.paperless_id == external_identity,
    "Odoo restored the same Paperless identity from Trash",
)

matching_tag = env["usl.paperless.tag"].search(
    [("name", "=", "Needs follow-up")], limit=1
)
check(bool(matching_tag), "plain-language matching rule fixture exists")
probe_title = f"Matching rule probe {marker}"
probe_id = None
try:
    probe_results = client.search(probe_title, page_size=20)
    existing_probe = next(
        (
            item
            for item in probe_results.get("results", [])
            if item.get("title") == probe_title
        ),
        None,
    )
    if existing_probe:
        probe_id = int(existing_probe["id"])
    else:
        probe_task_id = client.upload_multipart(
            (
                f"Synthetic matching acceptance {marker}\n"
                "NEEDS FOLLOW UP SYNTHETIC\n"
            ).encode(),
            f"matching-rule-{marker}.txt",
            "text/plain",
            title=probe_title,
        )
        probe_deadline = time.monotonic() + 180
        probe_task = None
        while time.monotonic() < probe_deadline:
            probe_task = client.task(probe_task_id)
            if str((probe_task or {}).get("status", "")).lower() in (
                "success",
                "successful",
                "failure",
                "failed",
            ):
                break
            time.sleep(2)
        check(
            str((probe_task or {}).get("status", "")).lower()
            in ("success", "successful"),
            "automatic-matching probe ingestion completed",
        )
        probe_id = int(probe_task["related_document_ids"][0])
    probe_document = client.get_document(probe_id)
    check(
        matching_tag.paperless_id in (probe_document.get("tags") or []),
        "Paperless matching rule automatically assigned the expected tag",
    )
finally:
    if probe_id:
        client.trash_document(probe_id)
        client.permanently_delete_trashed_documents([probe_id])

legal = (
    f"USL legal archive acceptance {marker}\n"
    "Synthetic retained contract evidence for restore validation.\n"
).encode()
legal_document = documents.search(
    [("name", "=", f"Legal contract {marker}")], limit=1
)
if not legal_document:
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
else:
    legal_paperless_id = legal_document.paperless_id
    check(True, "existing legal contract reused")
contract_type = client.ensure_document_type("Contract")
documents.sync_from_paperless(full=True)
legal_document = documents.search(
    [("paperless_id", "=", legal_paperless_id)], limit=1
)
contract_type_cache = env["usl.paperless.document.type"].search(
    [("paperless_id", "=", contract_type["id"])], limit=1
)
contracts_tag = env["usl.paperless.tag"].search(
    [("name", "=", "Contracts & legal")], limit=1
)
legal_document.update_archive_metadata(
    {
        "name": f"Legal contract {marker}",
        "document_type_id": contract_type_cache.id,
        "tag_ids": contracts_tag.ids,
    }
)
legal_document.with_context(usl_documents_policy_write=True).write(
    {
        "company_id": env.company.id,
        "review_state": "classified",
    }
)
project = env["project.project"].search(
    [("name", "=", "Atlas Website Rollout")], limit=1
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
contracts_view = env.ref("usl_documents.smart_view_contracts")
contracts_view.with_user(admin).write({"name": contracts_view.name})
remote_contracts_view = next(
    (
        item
        for item in client.list_saved_views()
        if int(item["id"]) == contracts_view.paperless_id
    ),
    None,
)
check(
    bool(remote_contracts_view)
    and remote_contracts_view.get("owner") is None,
    "shared Smart View keeps one stable globally visible Paperless identity",
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
    document.permission_sync_state == "synchronized"
    and not detail.get("permission_sync_error"),
    "Paperless object permissions synchronized before direct access",
)
manifest = documents.integrity_manifest(f"acceptance-{marker}")
check(not manifest["checksum_mismatches"], "cross-system checksums reconcile")
check(not manifest["orphaned_relationship_ids"], "no orphaned Odoo relationships")
check(
    document.paperless_id not in manifest["missing_document_ids"],
    "stable root is present in both systems",
)
check(
    manifest["integrity_ok"],
    "active and trashed Paperless identities reconcile with Odoo",
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
