# ruff: noqa: EM101, F821, T201
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
import sys
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

sys.path.insert(0, "/mnt/documents-archive-migration")
from classification import (  # noqa: E402
    INSTITUTION_PARTNERS,
    classify_group,
    folder_contexts,
    normalized_source_tag,
)
from classification import (
    TAG_COLORS as CLASSIFICATION_TAG_COLORS,
)
from role_backfill import resolve_link_role, resolve_root_role  # noqa: E402
from selection import resolve_company_scope, select_groups  # noqa: E402

SOURCE_FILESTORE = Path(
    os.getenv("DOCUMENTS_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
).resolve()
SOURCE_SNAPSHOT = os.environ["DOCUMENTS_SOURCE_SNAPSHOT"]
SOURCE_DUMP_SHA256 = os.environ["DOCUMENTS_SOURCE_DUMP_SHA256"]
MAX_IN_FLIGHT = max(1, int(os.getenv("DOCUMENTS_RESTORE_MAX_IN_FLIGHT", "16")))
PROCESSING_TIMEOUT = max(60, int(os.getenv("DOCUMENTS_RESTORE_TIMEOUT", "7200")))
SOURCE_LIMIT = max(0, int(os.getenv("DOCUMENTS_RESTORE_LIMIT", "0")))
SOURCE_PROFILE = os.getenv("DOCUMENTS_RESTORE_PROFILE", "full").strip().lower()
if SOURCE_PROFILE not in {"full", "accounting", "hr", "smoke"}:
    raise RuntimeError(
        "Documents restore: DOCUMENTS_RESTORE_PROFILE must be one of "
        "full, accounting, hr, or smoke",
    )
PAPERLESS_URL = os.environ["DOCUMENTS_PAPERLESS_URL"].rstrip("/")
PAPERLESS_PUBLIC_URL = os.getenv(
    "DOCUMENTS_PAPERLESS_PUBLIC_URL",
    PAPERLESS_URL,
).rstrip("/")
PAPERLESS_TOKEN = os.environ["DOCUMENTS_PAPERLESS_TOKEN"]
PAPERLESS_SERVICE_USER_ID = int(os.environ["DOCUMENTS_PAPERLESS_SERVICE_USER_ID"])

SOURCE_TAG_PALETTE = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc949",
    "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab", "#2b8cbe", "#31a354",
)
QUALIFIED_SEARCHABLE_DERIVATIVES = {
    388: {"mime_type": "application/zip", "kind": "FEC ZIP"},
    546: {"mime_type": "text/plain", "kind": "accounting XML"},
    607: {"mime_type": "text/calendar", "kind": "calendar evidence"},
    857: {
        "mime_type": "application/pdf",
        "kind": "corrupt base64-wrapped supplier invoice",
    },
}
GOVERNED_POST_DOCUMENTS_LINK_MODELS = {
    "b2c.accounting.session",
    "b2c.fulfilment.event",
    "b2c.order",
    "b2c.payment.event",
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
            "odoo_online_source_saas_19_3",
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
                   attachment.create_date AS attachment_create_date,
                   move.journal_id, move.move_type, move.invoice_date,
                   move.date AS move_date, move.partner_id AS move_partner_id,
                   journal.name AS journal_name, journal.type AS journal_type
              FROM documents_document document
              JOIN ir_attachment attachment
                ON attachment.id = document.attachment_id
              LEFT JOIN account_move move
                ON document.res_model = 'account.move' AND move.id = document.res_id
              LEFT JOIN account_journal journal ON journal.id = move.journal_id
             ORDER BY document.id
            """,
        )
        folders = rows(
            cursor,
            """
            SELECT id, folder_id, parent_path, name, company_id
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
                   attachment.create_uid, attachment.create_date AS attachment_create_date,
                   COALESCE(attachment.res_model, '') AS source_res_model
              FROM ir_attachment attachment
             WHERE (
                    COALESCE(attachment.res_model, '') = ''
                    OR attachment.res_model = 'ai.agent.source'
                   )
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
        employee_folder_mappings = rows(
            cursor,
            "SELECT id, name, hr_employee_folder_id, "
            "hr_employee_contract_folder_id FROM hr_employee "
            "WHERE hr_employee_folder_id IS NOT NULL "
            "OR hr_employee_contract_folder_id IS NOT NULL ORDER BY id",
        )
        project_folder_mappings = rows(
            cursor,
            "SELECT id, name, documents_folder_id FROM project_project "
            "WHERE documents_folder_id IS NOT NULL ORDER BY id",
        )
        classification_partners = rows(
            cursor,
            "SELECT id, name FROM res_partner WHERE name = ANY(%s) ORDER BY name, id",
            (sorted(set(INSTITUTION_PARTNERS.values())),),
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
        "employee_folder_mappings": employee_folder_mappings,
        "project_folder_mappings": project_folder_mappings,
        "classification_partners": classification_partners,
    }
    if not source["documents"]:
        fail("the source Documents perimeter is unexpectedly empty")
    unexpected_urls = [
        item for item in source["url_references"]
        if item["xmlid"] != "documents.documents_attachment_video_documents"
    ]
    if unexpected_urls:
        fail("the source contains unsupported Documents URL references")
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


def group_source(source):
    tags_by_document = defaultdict(list)
    for relation in source["tag_relations"]:
        tags_by_document[relation["document_id"]].append(relation["tag_id"])
    access_by_document = defaultdict(list)
    for access in source["accesses"]:
        access_by_document[access["document_id"]].append(access)
    setting_journals = {
        setting["id"]: setting["journal_id"]
        for setting in source["account_folder_settings"]
        if setting["journal_id"]
    }
    rule_tags_by_journal = defaultdict(list)
    for relation in source["account_folder_setting_tags"]:
        journal_id = setting_journals.get(relation["setting_id"])
        if journal_id:
            rule_tags_by_journal[journal_id].append(relation["tag_id"])
    source_tags = {row["id"]: text(row["name"]) for row in source["tags"]}
    paths, folder_companies = folder_contexts(source["folders"])
    grouped = defaultdict(list)
    for document in source["documents"]:
        document["tag_ids"] = sorted(
            set(tags_by_document[document["document_id"]])
            | set(rule_tags_by_journal[document.get("journal_id")]),
        )
        document["source_tag_names"] = [
            source_tags[tag_id] for tag_id in document["tag_ids"]
        ]
        document["access_rows"] = access_by_document[document["document_id"]]
        document["folder_path"] = paths.get(document["folder_id"], "")
        document["folder_company_id"] = folder_companies.get(document["folder_id"])
        document["kind"] = "document"
        grouped[document["checksum"]].append(document)
    for attachment in source["unassigned"]:
        restricted_business_evidence = (
            attachment.get("source_res_model") == "ai.agent.source"
        )
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
                # The strategy PDF attached to the discarded experimental AI
                # setup is genuine private business content. Preserve its
                # bytes without recreating the AI model or exposing it to
                # ordinary Documents users.
                "access_internal": (
                    "none" if restricted_business_evidence else "edit"
                ),
                "access_via_link": "none",
                "is_access_via_link_hidden": True,
                "document_token": "",
                "active": True,
                "name": attachment["filename"],
                "create_date": attachment["attachment_create_date"],
                "write_date": attachment["attachment_create_date"],
                "tag_ids": [],
                "source_tag_names": [],
                "access_rows": [],
                "folder_path": "",
                "folder_company_id": None,
                "kind": (
                    "restricted_unassigned_evidence"
                    if restricted_business_evidence
                    else "unassigned_evidence"
                ),
            },
        )
    return sorted(
        grouped.values(),
        key=lambda group: min(
            item["document_id"] or (1_000_000_000 + item["attachment_id"])
            for item in group
        ),
    )


def representative(group):
    return sorted(
        group,
        key=lambda item: (
            not bool(item["active"]),
            item["document_id"] or 1_000_000_000,
            item["attachment_id"],
        ),
    )[0]


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
                "source_res_model": item.get("source_res_model") or "",
                "folder_path": item["folder_path"],
                "folder_company_id": item.get("folder_company_id"),
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
                "classification": classify_group([item]),
            },
        )
    return payload


def searchable_archive_pdf(content, source_item):
    """Create a deterministic searchable PDF for a qualified text container.

    Paperless 3.0.5 rejects these three source formats. Their exact originals
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
            lambda attachment: attachment.rebuild_import_status == "imported",
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
                ),
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
                    "odoo_online_source_saas_19_3",
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
                    "Paperless 3.0.5."
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
                        "odoo_online_source_saas_19_3",
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
all_groups = group_source(source)
groups = select_groups(all_groups, SOURCE_PROFILE, SOURCE_LIMIT)
if not groups:
    fail(f"source profile {SOURCE_PROFILE} selected no document groups")
admin = env.ref("base.user_admin")
documents_model = env["usl.document"]
manager_group = env.ref("usl_documents.group_documents_manager")
companies = source_map(
    "res.company",
    [
        source_id
        for group in groups
        for item in group
        for source_id in (item.get("company_id"), item.get("folder_company_id"))
        if source_id
    ],
)
users = source_map(
    "res.users",
    [
        source_id
        for source_id in (
            [item["owner_id"] for group in groups for item in group if item["owner_id"]]
            + [
                item["create_uid"]
                for group in groups
                for item in group
                if item.get("create_uid")
            ]
            + [row["user_id"] for row in source["document_groups"]]
        )
        if source_id
    ],
)
partners = source_map(
    "res.partner",
    [
        source_id
        for source_id in (
            [item["partner_id"] for group in groups for item in group]
            + [item.get("move_partner_id") for group in groups for item in group]
            + [row["id"] for row in source["classification_partners"]]
        )
        if source_id
    ],
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
projects = source_map(
    "project.project",
    [row["id"] for row in source["project_folder_mappings"]]
    + [
        item["res_id"]
        for group in groups
        for item in group
        if item["res_model"] == "project.project" and item["res_id"]
    ],
)
tasks = source_map(
    "project.task",
    [
        item["res_id"]
        for group in groups
        for item in group
        if item["res_model"] == "project.task" and item["res_id"]
    ],
)
project_by_folder = {
    row["documents_folder_id"]: projects[row["id"]]
    for row in source["project_folder_mappings"]
}


def business_targets(group):
    """Resolve every native record relationship carried by a checksum group."""
    result = {}
    mappings = {
        "account.move": moves,
        "project.project": projects,
        "project.task": tasks,
    }
    for entry in group:
        mapping = mappings.get(entry["res_model"])
        if mapping and entry["res_id"]:
            target = mapping[entry["res_id"]]
            result[(target._name, target.id)] = target
        project = project_by_folder.get(entry["folder_id"])
        if project:
            result[(project._name, project.id)] = project
    return result


def primary_business_target(targets):
    """Choose the most specific deterministic target for archive policy context."""
    for model_name in ("project.task", "project.project", "account.move"):
        candidates = sorted(
            (
                target_id,
                target,
            )
            for (candidate_model, target_id), target in targets.items()
            if candidate_model == model_name
        )
        if candidates:
            return candidates[0][1]
    return None


tese_payroll_by_move_id = {}
if "usl.tese.payslip" in env:
    tese_payroll_by_move_id = {
        payroll.move_id.id: payroll
        for payroll in env["usl.tese.payslip"].sudo().search(
            [("move_id", "in", [move.id for move in moves.values()])],
        )
    }
employees = source_map(
    "hr.employee",
    [row["id"] for row in source["employee_folder_mappings"]],
)
employee_by_folder = {}
for row in source["employee_folder_mappings"]:
    employee = employees[row["id"]]
    for folder_id in (
        row["hr_employee_folder_id"],
        row["hr_employee_contract_folder_id"],
    ):
        if folder_id:
            employee_by_folder[folder_id] = employee
for membership in source["document_groups"]:
    user = users.get(membership["user_id"])
    if user:
        user.sudo().write({"group_ids": [Command.link(manager_group.id)]})

target_companies = env["res.company"].browse(  # noqa: F821
    sorted({company.id for company in companies.values()}),
)
primary_company = target_companies[:1] or env.company  # noqa: F821
admin = (
    env.ref("base.user_admin")  # noqa: F821
    .with_company(primary_company)
    .with_context(allowed_company_ids=target_companies.ids)
)
documents = documents_model.with_env(admin.env)
client = documents._paperless()
compatibility = client.compatibility()
if compatibility["api_version"] != "10":
    fail(f"Paperless API v10 is required, got {compatibility['api_version']}")
client.ensure_fail_closed_ingestion_policy()
legacy_custom_fields = [
    item
    for item in client.list_custom_fields()
    if (item.get("name") or "").startswith("Legacy Odoo ")
]
for custom_field in legacy_custom_fields:
    client.delete_custom_field(custom_field["id"])
documents._sync_metadata_catalogs(client)

classifications = {
    group[0]["checksum"]: classify_group(group)
    for group in groups
}
tag_model = env["usl.paperless.tag"].with_env(admin.env)
source_tag_colors = {}
for row in source["tags"]:
    name = normalized_source_tag(text(row["name"]))
    source_tag_colors.setdefault(
        name,
        SOURCE_TAG_PALETTE[int(row["color"] or 0) % len(SOURCE_TAG_PALETTE)],
    )
tags_by_name = {}
for name in sorted(
    {
        tag
        for classification in classifications.values()
        for tag in classification["tags"]
    },
    key=str.casefold,
):
    tag = tag_model.search([("name", "=ilike", name), ("active", "=", True)], limit=1)
    if not tag:
        tag = tag_model.create(
            {
                "name": name,
                "color": CLASSIFICATION_TAG_COLORS.get(
                    name,
                    source_tag_colors.get(name, "#4e79a7"),
                ),
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )
    tags_by_name[name] = tag

document_type_model = env["usl.paperless.document.type"].with_env(admin.env)
document_types = {}
for name in sorted(
    {
        classification["document_type"]
        for classification in classifications.values()
        if classification["document_type"]
    },
    key=str.casefold,
):
    document_type = document_type_model.search(
        [("name", "=ilike", name), ("active", "=", True)],
        limit=1,
    )
    if not document_type:
        document_type = document_type_model.create(
            {
                "name": name,
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )
    document_types[name] = document_type

correspondent_model = env["usl.paperless.correspondent"].with_env(admin.env)
source_partner_by_name = {
    row["name"]: row["id"] for row in source["classification_partners"]
}


def group_correspondent_partner_id(group, classification):
    direct = sorted(
        (
            item["document_id"] or 1_000_000_000,
            item.get("partner_id") or item.get("move_partner_id"),
        )
        for item in group
        if item.get("partner_id") or item.get("move_partner_id")
    )
    if direct:
        return direct[0][1]
    for name in classification["institution_partner_names"]:
        if source_partner_by_name.get(name):
            return source_partner_by_name[name]
    return None


correspondent_partner_by_checksum = {
    group[0]["checksum"]: group_correspondent_partner_id(
        group,
        classifications[group[0]["checksum"]],
    )
    for group in groups
}
correspondents = {}
for source_partner_id in sorted(
    {value for value in correspondent_partner_by_checksum.values() if value},
):
    partner = partners[source_partner_id]
    result = correspondent_model.create_from_partner(partner.id)
    correspondents[source_partner_id] = correspondent_model.browse(result["id"])
archive_correspondents = {}
for name in sorted(
    {
        name
        for classification in classifications.values()
        if not classification["institution_partner_names"]
        for name in classification["archive_correspondent_names"]
    },
    key=str.casefold,
):
    correspondent = correspondent_model.search(
        [("name", "=ilike", name), ("active", "=", True)],
        limit=1,
    )
    if not correspondent:
        correspondent = correspondent_model.create(
            {
                "name": name,
                "matching_algorithm": "0",
                "is_insensitive": True,
            },
        )
    archive_correspondents[name] = correspondent

all_attachments = env["ir.attachment"].sudo().with_context(
    skip_res_field_check=True,
)
baseline_attachment_count = all_attachments.search_count([])
pending = []
completed = []
failed = []
submitted_group_count = 0
submitted_bytes = 0
reused_group_count = 0
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


def existing_document_for(content_sha256, metadata_hash, company):
    matches = documents.search(
        [
            ("availability_state", "!=", "permanently_deleted"),
            "|",
            ("company_id", "=", company.id),
            ("company_id", "=", False),
            "|",
            ("checksum", "=", content_sha256),
            ("version_ids.checksum", "=", content_sha256),
        ],
    )
    exact_matches = matches.filtered(
        lambda document: document._archive_fingerprint_version(
            content_sha256,
            metadata_hash,
        )[0],
    )
    if len(exact_matches) > 1:
        fail(
            f"target contains {len(exact_matches)} archive roots for exact "
            f"SHA-256/classification fingerprint {content_sha256}",
        )
    return exact_matches


def existing_processing_operation_for(content_sha256, company, target):
    operations = env["usl.document.operation"].sudo().search(
        [
            ("checksum", "=", content_sha256),
            ("state", "=", "processing"),
        ],
        order="id",
    )
    if len(operations) > 1:
        fail(
            f"target contains {len(operations)} processing archive operations "
            f"for SHA-256 {content_sha256}",
        )
    operation = operations[:1]
    if not operation:
        return operation
    if operation.company_id != company:
        fail(
            f"processing archive operation {operation.id} belongs to "
            f"{operation.company_id.display_name}, not {company.display_name}",
        )
    expected_target = (target._name, target.id) if target else (False, 0)
    operation_target = (operation.res_model or False, operation.res_id or 0)
    if operation_target != expected_target:
        fail(
            f"processing archive operation {operation.id} targets "
            f"{operation_target}, not {expected_target}",
        )
    return operation


def settle_pending(force=False):
    deadline = time.monotonic() + PROCESSING_TIMEOUT
    while pending and (force or len(pending) >= MAX_IN_FLIGHT):
        progressed = False
        for item in list(pending):
            operation = item["operation"]
            # Poll in the operation's legal-company scope.  The submitter may
            # deliberately have access to fewer companies than the archive
            # administrator, and Odoo rejects a with_user() environment whose
            # allowed-company context includes an unauthorized company.
            operation.with_context(
                allowed_company_ids=operation.company_id.ids,
            ).poll()
            env.cr.commit()
            operation.invalidate_recordset()
            if operation.state == "archived" and operation.document_id:
                item["document"] = operation.document_id.with_env(admin.env)
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
    try:
        company_scope = resolve_company_scope(group)
    except ValueError as error:
        fail(f"checksum {item['checksum']} has unsafe company scope: {error}")
    source_company_id = company_scope["company_id"]
    company = companies.get(source_company_id) or next(iter(companies.values()), env.company)
    target_records = business_targets(group)
    target = primary_business_target(target_records)
    classification = classifications[item["checksum"]]
    confidentiality = (
        "private"
        if any(entry["access_internal"] == "none" for entry in group)
        else "hr" if classification["hr_restricted"]
        else "accounting" if classification["accounting_evidence"] else "internal"
    )
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
    if target:
        archive_context = target.with_context(
            usl_documents_policy_origin="documents_workspace",
        )._document_archive_context(None)
    else:
        archive_context = {
            "company_id": company.id,
            "confidentiality": confidentiality,
            "accounting_evidence": False,
            "access_scope": "company",
            "archive_mode": "automatic",
            "document_role": "library",
            "attachment_origin": "documents_workspace",
            "policy_reason": "generic_documents_upload",
            "tags": [],
            "entity_tags": [],
            "tag_record_ids": [],
            "tag_paperless_ids": [],
            "related_records": [],
        }
    metadata_hash = documents_model._archive_metadata_hash(archive_context)
    existing = existing_document_for(
        paperless_sha256,
        metadata_hash,
        company,
    )
    processing_operation = (
        env["usl.document.operation"]
        if existing
        else existing_processing_operation_for(paperless_sha256, company, target)
    )
    task = {
        "index": index,
        "group": group,
        "content": paperless_content,
        "source_content": content,
        "sha256": source_sha256,
        "paperless_original_sha256": paperless_sha256,
        "source_truth": source_truth_payload(group),
        "company_scope": company_scope,
        "state": "reused" if existing else "processing",
    }
    if existing:
        reused_group_count += 1
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
    if processing_operation:
        task["operation"] = processing_operation
        task["recovered_operation_id"] = processing_operation.id
        pending.append(task)
        settle_pending()
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
    submitted_group_count += 1
    submitted_bytes += len(paperless_content)
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
    classification = classifications[source_item["checksum"]]
    document = item["document"].with_env(admin.env)
    if document.availability_state == "trashed":
        # Paperless exposes Trash through a separate API. Temporarily restore
        # the same stable root so metadata, bytes, preview and permissions can
        # be revalidated, then return all-inactive source groups to Trash.
        document.restore_from_trash()
    company_scope = item["company_scope"]
    source_company_id = company_scope["company_id"]
    company = companies.get(source_company_id)
    if any(entry.get("operational_attachment_id") for entry in group):
        company = company or next(iter(companies.values()), env.company)
    target_links = business_targets(group)
    target_moves = {
        target_id: target
        for (model_name, target_id), target in target_links.items()
        if model_name == "account.move"
    }
    confidentiality = (
        "private"
        if any(entry["access_internal"] == "none" for entry in group)
        else "hr" if classification["hr_restricted"]
        else "accounting" if classification["accounting_evidence"] else "internal"
    )
    tags = env["usl.paperless.tag"].browse(
        [tags_by_name[name].id for name in classification["tags"]],
    )
    document_type = document_types.get(classification["document_type"])
    correspondent_partner_id = correspondent_partner_by_checksum[source_item["checksum"]]
    correspondent = correspondents.get(correspondent_partner_id)
    if not correspondent and classification["archive_correspondent_names"]:
        correspondent = archive_correspondents[
            classification["archive_correspondent_names"][0]
        ]
    metadata = {
        "name": (
            f"{text(source_item['name']) or source_item['filename']} — searchable archive copy"
            if any(entry.get("operational_attachment_id") for entry in group)
            else text(source_item["name"]) or source_item["filename"]
        ),
        "document_date": fields.Date.to_string(classification["document_date"]),
        "tag_ids": sorted(tags.ids),
        "correspondent_id": correspondent.id if correspondent else False,
        "document_type_id": document_type.id if document_type else False,
    }
    document.update_archive_metadata(metadata)
    refreshed = client.get_document(document.paperless_id)
    if (
        any(entry.get("operational_attachment_id") for entry in group)
        and not str(refreshed.get("content") or "").strip()
    ):
        fail(
            f"searchable archive representation has no extracted text for "
            f"Paperless document {document.paperless_id}",
        )
    cache_values = documents._paperless_values(refreshed)
    cache_values.pop("source", None)
    document.sudo().with_context(usl_documents_cache_write=True).write(cache_values)
    document._synchronize_versions(refreshed.get("versions") or [])
    added_source = min(
        group,
        key=lambda entry: (
            entry["create_date"],
            entry["document_id"] or 1_000_000_000,
            entry["attachment_id"],
        ),
    )
    submitted_user = users.get(
        added_source.get("owner_id") or added_source.get("create_uid"),
    ) or admin
    provenance_values = {
        "submitted_by_id": submitted_user.id,
        "submitted_at": fields.Datetime.to_string(classification["added_at"]),
    }
    document.sudo().with_context(usl_documents_cache_write=True).write(
        provenance_values,
    )
    received_original = document.version_ids.filtered("is_received_original")[:1]
    if received_original:
        received_original.sudo().write(provenance_values)
    for target in target_moves.values():
        payroll = tese_payroll_by_move_id.get(target.id)
        if payroll:
            target_links["usl.tese.payslip", payroll.id] = payroll
    partner_source_ids = {
        source_id
        for entry in group
        for source_id in (entry.get("partner_id"), entry.get("move_partner_id"))
        if source_id
    }
    partner_source_ids.update(
        source_partner_by_name[name]
        for name in classification["institution_partner_names"]
        if source_partner_by_name.get(name)
    )
    target_links.update(
        {
            ("res.partner", partners[source_id].id): partners[source_id]
            for source_id in partner_source_ids
        },
    )
    target_links.update(
        {
            ("hr.employee", employee_by_folder[entry["folder_id"]].id):
                employee_by_folder[entry["folder_id"]]
            for entry in group
            if entry["folder_id"] in employee_by_folder
        },
    )
    if not company and target_links:
        linked_companies = env["res.company"]
        for (model_name, _target_id), target in target_links.items():
            linked_company = (
                target
                if model_name == "res.company"
                else getattr(target, "company_id", False) or env.company
            )
            linked_companies |= linked_company
        if len(linked_companies) != 1:
            fail(
                f"business context has conflicting companies for source checksum "
                f"{source_item['checksum']}: {linked_companies.ids}",
            )
        company = linked_companies
    document.sudo().with_context(usl_documents_policy_write=True).write(
        {
            "company_id": company.id if company else False,
            "confidentiality": confidentiality,
            "accounting_evidence": classification["accounting_evidence"],
            "review_state": (
                "classified"
                if company and not classification["needs_attention"]
                else "needs_attention"
            ),
        },
    )
    root_policy = resolve_root_role(
        record_models=(model_name for model_name, _target_id in target_links),
        tags=classification["tags"],
        accounting_evidence=classification["accounting_evidence"],
        confidentiality=confidentiality,
        explicit_documents_record=bool(group[0].get("document_id")),
    )
    if document.intake_role != root_policy["document_role"]:
        document.sudo().with_context(usl_documents_policy_write=True).write(
            {"intake_role": root_policy["document_role"]},
        )
    for (model_name, _target_id), target in sorted(target_links.items()):
        link_policy = resolve_link_role(
            res_model=model_name,
            root_policy=root_policy,
        )
        document.link_to_record(
            model_name,
            target.id,
            archive_mode=link_policy["archive_mode"],
            policy_role=link_policy["document_role"],
            attachment_origin="migration",
            policy_reason=link_policy["policy_reason"],
        )
        link = document.sudo().link_ids.filtered(
            lambda candidate: (
                candidate.res_model == model_name
                and candidate.res_id == target.id
            ),
        )[:1]
        link.sudo().write(
            {
                "linked_by_id": submitted_user.id,
                "linked_at": fields.Datetime.to_string(classification["added_at"]),
            },
        )
    actual_target_ids = set(
        document.sudo().link_ids.filtered(
            lambda link: link.active and link.res_model == "account.move",
        ).mapped("res_id"),
    )
    if actual_target_ids != set(target_moves):
        fail(
            f"accounting links differ for source checksum {source_item['checksum']}",
        )
    actual_link_keys = {
        (link.res_model, link.res_id)
        for link in document.sudo().link_ids.filtered("active")
    }
    if not set(target_links).issubset(actual_link_keys):
        fail(
            f"business links differ for source checksum {source_item['checksum']}: "
            f"missing {sorted(set(target_links) - actual_link_keys)}",
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
        tuple(sorted(set(view_users))), tuple(sorted(set(change_users))),
    ].append(document)
    if document.company_id.id != (company.id if company else False):
        fail(f"company policy differs for Paperless document {document.paperless_id}")
    if document.confidentiality != confidentiality:
        fail(
            f"confidentiality policy differs for Paperless document "
            f"{document.paperless_id}",
        )
    if set(tags.ids) != set(document.tag_ids.ids):
        fail(f"translated tags differ for Paperless document {document.paperless_id}")
    if document.correspondent_id.id != (correspondent.id if correspondent else False):
        fail(f"correspondent differs for Paperless document {document.paperless_id}")
    if document.document_type_id.id != (document_type.id if document_type else False):
        fail(f"document type differs for Paperless document {document.paperless_id}")
    if document.accounting_evidence != classification["accounting_evidence"]:
        fail(f"accounting classification differs for Paperless document {document.paperless_id}")
    expected_submitted_at = fields.Datetime.to_datetime(
        fields.Datetime.to_string(classification["added_at"]),
    )
    if document.submitted_at != expected_submitted_at:
        fail(f"source added date differs for Paperless document {document.paperless_id}")
    if not document.version_ids.filtered(
        lambda version: version.checksum == item["paperless_original_sha256"],
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
    item["derived_classification"] = classification
    item["restored_links"] = [
        {"model": model_name, "target_id": target_id}
        for model_name, target_id in sorted(target_links)
    ]
    item["restored_company_id"] = company.id if company else None
    item["source_company_ids"] = company_scope["source_company_ids"]
    item["superseded_inactive_company_ids"] = company_scope[
        "superseded_inactive_company_ids"
    ]
    item["source_added_at"] = classification["added_at"]
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


def load_remote_documents(documents):
    result = {}
    for offset in range(0, len(documents), 100):
        chunk = documents[offset:offset + 100]
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
        rows = payload.get("results", []) if isinstance(payload, dict) else payload
        result.update({int(remote["id"]): remote for remote in rows})
    return result


def permissions_match(remote, view_users, change_users):
    permissions = remote.get("permissions") or {}
    remote_view = permissions.get("view") or {}
    remote_change = permissions.get("change") or {}
    return (
        int(remote.get("owner") or 0) == PAPERLESS_SERVICE_USER_ID
        and set(remote_view.get("users") or []) == set(view_users)
        and set(remote_change.get("users") or []) == set(change_users)
        and not remote_view.get("groups")
        and not remote_change.get("groups")
    )


for (view_users, change_users), grouped_documents in permission_groups.items():
    remote_documents = load_remote_documents(grouped_documents)
    expected_remote_ids = {document.paperless_id for document in grouped_documents}
    if set(remote_documents) != expected_remote_ids:
        fail("Paperless permission read-back returned a different document set")
    changed_documents = [
        document
        for document in grouped_documents
        if not permissions_match(
            remote_documents[document.paperless_id], view_users, change_users,
        )
    ]
    if changed_documents:
        payload = client._request(
            "POST",
            "/api/documents/bulk_edit/",
            body={
                "documents": sorted(
                    document.paperless_id for document in changed_documents
                ),
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
                "Paperless 3.0.5 returned an incompatible bulk-permission response: "
                f"{result!r}",
            )
        remote_documents.update(load_remote_documents(changed_documents))
    print(
        "DOCUMENTS_PERMISSION_WRITE="
        + json.dumps(
            {
                "document_count": len(changed_documents),
                "state": "accepted" if changed_documents else "unchanged",
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
    for document in grouped_documents:
        remote = remote_documents[document.paperless_id]
        if not permissions_match(remote, view_users, change_users):
            fail(
                f"actual Paperless permissions differ for document "
                f"{document.paperless_id}",
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

# The original implementation copied reconstruction evidence into Paperless
# custom fields and created every source catalog tag, including configuration
# tags that had never classified a document.  Finalization keeps the complete
# source truth in the sealed manifest and leaves only useful live metadata.
tag_model.synchronize_catalog(client=client)
all_migrated_documents = documents.sudo().search([])
assigned_tag_ids = set(all_migrated_documents.tag_ids.ids)
source_tag_candidate_names = {
    name
    for row in source["tags"]
    for name in (
        text(row["name"]),
        normalized_source_tag(text(row["name"])),
    )
    if name
}
unused_source_tags = tag_model.search(
    [
        ("active", "=", True),
        ("name", "in", sorted(source_tag_candidate_names)),
        ("id", "not in", sorted(assigned_tag_ids)),
    ],
)
pruned_empty_tags = sorted(unused_source_tags.mapped("name"), key=str.casefold)
if unused_source_tags:
    unused_source_tags.unlink()

correspondent_model.synchronize_catalog(client=client)
assigned_correspondent_ids = set(
    all_migrated_documents.correspondent_id.ids,
)
unused_source_correspondents = correspondent_model.search(
    [
        ("active", "=", True),
        ("partner_id", "in", [partner.id for partner in partners.values()]),
        ("id", "not in", sorted(assigned_correspondent_ids)),
    ],
)
pruned_empty_correspondents = sorted(
    unused_source_correspondents.mapped("name"),
    key=str.casefold,
)
if unused_source_correspondents:
    unused_source_correspondents.unlink()

document_type_model.synchronize_catalog(client=client)
assigned_document_type_ids = set(
    all_migrated_documents.document_type_id.ids,
)
unused_migration_types = document_type_model.search(
    [
        ("active", "=", True),
        ("name", "in", sorted(set(document_types) | {"KBis"})),
        ("id", "not in", sorted(assigned_document_type_ids)),
    ],
)
pruned_empty_document_types = sorted(
    unused_migration_types.mapped("name"),
    key=str.casefold,
)
if unused_migration_types:
    unused_migration_types.unlink()

documents._sync_metadata_catalogs(client)
legacy_custom_fields_after = [
    definition
    for definition in client.list_custom_fields()
    if (definition.get("name") or "").startswith("Legacy Odoo ")
]
if legacy_custom_fields_after:
    fail("legacy reconstruction fields remain in the live Paperless catalog")
active_tag_names = set(tag_model.search([("active", "=", True)]).mapped("name"))
excluded_empty_source_tags = sorted(
    source_tag_candidate_names - active_tag_names,
    key=str.casefold,
)
active_correspondent_names = set(
    correspondent_model.search([("active", "=", True)]).mapped("name"),
)
excluded_empty_source_correspondents = sorted(
    {
        partner.display_name
        for partner in partners.values()
        if partner.display_name not in active_correspondent_names
    },
    key=str.casefold,
)
active_document_type_names = set(
    document_type_model.search([("active", "=", True)]).mapped("name"),
)
excluded_empty_source_document_types = sorted(
    (set(document_types) | {"KBis"}) - active_document_type_names,
    key=str.casefold,
)
if tag_model.search_count(
    [
        ("active", "=", True),
        ("name", "in", sorted(source_tag_candidate_names)),
        ("id", "not in", sorted(assigned_tag_ids)),
    ],
):
    fail("empty source-only tags remain in the live Paperless catalog")
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

expected_relationships = {
    (
        item["odoo_document_id"],
        link["model"],
        link["target_id"],
    )
    for item in completed
    for link in item["restored_links"]
}
migrated_document_ids = [item["odoo_document_id"] for item in completed]
actual_relationships = {
    (link.document_id.id, link.res_model, link.res_id)
    for link in env["usl.document.link"].sudo().search(
        [("document_id", "in", migrated_document_ids), ("active", "=", True)],
    )
}
unexpected_relationships = actual_relationships - expected_relationships
governed_extension_relationships = {
    relationship
    for relationship in unexpected_relationships
    if relationship[1] in GOVERNED_POST_DOCUMENTS_LINK_MODELS
}
unsupported_relationships = (
    unexpected_relationships - governed_extension_relationships
)
if expected_relationships - actual_relationships or unsupported_relationships:
    fail(
        "the finalized business relationship set is not deterministic; "
        f"missing={sorted(expected_relationships - actual_relationships)}, "
        f"unexpected={sorted(unsupported_relationships)}",
    )
relationships_by_model = {
    model_name: sum(
        relationship[1] == model_name for relationship in expected_relationships
    )
    for model_name in sorted({item[1] for item in expected_relationships})
}
classification_tag_counts = {
    name: sum(name in item["derived_classification"]["tags"] for item in completed)
    for name in sorted(
        {
            tag
            for item in completed
            for tag in item["derived_classification"]["tags"]
        },
        key=str.casefold,
    )
}
classification_type_counts = {
    name: sum(
        item["derived_classification"]["document_type"] == name
        for item in completed
    )
    for name in sorted(
        {
            item["derived_classification"]["document_type"]
            for item in completed
            if item["derived_classification"]["document_type"]
        },
        key=str.casefold,
    )
}
classification_reconciliation = env[
    "usl.document"
].reconcile_linked_classification(limit=0)
env.cr.commit()

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
        "perimeter",
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
        f"expected={expected_after_attachment_count}, after={after_attachment_count}",
    )
result = {
    "schema": "usl-documents-source-restore-result-v1",
    "source_snapshot": SOURCE_SNAPSHOT,
    "source_dump_sha256": SOURCE_DUMP_SHA256,
    "paperless_version": compatibility["server_version"],
    "paperless_api_version": compatibility["api_version"],
    "limited_run": bool(SOURCE_LIMIT),
    "source_profile": SOURCE_PROFILE,
    "source_profile_is_full": SOURCE_PROFILE == "full" and not SOURCE_LIMIT,
    "source_document_identities": expected_source_documents,
    "source_unassigned_evidence": sum(
        entry["kind"] in {"unassigned_evidence", "restricted_unassigned_evidence"}
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
    "source_employee_folder_mappings": source["employee_folder_mappings"],
    "source_project_folder_mappings": source["project_folder_mappings"],
    "source_classification_partners": source["classification_partners"],
    "source_access_relationship_count": len(source["accesses"]),
    "checksum_groups": len(groups),
    "archived_groups": len(completed),
    "ocr_submissions": submitted_group_count,
    "reused_roots": reused_group_count,
    "submitted_bytes": submitted_bytes,
    "failed_groups": len(failed),
    "odoo_attachment_count_before": baseline_attachment_count,
    "odoo_attachment_count_after": after_attachment_count,
    "quarantined_attachment_count": sum(len(item.get("fallback_ids", [])) for item in failed),
    "cleaned_quarantine_attachment_count": len(cleaned_quarantine_ids),
    "cleaned_operational_duplicate_attachment_count": len(
        cleaned_operational_duplicate_ids,
    ),
    "retained_operational_attachment_ids": retained_operational_attachment_ids,
    "created_operational_attachment_count": len(created_operational_attachment_ids),
    "legacy_custom_fields_after": [],
    "excluded_empty_source_tags": excluded_empty_source_tags,
    "excluded_empty_source_correspondents": excluded_empty_source_correspondents,
    "excluded_empty_source_document_types": excluded_empty_source_document_types,
    "restored_relationship_count": len(expected_relationships),
    "restored_relationships_by_model": relationships_by_model,
    "preserved_governed_extension_relationship_count": len(
        governed_extension_relationships,
    ),
    "preserved_governed_extension_relationships_by_model": {
        model_name: sum(
            relationship[1] == model_name
            for relationship in governed_extension_relationships
        )
        for model_name in sorted(GOVERNED_POST_DOCUMENTS_LINK_MODELS)
    },
    "classification_tag_counts": classification_tag_counts,
    "classification_type_counts": classification_type_counts,
    "classification_reconciliation": classification_reconciliation,
    "source_added_dates_preserved": len(completed),
    "failed": failed,
    "documents": completed,
}
print("DOCUMENTS_SOURCE_RESTORE_RESULT=" + json.dumps(result, sort_keys=True, default=str))
if failed:
    fail(f"{len(failed)} checksum group(s) remain in migration quarantine")
