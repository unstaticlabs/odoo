"""Archive a small, explicitly selected PDF pilot from an Odoo source dump.

Run only through ``scripts/documents-stack ... source-pilot``.  The source dump,
manifest, and operator selection are read-only mounts.  No source binary is
copied into the delivered Odoo filestore: Odoo sends the verified bytes to the
supported Paperless API, keeps its business relationship, and records the
legacy source identity in Paperless custom fields.
"""

import base64
import csv
import hashlib
import json
import time
from pathlib import Path


SOURCE_ROOT = Path("/mnt/usl-source").resolve()
SOURCE_FILESTORE = (SOURCE_ROOT / "filestore").resolve()
SOURCE_DUMP = SOURCE_ROOT / "dump.sql"
SOURCE_MANIFEST = Path("/mnt/usl-source-manifest.csv")
SOURCE_SELECTION = Path("/mnt/usl-source-selection.json")
MAX_PILOT_DOCUMENTS = 10


def digest(path, algorithm):
    value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fail(message):
    raise RuntimeError(f"Documents source pilot: {message}")


def wait_for_operation(operation, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation.poll()
        env.cr.commit()
        operation.invalidate_recordset()
        if operation.state in ("archived", "duplicate", "failed"):
            return operation
        time.sleep(2)
    fail(f"Paperless operation {operation.id} timed out")


def ensure_metadata(model_name, name, extra=None):
    if not name:
        return env[model_name]
    model = env[model_name].with_user(admin)
    record = model.search(
        [("name", "=ilike", name), ("active", "=", True)],
        limit=1,
    )
    if record:
        return record
    return model.create(
        {
            "name": name,
            "matching_algorithm": "0",
            "is_insensitive": True,
            **(extra or {}),
        },
    )


def ensure_custom_field(name):
    existing = next(
        (item for item in client.list_custom_fields() if item.get("name") == name),
        None,
    )
    return existing or client.create_custom_field(
        {"name": name, "data_type": "string"},
    )


def response_header(headers, name):
    wanted = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == wanted),
        None,
    )


if not SOURCE_DUMP.is_file() or not SOURCE_FILESTORE.is_dir():
    fail("the mounted source dump or filestore is missing")
if not SOURCE_MANIFEST.is_file() or not SOURCE_SELECTION.is_file():
    fail("the private manifest or selection file is missing")

selection = json.loads(SOURCE_SELECTION.read_text(encoding="utf-8"))
if selection.get("schema") != "usl-documents-source-pilot-v1":
    fail("unsupported selection schema")
specs = selection.get("documents") or []
if not specs or len(specs) > MAX_PILOT_DOCUMENTS:
    fail(f"select between 1 and {MAX_PILOT_DOCUMENTS} documents")
attachment_ids = [int(spec.get("attachment_id") or 0) for spec in specs]
if 0 in attachment_ids or len(set(attachment_ids)) != len(attachment_ids):
    fail("attachment IDs must be positive and unique")

expected_dump_sha256 = selection.get("source_dump_sha256")
if not expected_dump_sha256 or digest(SOURCE_DUMP, "sha256") != expected_dump_sha256:
    fail("the selected source dump does not match the approved SHA-256")

with SOURCE_MANIFEST.open(newline="", encoding="utf-8") as handle:
    manifest = {
        int(row["id"]): row
        for row in csv.DictReader(handle)
        if int(row["id"]) in attachment_ids
    }
if set(manifest) != set(attachment_ids):
    fail("one or more selected attachment IDs are absent from the manifest")

admin = env.ref("base.user_admin")
documents = env["usl.document"].with_user(admin)
client = documents._paperless()
compatibility = client.compatibility()
if compatibility["api_version"] != "10":
    fail("Paperless API v10 is required")

source_item_field = ensure_custom_field("Legacy Odoo source")
source_attachment_field = ensure_custom_field("Legacy Odoo attachment")
documents._sync_metadata_catalogs(client)

attachment_count_before = env["ir.attachment"].sudo().search_count([])
results = []
for spec in specs:
    attachment_id = int(spec["attachment_id"])
    row = manifest[attachment_id]
    if row.get("mimetype") != "application/pdf":
        fail(f"attachment {attachment_id} is not declared as a PDF")
    if spec.get("source_model") and row.get("res_model") != spec["source_model"]:
        fail(f"attachment {attachment_id} source model changed")
    if spec.get("source_record_id") and int(row.get("res_id") or 0) != int(
        spec["source_record_id"],
    ):
        fail(f"attachment {attachment_id} source record changed")

    source_file = (SOURCE_FILESTORE / row["store_fname"]).resolve()
    if SOURCE_FILESTORE not in source_file.parents or not source_file.is_file():
        fail(f"attachment {attachment_id} has an unsafe or missing filestore path")
    content = source_file.read_bytes()
    if not content.startswith(b"%PDF-"):
        fail(f"attachment {attachment_id} does not contain a PDF binary")
    if len(content) != int(row["file_size"] or 0):
        fail(f"attachment {attachment_id} size does not match the source manifest")
    if hashlib.sha1(content).hexdigest() != row["checksum"]:
        fail(f"attachment {attachment_id} SHA-1 does not match the source manifest")
    source_sha256 = hashlib.sha256(content).hexdigest()

    target = env.ref(spec.get("target_xmlid", ""), raise_if_not_found=False)
    if not target or target._name not in env["usl.document.link"]._allowed_models():
        fail(f"attachment {attachment_id} has no supported target record")
    target.check_access("read")
    company = target if target._name == "res.company" else target.company_id
    confidentiality = spec.get("confidentiality", "internal")
    if confidentiality not in dict(documents._fields["confidentiality"].selection):
        fail(f"attachment {attachment_id} has an invalid confidentiality policy")

    upload = documents.upload_from_odoo(
        row["name"],
        base64.b64encode(content).decode(),
        "application/pdf",
        res_model=target._name,
        res_id=target.id,
        company_id=company.id,
        confidentiality=confidentiality,
        source="odoo_attachment",
    )
    if upload["state"] == "processing":
        operation = wait_for_operation(
            env["usl.document.operation"].browse(upload["operation_id"]),
        )
        if operation.state != "archived" or not operation.document_id:
            fail(
                f"attachment {attachment_id} was not archived: "
                f"{operation.error_message or operation.state}",
            )
        document = operation.document_id.with_user(admin)
        ingestion_state = "archived"
    elif upload["state"] == "duplicate" and upload.get("document_id"):
        document = documents.browse(upload["document_id"])
        ingestion_state = "reused"
    else:
        fail(f"attachment {attachment_id} returned an unsupported upload state")

    tag_ids = []
    for tag in spec.get("tags") or []:
        tag_values = tag if isinstance(tag, dict) else {"name": tag}
        tag_ids.append(
            ensure_metadata(
                "usl.paperless.tag",
                tag_values["name"],
                {"color": tag_values.get("color", "#a6cee3")},
            ).id,
        )
    correspondent = ensure_metadata(
        "usl.paperless.correspondent",
        spec.get("correspondent"),
    )
    document_type = ensure_metadata(
        "usl.paperless.document.type",
        spec.get("document_type"),
    )
    metadata = {
        "name": spec.get("title") or row["name"],
        "tag_ids": tag_ids,
        "correspondent_id": correspondent.id,
        "document_type_id": document_type.id,
    }
    if "document_date" in spec:
        metadata["document_date"] = spec.get("document_date") or False
    document.update_archive_metadata(metadata)
    document.sudo().with_context(usl_documents_policy_write=True).write(
        {
            "company_id": company.id,
            "confidentiality": confidentiality,
            "accounting_evidence": bool(spec.get("accounting_evidence")),
            "review_state": spec.get("review_state", "reviewed"),
        },
    )

    remote = client.get_document(document.paperless_id)
    custom_fields = {
        int(item["field"]): item
        for item in (remote.get("custom_fields") or [])
        if item.get("field")
    }
    custom_fields[int(source_item_field["id"])] = {
        "field": int(source_item_field["id"]),
        "value": f"{row['res_model']}:{row['res_id']}",
    }
    custom_fields[int(source_attachment_field["id"])] = {
        "field": int(source_attachment_field["id"]),
        "value": str(attachment_id),
    }
    client.update_document_metadata(
        document.paperless_id,
        {"custom_fields": list(custom_fields.values())},
    )
    refreshed = client.get_document(document.paperless_id)
    cache_values = documents._paperless_values(refreshed)
    cache_values.pop("source", None)
    document.sudo().with_context(usl_documents_cache_write=True).write(cache_values)
    document._synchronize_versions(refreshed.get("versions") or [])
    document.with_user(admin).action_sync_permissions()

    duplicate_roots = documents.search(
        [
            ("availability_state", "!=", "permanently_deleted"),
            "|",
            ("checksum", "=", source_sha256),
            ("version_ids.checksum", "=", source_sha256),
        ],
    )
    if len(duplicate_roots) != 1 or duplicate_roots != document:
        fail(
            f"attachment {attachment_id} resolved to multiple live archive roots: "
            f"{duplicate_roots.mapped('paperless_id')}",
        )

    original, original_headers = client.download(document.paperless_id, original=True)
    if hashlib.sha256(original).hexdigest() != source_sha256:
        fail(f"attachment {attachment_id} original changed after archival")
    preview, preview_headers = client.preview(document.paperless_id)
    if not preview:
        fail(f"attachment {attachment_id} has no Paperless preview")
    if document.permission_sync_state != "synchronized":
        fail(f"attachment {attachment_id} permissions were not synchronized")
    if not document.link_ids.filtered(
        lambda link: link.active
        and link.res_model == target._name
        and link.res_id == target.id
    ):
        fail(f"attachment {attachment_id} has no durable Odoo relationship")

    results.append(
        {
            "source_attachment_id": attachment_id,
            "source_sha1": row["checksum"],
            "source_sha256": source_sha256,
            "odoo_document_id": document.id,
            "paperless_document_id": document.paperless_id,
            "target": f"{target._name}:{target.id}",
            "ingestion_state": ingestion_state,
            "original_bytes": len(original),
            "original_content_type": response_header(
                original_headers,
                "Content-Type",
            ),
            "preview_bytes": len(preview),
            "preview_content_type": response_header(
                preview_headers,
                "Content-Type",
            ),
        },
    )
    env.cr.commit()

documents.sync_from_paperless(full=True)
attachment_count_after = env["ir.attachment"].sudo().search_count([])
if attachment_count_after != attachment_count_before:
    fail("the pilot unexpectedly created an Odoo attachment binary")
env.cr.commit()
print(
    "DOCUMENTS_SOURCE_PILOT_RESULT="
    + json.dumps(
        {
            "schema": "usl-documents-source-pilot-result-v1",
            "source_dump_sha256": expected_dump_sha256,
            "paperless_version": compatibility["server_version"],
            "paperless_api_version": compatibility["api_version"],
            "odoo_attachment_count_before": attachment_count_before,
            "odoo_attachment_count_after": attachment_count_after,
            "documents": results,
        },
        sort_keys=True,
    ),
)
