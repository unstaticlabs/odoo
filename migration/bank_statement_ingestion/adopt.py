# ruff: noqa: EM101, F821, T201
"""Adopt exact migrated bank identities before scheduled-email activation.

Run with ``odoo shell``. The shell injects ``env``; configuration is supplied
through the environment variables documented beside this script.
"""

import hashlib
import json
import os
from pathlib import Path

from odoo import fields
from odoo.tools import BinaryBytes

from odoo.addons.base.models.res_partner_bank import sanitize_account_number


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
    ofx_path = Path(os.environ["USL_BANK_ADOPTION_OFX"]).expanduser().resolve()
    content = ofx_path.read_bytes()
    wizard = (
        env["account.statement.import"]
        .sudo()
        .with_context(journal_id=config.journal_id.id)
        .create(
            {
                "statement_file": BinaryBytes(content),
                "statement_filename": ofx_path.name,
            },
        )
    )
    ofx = wizard._check_ofx(content)
    parsed_accounts = wizard._parse_file(content) if ofx else []
    if len(parsed_accounts) != 1 or len(ofx.accounts) != 1:
        raise RuntimeError("The cut-over OFX must contain exactly one bank account")
    _currency_code, account_number, statements = parsed_accounts[0]
    if sanitize_account_number(account_number) != sanitize_account_number(
        config.source_account_identifier,
    ):
        raise RuntimeError("The cut-over OFX account does not match the configuration")
    if len(statements) != 1:
        raise RuntimeError("The cut-over OFX has an ambiguous statement population")
    parsed_transactions = statements[0].get("transactions", [])
    raw_transactions = list(ofx.accounts[0].statement.transactions)
    if not parsed_transactions or len(parsed_transactions) != len(raw_transactions):
        raise RuntimeError(
            "The cut-over OFX transaction population is empty or incomplete",
        )

    source = {}
    for parsed, raw in zip(parsed_transactions, raw_transactions):
        identity = str(raw.id or "").strip()
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()
        if not identity:
            raise RuntimeError("The cut-over OFX contains a transaction without FITID")
        if identity in source:
            raise RuntimeError(
                f"The cut-over OFX contains duplicate FITID hash {identity_hash}",
            )
        transaction_date = fields.Date.to_date(parsed["date"])
        if transaction_date < config.automatic_start_date:
            raise RuntimeError("The cut-over OFX predates the configured start date")
        source[identity] = {
            "date": transaction_date,
            "amount": float(parsed["amount"]),
        }

    lines = (
        env["account.bank.statement.line"]
        .sudo()
        .search(
            [
                ("journal_id", "=", config.journal_id.id),
                ("date", ">=", min(item["date"] for item in source.values())),
                ("date", "<=", max(item["date"] for item in source.values())),
            ],
            order="date, id",
        )
    )
    identities = {identity: [] for identity in source}
    conflicts = {}
    candidates = []
    already_adopted = 0
    for line in lines:
        identity = _identity(line)
        if identity in identities:
            identities[identity].append(line)
    for identity, matches in identities.items():
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()
        if len(matches) != 1:
            conflicts[identity_hash] = {
                "reason": "missing or duplicate exact migrated identity",
                "line_ids": matches.ids,
            }
            continue
        line = matches[0]
        source_fact = source[identity]
        currency = line.journal_id.currency_id or line.company_id.currency_id
        if (
            line.date != source_fact["date"]
            or currency.compare_amounts(line.amount, source_fact["amount"]) != 0
        ):
            conflicts[identity_hash] = {
                "reason": "migrated date or amount differs from OFX",
                "line_ids": line.ids,
            }
            continue
        oca_identity = {"unique_import_id": identity}
        config.journal_id._statement_line_import_update_unique_import_id(
            oca_identity,
            account_number,
        )
        expected = {
            "provider_code": config.provider,
            "provider_account_id": sanitize_account_number(account_number),
            "provider_transaction_id": identity,
            "provider_identity_kind": "stable",
            "unique_import_id": oca_identity["unique_import_id"],
        }
        populated = {key: line[key] for key in expected if line[key]}
        if populated and populated != {key: expected[key] for key in populated}:
            conflicts[identity_hash] = {
                "reason": "existing provider or OCA identity conflicts",
                "line_ids": line.ids,
            }
            continue
        unique_owner = (
            env["account.bank.statement.line"]
            .sudo()
            .search(
                [
                    ("unique_import_id", "=", expected["unique_import_id"]),
                    ("id", "!=", line.id),
                ],
                limit=1,
            )
        )
        if unique_owner:
            conflicts[identity_hash] = {
                "reason": "OCA import identity already belongs to another line",
                "line_ids": (line | unique_owner).ids,
            }
            continue
        if all(line[key] == value for key, value in expected.items()):
            already_adopted += 1
            continue
        candidates.append((line, expected, identity))

    if conflicts:
        raise RuntimeError(
            "Conflicting provider identities block adoption: "
            + json.dumps(conflicts, sort_keys=True),
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
        "source_file_sha256": hashlib.sha256(content).hexdigest(),
        "source_transaction_count": len(source),
        "scanned_line_count": len(lines),
        "exact_identity_count": len(identities),
        "candidate_count": len(candidates),
        "already_adopted_count": already_adopted,
        "candidate_line_ids": [
            line.id for line, _values, _identity_value in candidates
        ],
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
    if mode == "apply":
        env.cr.commit()
    print(json.dumps(report, sort_keys=True))
    return report


run(env)
