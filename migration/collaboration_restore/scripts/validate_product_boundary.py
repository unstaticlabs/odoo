# ruff: noqa: EM101, F821, T201
import hashlib
import json
import os
import stat
from pathlib import Path

module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_collaboration_restore")], limit=1,
)
if module and module.state == "installed":
    raise RuntimeError("The Collaboration migration module remains installed")
for name in ("usl.collaboration.restore.run", "usl.collaboration.restore.mapping"):
    if name in env:
        raise RuntimeError(f"Migration model {name} remains in the product registry")
evidence = Path(os.environ["COLLABORATION_EVIDENCE_DIR"]) / "collaboration-disposition.json"
payload = json.loads(evidence.read_text(encoding="utf-8"))
attachment_ledger_path = evidence.parents[2] / "attachment-disposition-ledger.json"
attachment_ledger = json.loads(attachment_ledger_path.read_text(encoding="utf-8"))
source_attachments = {
    int(row["id"]): row for row in attachment_ledger["entries"]
}
if stat.S_IMODE(evidence.stat().st_mode) != 0o600:
    raise RuntimeError("Collaboration evidence is not mode 0600")
actual_evidence_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
sidecar = evidence.with_suffix(".json.sha256").read_text(encoding="utf-8").split()[0]
if sidecar != actual_evidence_sha:
    raise RuntimeError("Collaboration evidence checksum seal is invalid")
if payload.get("source_dump_sha256") != "ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1":
    raise RuntimeError("Product validation evidence has the wrong source identity")
if (
    payload.get("visible_message_count") != 49186
    or payload.get("external_message_count") != 0
    or payload.get("deliberately_not_copied_message_count") != 819
):
    raise RuntimeError("Product validation evidence has incomplete message dispositions")
if any(payload.get("outbound_side_effect_delta", {}).values()):
    raise RuntimeError("Product validation evidence reports outbound migration side effects")
missing = []
changed = []
for row in payload["dispositions"]["messages"]:
    target_id = row.get("target_message_id")
    if not target_id:
        continue
    message = env["mail.message"].sudo().browse(target_id).exists()
    if not message:
        missing.append(row["id"])
    elif hashlib.sha256(str(message.body or "").encode()).hexdigest() != row["body_sha256"]:
        changed.append(row["id"])
if missing or changed:
    raise RuntimeError(f"Final Collaboration messages differ: missing={missing[:20]}, changed={changed[:20]}")
dispositions = payload["dispositions"]
missing_tracking = []
for row in dispositions["tracking"]:
    if row["disposition"] == "native_tracking":
        if not env["mail.tracking.value"].sudo().browse(row["target_tracking_id"]).exists():
            missing_tracking.append(row["id"])
    elif row["disposition"] == "visible_legacy_note":
        note = env["mail.message"].sudo().browse(row["target_legacy_note_id"]).exists()
        if not note or "Legacy field changes" not in str(note.body):
            missing_tracking.append(row["id"])
if missing_tracking:
    raise RuntimeError(f"Final tracking history is missing: {missing_tracking[:20]}")
missing_followers = [
    row["id"] for row in dispositions["followers"]
    if row["disposition"] == "live_internal_subscription"
    and not env["mail.followers"].sudo().browse(row["target_follower_id"]).exists()
]
if missing_followers:
    raise RuntimeError(f"Final live followers are missing: {missing_followers[:20]}")
changed_activities = []
for row in dispositions["activities"]:
    if row["disposition"] != "native_activity":
        continue
    activity = env["mail.activity"].sudo().with_context(active_test=False).browse(
        row["target_activity_id"],
    ).exists()
    if (
        not activity
        or activity.user_id.id != row["target_user_id"]
        or activity.active != bool(row["source"]["active"])
    ):
        changed_activities.append(row["id"])
if changed_activities:
    raise RuntimeError(f"Final activities differ: {changed_activities[:20]}")
completed_sign = [
    row for row in dispositions["activities"]
    if row["source"]["res_model"] == "sign.request"
    and not row["source"]["active"]
]
if len(completed_sign) != 1 or completed_sign[0]["disposition"] != "completed_sign_note":
    raise RuntimeError("Final completed Sign activity disposition differs")
sign_note = env["mail.message"].sudo().browse(
    completed_sign[0]["target_message_id"],
).exists()
if (
    not sign_note
    or not sign_note.is_internal
    or "Completed legacy signing activity" not in str(sign_note.body)
):
    raise RuntimeError("Final completed Sign activity note differs")
message_targets = {
    row["id"]: row.get("target_message_id") for row in dispositions["messages"]
}
attachment_errors = []
for row in dispositions["other"]:
    if row["model"] != "message_attachment_rel":
        continue
    if row["disposition"] == "native_message_attachment":
        attachment = env["ir.attachment"].sudo().browse(row["target_attachment_id"]).exists()
        source_message_id, source_attachment_id = (
            int(value) for value in row["id"].split(":", 1)
        )
        message = env["mail.message"].sudo().browse(message_targets[source_message_id]).exists()
        source_attachment = source_attachments.get(source_attachment_id)
        source_checksum = (source_attachment or {}).get("checksum")
        payload_matches = bool(attachment and source_attachment) and (
            attachment.checksum == source_checksum
            if source_checksum
            else (
                source_attachment.get("type") == "url"
                and attachment.type == "url"
                and attachment.url == source_attachment.get("url")
                and attachment.name == source_attachment.get("name")
            )
        )
        if (
            not attachment or not message or attachment not in message.attachment_ids
            or not payload_matches
        ):
            attachment_errors.append(row["id"])
    elif row["disposition"] == "canonical_document_payload":
        if not env["usl.document"].sudo().browse(row["target_document_id"]).exists():
            attachment_errors.append(row["id"])
if attachment_errors:
    raise RuntimeError(f"Final Collaboration attachments differ: {attachment_errors[:20]}")
for archive in payload["archives"]:
    for kind in ("html", "json"):
        path = evidence.parent / "technical-threads" / archive[f"{kind}_file"]
        if (
            not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
            or hashlib.sha256(path.read_bytes()).hexdigest() != archive[f"{kind}_sha256"]
        ):
            raise RuntimeError(f"Technical archive seal differs: {path}")
alias_errors = []
for row in dispositions["other"]:
    if row["model"] != "mail.alias" or row["disposition"] != "target_domain_alias":
        continue
    alias = env["mail.alias"].sudo().browse(row["target_id"]).exists()
    if (
        not alias or alias.alias_name != row["target_local_part"]
        or alias.alias_domain_id.name != row["target_domain"]
        or alias.alias_domain_id.name == (row["source"].get("source_alias_domain") or "")
    ):
        alias_errors.append(row["id"])
if alias_errors:
    raise RuntimeError(f"Final target-domain aliases differ: {alias_errors}")
legacy_rules = env["rebuild.account.declaration.rule"].sudo().with_context(active_test=False).search([
    ("category", "=", "legacy"), ("version", "=", "retired"),
])
legacy_declarations = env["rebuild.account.declaration"].sudo().search([
    ("rule_id", "in", legacy_rules.ids), ("status", "=", "archived"),
])
if len(legacy_rules) != 9 or len(legacy_declarations) != 36 or legacy_rules.filtered("active"):
    raise RuntimeError("Final retired declaration history differs")
print(json.dumps({
    "status": "passed",
    "visible_messages": 49186,
    "deliberately_not_copied_messages": 819,
}, sort_keys=True))
