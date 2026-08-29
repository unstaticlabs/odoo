"""Read-only Odoo Online Sign extraction and deterministic artifact matching."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def text(value):
    if isinstance(value, dict):
        return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
    return value or ""


def normalized_name(value):
    return unicodedata.normalize("NFC", (value or "").strip()).casefold()


def source_datetime(value):
    """Parse a source timestamp without discarding PostgreSQL microseconds."""
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def redact_historical_links(value):
    value = value or ""
    value = re.sub(
        r"(?i)(?:https?://[^\s\"'<>]+)?/(?:sign|sign_oca)/(?:document/)?[^\s\"'<>]+",
        "[redacted historical signing link]",
        value,
    )
    return re.sub(
        r"(?i)(access_token|token)=([^&\s\"'<>]+)",
        r"\1=[redacted]",
        value,
    )


def sha1(content):
    return hashlib.sha1(content, usedforsecurity=False).hexdigest()


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def identity_search_domains(model, *, login=None, email=None):
    """Return ordered, stable identity fallbacks for reconstructed records."""
    domains = []
    if model == "res.users":
        if login:
            domains.append(("login", [("login", "=ilike", login)]))
        if email:
            domains.append(
                ("linked partner email", [("partner_id.email", "=ilike", email)]),
            )
    elif email:
        domains.append(("email", [("email", "=ilike", email)]))
    return domains


class SourceReader:
    def __init__(self, options):
        self.options = options
        self.filestore = Path(options["filestore"]).resolve()

    def _connect(self):
        import psycopg2  # noqa: PLC0415
        import psycopg2.extras  # noqa: PLC0415

        connection = psycopg2.connect(
            host=self.options["host"],
            port=self.options["port"],
            user=self.options["user"],
            password=self.options["password"],
            dbname=self.options["database"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        connection.set_session(readonly=True, autocommit=False)
        return connection

    @staticmethod
    def _rows(cursor, query):
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def read(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()["transaction_read_only"] != "on":
                message = "Sign source connection is not read-only"
                raise RuntimeError(message)
            result = {
                "requests": self._rows(
                    cursor,
                    """
                    SELECT request.id, request.template_id, request.create_uid,
                           request.subject,
                           request.reference, request.message, request.message_cc,
                           request.state, request.completion_date,
                           request.create_date, request.write_date,
                           creator.login AS creator_login,
                           creator_partner.name AS creator_name,
                           creator_partner.email AS creator_email,
                           original.id AS original_attachment_id,
                           original.name AS original_filename,
                           original.store_fname AS original_store_fname,
                           original.checksum AS original_checksum,
                           original.file_size AS original_file_size,
                           original.mimetype AS original_mimetype
                      FROM sign_request request
                      JOIN res_users creator ON creator.id = request.create_uid
                      JOIN res_partner creator_partner
                        ON creator_partner.id = creator.partner_id
                      JOIN sign_document document
                        ON document.template_id = request.template_id
                       AND document.sequence = (
                           SELECT min(candidate.sequence)
                             FROM sign_document candidate
                            WHERE candidate.template_id = request.template_id
                       )
                      JOIN ir_attachment original
                        ON original.id = document.attachment_id
                     ORDER BY request.id
                    """,
                ),
                "signers": self._rows(
                    cursor,
                    """
                    SELECT signer.id, signer.sign_request_id, signer.partner_id,
                           signer.signer_email, signer.mail_sent_order,
                           signer.state, signer.signing_date,
                           signer.create_date, signer.write_date,
                           partner.name AS partner_name, partner.email AS partner_email,
                           role.id AS source_role_id, role.name AS role_name
                      FROM sign_request_item signer
                      JOIN res_partner partner ON partner.id = signer.partner_id
                      JOIN sign_item_role role ON role.id = signer.role_id
                     ORDER BY signer.sign_request_id, signer.mail_sent_order, signer.id
                    """,
                ),
                "logs": self._rows(
                    cursor,
                    """
                    SELECT log.id, log.sign_request_id, log.sign_request_item_id,
                           log.user_id, log.partner_id, log.action,
                           log.request_state, log.ip, log.latitude, log.longitude,
                           log.log_hash, log.log_date, log.create_date
                      FROM sign_log log
                     ORDER BY log.sign_request_id, log.log_date, log.id
                    """,
                ),
                "messages": self._rows(
                    cursor,
                    """
                    SELECT message.id, message.res_id AS sign_request_id,
                           message.author_id, message.subject, message.body,
                           message.message_type, message.email_from,
                           message.is_internal, message.date,
                           author.name AS author_name, author.email AS author_email
                      FROM mail_message message
                 LEFT JOIN res_partner author ON author.id = message.author_id
                     WHERE message.model = 'sign.request'
                     ORDER BY message.res_id, message.date, message.id
                    """,
                ),
                "field_values": self._rows(
                    cursor,
                    """
                    SELECT value.id, signer.sign_request_id,
                           value.sign_request_item_id, value.sign_item_id,
                           item.page, item."posX" AS position_x,
                           item."posY" AS position_y, item.width, item.height,
                           item.required, item.constant,
                           type.item_type, type.name AS type_name,
                           value.value, value.frame_value, value.frame_has_hash,
                           value.create_date, value.write_date
                      FROM sign_request_item_value value
                      JOIN sign_request_item signer
                        ON signer.id = value.sign_request_item_id
                      JOIN sign_item item ON item.id = value.sign_item_id
                      JOIN sign_item_type type ON type.id = item.type_id
                     ORDER BY signer.sign_request_id, value.id
                    """,
                ),
                "attachments": self._rows(
                    cursor,
                    """
                    WITH generated AS (
                        SELECT completed.sign_request_id,
                               attachment.checksum AS signed_checksum
                          FROM sign_completed_document completed
                          JOIN ir_attachment attachment
                            ON attachment.res_model = 'sign.completed.document'
                           AND attachment.res_id = completed.id
                           AND attachment.res_field = 'file'
                    )
                    SELECT relation.sign_request_id, attachment.id,
                           attachment.name, attachment.store_fname,
                           attachment.checksum, attachment.file_size,
                           attachment.mimetype,
                           CASE WHEN attachment.checksum = generated.signed_checksum
                                THEN 'signed' ELSE 'source_certificate' END AS kind
                      FROM sign_request_completed_document_rel relation
                      JOIN ir_attachment attachment
                        ON attachment.id = relation.ir_attachment_id
                      JOIN generated
                        ON generated.sign_request_id = relation.sign_request_id
                     ORDER BY relation.sign_request_id, kind, attachment.id
                    """,
                ),
                "classifications": self._rows(
                    cursor,
                    """
                    SELECT completed.sign_request_id,
                           document.id AS source_document_id,
                           document.name
                      FROM sign_completed_document completed
                      JOIN documents_document document
                        ON document.id = completed.document_id
                     ORDER BY completed.sign_request_id, document.id
                    """,
                ),
                "attachment_inventory": self._rows(
                    cursor,
                    """
                    SELECT attachment.id, attachment.name, attachment.res_model,
                           attachment.res_id, attachment.res_field,
                           attachment.store_fname, attachment.checksum,
                           attachment.file_size, attachment.mimetype
                      FROM ir_attachment attachment
                     WHERE attachment.res_model LIKE 'sign.%'
                        OR attachment.res_field LIKE 'sign_%'
                     ORDER BY attachment.res_model, attachment.res_id, attachment.id
                    """,
                ),
            }
        expected = {
            "requests": 8,
            "signers": 11,
            "logs": 61,
            "messages": 25,
            "field_values": 87,
            "attachments": 16,
            "attachment_inventory": 50,
        }
        actual = {key: len(result[key]) for key in expected}
        if actual != expected:
            raise RuntimeError(f"Sign source perimeter changed: {actual} != {expected}")
        validate_source_structure(result)
        return result

    def binary(self, row):
        path = (self.filestore / row["store_fname"]).resolve()
        if self.filestore not in path.parents or not path.is_file():
            raise RuntimeError(f"Sign source attachment {row['id']} is missing or unsafe")
        content = path.read_bytes()
        if len(content) != row["file_size"] or sha1(content) != row["checksum"]:
            raise RuntimeError(f"Sign source attachment {row['id']} changed")
        return content


def source_options():
    return {
        "host": os.getenv("SIGN_SOURCE_DB_HOST", "accounting-source-db"),
        "port": int(os.getenv("SIGN_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("SIGN_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("SIGN_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv("SIGN_SOURCE_DATABASE", "odoo_online_source_saas_19_3"),
        "filestore": os.getenv("SIGN_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
    }


def validate_source_structure(source):
    """Fail closed if the signed-record perimeter changes from the reviewed dump."""
    request_ids = {row["id"] for row in source["requests"]}
    if len(request_ids) != len(source["requests"]):
        message = "The Sign source contains duplicate request identities"
        raise RuntimeError(message)
    unexpected_request_states = {
        row["id"]: row["state"]
        for row in source["requests"]
        if row["state"] != "signed"
    }
    if unexpected_request_states:
        raise RuntimeError(
            f"The Sign source contains requests outside signed state: {unexpected_request_states}",
        )
    unexpected_signer_states = {
        row["id"]: row["state"]
        for row in source["signers"]
        if row["state"] != "completed"
    }
    if unexpected_signer_states:
        raise RuntimeError(
            f"The Sign source contains signers outside completed state: {unexpected_signer_states}",
        )
    if {
        row["sign_request_id"] for row in source["signers"]
    } - request_ids:
        message = "The Sign source contains an orphan signer"
        raise RuntimeError(message)
    attachment_kinds = Counter(
        (row["sign_request_id"], row["kind"])
        for row in source["attachments"]
    )
    expected_attachment_kinds = Counter(
        (request_id, kind)
        for request_id in request_ids
        for kind in ("signed", "source_certificate")
    )
    if attachment_kinds != expected_attachment_kinds:
        message = (
            "Every Sign request must have exactly one signed attachment and one "
            "source completion certificate"
        )
        raise RuntimeError(message)


def match_exports(source, export_directory):
    export_directory = Path(export_directory).resolve()
    files = sorted(export_directory.glob("*.pdf"), key=lambda path: normalized_name(path.name))
    certificate_files = [path for path in files if path.name.startswith("Certificate - ")]
    certificates = {
        normalized_name(path.name.removeprefix("Certificate - ")): path
        for path in certificate_files
    }
    if len(certificates) != len(certificate_files):
        message = "The Sign export folder contains ambiguous certificate filenames"
        raise RuntimeError(message)
    signed_exports = [path for path in files if not path.name.startswith("Certificate - ")]
    by_identity = defaultdict(list)
    for path in signed_exports:
        content = path.read_bytes()
        by_identity[sha1(content), len(content)].append(path)
    matches = {}
    used_signed = set()
    used_certificates = set()
    signed_row_lists = defaultdict(list)
    for row in source["attachments"]:
        if row["kind"] == "signed":
            signed_row_lists[row["sign_request_id"]].append(row)
    if any(len(rows) != 1 for rows in signed_row_lists.values()):
        message = "A source request has an ambiguous signed attachment"
        raise RuntimeError(message)
    signed_rows = {request_id: rows[0] for request_id, rows in signed_row_lists.items()}
    for request in source["requests"]:
        request_id = request["id"]
        if request_id not in signed_rows:
            raise RuntimeError(f"Request {request_id} has no source signed attachment")
        signed_row = signed_rows[request_id]
        candidates = by_identity[signed_row["checksum"], signed_row["file_size"]]
        if len(candidates) != 1:
            raise RuntimeError(
                f"Request {request_id} has {len(candidates)} signed export matches",
            )
        signed_path = candidates[0]
        certificate = certificates.get(normalized_name(signed_path.name))
        if not certificate:
            raise RuntimeError(f"Request {request_id} has no exported certificate")
        if signed_path in used_signed or certificate in used_certificates:
            message = "An exported Sign artifact was reused across requests"
            raise RuntimeError(message)
        used_signed.add(signed_path)
        used_certificates.add(certificate)
        matches[request_id] = {
            "signed": signed_path,
            "certificate": certificate,
            "signed_sha256": sha256(signed_path.read_bytes()),
            "certificate_sha256": sha256(certificate.read_bytes()),
        }
    if used_signed != set(signed_exports) or used_certificates != set(certificates.values()):
        message = "The Sign export folder contains unmatched PDF artifacts"
        raise RuntimeError(message)
    return matches


def history_payload(source, request_id):
    def safe_value(row):
        row = dict(row)
        if row["item_type"] in {"signature", "initial"}:
            for field in ("value", "frame_value"):
                raw = row.get(field)
                row[field] = (
                    {"redacted": True, "sha256": sha256(str(raw).encode())}
                    if raw
                    else None
                )
        return {
            "field_type": row["item_type"],
            "field_name": text(row.get("type_name")),
            "page": row["page"],
            "position_x": row["position_x"],
            "position_y": row["position_y"],
            "width": row["width"],
            "height": row["height"],
            "required": row["required"],
            "constant": row["constant"],
            "value": row["value"],
            "frame_value": row["frame_value"],
            "frame_has_hash": row["frame_has_hash"],
            "created_at": row["create_date"],
            "updated_at": row["write_date"],
        }

    request = next(row for row in source["requests"] if row["id"] == request_id)
    source_certificate = next(
        row
        for row in source["attachments"]
        if row["sign_request_id"] == request_id and row["kind"] == "source_certificate"
    )
    return {
        "format": "usl-sign-odoo-online-external-history-v1",
        "statement": (
            "This is a preservation record of Odoo Online data. It is not a USL Sign "
            "validation, completion certificate, identity decision, or trust decision."
        ),
        "request": {
            "subject": request["subject"],
            "reference": request["reference"],
            "message": request["message"],
            "message_cc": request["message_cc"],
            "recorded_state": request["state"],
            "recorded_completion_date": request["completion_date"],
            "created_at": request["create_date"],
            "updated_at": request["write_date"],
            "created_by": request["creator_name"],
            "creator_email": request["creator_email"],
            "original_filename": request["original_filename"],
            "original_checksum_sha1": request["original_checksum"],
            "original_file_size": request["original_file_size"],
            "original_mimetype": request["original_mimetype"],
        },
        "signers": [
            {
                "name": row["partner_name"],
                "email": row["signer_email"] or row["partner_email"],
                "role": text(row["role_name"]),
                "recorded_state": row["state"],
                "recorded_signing_date": row["signing_date"],
                "signing_order": row["mail_sent_order"],
                "created_at": row["create_date"],
                "updated_at": row["write_date"],
            }
            for row in source["signers"]
            if row["sign_request_id"] == request_id
        ],
        "audit_events": [
            {
                "action": row["action"],
                "recorded_request_state": row["request_state"],
                "ip": row["ip"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "source_log_hash": row["log_hash"],
                "recorded_at": row["log_date"] or row["create_date"],
            }
            for row in source["logs"]
            if row["sign_request_id"] == request_id
        ],
        "chatter": [
            {
                "author": row["author_name"],
                "author_email": row["author_email"],
                "subject": row["subject"],
                "body": redact_historical_links(row["body"]),
                "message_type": row["message_type"],
                "email_from": row["email_from"],
                "is_internal": row["is_internal"],
                "date": row["date"],
            }
            for row in source["messages"]
            if row["sign_request_id"] == request_id
        ],
        "field_values": [
            safe_value(row)
            for row in source["field_values"]
            if row["sign_request_id"] == request_id
        ],
        "source_certificate_attachment": {
            "name": source_certificate["name"],
            "checksum_sha1": source_certificate["checksum"],
            "file_size": source_certificate["file_size"],
            "mimetype": source_certificate["mimetype"],
        },
        "source_classifications": [
            text(row["name"])
            for row in source["classifications"]
            if row["sign_request_id"] == request_id
        ],
        "excluded_reusable_marks": [
            {
                "checksum": row["checksum"],
                "file_size": row["file_size"],
                "reason": "Not imported as a reusable mark; the signed PDF preserves its rendered use.",
            }
            for row in source["attachment_inventory"]
            if (
                row["res_model"] == "sign.request.item"
                and row["res_id"]
                in {
                    signer["id"]
                    for signer in source["signers"]
                    if signer["sign_request_id"] == request_id
                }
            )
        ],
    }


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    ).encode()
