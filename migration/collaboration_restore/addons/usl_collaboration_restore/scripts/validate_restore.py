# ruff: noqa: EM101, F821, T201
import json
import os
from pathlib import Path

run = env["usl.collaboration.restore.run"].sudo().search([], limit=1)
if not run or run.status != "passed":
    raise RuntimeError("The latest Collaboration restoration did not pass")
expected = {
    "messages": 51491,
    "aliases": 29,
    "tracking": 37579,
    "followers": 6010,
    "activities": 918,
    "notifications": 78,
    "parent_links": 24069,
    "mail_queue": 31,
    "attachment_relations": 558,
    "cross_accounting_parent_links": 1643,
    "visible_messages": 50588,
    "external_messages": 0,
    "deliberately_not_copied_messages": 903,
}
actual = {name: run.statistics_json.get(name) for name in expected}
if actual != expected:
    raise RuntimeError(f"Collaboration counts differ: {actual}")
evidence = Path(os.environ["COLLABORATION_EVIDENCE_DIR"]) / "collaboration-disposition.json"
if not evidence.is_file():
    raise RuntimeError("Collaboration disposition evidence is missing")
if len(env["usl.collaboration.restore.mapping"].sudo().search([
    ("source_model", "=", "mail.message"),
])) != 50588:
    raise RuntimeError("Collaboration source bindings are incomplete")
if env["mail.message.reaction"].sudo().search_count([]) < 2:
    raise RuntimeError("Source reactions were not restored")
if env["discuss.channel"].sudo().with_context(active_test=False).search_count([]) < 6:
    raise RuntimeError("Source Discuss channels were not restored")
payload = json.loads(evidence.read_text(encoding="utf-8"))
dispositions = payload["dispositions"]
for name, expected_count in (
    ("messages", 51491), ("tracking", 37579),
    ("followers", 6010), ("activities", 918),
):
    if len(dispositions[name]) != expected_count:
        raise RuntimeError(f"{name} do not have exact Collaboration dispositions")
dropped_messages = [
    row
    for row in dispositions["messages"]
    if row["disposition"] == "deliberately_not_copied"
]
if len(dropped_messages) != 903:
    raise RuntimeError("Approved Collaboration exclusions differ")
roger_assignments = [
    row for row in dispositions["activities"]
    if row["disposition"] == "native_activity"
    and row["source"]["res_model"] == "project.task"
    and row["source"]["active"]
    and not row["source"]["user_id"]
]
if len(roger_assignments) != 179 or len({row["target_user_id"] for row in roger_assignments}) != 1:
    raise RuntimeError("The 179 unassigned Project To-Dos were not assigned uniquely to Roger")
open_project = [
    row for row in dispositions["activities"]
    if row["disposition"] == "native_activity"
    and row["source"]["res_model"] == "project.task"
    and row["source"]["active"]
]
if len(open_project) != 208:
    raise RuntimeError("The 208 open Project activities were not retained")
completed_sign = [
    row for row in dispositions["activities"]
    if row["source"]["res_model"] == "sign.request"
    and not row["source"]["active"]
]
if len(completed_sign) != 1 or completed_sign[0]["disposition"] != "completed_sign_note":
    raise RuntimeError("The completed Sign activity was not materialized as a dated note")
sign_note = env["mail.message"].sudo().browse(
    completed_sign[0]["target_message_id"],
).exists()
if (
    not sign_note
    or not sign_note.is_internal
    or "Completed legacy signing activity" not in str(sign_note.body)
):
    raise RuntimeError("The completed Sign activity note is missing or not internal")
legacy_rules = env["rebuild.account.declaration.rule"].sudo().with_context(active_test=False).search([
    ("category", "=", "legacy"), ("version", "=", "retired"),
])
if len(legacy_rules) != 9 or legacy_rules.filtered(lambda rule: rule.active or rule.lifecycle != "deprecated"):
    raise RuntimeError("The nine retired declaration rules are missing or schedulable")
legacy_declarations = env["rebuild.account.declaration"].sudo().search([
    ("rule_id", "in", legacy_rules.ids),
])
if len(legacy_declarations) != 36 or legacy_declarations.filtered(lambda item: item.status != "archived"):
    raise RuntimeError("The 36 historical declarations are missing or not archived")
if len(env["usl.collaboration.restore.mapping"].sudo().search([
    ("source_model", "=", "discuss.channel.member"),
])) != 11:
    raise RuntimeError("The eleven Discuss memberships were not mapped")
print(json.dumps({"status": "passed", "counts": actual, "evidence_sha256": run.evidence_sha256}, indent=2, sort_keys=True))
