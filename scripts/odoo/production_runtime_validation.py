"""Validate the admitted production cron inventory and Odoo queues."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os
from datetime import timedelta

from odoo import fields


def load_object(name):
    try:
        value = json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name} is missing or invalid.") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a JSON object.")
    return value


policy = load_object("USL_PRODUCTION_CRON_POLICY_JSON")
gates = load_object("USL_PRODUCTION_CRON_GATES_JSON")
rules = policy.get("crons") or {}
if policy.get("schema") != "usl-production-cron-policy-v1":
    raise RuntimeError("The production cron policy schema is unsupported.")
if set(gates) != set(policy.get("gates") or []) or gates.get("always") is not True:
    raise RuntimeError("The production cron gate decision is incomplete.")

Cron = env["ir.cron"].sudo().with_context(active_test=False)  # noqa: F821
crons = Cron.search([])
xmlid_rows = env["ir.model.data"].sudo().search([  # noqa: F821
    ("model", "=", "ir.cron"),
    ("res_id", "in", crons.ids),
])
xmlids_by_id = {}
for row in xmlid_rows:
    xmlids_by_id.setdefault(row.res_id, []).append(f"{row.module}.{row.name}")
if set(xmlids_by_id) != set(crons.ids) or any(len(values) != 1 for values in xmlids_by_id.values()):
    raise RuntimeError("Every installed production cron must have exactly one XML ID.")
installed = {values[0]: crons.browse(record_id) for record_id, values in xmlids_by_id.items()}
if set(installed) != set(rules):
    raise RuntimeError("The installed cron inventory differs from the versioned policy.")
desired = {
    xmlid
    for xmlid, rule in rules.items()
    if rule.get("gate") is not None and gates[rule["gate"]]
}
active = {xmlid for xmlid, cron in installed.items() if cron.active}

now = fields.Datetime.now()
lag = {}
failures = {}
for xmlid in sorted(active):
    cron = installed[xmlid]
    if cron.failure_count:
        failures[xmlid] = cron.failure_count
    if not cron.nextcall or cron.nextcall < now - timedelta(minutes=2):
        lag[xmlid] = {
            "lastcall": fields.Datetime.to_string(cron.lastcall) if cron.lastcall else None,
            "nextcall": fields.Datetime.to_string(cron.nextcall) if cron.nextcall else None,
            "reason": "nextcall-overdue",
        }
        continue
    seconds = {
        "minutes": 60,
        "hours": 3600,
        "days": 86400,
        "weeks": 604800,
        "months": 2592000,
    }[cron.interval_type] * cron.interval_number
    if seconds <= 3600 and (
        not cron.lastcall or cron.lastcall < now - timedelta(seconds=seconds * 2 + 120)
    ):
        lag[xmlid] = {
            "lastcall": fields.Datetime.to_string(cron.lastcall) if cron.lastcall else None,
            "nextcall": fields.Datetime.to_string(cron.nextcall),
            "reason": "recent-job-not-observed-within-two-intervals",
        }

queue_counts = {
    "mail_pending": env["mail.mail"].sudo().search_count([  # noqa: F821
        ("state", "in", ["outgoing", "exception"]),
    ]),
    "payment_pending": env["payment.transaction"].sudo().search_count([  # noqa: F821
        ("state", "in", ["draft", "pending", "authorized", "error"]),
    ]),
}
if "usl.document.operation" in env.registry:  # noqa: F821
    queue_counts["documents_unsettled"] = env["usl.document.operation"].sudo().search_count([  # noqa: F821
        ("state", "in", ["pending", "uploading", "processing", "failed", "duplicate"]),
    ])
if "account.bank.ingestion" in env.registry:  # noqa: F821
    queue_counts["bank_ingestion_unsettled"] = env["account.bank.ingestion"].sudo().search_count([  # noqa: F821
        ("state", "in", ["received", "processing", "failed"]),
    ])
if "sign.oca.request" in env.registry and "archive_status" in env["sign.oca.request"]._fields:  # noqa: F821
    queue_counts["sign_archive_unsettled"] = env["sign.oca.request"].sudo().search_count([  # noqa: F821
        ("archive_status", "in", ["pending", "processing", "failed"]),
    ])

stale_progress = 0
if "ir.cron.progress" in env.registry:  # noqa: F821
    Progress = env["ir.cron.progress"].sudo()  # noqa: F821
    domain = []
    if "create_date" in Progress._fields:
        domain = [("create_date", "<", now - timedelta(hours=2))]
    stale_progress = Progress.search_count(domain)

blockers = {}
if active != desired:
    blockers["active_policy_mismatch"] = {
        "unexpected": sorted(active - desired),
        "missing": sorted(desired - active),
    }
if failures:
    blockers["cron_failures"] = failures
if lag:
    blockers["cron_lag"] = lag
if any(queue_counts.values()):
    blockers["queues"] = queue_counts
if stale_progress:
    blockers["stale_cron_progress"] = stale_progress

result = {
    "schema": "usl-production-runtime-validation-v1",
    "status": "failed" if blockers else "passed",
    "active_cron_xmlids": sorted(active),
    "disabled_cron_xmlids": sorted(set(rules) - active),
    "cron_failure_count": sum(failures.values()),
    "cron_lag_count": len(lag),
    "queue_counts": queue_counts,
    "stale_cron_progress": stale_progress,
    "blockers": blockers,
}
print("USL_PRODUCTION_RUNTIME_VALIDATION=" + json.dumps(result, sort_keys=True))
if blockers:
    raise RuntimeError("Production runtime validation failed: " + json.dumps(blockers, sort_keys=True))
