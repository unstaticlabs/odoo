# ruff: noqa: F821, T201
"""Restore completed Odoo Online Sign records as non-verified external archives."""

import base64
import hashlib
import json
import os
import re
import sys
import textwrap
import time
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from markupsafe import Markup, escape
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from odoo import fields
from odoo.tools.pdf import PdfReader

from odoo.addons.usl_sign.models.constants import INTERNAL_OPERATION
from odoo.addons.usl_sign.services import field_content, field_value

sys.path.insert(0, "/mnt/sign-restore-migration")
from source import (  # noqa: E402
    SourceReader,
    canonical_json,
    history_payload,
    identity_search_domains,
    match_exports,
    redact_historical_links,
    sha256,
    source_datetime,
    source_options,
    text,
)

SOURCE_SNAPSHOT = os.environ["SIGN_SOURCE_SNAPSHOT"]
SOURCE_DUMP_SHA256 = os.environ["SIGN_SOURCE_DUMP_SHA256"]
EXPORT_DIRECTORY = Path(
    os.getenv("SIGN_EXPORT_DIRECTORY", "/mnt/accounting-source/sign"),
)
PAPERLESS_URL = os.environ["SIGN_PAPERLESS_URL"].rstrip("/")
PAPERLESS_PUBLIC_URL = os.environ["SIGN_PAPERLESS_PUBLIC_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["SIGN_PAPERLESS_TOKEN"]
PAPERLESS_SERVICE_USER_ID = int(os.environ["SIGN_PAPERLESS_SERVICE_USER_ID"])
TIMEOUT = max(60, int(os.getenv("SIGN_RESTORE_TIMEOUT", "900")))
XMLID_MODULE = "usl_sign_restore"


def fail(message):
    raise RuntimeError(f"Sign restore: {message}")


def xmlid(name, model=None):
    binding = env["ir.model.data"].sudo().search(  # noqa: F821
        [("module", "=", XMLID_MODULE), ("name", "=", name)],
        limit=1,
    )
    if not binding:
        return env[model] if model else False  # noqa: F821
    if model and binding.model != model:
        fail(f"XML ID {name} changed model from {model} to {binding.model}")
    return env[binding.model].sudo().browse(binding.res_id).exists()  # noqa: F821


def bind(name, record):
    existing = xmlid(name, record._name)
    if existing and existing != record:
        fail(f"XML ID {name} already identifies another record")
    if not existing:
        env["ir.model.data"].sudo().create(  # noqa: F821
            {
                "module": XMLID_MODULE,
                "name": name,
                "model": record._name,
                "res_id": record.id,
                "noupdate": True,
            },
        )


def source_identity(model, source_id, *, email=None, login=None):
    records = env[model].sudo().with_context(active_test=False)  # noqa: F821
    if "rebuild_source_id" in records._fields:
        record = records.search(
            [
                ("rebuild_source_model", "=", model),
                ("rebuild_source_id", "=", source_id),
                ("rebuild_source_snapshot", "=", SOURCE_SNAPSHOT),
            ],
        )
        if len(record) == 1:
            return record
        if len(record) > 1:
            fail(f"Ambiguous restored {model} source identity {source_id}")
    for identity_name, domain in identity_search_domains(
        model,
        login=login,
        email=email,
    ):
        candidates = records.search(domain)
        if len(candidates) == 1:
            return candidates
        if len(candidates) > 1:
            fail(
                f"Ambiguous target {model} {identity_name} for source id {source_id}",
            )
    return fail(f"Could not resolve one target {model} for source id {source_id}")


def optional_source_partner(source_id, *, email=None):
    """Resolve a historical chatter author only when the match is unambiguous."""
    if not source_id:
        return env["res.partner"]  # noqa: F821
    records = env["res.partner"].sudo().with_context(active_test=False)  # noqa: F821
    if "rebuild_source_id" in records._fields:
        traced = records.search(
            [
                ("rebuild_source_model", "=", "res.partner"),
                ("rebuild_source_id", "=", source_id),
                ("rebuild_source_snapshot", "=", SOURCE_SNAPSHOT),
            ],
        )
        if len(traced) == 1:
            return traced
        if len(traced) > 1:
            fail(f"Ambiguous restored res.partner source identity {source_id}")
    if not email:
        return records.browse()
    candidates = records.search([("email", "=ilike", email)])
    return candidates if len(candidates) == 1 else records.browse()


def pdf_text(content):
    reader = PdfReader(BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def verify_certificate(content, request_id, signer_emails):
    extracted = pdf_text(content)
    identifier = re.compile(
        rf"(?:Document ID|ID du document)\s*:?\s*{request_id}(?:\D|$)",
        re.IGNORECASE,
    )
    if not identifier.search(extracted):
        fail(f"Exported certificate does not identify source request {request_id}")
    missing = [email for email in signer_emails if email and email.casefold() not in extracted.casefold()]
    if missing:
        fail(f"Certificate for request {request_id} omits signer emails {missing}")


def history_pdf(content, request_id):
    """Render the complete sanitized JSON as an archive-compatible PDF."""
    stream = BytesIO()
    page = canvas.Canvas(stream, pagesize=A4, pageCompression=1, invariant=1)
    _width, height = A4
    left = 36
    top = height - 38
    line_height = 8

    def heading():
        page.setFont("Helvetica-Bold", 12)
        page.drawString(left, top, f"Odoo Online Sign request {request_id} — external history")
        page.setFont("Helvetica", 8)
        page.drawString(
            left,
            top - 16,
            "Preservation record only; not a USL signature, identity, trust, or validation decision.",
        )
        page.drawString(left, top - 28, f"Canonical JSON SHA-256: {sha256(content)}")
        page.setFont("Courier", 6.5)
        return top - 46

    y = heading()
    printable = content.decode("utf-8").encode("ascii", "backslashreplace").decode("ascii")
    for source_line in printable.splitlines():
        lines = textwrap.wrap(
            source_line,
            width=126,
            replace_whitespace=False,
            drop_whitespace=False,
        ) or [""]
        for line in lines:
            if y < 34:
                page.showPage()
                y = heading()
            page.drawString(left, y, line)
            y -= line_height
    page.save()
    return stream.getvalue()


def metadata_record(model, name, **values):
    record = env[model].sudo().search(  # noqa: F821
        [("name", "=ilike", name), ("active", "=", True)],
        limit=1,
    )
    if not record:
        record = env[model].sudo().create({"name": name, **values})  # noqa: F821
    return record


def await_upload(result):
    if result["state"] == "duplicate" and result.get("document_id"):
        return env["usl.document"].sudo().browse(result["document_id"]).exists()  # noqa: F821
    operation = env["usl.document.operation"].sudo().browse(  # noqa: F821
        result.get("operation_id"),
    ).exists()
    if not operation:
        fail(f"Paperless upload returned no usable operation: {result}")
    deadline = time.monotonic() + TIMEOUT
    while operation.state in {"uploading", "processing"} and time.monotonic() < deadline:
        operation.poll()
        env.cr.commit()  # noqa: F821
        operation.invalidate_recordset()
        if operation.state in {"uploading", "processing"}:
            time.sleep(1)
    if operation.state != "archived" or not operation.document_id:
        fail(f"Paperless operation {operation.id} ended in {operation.state}: {operation.error_message}")
    return operation.document_id


def archive_file(admin, *, filename, content, company, document_date, tags, document_type):
    tag_records = env["usl.paperless.tag"].browse()  # noqa: F821
    for tag_name in tags:
        tag_records |= metadata_record(
            "usl.paperless.tag",
            tag_name,
            color="#4e79a7",
            matching_algorithm="0",
            is_insensitive=True,
        )
    type_record = metadata_record(
        "usl.paperless.document.type",
        document_type,
        matching_algorithm="0",
        is_insensitive=True,
    )
    result = (
        env["usl.document"]  # noqa: F821
        .with_env(admin.env)
        .upload_from_odoo(
            filename,
            base64.b64encode(content),
            "application/pdf" if filename.lower().endswith(".pdf") else "text/plain",
            company_id=company.id,
            confidentiality="private",
            source="odoo_upload",
            document_date=document_date,
            document_type_id=type_record.id,
            tag_ids=tag_records.ids,
        )
    )
    reused = result["state"] == "duplicate"
    document = await_upload(result)
    if reused:
        # A full reconstruction may already contain the exact source binary
        # from the Documents stage. The ingestion race is over for an existing
        # root, so merge only the Sign purpose tags without replacing its
        # source classification or creating another Paperless document.
        document.with_env(admin.env).update_archive_metadata(
            {"tag_ids": sorted(set(document.tag_ids.ids) | set(tag_records.ids))},
        )
        document.invalidate_recordset()
    if (
        (not reused and document.document_type_id != type_record)
        or not set(tag_records.ids).issubset(document.tag_ids.ids)
    ):
        fail(f"Paperless metadata did not persist atomically for {filename}")
    return document


def role_for(name):
    name = text(name).strip() or "Signer"
    role = env["sign.oca.role"].sudo().search([("name", "=ilike", name)], limit=1)  # noqa: F821
    return role or env["sign.oca.role"].sudo().create(  # noqa: F821
        {"name": name, "partner_selection_policy": "empty", "domain": "[]"},
    )


def archived_message(request_record, name, values):
    existing = xmlid(name, "mail.message")
    if existing:
        if existing.model != request_record._name or existing.res_id != request_record.id:
            fail(f"History binding {name} points to another request")
        return existing
    expected_date = source_datetime(values["date"])
    expected_subject = values.get("subject") or False
    expected_type = values.get("message_type") or "comment"
    expected_author_id = values.get("author_id") or False
    expected_email_from = values.get("email_from") or False
    candidates = request_record.message_ids.sudo().filtered(
        lambda message: (
            message.date == expected_date
            and (message.subject or False) == expected_subject
            and message.message_type == expected_type
            and (message.author_id.id or False) == expected_author_id
            and (message.email_from or False) == expected_email_from
        ),
    )
    if len(candidates) > 1:
        fail(f"Ambiguous finalized history message {name}")
    if candidates:
        candidate = candidates[0]
        if str(candidate.body or "") != str(values["body"]):
            fail(f"Finalized history message {name} changed body")
        bind(name, candidate)
        return candidate
    message = env["mail.message"].sudo().create(  # noqa: F821
        {
            "model": request_record._name,
            "res_id": request_record.id,
            "message_type": values.get("message_type") or "comment",
            "subtype_id": env.ref("mail.mt_note").id,  # noqa: F821
            "subject": values.get("subject") or False,
            "body": values["body"],
            "author_id": values.get("author_id") or False,
            "email_from": values.get("email_from") or False,
            "date": expected_date,
        },
    )
    env.cr.execute(  # noqa: F821
        "UPDATE mail_message SET create_date = %s, write_date = %s WHERE id = %s",
        [expected_date, expected_date, message.id],
    )
    bind(name, message)
    return message


parameters = env["ir.config_parameter"].sudo()  # noqa: F821
parameters.set_str("usl_documents.paperless_url", PAPERLESS_URL)
parameters.set_str("usl_documents.paperless_public_url", PAPERLESS_PUBLIC_URL)
parameters.set_str("usl_documents.paperless_token", PAPERLESS_TOKEN)
parameters.set_int("usl_documents.paperless_service_user_id", PAPERLESS_SERVICE_USER_ID)
parameters.set_int("usl_documents.paperless_timeout", TIMEOUT)

reader = SourceReader(source_options())
source = reader.read()
matches = match_exports(source, EXPORT_DIRECTORY)
request_signers = defaultdict(list)
for signer in source["signers"]:
    request_signers[signer["sign_request_id"]].append(signer)
request_messages = defaultdict(list)
for message in source["messages"]:
    request_messages[message["sign_request_id"]].append(message)
request_logs = defaultdict(list)
for log in source["logs"]:
    request_logs[log["sign_request_id"]].append(log)
request_attachments = defaultdict(list)
for attachment in source["attachments"]:
    request_attachments[attachment["sign_request_id"]].append(attachment)
request_classifications = defaultdict(list)
for classification in source["classifications"]:
    request_classifications[classification["sign_request_id"]].append(
        text(classification["name"]),
    )

companies = env["res.company"].sudo().search([])  # noqa: F821
company = companies.filtered(lambda item: item.name == "Unstatic Labs")[:1] or env.company  # noqa: F821
admin = (
    env.ref("base.user_root")  # noqa: F821
    .with_company(company)
    .with_context(allowed_company_ids=company.ids)
)
documents = env["usl.document"].with_env(admin.env)  # noqa: F821
client = documents._paperless()
compatibility = client.compatibility()
if compatibility["api_version"] != "10":
    fail(f"Paperless API v10 is required, got {compatibility['api_version']}")
client.ensure_fail_closed_ingestion_policy()
documents._sync_metadata_catalogs(client)

statistics = {
    "source_snapshot": SOURCE_SNAPSHOT,
    "source_dump_sha256": SOURCE_DUMP_SHA256,
    "requests": 0,
    "signers": 0,
    "messages": 0,
    "audit_events": 0,
    "paperless_links": 0,
    "records": [],
}
used_original_ids = {row["original_attachment_id"] for row in source["requests"]}
inactive_template_attachments = [
    row
    for row in source["attachment_inventory"]
    if row["res_model"] == "sign.document" and row["id"] not in used_original_ids
]
inactive_template_checksums = {
    row["checksum"] for row in inactive_template_attachments
}

for request_row in source["requests"]:
    source_id = request_row["id"]
    artifact_match = matches[source_id]
    signed_content = artifact_match["signed"].read_bytes()
    certificate_content = artifact_match["certificate"].read_bytes()
    signers = request_signers[source_id]
    signer_emails = [row["signer_email"] or row["partner_email"] for row in signers]
    verify_certificate(certificate_content, source_id, signer_emails)
    completion_at = max(
        source_datetime(value)
        for value in (
            [row["signing_date"] for row in signers if row["signing_date"]]
            + [request_row["completion_date"]]
        )
    )
    classification_tags = sorted(set(request_classifications[source_id]), key=str.casefold)
    common_tags = ["Odoo Online (External)", *classification_tags]
    signed_document = archive_file(
        admin,
        filename=artifact_match["signed"].name,
        content=signed_content,
        company=company,
        document_date=fields.Date.to_date(completion_at),
        tags=[*common_tags, "Signed document"],
        document_type="Signed agreement",
    )
    exported_certificate = archive_file(
        admin,
        filename=artifact_match["certificate"].name,
        content=certificate_content,
        company=company,
        document_date=fields.Date.to_date(completion_at),
        tags=[*common_tags, "Signing certificate"],
        document_type="Signing certificate",
    )
    original_content = reader.binary(
        {
            "id": request_row["original_attachment_id"],
            "store_fname": request_row["original_store_fname"],
            "file_size": request_row["original_file_size"],
            "checksum": request_row["original_checksum"],
        },
    )
    original_document = archive_file(
        admin,
        filename=request_row["original_filename"],
        content=original_content,
        company=company,
        document_date=fields.Date.to_date(request_row["create_date"]),
        tags=[
            *common_tags,
            "Original signing document",
            *(
                ["Inactive Odoo Online template"]
                if request_row["original_checksum"] in inactive_template_checksums
                else []
            ),
        ],
        document_type="Signing source document",
    )
    source_certificate_row = next(
        row for row in request_attachments[source_id] if row["kind"] == "source_certificate"
    )
    source_certificate_content = reader.binary(source_certificate_row)
    source_certificate = archive_file(
        admin,
        filename=f"Source attachment - {source_certificate_row['name']}",
        content=source_certificate_content,
        company=company,
        document_date=fields.Date.to_date(completion_at),
        tags=[*common_tags, "Source completion certificate"],
        document_type="Signing certificate",
    )
    history_content = canonical_json(history_payload(source, source_id))
    history_document = archive_file(
        admin,
        filename=f"odoo-online-sign-request-{source_id}-history.pdf",
        content=history_pdf(history_content, source_id),
        company=company,
        document_date=fields.Date.to_date(completion_at),
        tags=[*common_tags, "Signing history"],
        document_type="Signing history",
    )

    creator = source_identity(
        "res.users",
        request_row.get("create_uid", 0),
        login=request_row["creator_login"],
        email=request_row["creator_email"],
    )
    signer_expectations = []
    signer_commands = []
    for index, signer in enumerate(signers, start=1):
        partner = source_identity(
            "res.partner",
            signer["partner_id"],
            email=signer["signer_email"] or signer["partner_email"],
        )
        role = role_for(signer["role_name"])
        sequence = (signer["mail_sent_order"] or index) * 10
        signer_expectations.append((signer, partner, role, sequence))
        signer_commands.append(
            (
                0,
                0,
                {
                    "partner_id": partner.id,
                    "role_id": role.id,
                    "sequence": sequence,
                    "signed_on": source_datetime(signer["signing_date"]),
                },
            ),
        )

    request_record = xmlid(f"request_{source_id}", "sign.oca.request")
    request_created = False
    if not request_record:
        finalized_candidates = env["sign.oca.request"].sudo().search(  # noqa: F821
            [
                ("record_kind", "=", "external_archive"),
                ("state", "=", "external_archived"),
                ("original_sha256", "=", artifact_match["signed_sha256"]),
                ("archive_document_id", "=", signed_document.id),
                ("archive_dossier_document_id", "=", exported_certificate.id),
                ("company_id", "=", company.id),
            ],
        )
        if len(finalized_candidates) > 1:
            fail(f"Ambiguous finalized request for source request {source_id}")
        request_record = finalized_candidates
    if not request_record:
        request_record = env["sign.oca.request"].with_env(admin.env)._create_external_archive(  # noqa: F821
            {
                "name": request_row["reference"] or artifact_match["signed"].stem,
                "data": field_value(signed_content),
                "filename": artifact_match["signed"].name,
                "current_hash": hashlib.sha1(signed_content, usedforsecurity=False).hexdigest(),
                "original_sha256": sha256(signed_content),
                "company_id": company.id,
                "user_id": creator.id,
                "responsible_message": request_row["message"] or False,
                "completed_at": completion_at,
                "archive_document_id": signed_document.id,
                "archive_dossier_document_id": exported_certificate.id,
                "signer_ids": signer_commands,
                "create_date": source_datetime(request_row["create_date"]),
            },
        )
        request_created = True
    expected_archives = {
        "archive_document_id": signed_document,
        "archive_dossier_document_id": exported_certificate,
    }
    archive_changes = {}
    for field_name, expected_document in expected_archives.items():
        current_document = request_record[field_name]
        if current_document and current_document != expected_document:
            fail(f"Finalized request {source_id} changed {field_name}")
        if not current_document:
            archive_changes[field_name] = expected_document.id
    if archive_changes:
        request_record.with_context(
            usl_sign_transition=INTERNAL_OPERATION,
            usl_sign_freeze=INTERNAL_OPERATION,
        ).write(archive_changes)
    if request_record.user_id != creator:
        fail(f"Finalized request {source_id} changed creator")
    if sha256(field_content(request_record.data)) != artifact_match["signed_sha256"]:
        fail(f"Finalized request {source_id} changed signed content")
    bind(f"request_{source_id}", request_record)
    target_signers = request_record.signer_ids.sorted(lambda signer: (signer.sequence, signer.id))
    if len(target_signers) != len(signer_expectations):
        fail(f"Finalized request {source_id} changed signer count")
    for (source_signer, partner, role, sequence), target_signer in zip(
        signer_expectations,
        target_signers,
    ):
        expected_signed_on = source_datetime(source_signer["signing_date"])
        if (
            target_signer.partner_id != partner
            or target_signer.role_id != role
            or target_signer.sequence != sequence
            or target_signer.signed_on != expected_signed_on
            or target_signer.state != "external_recorded"
            or target_signer.authentication_method != "external_record"
        ):
            fail(f"Finalized signer {source_signer['id']} changed business identity")
        bind(f"signer_{source_signer['id']}", target_signer)
        if request_created:
            env.cr.execute(  # noqa: F821
                "UPDATE sign_oca_request_signer SET create_date=%s, write_date=%s WHERE id=%s",
                [source_signer["create_date"], source_signer["write_date"], target_signer.id],
            )
    if request_created:
        env.cr.execute(  # noqa: F821
            "UPDATE sign_oca_request SET create_date=%s, write_date=%s WHERE id=%s",
            [request_row["create_date"], request_row["write_date"], request_record.id],
        )

    participants = request_record.user_id.partner_id | request_record.signer_ids.partner_id
    archived_documents = (
        signed_document
        | exported_certificate
        | original_document
        | source_certificate
        | history_document
    )
    for document in archived_documents:
        document.with_env(admin.env).link_to_record(request_record._name, request_record.id)
        for partner in participants:
            document.with_env(admin.env).link_to_record("res.partner", partner.id)
        document.with_env(admin.env).action_sync_permissions()
        statistics["paperless_links"] += 1

    archived_message(
        request_record,
        f"request_{source_id}_notice",
        {
            "subject": "Odoo Online external signing record",
            "body": Markup(
                "<p><strong>Imported from Odoo Online as an external record.</strong></p>"
                "<p>The signed PDF, original Odoo Online certificate, source PDF, "
                "source-time certificate, and a sanitized history export are preserved "
                "in Documents. USL Sign did not rerun or certify the historical signing, "
                "identity, trust, certificate, or revocation checks.</p>",
            ),
            "date": request_row["create_date"],
            "author_id": request_record.user_id.partner_id.id,
        },
    )
    for message in request_messages[source_id]:
        author = optional_source_partner(
            message["author_id"],
            email=message["author_email"],
        )
        archived_message(
            request_record,
            f"message_{message['id']}",
            {
                "subject": message["subject"],
                "body": Markup(
                    "<div class='text-muted'><em>Preserved Odoo Online message</em></div>",
                )
                + Markup(redact_historical_links(message["body"])),
                "message_type": "comment",
                "date": message["date"],
                "author_id": author.id,
                "email_from": message["email_from"],
            },
        )
    signer_by_source = {
        source_signer["id"]: target_signer
        for source_signer, target_signer in zip(signers, request_record.signer_ids)
    }
    for log in request_logs[source_id]:
        details = [
            f"Action: {escape(log['action'])}",
            f"Recorded request state: {escape(log['request_state'] or '—')}",
        ]
        if log["sign_request_item_id"] in signer_by_source:
            details.append(
                f"Signer: {escape(signer_by_source[log['sign_request_item_id']].partner_id.display_name)}",
            )
        if log["ip"]:
            details.append(f"IP recorded by Odoo Online: {escape(log['ip'])}")
        if log["latitude"] is not None and log["longitude"] is not None:
            details.append(
                f"Location recorded by Odoo Online: {log['latitude']}, {log['longitude']}",
            )
        if log["log_hash"]:
            details.append(f"Source log hash: {escape(log['log_hash'])}")
        archived_message(
            request_record,
            f"log_{log['id']}",
            {
                "subject": f"Odoo Online audit event: {log['action']}",
                "body": Markup("<p><em>Preserved external audit event; not a USL Sign event.</em></p><ul><li>")
                + Markup("</li><li>").join(details)
                + Markup("</li></ul>"),
                "date": log["log_date"] or log["create_date"],
            },
        )

    statistics["requests"] += 1
    statistics["signers"] += len(signers)
    statistics["messages"] += len(request_messages[source_id]) + 1
    statistics["audit_events"] += len(request_logs[source_id])
    statistics["records"].append(
        {
            "source_request_id": source_id,
            "target_request_id": request_record.id,
            "signed_sha256": artifact_match["signed_sha256"],
            "certificate_sha256": artifact_match["certificate_sha256"],
            "history_sha256": sha256(history_content),
            "paperless_document_ids": sorted(archived_documents.ids),
        },
    )
    env.cr.commit()  # noqa: F821

inactive_template_documents = []
for attachment in inactive_template_attachments:
    content = reader.binary(attachment)
    document = archive_file(
        admin,
        filename=attachment["name"],
        content=content,
        company=company,
        document_date=fields.Date.context_today(env["usl.document"]),  # noqa: F821
        tags=["Odoo Online (External)", "Inactive Odoo Online template"],
        document_type="Signing source document",
    )
    document.with_env(admin.env).link_to_record("res.company", company.id)
    document.with_env(admin.env).action_sync_permissions()
    inactive_template_documents.append(
        {
            "source_attachment_id": attachment["id"],
            "checksum": attachment["checksum"],
            "paperless_document_id": document.id,
        },
    )

statistics["inactive_template_documents"] = inactive_template_documents
statistics["attachment_disposition"] = {
    "total": len(source["attachment_inventory"]),
    "archived_or_checksum_reused": len(
        [
            row
            for row in source["attachment_inventory"]
            if row["res_model"] in {"sign.document", "sign.completed.document", "sign.request"}
            and row.get("res_field") != "signature"
        ],
    ),
    "rendered_marks_not_reusable": len(
        [row for row in source["attachment_inventory"] if row["res_model"] == "sign.request.item"],
    ),
    "user_preferences_not_reusable": len(
        [row for row in source["attachment_inventory"] if row["res_model"] == "res.users"],
    ),
}

print("SIGN_RESTORE_RESULT=" + json.dumps(statistics, sort_keys=True))
