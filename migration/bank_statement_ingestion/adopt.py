"""Adopt exact migrated bank identities before scheduled-email activation.

Run with ``odoo shell``. The shell injects ``env``; configuration is supplied
through the environment variables documented beside this script.
"""

import hashlib
import json
import os
from pathlib import Path

from odoo import fields


def _identity(line):
    details = line.transaction_details or {}
    return str((details.get("extra") or {}).get("id") or "").strip()


def run(env):
    mode = os.environ.get("USL_BANK_ADOPTION_MODE", "preview").strip().lower()
    if mode not in {"preview", "apply"}:
        raise RuntimeError("USL_BANK_ADOPTION_MODE must be preview or apply")
    config_id = int(os.environ["USL_BANK_ADOPTION_CONFIG_ID"])
    config = env["account.bank.ingestion.config"].sudo().browse(config_id).exists()
    if not config:
        raise RuntimeError(f"Bank ingestion configuration {config_id} does not exist")

    env.cr.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        [f"bank-identity-adoption:{config.id}"],
    )
    lines = env["account.bank.statement.line"].sudo().search(
        [
            ("journal_id", "=", config.journal_id.id),
            ("date", ">=", config.automatic_start_date),
        ],
        order="date, id",
    )
    identities = {}
    conflicts = {}
    candidates = []
    already_adopted = 0
    for line in lines:
        identity = _identity(line)
        if not identity:
            continue
        identities.setdefault(identity, []).append(line)
    for identity, matches in identities.items():
        if len(matches) != 1:
            conflicts[hashlib.sha256(identity.encode()).hexdigest()] = matches.ids
            continue
        line = matches[0]
        expected = {
            "provider_code": config.provider,
            "provider_account_id": config.source_account_identifier.replace(" ", ""),
            "provider_transaction_id": identity,
            "provider_identity_kind": "stable",
        }
        populated = {key: line[key] for key in expected if line[key]}
        if populated and populated != {key: expected[key] for key in populated}:
            conflicts[hashlib.sha256(identity.encode()).hexdigest()] = line.ids
            continue
        if all(line[key] == value for key, value in expected.items()):
            already_adopted += 1
            continue
        candidates.append((line, expected, identity))

    if conflicts:
        raise RuntimeError(
            "Conflicting provider identities block adoption: "
            + json.dumps(conflicts, sort_keys=True)
        )
    if mode == "apply":
        for line, values, _identity_value in candidates:
            line.with_context(bank_review_internal=True).write(values)

    report = {
        "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        "mode": mode,
        "config_id": config.id,
        "company_id": config.company_id.id,
        "journal_id": config.journal_id.id,
        "cutover_date": fields.Date.to_string(config.automatic_start_date),
        "scanned_line_count": len(lines),
        "exact_identity_count": len(identities),
        "candidate_count": len(candidates),
        "already_adopted_count": already_adopted,
        "candidate_line_ids": [line.id for line, _values, _identity_value in candidates],
        "candidate_identity_sha256": [
            hashlib.sha256(identity.encode()).hexdigest()
            for _line, _values, identity in candidates
        ],
    }
    report_path = os.environ.get("USL_BANK_ADOPTION_REPORT")
    if report_path:
        path = Path(report_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return report


run(env)
