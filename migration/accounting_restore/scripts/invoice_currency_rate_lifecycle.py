# ruff: noqa: F821, T201
"""Capture, restore, and verify Online invoice currency rates.

This script is executed through ``odoo shell``.  It deliberately uses only
native target identities and an external private manifest so it remains usable
after the one-shot Accounting migration module has been uninstalled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, getcontext


SCHEMA = "usl-invoice-currency-rate-manifest/v1"
INVOICE_MOVE_TYPES = (
    "out_invoice",
    "out_refund",
    "in_invoice",
    "in_refund",
)
RATE_PATH = os.environ.get(
    "USL_INVOICE_CURRENCY_RATE_MANIFEST",
    "/mnt/migration-evidence/invoice-currency-rates.json",
)
getcontext().prec = 40


def _rows(cursor, query, parameters=()):
    cursor.execute(query, parameters)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _normalized_company(value, fallback):
    registration = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return registration or f"name:{(fallback or '').strip().casefold()}"


def _normalized_text(value):
    return " ".join((value or "").split())


def _normalized_move_name(value):
    value = _normalized_text(value)
    return "" if value == "/" else value


def document_key(row):
    """Return a stable native identity available before and after cleanup."""
    return (
        _normalized_company(row.get("company_vat"), row.get("company_name")),
        _normalized_text(row.get("journal_code")),
        _normalized_move_name(row.get("name")),
        row.get("move_type") or "",
        str(row.get("invoice_date") or ""),
        _normalized_text(row.get("currency_name")),
        _normalized_text(row.get("reference")),
        _normalized_text(row.get("payment_reference")),
        _normalized_text(row.get("partner_name")),
    )


def _serialized_key(key):
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


def _document_rows(cursor, *, source_company_ids=None, target_ids=None):
    where = ["move.move_type = ANY(%s)"]
    parameters = [list(INVOICE_MOVE_TYPES)]
    if source_company_ids is not None:
        where.append("move.company_id = ANY(%s)")
        parameters.append(source_company_ids)
    if target_ids is not None:
        where.append("move.id = ANY(%s)")
        parameters.append(target_ids)
    return _rows(
        cursor,
        f"""
        SELECT move.id,
               move.name,
               move.move_type,
               move.state,
               move.invoice_date,
               move.ref AS reference,
               move.payment_reference,
               move.invoice_currency_rate,
               journal.code AS journal_code,
               company.name AS company_name,
               company_partner.vat AS company_vat,
               currency.name AS currency_name,
               partner.name AS partner_name
          FROM account_move move
          JOIN account_journal journal ON journal.id = move.journal_id
          JOIN res_company company ON company.id = move.company_id
          JOIN res_partner company_partner
            ON company_partner.id = company.partner_id
          JOIN res_currency currency ON currency.id = move.currency_id
          LEFT JOIN res_partner partner ON partner.id = move.partner_id
         WHERE {' AND '.join(where)}
         ORDER BY move.id
        """,
        parameters,
    )


def _unique_by_key(rows, label):
    result = {}
    duplicates = []
    for row in rows:
        key = document_key(row)
        if key in result:
            duplicates.append({
                "identity": key,
                "first_id": result[key]["id"],
                "second_id": row["id"],
            })
        else:
            result[key] = row
    if duplicates:
        raise RuntimeError(
            f"{label} invoice identities are not unique: "
            + json.dumps(duplicates[:20], ensure_ascii=False, default=str),
        )
    return result


def _decimal(value):
    return Decimal(str(value or 0))


def _analysis_aggregates(cursor, records, id_field):
    ids = [record[id_field] for record in records]
    if not ids:
        return []
    identity_by_id = {
        record[id_field]: record["identity"]["company"]
        for record in records
    }
    lines = _rows(
        cursor,
        """
        SELECT move.id AS move_id,
               move.move_type,
               move.state,
               move.invoice_currency_rate,
               line.price_total
          FROM account_move move
          JOIN account_move_line line ON line.move_id = move.id
         WHERE move.id = ANY(%s)
           AND line.account_id IS NOT NULL
           AND line.display_type = 'product'
         ORDER BY move.id, line.id
        """,
        (ids,),
    )
    totals = defaultdict(Decimal)
    documents = defaultdict(set)
    for line in lines:
        rate = _decimal(line["invoice_currency_rate"])
        if rate <= 0:
            raise RuntimeError(
                f"Invoice {line['move_id']} has a non-positive currency rate.",
            )
        sign = -1 if line["move_type"] in (
            "in_invoice",
            "out_refund",
            "in_receipt",
        ) else 1
        key = (
            identity_by_id[line["move_id"]],
            line["move_type"],
            line["state"],
        )
        totals[key] += _decimal(line["price_total"]) * sign / rate
        documents[key].add(line["move_id"])
    return [
        {
            "company": key[0],
            "move_type": key[1],
            "state": key[2],
            "document_count": len(documents[key]),
            "raw_total": str(total.quantize(Decimal("0.00000001"))),
            "ui_total": str(
                total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            ),
        }
        for key, total in sorted(totals.items())
    ]


def _fingerprint_payload(manifest):
    return {
        "schema": manifest["schema"],
        "source_dump_sha256": manifest["source_dump_sha256"],
        "source_database": manifest["source_database"],
        "target_database": manifest["target_database"],
        "records": manifest["records"],
        "analysis_aggregates": manifest["analysis_aggregates"],
    }


def manifest_fingerprint(manifest):
    payload = json.dumps(
        _fingerprint_payload(manifest),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _source_connection():
    import psycopg2

    connection = psycopg2.connect(
        host=os.environ.get("ACCOUNTING_SOURCE_DB_HOST", "accounting-source-db"),
        port=os.environ.get("ACCOUNTING_SOURCE_DB_PORT", "5432"),
        dbname=os.environ.get(
            "ACCOUNTING_SOURCE_DB_NAME",
            "odoo_online_source_saas_19_3",
        ),
        user=os.environ.get("ACCOUNTING_SOURCE_POSTGRES_USER", "odoo"),
        password=os.environ.get("ACCOUNTING_SOURCE_POSTGRES_PASSWORD", "odoo"),
    )
    with connection.cursor() as cursor:
        cursor.execute("SET ROLE accounting_source_ro")
    return connection


def capture_manifest(odoo_env):
    source_sha256 = os.environ.get("USL_MIGRATION_SOURCE_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise RuntimeError("The frozen source checksum is missing or invalid.")
    company_ids = [
        int(value)
        for value in os.environ.get(
            "USL_INVOICE_CURRENCY_RATE_SOURCE_COMPANY_IDS",
            "1,8",
        ).split(",")
        if value
    ]
    source = _source_connection()
    try:
        with source.cursor() as source_cursor:
            source_rows = _document_rows(
                source_cursor,
                source_company_ids=company_ids,
            )
            source_by_key = _unique_by_key(source_rows, "Source")
            target_by_key = _unique_by_key(
                _document_rows(odoo_env.cr),
                "Target",
            )
            missing = [
                {"source_id": row["id"], "identity": key}
                for key, row in source_by_key.items()
                if key not in target_by_key
            ]
            if missing:
                raise RuntimeError(
                    "Target invoices do not cover the Online rate scope: "
                    + json.dumps(missing[:20], ensure_ascii=False, default=str),
                )
            records = []
            for key, source_row in sorted(
                source_by_key.items(),
                key=lambda item: item[1]["id"],
            ):
                target_row = target_by_key[key]
                source_rate = _decimal(source_row["invoice_currency_rate"])
                if source_rate <= 0:
                    raise RuntimeError(
                        f"Source invoice {source_row['id']} has an invalid rate.",
                    )
                records.append({
                    "source_id": source_row["id"],
                    "target_id": target_row["id"],
                    "rate": str(source_rate),
                    "identity_key": _serialized_key(key),
                    "identity": {
                        "company": key[0],
                        "journal_code": key[1],
                        "name": key[2],
                        "move_type": key[3],
                        "invoice_date": key[4],
                        "currency": key[5],
                        "reference": key[6],
                        "payment_reference": key[7],
                        "partner_name": key[8],
                    },
                })
            manifest = {
                "schema": SCHEMA,
                "source_dump_sha256": source_sha256,
                "source_database": os.environ.get(
                    "ACCOUNTING_SOURCE_DB_NAME",
                    "odoo_online_source_saas_19_3",
                ),
                "source_company_ids": company_ids,
                "target_database": odoo_env.cr.dbname,
                "record_count": len(records),
                "records": records,
                "analysis_aggregates": _analysis_aggregates(
                    source_cursor,
                    records,
                    "source_id",
                ),
            }
            manifest["fingerprint_sha256"] = manifest_fingerprint(manifest)
            return manifest
    finally:
        source.close()


def load_manifest(path=RATE_PATH):
    with open(path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("schema") != SCHEMA:
        raise RuntimeError("Unsupported invoice currency-rate manifest schema.")
    if manifest.get("record_count") != len(manifest.get("records") or []):
        raise RuntimeError("Invoice currency-rate manifest count is invalid.")
    if manifest.get("fingerprint_sha256") != manifest_fingerprint(manifest):
        raise RuntimeError("Invoice currency-rate manifest fingerprint changed.")
    expected_sha256 = os.environ.get("USL_MIGRATION_SOURCE_SHA256")
    if expected_sha256 and manifest["source_dump_sha256"] != expected_sha256:
        raise RuntimeError("Invoice currency-rate source checksum changed.")
    return manifest


def _ledger_signature(cursor):
    row = _rows(
        cursor,
        """
        SELECT COUNT(line.id) AS line_count,
               COALESCE(SUM(line.debit), 0) AS debit,
               COALESCE(SUM(line.credit), 0) AS credit,
               COALESCE(SUM(line.balance), 0) AS balance
          FROM account_move_line line
          JOIN account_move move ON move.id = line.move_id
         WHERE move.state = 'posted'
        """,
    )[0]
    return {key: str(value) for key, value in row.items()}


def _validate_target_identities(cursor, manifest):
    expected = {record["target_id"]: record for record in manifest["records"]}
    rows = _document_rows(cursor, target_ids=list(expected))
    actual = {row["id"]: row for row in rows}
    issues = []
    for target_id, record in expected.items():
        row = actual.get(target_id)
        if not row:
            issues.append({"target_id": target_id, "issue": "missing"})
            continue
        actual_key = _serialized_key(document_key(row))
        if actual_key != record["identity_key"]:
            issues.append({
                "target_id": target_id,
                "issue": "identity_mismatch",
                "expected": record["identity_key"],
                "actual": actual_key,
            })
    if issues:
        raise RuntimeError(
            "Target invoice identities differ from the captured cohort: "
            + json.dumps(issues[:20], ensure_ascii=False),
        )
    return actual


def _rate_mismatches(actual, manifest):
    mismatches = []
    for record in manifest["records"]:
        row = actual[record["target_id"]]
        if _decimal(row["invoice_currency_rate"]) != _decimal(record["rate"]):
            mismatches.append({
                "source_id": record["source_id"],
                "target_id": record["target_id"],
                "name": record["identity"]["name"],
                "expected_rate": record["rate"],
                "actual_rate": str(row["invoice_currency_rate"]),
            })
    return mismatches


def _assert_analysis_parity(cursor, manifest):
    actual = _analysis_aggregates(
        cursor,
        manifest["records"],
        "target_id",
    )
    expected = manifest["analysis_aggregates"]
    if actual != expected:
        raise RuntimeError(
            "Invoice/Bills Analysis aggregates differ from Online: "
            + json.dumps(
                {"expected": expected, "actual": actual},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    return actual


def restore_or_verify(odoo_env, *, restore):
    manifest = load_manifest()
    if manifest["target_database"] != odoo_env.cr.dbname:
        raise RuntimeError("Invoice currency-rate target database changed.")
    actual = _validate_target_identities(odoo_env.cr, manifest)
    mismatches_before = _rate_mismatches(actual, manifest)
    ledger_before = _ledger_signature(odoo_env.cr)
    if restore and mismatches_before:
        odoo_env.cr.executemany(
            """
            UPDATE account_move
               SET invoice_currency_rate = %s
             WHERE id = %s
               AND invoice_currency_rate IS DISTINCT FROM %s
            """,
            [
                (
                    _decimal(record["rate"]),
                    record["target_id"],
                    _decimal(record["rate"]),
                )
                for record in manifest["records"]
            ],
        )
    actual = _validate_target_identities(odoo_env.cr, manifest)
    mismatches_after = _rate_mismatches(actual, manifest)
    if mismatches_after:
        raise RuntimeError(
            "Historical invoice currency rates differ from Online: "
            + json.dumps(mismatches_after[:20], ensure_ascii=False),
        )
    aggregates = _assert_analysis_parity(odoo_env.cr, manifest)
    ledger_after = _ledger_signature(odoo_env.cr)
    if ledger_after != ledger_before:
        raise RuntimeError("Ledger totals changed while restoring invoice rates.")
    if restore:
        odoo_env.cr.commit()
    return {
        "schema": "usl-invoice-currency-rate-lifecycle-result/v1",
        "mode": "restore" if restore else "verify",
        "manifest_fingerprint_sha256": manifest["fingerprint_sha256"],
        "source_document_count": manifest["record_count"],
        "changed_document_count": len(mismatches_before) if restore else 0,
        "matching_document_count": manifest["record_count"],
        "ledger_unchanged": True,
        "analysis_aggregates": aggregates,
    }


def run(odoo_env):
    mode = os.environ.get("USL_INVOICE_CURRENCY_RATE_MODE", "verify")
    if mode == "capture":
        return capture_manifest(odoo_env)
    if mode == "restore":
        return restore_or_verify(odoo_env, restore=True)
    if mode == "verify":
        return restore_or_verify(odoo_env, restore=False)
    raise RuntimeError(f"Unsupported invoice currency-rate mode: {mode}")


if "env" in globals():
    try:
        print(json.dumps(run(env), indent=2, ensure_ascii=False, sort_keys=True))
    except Exception:
        env.cr.rollback()
        raise
