# ruff: noqa: F821, T201
"""Validate exact external Sign reconstruction before source bindings are removed."""

import json
import os
import sys
from collections import Counter
from pathlib import Path

from odoo import fields

sys.path.insert(0, "/mnt/sign-restore-migration")
from source import SourceReader, match_exports, sha256, source_options  # noqa: E402

from odoo.addons.usl_sign.services import field_content

XMLID_MODULE = "usl_sign_restore"
SOURCE_SNAPSHOT = os.environ["SIGN_SOURCE_SNAPSHOT"]
reader = SourceReader(source_options())
source = reader.read()
matches = match_exports(
    source,
    Path(os.getenv("SIGN_EXPORT_DIRECTORY", "/mnt/accounting-source/sign")),
)


def bound(name, model):
    binding = env["ir.model.data"].sudo().search(  # noqa: F821
        [("module", "=", XMLID_MODULE), ("name", "=", name), ("model", "=", model)],
    )
    assert len(binding) == 1, f"Missing or duplicate Sign restore binding {name}"
    record = env[model].sudo().browse(binding.res_id).exists()  # noqa: F821
    assert record, f"Sign restore binding {name} is dangling"
    return record


request_signers = {}
for row in source["signers"]:
    request_signers.setdefault(row["sign_request_id"], []).append(row)

records = []
all_linked_documents = env["usl.document"].browse()  # noqa: F821
for request_row in source["requests"]:
    source_id = request_row["id"]
    request_record = bound(f"request_{source_id}", "sign.oca.request")
    assert request_record.record_kind == "external_archive"
    assert request_record.state == "external_archived"
    assert request_record.archive_status == "archived"
    assert request_record.validation_status == "not_started"
    assert request_record.evidence_status == "not_started"
    assert not request_record.requested_trust
    assert not request_record.recommended_trust
    assert not request_record.achieved_trust
    assert not request_record.policy_id
    assert not request_record.final_data
    assert not request_record.final_sha256
    assert not request_record.completion_certificate
    assert not request_record.evidence_manifest
    assert not request_record.dossier_data
    assert not request_record.validation_ids
    assert not request_record.evidence_ids
    assert len(request_record.event_ids) == 1
    assert request_record.event_ids.event_type == "external_record_imported"
    request_record.event_ids.verify_chain()
    signed_content = field_content(request_record.data)
    assert sha256(signed_content) == matches[source_id]["signed_sha256"]
    assert request_record.original_sha256 == matches[source_id]["signed_sha256"]
    assert request_record.archive_document_id.checksum == matches[source_id]["signed_sha256"]
    assert (
        request_record.archive_dossier_document_id.checksum
        == matches[source_id]["certificate_sha256"]
    )
    assert request_record.archive_document_id.availability_state == "available"
    assert request_record.archive_dossier_document_id.availability_state == "available"
    assert request_record.archive_document_id.permission_sync_state == "synchronized"
    assert request_record.archive_dossier_document_id.permission_sync_state == "synchronized"
    assert "Odoo Online (External)" in request_record.archive_document_id.tag_ids.mapped("name")
    assert "Signed document" in request_record.archive_document_id.tag_ids.mapped("name")
    assert "Signing certificate" in request_record.archive_dossier_document_id.tag_ids.mapped("name")

    source_signers = request_signers[source_id]
    assert len(request_record.signer_ids) == len(source_signers)
    for source_signer, target_signer in zip(source_signers, request_record.signer_ids):
        assert bound(f"signer_{source_signer['id']}", target_signer._name) == target_signer
        assert target_signer.state == "external_recorded"
        assert target_signer.authentication_method == "external_record"
        assert not target_signer.signature_hash
        assert not target_signer.certificate_serial
        assert target_signer.signed_on == fields.Datetime.to_datetime(
            str(source_signer["signing_date"]),
        )

    messages = request_record.message_ids
    expected_message_ids = {
        f"request_{source_id}_notice",
        *{
            f"message_{row['id']}"
            for row in source["messages"]
            if row["sign_request_id"] == source_id
        },
        *{
            f"log_{row['id']}"
            for row in source["logs"]
            if row["sign_request_id"] == source_id
        },
    }
    bound_messages = env["ir.model.data"].sudo().search(  # noqa: F821
        [("module", "=", XMLID_MODULE), ("name", "in", sorted(expected_message_ids))],
    )
    assert len(bound_messages) == len(expected_message_ids)
    assert set(bound_messages.mapped("res_id")) <= set(messages.ids)

    linked_documents = env["usl.document.link"].sudo().search(  # noqa: F821
        [("res_model", "=", request_record._name), ("res_id", "=", request_record.id), ("active", "=", True)],
    ).document_id
    assert len(linked_documents) == 5, (
        f"Request {source_id} has {len(linked_documents)} linked archive roots instead of 5"
    )
    all_linked_documents |= linked_documents
    tag_counts = Counter(linked_documents.tag_ids.mapped("name"))
    for required_tag in (
        "Signed document",
        "Signing certificate",
        "Original signing document",
        "Source completion certificate",
        "Signing history",
    ):
        assert tag_counts[required_tag] >= 1, (
            f"Request {source_id} is missing linked Paperless tag {required_tag}"
        )
    records.append(
        {
            "source_request_id": source_id,
            "target_request_id": request_record.id,
            "signed_sha256": matches[source_id]["signed_sha256"],
            "certificate_sha256": matches[source_id]["certificate_sha256"],
            "signers": len(request_record.signer_ids),
            "history_messages": len(expected_message_ids),
            "linked_paperless_documents": len(linked_documents),
        },
    )

assert len(all_linked_documents) == 40
external_tag = env["usl.paperless.tag"].sudo().search(  # noqa: F821
    [("name", "=", "Odoo Online (External)"), ("active", "=", True)],
)
assert len(external_tag) == 1
external_documents = env["usl.document"].sudo().search(  # noqa: F821
    [("tag_ids", "in", external_tag.ids), ("availability_state", "=", "available")],
)
used_original_ids = {row["original_attachment_id"] for row in source["requests"]}
inactive_template_checksums = {
    sha256(reader.binary(row))
    for row in source["attachment_inventory"]
    if row["res_model"] == "sign.document" and row["id"] not in used_original_ids
}
inactive_template_documents = external_documents - all_linked_documents
assert len(inactive_template_documents) == len(inactive_template_checksums)
assert set(inactive_template_documents.mapped("checksum")) == inactive_template_checksums
assert len(external_documents) == (
    len(all_linked_documents) + len(inactive_template_documents)
)

bindings = env["ir.model.data"].sudo().search([("module", "=", XMLID_MODULE)])  # noqa: F821
counts = Counter(bindings.mapped("model"))
assert counts["sign.oca.request"] == 8
assert counts["sign.oca.request.signer"] == 11
assert counts["mail.message"] == (
    len(source["requests"]) + len(source["messages"]) + len(source["logs"])
)
assert env["sign.oca.request"].sudo().search_count(  # noqa: F821
    [("record_kind", "=", "external_archive"), ("state", "!=", "external_archived")],
) == 0

result = {
    "source_snapshot": SOURCE_SNAPSHOT,
    "bindings": dict(sorted(counts.items())),
    "records": records,
    "status": "passed",
}
print("SIGN_RESTORE_VALIDATION=" + json.dumps(result, sort_keys=True))
