"""Enable Banking AIS data boundaries; no Odoo, credentials or network access."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json


class ProviderDataError(ValueError):
    """A stable, non-sensitive code suitable for an operator-facing result."""


@dataclass(frozen=True)
class Transactions:
    transactions: list[dict]
    duplicate_count: int = 0
    pending_count: int = 0


def account_identity(value):
    if not isinstance(value, dict):
        raise ProviderDataError("invalid_account_identity")
    uid = value.get("uid")
    hashes = value.get("identification_hashes") or []
    primary = value.get("identification_hash")
    if not isinstance(hashes, list):
        raise ProviderDataError("invalid_account_identity")
    aliases = tuple(sorted({x for x in [primary, *hashes] if isinstance(x, str) and x}))
    if not isinstance(uid, str) or not uid or not aliases:
        raise ProviderDataError("missing_account_identity")
    return uid, aliases


def import_identity(company_id, account_key, entry_reference):
    value = json.dumps([company_id, account_key, entry_reference], separators=(",", ":"))
    return "enablebanking:" + hashlib.sha256(value.encode()).hexdigest()


def financial_fingerprint(value):
    payload = [value["booking_date"], value["currency"], str(Decimal(str(value["amount"])).normalize())]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def normalise_transactions(rows, currency, *, date_since=None, date_until=None):
    transactions, seen, duplicates, pending = [], {}, 0, 0
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderDataError("invalid_transaction")
        status = row.get("status")
        if not isinstance(status, str) or status not in {"BOOK", "CNCL", "HOLD", "OTHR", "PDNG", "RJCT", "SCHD"}:
            raise ProviderDataError("invalid_transaction_status")
        if row.get("status") != "BOOK":
            pending += 1
            continue
        booking_date = row.get("booking_date")
        if not booking_date:
            raise ProviderDataError("missing_booking_date")
        try:
            booking_date = date.fromisoformat(booking_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ProviderDataError("invalid_booking_date") from error
        if (date_since and booking_date < date_since) or (date_until and booking_date > date_until):
            continue
        reference = row.get("entry_reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ProviderDataError("missing_entry_reference")
        amount_data = row.get("transaction_amount")
        if not isinstance(amount_data, dict):
            raise ProviderDataError("invalid_amount")
        if amount_data.get("currency") != currency:
            raise ProviderDataError("currency_mismatch")
        try:
            amount = Decimal(str(amount_data["amount"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as error:
            raise ProviderDataError("invalid_amount") from error
        if not amount.is_finite() or abs(amount) > Decimal("999999999999999"):
            raise ProviderDataError("invalid_amount")
        direction = row.get("credit_debit_indicator")
        if not isinstance(direction, str) or direction not in {"CRDT", "DBIT"}:
            raise ProviderDataError("invalid_direction")
        amount = abs(amount) * (-1 if direction == "DBIT" else 1)
        if Decimal(str(float(amount))) != amount:
            raise ProviderDataError("invalid_amount")
        remittance = row.get("remittance_information") or []
        if not isinstance(remittance, list) or not all(isinstance(x, str) for x in remittance):
            raise ProviderDataError("invalid_remittance")
        value = {
            "entry_reference": reference,
            "booking_date": booking_date,
            "value_date": row.get("value_date") or None,
            "amount": float(amount),
            "currency": currency,
            "payment_ref": " ".join(remittance) or "Bank transaction",
        }
        fingerprint = financial_fingerprint(value)
        if reference in seen:
            if seen[reference] != fingerprint:
                raise ProviderDataError("identity_conflict")
            duplicates += 1
            continue
        seen[reference] = fingerprint
        transactions.append(value)
    return Transactions(transactions, duplicates, pending)


def collect_transaction_pages(get_page, *, max_pages=1000):
    """Return the entire result or raise; never expose a partial import."""
    rows, seen, cursor = [], set(), None
    for _page in range(max_pages):
        data = get_page(cursor)
        if not isinstance(data, dict) or not isinstance(data.get("transactions"), list):
            raise ProviderDataError("invalid_page")
        rows.extend(data["transactions"])
        next_cursor = data.get("continuation_key")
        if next_cursor is None:
            return rows
        if not isinstance(next_cursor, str) or not next_cursor:
            raise ProviderDataError("invalid_cursor")
        if next_cursor in seen:
            raise ProviderDataError("repeated_cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    raise ProviderDataError("page_limit_exceeded")
