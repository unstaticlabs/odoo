# ruff: noqa: F821, T201
"""Restore the complete Odoo Online Documents binary perimeter to Paperless.

This script runs in an Odoo shell started by ``scripts/documents-restore``.
The source database and filestore are read-only. Paperless keeps one root per
exact checksum; every legacy Documents identity remains recorded on that root.
"""

import base64
import hashlib
import io
import json
import os
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from odoo import Command, fields
from odoo.exceptions import AccessError


SOURCE_FILESTORE = Path(
    os.getenv("DOCUMENTS_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
).resolve()
SOURCE_SNAPSHOT = os.environ["DOCUMENTS_SOURCE_SNAPSHOT"]
SOURCE_DUMP_SHA256 = os.environ["DOCUMENTS_SOURCE_DUMP_SHA256"]
MAX_IN_FLIGHT = max(1, int(os.getenv("DOCUMENTS_RESTORE_MAX_IN_FLIGHT", "16")))
PROCESSING_TIMEOUT = max(60, int(os.getenv("DOCUMENTS_RESTORE_TIMEOUT", "7200")))
SOURCE_LIMIT = max(0, int(os.getenv("DOCUMENTS_RESTORE_LIMIT", "0")))
PAPERLESS_URL = os.environ["DOCUMENTS_PAPERLESS_URL"].rstrip("/")
PAPERLESS_PUBLIC_URL = os.getenv(
    "DOCUMENTS_PAPERLESS_PUBLIC_URL",
    PAPERLESS_URL,
).rstrip("/")
PAPERLESS_TOKEN = os.environ["DOCUMENTS_PAPERLESS_TOKEN"]
PAPERLESS_SERVICE_USER_ID = int(os.environ["DOCUMENTS_PAPERLESS_SERVICE_USER_ID"])

TAG_COLORS = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc949",
    "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab", "#2b8cbe", "#31a354",
)
QUALIFIED_SOURCE = {
    "dump_sha256": "e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9",
    "documents": 567,
    "folders": 77,
    "tags": 49,
    "tag_relations": 380,
    "accesses": 625,
    "unassigned": 9,
    "document_groups": 5,
    "url_references": 1,
    "account_folder_settings": 38,
    "account_folder_setting_tags": 37,
    "hr_contract_tags": 1,
    "checksum_groups": 548,
}
QUALIFIED_SEARCHABLE_DERIVATIVES = {
    388: {"mime_type": "application/zip", "kind": "FEC ZIP"},
    546: {"mime_type": "text/plain", "kind": "accounting XML"},
    607: {"mime_type": "text/calendar", "kind": "calendar evidence"},
}


def fail(message):
    raise RuntimeError(f"Documents restore: {message}")


def text(value):
    if isinstance(value, dict):
        return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
    return value or ""


def source_connection():
    connection = psycopg2.connect(
        host=os.getenv("DOCUMENTS_SOURCE_DB_HOST", "accounting-source-db"),
        port=int(os.getenv("DOCUMENTS_SOURCE_DB_PORT", "5432")),
        user=os.getenv("DOCUMENTS_SOURCE_DB_USER", "odoo"),
        password=os.getenv("DOCUMENTS_SOURCE_DB_PASSWORD", "odoo"),
        dbname=os.getenv(
            "DOCUMENTS_SOURCE_DATABASE",
            "odoo_online_source_saas_19_2",
        ),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    connection.set_session(readonly=True, autocommit=False)
    return connection


def rows(cursor, query, parameters=None):
    cursor.execute(query, parameters or ())
    return [dict(item) for item in cursor.fetchall()]


def read_source():
    with source_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SHOW transaction_read_only")
        if cursor.fetchone()["transaction_read_only"] != "on":
            fail("source database connection is not read-only")
        documents = rows(
            cursor,
            """
            SELECT document.id AS document_id, document.attachment_id,
                   document.folder_id, document.company_id, document.partner_id,
                   document.owner_id, document.res_model, document.res_id,
                   document.access_internal, document.access_via_link,
                   document.is_access_via_link_hidden, document.document_token,
                   document.active, document.name,
                   document.create_date, document.write_date,
                   attachment.name AS filename, attachment.store_fname,
                   attachment.checksum, attachment.file_size,
                   attachment.mimetype, attachment.create_uid,
                   attachment.create_date AS attachment_create_date
              FROM documents_document document
              JOIN ir_attachment attachment
                ON attachment.id = document.attachment_id
             ORDER BY document.id
            """,
        )
        folders = rows(
            cursor,
            """
            SELECT id, folder_id, parent_path, name
              FROM documents_document
             WHERE type = 'folder'
             ORDER BY id
            """,
        )
        tags = rows(
            cursor,
            "SELECT id, sequence, color, name FROM documents_tag ORDER BY id",
        )
        tag_relations = rows(
            cursor,
            "SELECT documents_document_id AS document_id, "
            "documents_tag_id AS tag_id FROM document_tag_rel "
            "ORDER BY documents_document_id, documents_tag_id",
        )
        accesses = rows(
            cursor,
            "SELECT id, document_id, partner_id, role, last_access_date, expiration_date "
            "FROM documents_access ORDER BY id",
        )
        unassigned = rows(
            cursor,
            """
            SELECT attachment.id AS attachment_id, attachment.name AS filename,
                   attachment.store_fname, attachment.checksum,
                   attachment.file_size, attachment.mimetype,
                   attachment.create_uid, attachment.create_date AS attachment_create_date
              FROM ir_attachment attachment
             WHERE COALESCE(attachment.res_model, '') = ''
               AND attachment.type = 'binary'
               AND attachment.name != 'res.company.scss'
               AND NOT EXISTS (
                   SELECT 1 FROM message_attachment_rel relation
                    WHERE relation.attachment_id = attachment.id
               )
             ORDER BY attachment.id
            """,
        )
        document_groups = rows(
            cursor,
            """
            SELECT relation.uid AS user_id,
                   data.module || '.' || data.name AS xmlid
              FROM res_groups_users_rel relation
              JOIN ir_model_data data
                ON data.model = 'res.groups' AND data.res_id = relation.gid
             WHERE data.module = 'documents'
               AND data.name IN ('group_documents_manager', 'group_documents_system')
             ORDER BY relation.uid, data.name
            """,
        )
        url_references = rows(
            cursor,
            """
            SELECT document.id, document.name, document.url, document.folder_id,
                   document.company_id, document.owner_id, document.partner_id,
                   document.access_internal, document.access_via_link,
                   document.active, document.create_date, document.write_date,
                   data.module || '.' || data.name AS xmlid
              FROM documents_document document
              LEFT JOIN ir_model_data data
                ON data.model = 'documents.document' AND data.res_id = document.id
             WHERE document.type = 'url'
             ORDER BY document.id
            """,
        )
        account_folder_settings = rows(
            cursor,
            "SELECT id, company_id, journal_id, folder_id, create_date, write_date "
            "FROM documents_account_folder_setting ORDER BY id",
        )
        account_folder_setting_tags = rows(
            cursor,
            "SELECT documents_account_folder_setting_id AS setting_id, "
            "documents_tag_id AS tag_id "
            "FROM documents_account_folder_setting_documents_tag_rel "
            "ORDER BY documents_account_folder_setting_id, documents_tag_id",
        )
        hr_contract_tags = rows(
            cursor,
            "SELECT res_company_id AS company_id, documents_tag_id AS tag_id "
            "FROM documents_hr_contracts_tags_table "
            "ORDER BY res_company_id, documents_tag_id",
        )
    source = {
        "documents": documents,
        "folders": folders,
        "tags": tags,
        "tag_relations": tag_relations,
        "accesses": accesses,
        "unassigned": unassigned,
        "document_groups": document_groups,
        "url_references": url_references,
        "account_folder_settings": account_folder_settings,
        "account_folder_setting_tags": account_folder_setting_tags,
        "hr_contract_tags": hr_contract_tags,
    }
    if SOURCE_DUMP_SHA256 != QUALIFIED_SOURCE["dump_sha256"]:
        fail(
            "this migration contract has not qualified source dump "
            f"{SOURCE_DUMP_SHA256}"
        )
    for key in (
        "documents", "folders", "tags", "tag_relations", "accesses",
        "unassigned", "document_groups", "url_references",
        "account_folder_settings", "account_folder_setting_tags",
        "hr_contract_tags",
    ):
        if len(source[key]) != QUALIFIED_SOURCE[key]:
            fail(
                f"qualified source {key} changed: expected "
                f"{QUALIFIED_SOURCE[key]}, got {len(source[key])}"
            )
    if source["url_references"][0]["xmlid"] != (
        "documents.documents_attachment_video_documents"
    ):
        fail("the only source URL reference is not the qualified upstream tutorial")
    return source


def source_content(item):
    path = (SOURCE_FILESTORE / item["store_fname"]).resolve()
    if SOURCE_FILESTORE not in path.parents or not path.is_file():
        fail(f"source attachment {item['attachment_id']} has an unsafe or missing path")
    content = path.read_bytes()
    if len(content) != item["file_size"]:
        fail(f"source attachment {item['attachment_id']} size changed")
    checksum = hashlib.sha1(content, usedforsecurity=False).hexdigest()
    if checksum != item["checksum"]:
        fail(f"source attachment {item['attachment_id']} checksum changed")
    return content


def source_map(model_name, source_ids):
    source_ids = sorted(set(source_ids))
    if not source_ids:
        return {}
    if model_name == "res.users":
        # res.users delegates to res.partner, whose inherited trace fields hide
        # the deliberate user-level trace columns at the ORM layer. The identity
        # restore owns and validates those direct columns, so resolve them with a
        # parameterized target-side query and return ordinary user records.
        env.cr.execute(
            """
            SELECT id
              FROM res_users
             WHERE rebuild_source_model = 'res.users'
               AND rebuild_source_id = ANY(%s)
             ORDER BY id
            """,
            (source_ids,),
        )
        records = env[model_name].sudo().browse([row[0] for row in env.cr.fetchall()])
        result = {}
        env.cr.execute(
            """
            SELECT id, rebuild_source_id
              FROM res_users
             WHERE rebuild_source_model = 'res.users'
               AND rebuild_source_id = ANY(%s)
             ORDER BY id
            """,
            (source_ids,),
        )
        for target_id, source_id in env.cr.fetchall():
            if source_id in result:
                fail(f"target has duplicate {model_name} source identities")
            result[source_id] = records.browse(target_id)
    else:
        records = (
            env[model_name]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", model_name),
                    ("rebuild_source_id", "in", source_ids),
                ],
            )
        )
        result = {record.rebuild_source_id: record for record in records}
    if len(records) != len(result):
        fail(f"target has duplicate {model_name} source identities")
    missing = sorted(set(source_ids) - set(result))
    if missing:
        fail(f"target is missing {model_name} source identities: {missing}")
    return result


def folder_paths(source):
    folders = {row["id"]: row for row in source["folders"]}
    result = {}
    for folder_id, row in folders.items():
        source_ids = [int(part) for part in (row["parent_path"] or "").split("/") if part]
        names = [text(folders[item]["name"]) for item in source_ids if item in folders]
        result[folder_id] = " / ".join(name for name in names if name)
    return result


def group_source(source):
    tags_by_document = defaultdict(list)
    for relation in source["tag_relations"]:
        tags_by_document[relation["document_id"]].append(relation["tag_id"])
    access_by_document = defaultdict(list)
    for access in source["accesses"]:
        access_by_document[access["document_id"]].append(access)
    paths = folder_paths(source)
    grouped = defaultdict(list)
    for document in source["documents"]:
        document["tag_ids"] = tags_by_document[document["document_id"]]
        document["access_rows"] = access_by_document[document["document_id"]]
        document["folder_path"] = paths.get(document["folder_id"], "")
        document["kind"] = "document"
        grouped[document["checksum"]].append(document)
    for attachment in source["unassigned"]:
        grouped[attachment["checksum"]].append(
            {
                **attachment,
                "document_id": None,
                "folder_id": None,
                "company_id": None,
                "partner_id": None,
                "owner_id": None,
                "res_model": None,
                "res_id": 0,
                "access_internal": "edit",
                "access_via_link": "none",
                "is_access_via_link_hidden": True,
                "document_token": "",
                "active": True,
                "name": attachment["filename"],
                "create_date": attachment["attachment_create_date"],
                "write_date": attachment["attachment_create_date"],
                "tag_ids": [],
                "access_rows": [],
                "folder_path": "",
                "kind": "unassigned_evidence",
            },
        )
    ordered = sorted(
        grouped.values(),
        key=lambda group: min(
            item["document_id"] or (1_000_000_000 + item["attachment_id"])
            for item in group
        ),
    )
    return ordered[:SOURCE_LIMIT] if SOURCE_LIMIT else ordered


def representative(group):
    return sorted(
        group,
        key=lambda item: (
            not bool(item["active"]),
            item["document_id"] or 1_000_000_000,
            item["attachment_id"],
        ),
    )[0]


def ensure_custom_field(client, name):
    existing = next(
        (item for item in client.list_custom_fields() if item.get("name") == name),
        None,
    )
    return existing or client.create_custom_field({"name": name, "data_type": "string"})


def bounded_custom_value(value):
    value = str(value)
    if len(value) <= 128:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"manifest-sha256:{digest}"


def custom_field_values(custom_fields, group):
    document_items = [item for item in group if item["document_id"]]
    payloads = {
        "Legacy Odoo document identities": ",".join(
            str(item["document_id"]) for item in document_items
        ),
        "Legacy Odoo attachment identities": ",".join(
            str(value) for value in sorted({item["attachment_id"] for item in group})
        ),
        "Legacy Odoo binary manifests": f"sha1:{group[0]['checksum']}",
        "Legacy Odoo folder paths": " | ".join(
            sorted({item["folder_path"] for item in group if item["folder_path"]})
        ),
        "Legacy Odoo tags": ",".join(
            str(value)
            for value in sorted(
                {tag_id for item in document_items for tag_id in item["tag_ids"]}
            )
        ),
        "Legacy Odoo record identities": ",".join(sorted(
            {
                f"{item['res_model']}:{item['res_id']}"
                for item in document_items
                if item["res_model"] and item["res_id"]
            },
        )),
        "Legacy Odoo access policy": ";".join(
            f"{item['document_id']}:{item['access_internal']}:"
            f"link-{item['access_via_link']}:owner-{item['owner_id'] or 0}"
            for item in document_items
        ),
        "Legacy Odoo lifecycle": ";".join(
            f"{item['document_id']}:{'active' if item['active'] else 'inactive'}:"
            f"{item['create_date']}:{item['write_date']}"
            for item in document_items
        ),
        "Legacy Odoo source snapshot": SOURCE_SNAPSHOT,
        "Legacy Odoo operational attachment": ",".join(
            str(value)
            for value in sorted(
                {
                    item["operational_attachment_id"]
                    for item in group
                    if item.get("operational_attachment_id")
                }
            )
        ),
    }
    return [
        {
            "field": int(custom_fields[name]["id"]),
            "value": bounded_custom_value(value),
        }
        for name, value in payloads.items()
    ]


def source_truth_payload(group):
    payload = []
    for item in group:
        payload.append(
            {
                "document_id": item["document_id"],
                "attachment_id": item["attachment_id"],
                "filename": item["filename"],
                "mime_type": item["mimetype"],
                "size": item["file_size"],
                "sha1": item["checksum"],
                "company_id": item["company_id"],
                "partner_id": item["partner_id"],
                "owner_id": item["owner_id"],
                "res_model": item["res_model"],
                "res_id": item["res_id"],
                "folder_path": item["folder_path"],
                "tag_ids": item["tag_ids"],
                "access_internal": item["access_internal"],
                "access_via_link": item["access_via_link"],
                "access_via_link_hidden": bool(item["is_access_via_link_hidden"]),
                "legacy_link_token_sha256": (
                    hashlib.sha256(item["document_token"].encode()).hexdigest()
                    if item["document_token"]
                    else ""
                ),
                "access_rows": item["access_rows"],
                "active": bool(item["active"]),
                "created": item["create_date"],
                "modified": item["write_date"],
                "name": item["name"],
                "kind": item["kind"],
                "operational_attachment_id": item.get("operational_attachment_id"),
                "searchable_source_member": item.get("searchable_source_member", ""),
            },
        )
    return payload


def searchable_archive_pdf(content, source_item):
    """Create a deterministic searchable PDF for a qualified text container.

    Paperless 3.0.4 rejects these three source formats. Their exact originals
    remain operational Odoo attachments; Paperless receives an explicitly
    labelled searchable representation for preview and discovery.
    """
    document_id = source_item["document_id"]
    qualification = QUALIFIED_SEARCHABLE_DERIVATIVES.get(document_id)
    if not qualification or source_item["mimetype"] != qualification["mime_type"]:
        fail("an unqualified source entered searchable-derivative handling")
    member_filename = source_item["filename"]
    extracted = content
    if document_id == 388:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                fail("the qualified FEC ZIP no longer contains exactly one file")
            member = members[0]
            member_path = Path(member.filename)
            if (
                member.flag_bits & 0x1
                or member_path.is_absolute()
                or ".." in member_path.parts
                or member_path.suffix.lower() != ".txt"
            ):
                fail("the qualified FEC ZIP member is encrypted or unsafe")
            extracted = archive.read(member)
            member_filename = member.filename
            if len(extracted) != member.file_size:
                fail("the qualified FEC ZIP member size differs")
    try:
        source_text = extracted.decode("utf-8-sig")
    except UnicodeDecodeError:
        source_text = extracted.decode("latin-1")
    output = io.BytesIO()
    pdf = canvas.Canvas(
        output,
        pagesize=landscape(A4),
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle(text(source_item["name"]) or source_item["filename"])
    pdf.setAuthor("USL deterministic Odoo Online migration")
    width, height = landscape(A4)
    left = 24
    top = height - 28
    bottom = 24
    line_height = 7

    def begin_page(page_number):
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(
            left,
            height - 16,
            f"{qualification['kind']} - searchable archive representation",
        )
        pdf.setFont("Helvetica", 6)
        pdf.drawRightString(width - left, height - 16, f"Page {page_number}")
        pdf.setFont("Courier", 5.5)
        return top

    page = 1
    y = begin_page(page)
    provenance = (
        f"Authoritative source: {source_item['filename']} | "
        f"Odoo attachment {source_item['attachment_id']} | "
        f"SHA-1 {source_item['checksum']} | represented content {member_filename}"
    )
    logical_lines = [provenance, ""] + source_text.splitlines()
    for logical_line in logical_lines:
        safe = logical_line.encode("cp1252", errors="replace").decode("cp1252")
        chunks = [safe[index:index + 220] for index in range(0, len(safe), 220)] or [""]
        for chunk in chunks:
            if y < bottom:
                pdf.showPage()
                page += 1
                y = begin_page(page)
            pdf.drawString(left, y, chunk)
            y -= line_height
    pdf.save()
    return output.getvalue(), member_filename


def ensure_operational_source_attachment(group, content, company, target):
    source_item = representative(group)
    document_ids = [item["document_id"] for item in group]
    qualification = QUALIFIED_SEARCHABLE_DERIVATIVES.get(source_item["document_id"])
    if (
        len(document_ids) != 1
        or not qualification
        or source_item["mimetype"] != qualification["mime_type"]
    ):
        fail("an unqualified file entered the operational-attachment exception")
    # Odoo deliberately hides attachments backing Binary fields from ordinary
    # searches unless ``skip_res_field_check`` is explicit. Accounting XML can
    # already exist through such a field, and missing it would create a second
    # operational copy with the same source identity.
    attachments = env["ir.attachment"].sudo().with_context(
        skip_res_field_check=True,
    )
    existing_records = attachments.search(
        [
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_id", "=", source_item["attachment_id"]),
        ],
    )
    created = False
    if existing_records:
        if any(
            attachment.checksum != source_item["checksum"]
            for attachment in existing_records
        ):
            fail("a retained operational source differs from the source original")
        imported = existing_records.filtered(
            lambda attachment: attachment.rebuild_import_status == "imported"
        )
        if len(imported) > 1:
            field_backed = imported.filtered(lambda attachment: attachment.res_field)
            archive_fallbacks = imported.filtered(
                lambda attachment: (
                    not attachment.res_field
                    and attachment.rebuild_import_note
                    == (
                        "Exact authoritative source retained; searchable "
                        "representation archived."
                    )
                )
            )
            if (
                len(field_backed) != 1
                or set(imported.ids)
                != set(field_backed.ids + archive_fallbacks.ids)
            ):
                fail("the target contains ambiguous imported operational attachments")
            cleaned_operational_duplicate_ids.extend(archive_fallbacks.ids)
            archive_fallbacks.unlink()
            imported = field_backed
        existing = imported or existing_records.sorted("id")[:1]
    else:
        existing = attachments.create(
            {
                "name": source_item["filename"],
                "raw": content,
                "mimetype": source_item["mimetype"],
                "rebuild_source_database": os.getenv(
                    "DOCUMENTS_SOURCE_DATABASE",
                    "odoo_online_source_saas_19_2",
                ),
                "rebuild_source_model": "ir.attachment",
                "rebuild_source_id": source_item["attachment_id"],
                "rebuild_source_snapshot": SOURCE_SNAPSHOT,
            },
        )
        created = True
    if existing.checksum != source_item["checksum"]:
        fail("the retained operational source differs from the source original")
    target_record = target or company
    values = {
        "res_model": target_record._name,
        "res_id": target_record.id,
        "company_id": company.id,
        "rebuild_import_status": "imported",
    }
    if created:
        values.update(
            {
                "description": (
                    f"Authoritative {qualification['kind']} retained from Odoo "
                    "Online. Paperless stores a checksum-linked searchable PDF "
                    "representation because the source format is not accepted by "
                    "Paperless 3.0.4."
                ),
                "rebuild_import_note": (
                    "Exact authoritative source retained; searchable representation "
                    "archived."
                ),
            },
        )
    existing.write(values)
    for item in group:
        item["operational_attachment_id"] = existing.id
    return existing, created


def ensure_fallback(group, content, source_snapshot):
    """Keep failed parser input byte-for-byte in Odoo without hiding the failure."""
    attachments = env["ir.attachment"].sudo()
    restored = []
    for item in group:
        existing = attachments.search(
            [
                ("rebuild_source_model", "=", "ir.attachment"),
                ("rebuild_source_id", "=", item["attachment_id"]),
            ],
            limit=1,
        )
        if not existing:
            existing = attachments.create(
                {
                    "name": item["filename"],
                    "raw": content,
                    "mimetype": item["mimetype"],
                    "description": (
                        "Migration quarantine: Paperless could not parse this source "
                        "file. Resolve before the Documents scope can pass."
                    ),
                    "rebuild_source_database": os.getenv(
                        "DOCUMENTS_SOURCE_DATABASE",
                        "odoo_online_source_saas_19_2",
                    ),
                    "rebuild_source_model": "ir.attachment",
                    "rebuild_source_id": item["attachment_id"],
                    "rebuild_source_snapshot": source_snapshot,
                    "rebuild_import_status": "failed",
                    "rebuild_import_note": "Paperless parser failure; retained byte-for-byte.",
                },
            )
        if existing.checksum != item["checksum"]:
            fail(f"quarantined attachment {item['attachment_id']} checksum differs")
        restored.append(existing.id)
    return restored


parameters = env["ir.config_parameter"].sudo()
parameters.set_str("usl_documents.paperless_url", PAPERLESS_URL)
parameters.set_str("usl_documents.paperless_public_url", PAPERLESS_PUBLIC_URL)
parameters.set_str("usl_documents.paperless_token", PAPERLESS_TOKEN)
parameters.set_int(
    "usl_documents.paperless_service_user_id",
    PAPERLESS_SERVICE_USER_ID,
)
parameters.set_int("usl_documents.paperless_timeout", PROCESSING_TIMEOUT)
parameters.set_int("usl_documents.paperless_trash_retention_days", 36500)

source = read_source()
groups = group_source(source)
if not SOURCE_LIMIT and len(groups) != QUALIFIED_SOURCE["checksum_groups"]:
    fail(
        "qualified source checksum groups changed: expected "
        f"{QUALIFIED_SOURCE['checksum_groups']}, got {len(groups)}"
    )
admin = env.ref("base.user_admin")
documents_model = env["usl.document"]
manager_group = env.ref("usl_documents.group_documents_manager")
companies = source_map(
    "res.company",
    [item["company_id"] for group in groups for item in group if item["company_id"]],
)
users = source_map(
    "res.users",
    [
        source_id
        for source_id in (
            [item["owner_id"] for group in groups for item in group if item["owner_id"]]
            + [row["user_id"] for row in source["document_groups"]]
        )
        if source_id
    ],
)
partners = source_map(
    "res.partner",
    [item["partner_id"] for group in groups for item in group if item["partner_id"]],
)
moves = source_map(
    "account.move",
    [
        item["res_id"]
        for group in groups
        for item in group
        if item["res_model"] == "account.move" and item["res_id"]
    ],
)
expected_move_ids = {
    item["res_id"]
    for group in groups
    for item in group
    if item["res_model"] == "account.move" and item["res_id"]
}
if set(moves) != expected_move_ids:
    fail(f"Accounting move mappings are incomplete: {sorted(expected_move_ids - set(moves))}")

for membership in source["document_groups"]:
    user = users.get(membership["user_id"])
    if user:
        user.sudo().write({"group_ids": [Command.link(manager_group.id)]})

admin = env.ref("base.user_admin").with_company(next(iter(companies.values()), env.company))
documents = documents_model.with_user(admin)
client = documents._paperless()
compatibility = client.compatibility()
if compatibility["api_version"] != "10":
    fail(f"Paperless API v10 is required, got {compatibility['api_version']}")
client.ensure_fail_closed_ingestion_policy()
documents._sync_metadata_catalogs(client)

tag_model = env["usl.paperless.tag"].with_user(admin)
source_tags = {}
for row in source["tags"]:
    name = text(row["name"])
    tag = tag_model.search([("name", "=ilike", name), ("active", "=", True)], limit=1)
    if not tag:
        tag = tag_model.create(
            {
                "name": name,
                "color": TAG_COLORS[int(row["color"] or 0) % len(TAG_COLORS)],
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )
    source_tags[row["id"]] = tag

correspondent_model = env["usl.paperless.correspondent"].with_user(admin)
correspondents = {}
for source_partner_id, partner in partners.items():
    result = correspondent_model.create_from_partner(partner.id)
    correspondents[source_partner_id] = correspondent_model.browse(result["id"])

custom_field_names = (
    "Legacy Odoo document identities",
    "Legacy Odoo attachment identities",
    "Legacy Odoo binary manifests",
    "Legacy Odoo folder paths",
    "Legacy Odoo tags",
    "Legacy Odoo record identities",
    "Legacy Odoo access policy",
    "Legacy Odoo lifecycle",
    "Legacy Odoo source snapshot",
    "Legacy Odoo operational attachment",
)
custom_fields = {name: ensure_custom_field(client, name) for name in custom_field_names}

all_attachments = env["ir.attachment"].sudo().with_context(
    skip_res_field_check=True,
)
baseline_attachment_count = all_attachments.search_count([])
pending = []
completed = []
failed = []
cleaned_quarantine_ids = []
cleaned_operational_duplicate_ids = []
retained_operational_attachment_ids = []
created_operational_attachment_ids = []
permission_groups = defaultdict(list)
trash_after_permission_sync = []
permission_mappings = env["usl.paperless.user.mapping"].search(
    [
        ("active", "=", True),
        ("sync_state", "=", "synchronized"),
    ],
).filtered(lambda mapping: mapping._identity_is_safe())


def existing_document_for(content_sha256):
    matches = documents.search(
        [
            ("availability_state", "!=", "permanently_deleted"),
            "|",
            ("checksum", "=", content_sha256),
            ("version_ids.checksum", "=", content_sha256),
        ],
    )
    if len(matches) > 1:
        fail(
            f"target contains {len(matches)} archive roots for SHA-256 "
            f"{content_sha256}"
        )
    return matches


def settle_pending(force=False):
    deadline = time.monotonic() + PROCESSING_TIMEOUT
    while pending and (force or len(pending) >= MAX_IN_FLIGHT):
        progressed = False
        for item in list(pending):
            operation = item["operation"]
            operation.poll()
            env.cr.commit()
            operation.invalidate_recordset()
            if operation.state == "archived" and operation.document_id:
                item["document"] = operation.document_id.with_user(admin)
                item["state"] = "archived"
                pending.remove(item)
                completed.append(item)
                progressed = True
            elif operation.state in ("failed", "duplicate"):
                item["error"] = operation.error_message or operation.state
                item["state"] = operation.state
                item["fallback_ids"] = ensure_fallback(
                    item["group"],
                    item["source_content"],
                    SOURCE_SNAPSHOT,
                )
                pending.remove(item)
                failed.append(item)
                env.cr.commit()
                progressed = True
        if pending and not progressed:
            if time.monotonic() >= deadline:
                for item in list(pending):
                    item["error"] = "Paperless processing timed out"
                    item["state"] = "failed"
                    item["fallback_ids"] = ensure_fallback(
                        item["group"], item["source_content"], SOURCE_SNAPSHOT,
                    )
                    failed.append(item)
                    pending.remove(item)
                env.cr.commit()
                break
            time.sleep(2)
        if not force and len(pending) < MAX_IN_FLIGHT:
            break


for index, group in enumerate(groups, start=1):
    item = representative(group)
    source_contents = [source_content(entry) for entry in group]
    content = source_contents[0]
    if any(candidate != content for candidate in source_contents[1:]):
        fail(f"source SHA-1 group {item['checksum']} contains different binaries")
    source_sha256 = hashlib.sha256(content).hexdigest()
    source_company_ids = sorted({entry["company_id"] for entry in group if entry["company_id"]})
    if len(source_company_ids) > 1:
        fail(f"checksum {item['checksum']} spans several legal companies")
    source_company_id = source_company_ids[0] if source_company_ids else None
    company = companies.get(source_company_id) or next(iter(companies.values()), env.company)
    target_moves = [
        moves[entry["res_id"]]
        for entry in group
        if entry["res_model"] == "account.move" and entry["res_id"]
    ]
    target = target_moves[0] if target_moves else None
    paperless_content = content
    paperless_filename = item["filename"]
    paperless_mime_type = item["mimetype"]
    paperless_source = "odoo_attachment"
    if item["document_id"] in QUALIFIED_SEARCHABLE_DERIVATIVES:
        operational_attachment, created = ensure_operational_source_attachment(
            group,
            content,
            company,
            target,
        )
        retained_operational_attachment_ids.append(operational_attachment.id)
        if created:
            created_operational_attachment_ids.append(operational_attachment.id)
        paperless_content, member_filename = searchable_archive_pdf(content, item)
        paperless_filename = f"{Path(item['filename']).stem} - searchable archive.pdf"
        paperless_mime_type = "application/pdf"
        paperless_source = (
            "odoo_generated" if item["document_id"] == 388 else "odoo_attachment"
        )
        for entry in group:
            entry["searchable_source_member"] = member_filename
    paperless_sha256 = hashlib.sha256(paperless_content).hexdigest()
    existing = existing_document_for(paperless_sha256)
    task = {
        "index": index,
        "group": group,
        "content": paperless_content,
        "source_content": content,
        "sha256": source_sha256,
        "paperless_original_sha256": paperless_sha256,
        "source_truth": source_truth_payload(group),
        "state": "reused" if existing else "processing",
    }
    if existing:
        task["document"] = existing
        completed.append(task)
        if index % 25 == 0 or index == len(groups):
            print(
                "DOCUMENTS_SOURCE_RESTORE_PROGRESS="
                + json.dumps(
                    {
                        "selected_groups": len(groups),
                        "submitted_groups": index,
                        "archived_or_reused_groups": len(completed),
                        "processing_groups": len(pending),
                        "failed_groups": len(failed),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        continue
    source_owner_ids = sorted({entry["owner_id"] for entry in group if entry["owner_id"]})
    submitter = users.get(source_owner_ids[0]) if source_owner_ids else admin
    if not submitter or not submitter.has_group("usl_documents.group_documents_manager"):
        submitter = admin
    confidentiality = (
        "private"
        if any(entry["access_internal"] == "none" for entry in group)
        else "accounting" if target_moves else "internal"
    )
    upload = documents_model.with_user(submitter).upload_from_odoo(
        paperless_filename,
        base64.b64encode(paperless_content).decode(),
        paperless_mime_type,
        res_model=target._name if target else None,
        res_id=target.id if target else None,
        company_id=company.id,
        confidentiality=confidentiality,
        source=paperless_source,
    )
    if upload["state"] == "duplicate" and upload.get("document_id"):
        task["document"] = documents.browse(upload["document_id"])
        task["state"] = "reused"
        completed.append(task)
    elif upload["state"] == "processing":
        task["operation"] = env["usl.document.operation"].browse(upload["operation_id"])
        pending.append(task)
        env.cr.commit()
        settle_pending()
    else:
        task["error"] = upload.get("message") or f"unsupported state {upload['state']}"
        task["state"] = "failed"
        task["fallback_ids"] = ensure_fallback(group, content, SOURCE_SNAPSHOT)
        failed.append(task)
        env.cr.commit()
    if index % 25 == 0 or index == len(groups):
        print(
            "DOCUMENTS_SOURCE_RESTORE_PROGRESS="
            + json.dumps(
                {
                    "selected_groups": len(groups),
                    "submitted_groups": index,
                    "archived_or_reused_groups": len(completed),
                    "processing_groups": len(pending),
                    "failed_groups": len(failed),
                },
                sort_keys=True,
            ),
            flush=True,
        )

settle_pending(force=True)

for item in completed:
    group = item["group"]
    source_item = representative(group)
    document = item["document"].with_user(admin)
    if document.availability_state == "trashed":
        # Paperless exposes Trash through a separate API. Temporarily restore
        # the same stable root so metadata, bytes, preview and permissions can
        # be revalidated, then return all-inactive source groups to Trash.
        document.restore_from_trash()
    source_company_ids = sorted({entry["company_id"] for entry in group if entry["company_id"]})
    source_company_id = source_company_ids[0] if source_company_ids else None
    company = companies.get(source_company_id)
    if any(entry.get("operational_attachment_id") for entry in group):
        company = company or next(iter(companies.values()), env.company)
    target_moves = {
        moves[entry["res_id"]].id: moves[entry["res_id"]]
        for entry in group
        if entry["res_model"] == "account.move" and entry["res_id"]
    }
    confidentiality = (
        "private"
        if any(entry["access_internal"] == "none" for entry in group)
        else "accounting" if target_moves else "internal"
    )
    tags = env["usl.paperless.tag"]
    for entry in group:
        tags |= env["usl.paperless.tag"].browse(
            [source_tags[tag_id].id for tag_id in entry["tag_ids"]],
        )
    correspondent_source_ids = sorted(
        (
            entry["document_id"] or 1_000_000_000,
            entry["partner_id"],
        )
        for entry in group
        if entry["partner_id"]
    )
    correspondent = (
        correspondents.get(correspondent_source_ids[0][1])
        if correspondent_source_ids
        else None
    )
    metadata = {
        "name": (
            f"{text(source_item['name']) or source_item['filename']} — searchable archive copy"
            if any(entry.get("operational_attachment_id") for entry in group)
            else text(source_item["name"]) or source_item["filename"]
        ),
        "document_date": fields.Date.to_string(source_item["create_date"].date()),
        "tag_ids": sorted((document.tag_ids | tags).ids),
        "correspondent_id": correspondent.id if correspondent else False,
    }
    document.update_archive_metadata(metadata)
    remote = client.get_document(document.paperless_id)
    existing_custom_fields = {
        int(value["field"]): value
        for value in (remote.get("custom_fields") or [])
        if value.get("field")
    }
    for value in custom_field_values(custom_fields, group):
        existing_custom_fields[int(value["field"])] = value
    client.update_document_metadata(
        document.paperless_id,
        {"custom_fields": list(existing_custom_fields.values())},
    )
    refreshed = client.get_document(document.paperless_id)
    if (
        any(entry.get("operational_attachment_id") for entry in group)
        and not str(refreshed.get("content") or "").strip()
    ):
        fail(
            f"searchable archive representation has no extracted text for "
            f"Paperless document {document.paperless_id}"
        )
    cache_values = documents._paperless_values(refreshed)
    cache_values.pop("source", None)
    document.sudo().with_context(usl_documents_cache_write=True).write(cache_values)
    document._synchronize_versions(refreshed.get("versions") or [])
    document.sudo().with_context(usl_documents_policy_write=True).write(
        {
            "company_id": company.id if company else False,
            "confidentiality": confidentiality,
            "accounting_evidence": bool(target_moves),
            "review_state": "classified" if company else "needs_attention",
        },
    )
    for target in target_moves.values():
        document.link_to_record("account.move", target.id)
    actual_target_ids = set(
        document.sudo().link_ids.filtered(
            lambda link: link.active and link.res_model == "account.move"
        ).mapped("res_id")
    )
    if actual_target_ids != set(target_moves):
        fail(
            f"accounting links differ for source checksum {source_item['checksum']}"
        )
    view_users = []
    change_users = []
    for mapping in permission_mappings:
        try:
            document.with_user(mapping.user_id).check_access("read")
        except AccessError:
            continue
        view_users.append(mapping.paperless_user_id)
        if mapping.user_id.has_group("usl_documents.group_documents_manager"):
            change_users.append(mapping.paperless_user_id)
    permission_groups[
        (tuple(sorted(set(view_users))), tuple(sorted(set(change_users))))
    ].append(document)
    if document.company_id.id != (company.id if company else False):
        fail(f"company policy differs for Paperless document {document.paperless_id}")
    if document.confidentiality != confidentiality:
        fail(
            f"confidentiality policy differs for Paperless document "
            f"{document.paperless_id}"
        )
    if set(tags.ids) - set(document.tag_ids.ids):
        fail(f"source tags differ for Paperless document {document.paperless_id}")
    if document.correspondent_id.id != (correspondent.id if correspondent else False):
        fail(f"correspondent differs for Paperless document {document.paperless_id}")
    if not document.version_ids.filtered(
        lambda version: version.checksum == item["paperless_original_sha256"]
    ):
        fail(f"source file version is missing for Paperless document {document.paperless_id}")
    original, _headers = client.download(document.paperless_id, original=True)
    if hashlib.sha256(original).hexdigest() != item["paperless_original_sha256"]:
        fail(f"Paperless original differs for source checksum {source_item['checksum']}")
    preview, preview_headers = client.preview(document.paperless_id)
    preview_content_type = next(
        (
            value
            for key, value in preview_headers.items()
            if key.lower() == "content-type"
        ),
        "",
    )
    if not preview:
        fail(f"Paperless preview is empty for source checksum {source_item['checksum']}")
    requires_pdf_preview = (
        source_item["mimetype"] == "application/pdf"
        or any(entry.get("operational_attachment_id") for entry in group)
    )
    if requires_pdf_preview and (
        not preview.startswith(b"%PDF-")
        or "application/pdf" not in preview_content_type
    ):
        fail(f"Paperless preview is not a PDF for source checksum {source_item['checksum']}")
    item["preview_content_type"] = preview_content_type.split(";", 1)[0]
    item["preview_sha256"] = hashlib.sha256(preview).hexdigest()
    quarantine = env["ir.attachment"].sudo().search(
        [
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_id", "in", [entry["attachment_id"] for entry in group]),
            ("rebuild_import_status", "=", "failed"),
            ("description", "ilike", "Migration quarantine: Paperless could not parse"),
        ],
    )
    for fallback in quarantine:
        expected = next(
            entry["checksum"]
            for entry in group
            if entry["attachment_id"] == fallback.rebuild_source_id
        )
        if fallback.checksum != expected:
            fail(f"migration quarantine {fallback.id} changed before cleanup")
    cleaned_quarantine_ids.extend(quarantine.ids)
    quarantine.unlink()
    if all(not entry["active"] for entry in group):
        trash_after_permission_sync.append(document)
    item["paperless_id"] = document.paperless_id
    item["odoo_document_id"] = document.id
    item.pop("content", None)
    item.pop("source_content", None)
    item.pop("document", None)
    item.pop("operation", None)
    env.cr.commit()

for (view_users, change_users), grouped_documents in permission_groups.items():
    payload = client._request(
        "POST",
        "/api/documents/bulk_edit/",
        body={
            "documents": sorted(document.paperless_id for document in grouped_documents),
            "method": "set_permissions",
            "parameters": {
                "set_permissions": {
                    "view": {"users": list(view_users), "groups": []},
                    "change": {"users": list(change_users), "groups": []},
                },
                "owner": PAPERLESS_SERVICE_USER_ID,
                "merge": False,
            },
        },
    )[0]
    result = payload.get("result") if isinstance(payload, dict) else payload
    if result != "OK":
        fail(
            "Paperless 3.0.4 returned an incompatible bulk-permission response: "
            f"{result!r}"
        )
    print(
        "DOCUMENTS_PERMISSION_WRITE="
        + json.dumps(
            {
                "document_count": len(grouped_documents),
                "state": "accepted",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print(
        "DOCUMENTS_PERMISSION_READBACK="
        + json.dumps(
            {
                "document_count": len(grouped_documents),
                "state": "started",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    remote_documents = {}
    for offset in range(0, len(grouped_documents), 100):
        chunk = grouped_documents[offset:offset + 100]
        payload = client._request(
            "GET",
            "/api/documents/",
            query={
                "id__in": ",".join(str(document.paperless_id) for document in chunk),
                "full_perms": "true",
                "page_size": 100,
                "ordering": "id",
            },
        )[0]
        results = payload.get("results", []) if isinstance(payload, dict) else payload
        remote_documents.update({int(remote["id"]): remote for remote in results})
    expected_remote_ids = {document.paperless_id for document in grouped_documents}
    if set(remote_documents) != expected_remote_ids:
        fail("Paperless permission read-back returned a different document set")
    for document in grouped_documents:
        remote = remote_documents[document.paperless_id]
        permissions = remote.get("permissions") or {}
        remote_view = permissions.get("view") or {}
        remote_change = permissions.get("change") or {}
        if (
            int(remote.get("owner") or 0) != PAPERLESS_SERVICE_USER_ID
            or set(remote_view.get("users") or []) != set(view_users)
            or set(remote_change.get("users") or []) != set(change_users)
            or remote_view.get("groups")
            or remote_change.get("groups")
        ):
            fail(
                f"actual Paperless permissions differ for document "
                f"{document.paperless_id}"
            )
        document.sudo().with_context(
            skip_permission_invalidation=True,
            usl_documents_cache_write=True,
        ).write(
            {
                "permission_sync_state": "synchronized",
                "permission_sync_error": False,
                "permission_checked_at": fields.Datetime.now(),
                "availability_state": "available",
            },
        )
    env.cr.commit()
    print(
        "DOCUMENTS_PERMISSION_READBACK="
        + json.dumps(
            {
                "document_count": len(grouped_documents),
                "state": "verified",
            },
            sort_keys=True,
        ),
        flush=True,
    )

for document in trash_after_permission_sync:
    if document.availability_state == "available":
        document.move_to_trash()
env.cr.commit()

for item in failed:
    item.pop("content", None)
    item.pop("source_content", None)
    item.pop("operation", None)
    item["source_attachment_ids"] = [entry["attachment_id"] for entry in item["group"]]
    item.pop("group", None)
for item in completed:
    item["source_document_ids"] = [
        entry["document_id"] for entry in item["group"] if entry["document_id"]
    ]
    item["source_attachment_ids"] = [entry["attachment_id"] for entry in item["group"]]
    item.pop("group", None)

after_attachment_count = all_attachments.search_count([])
expected_source_documents = sum(
    bool(entry["document_id"])
    for group in groups
    for entry in group
)
expected_source_attachments = {
    entry["attachment_id"]
    for group in groups
    for entry in group
}
restored_source_attachments = {
    source_id
    for item in completed
    for source_id in item["source_attachment_ids"]
}
if restored_source_attachments != expected_source_attachments and not failed:
    fail(
        "restored source attachment identities differ from the selected source "
        "perimeter"
    )
expected_after_attachment_count = (
    baseline_attachment_count
    - len(cleaned_quarantine_ids)
    - len(cleaned_operational_duplicate_ids)
    + len(created_operational_attachment_ids)
)
if not failed and after_attachment_count != expected_after_attachment_count:
    fail(
        "successful archive migration changed the Odoo binary perimeter outside "
        "verified quarantine cleanup; "
        f"expected={expected_after_attachment_count}, after={after_attachment_count}"
    )
result = {
    "schema": "usl-documents-source-restore-result-v1",
    "source_snapshot": SOURCE_SNAPSHOT,
    "source_dump_sha256": SOURCE_DUMP_SHA256,
    "paperless_version": compatibility["server_version"],
    "paperless_api_version": compatibility["api_version"],
    "limited_run": bool(SOURCE_LIMIT),
    "source_document_identities": expected_source_documents,
    "source_unassigned_evidence": sum(
        entry["kind"] == "unassigned_evidence"
        for group in groups
        for entry in group
    ),
    "source_tag_catalog": source["tags"],
    "source_folder_catalog": source["folders"],
    "source_documents_group_memberships": source["document_groups"],
    "source_recomputed_url_references": source["url_references"],
    "source_superseded_account_folder_settings": source["account_folder_settings"],
    "source_superseded_account_folder_setting_tags": source[
        "account_folder_setting_tags"
    ],
    "source_superseded_hr_contract_tags": source["hr_contract_tags"],
    "source_access_relationship_count": len(source["accesses"]),
    "checksum_groups": len(groups),
    "archived_groups": len(completed),
    "failed_groups": len(failed),
    "odoo_attachment_count_before": baseline_attachment_count,
    "odoo_attachment_count_after": after_attachment_count,
    "quarantined_attachment_count": sum(len(item.get("fallback_ids", [])) for item in failed),
    "cleaned_quarantine_attachment_count": len(cleaned_quarantine_ids),
    "cleaned_operational_duplicate_attachment_count": len(
        cleaned_operational_duplicate_ids
    ),
    "retained_operational_attachment_ids": retained_operational_attachment_ids,
    "created_operational_attachment_count": len(created_operational_attachment_ids),
    "failed": failed,
    "documents": completed,
}
print("DOCUMENTS_SOURCE_RESTORE_RESULT=" + json.dumps(result, sort_keys=True, default=str))
if failed:
    fail(f"{len(failed)} checksum group(s) remain in migration quarantine")
