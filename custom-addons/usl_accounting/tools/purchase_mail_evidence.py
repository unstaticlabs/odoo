"""Bounded, read-only EN/FR text evidence; filenames never establish a role."""

import hashlib
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from odoo.tools.pdf import OdooPdfFileReader

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_BYTES = 30 * 1024 * 1024
MAX_FILES = 64
MAX_PAGES = 10
MAX_TEXT_CHARS = 65536
VERSION = 1


def normalized(value):
    return " ".join(unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower().split())


def decimal_amount(value):
    try:
        amount = Decimal((value or "").replace(" ", "").replace("\u00a0", "").replace(",", "."))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def unknown(raw, reason):
    return {"role": "unknown", "reason": reason, "sha256": hashlib.sha256(raw).hexdigest()}


def _single(lines, pattern):
    values = [match.group(1).strip() for line in lines if (match := re.fullmatch(pattern, line))]
    unique = set(values)
    return unique.pop() if len(unique) == 1 else None


def _document_date(lines, receipt):
    labels = r"date de paiement|payment date" if receipt else r"date de facture|invoice date"
    values = set()
    for line in lines:
        match = re.fullmatch(r"(" + labels + r")\s*:\s*(.{1,40})", line)
        if not match:
            continue
        label, value = match.groups()
        try:
            if label.startswith("date de ") and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
                day, month, year = map(int, value.split("/"))
                parsed = date(year, month, day)
            else:
                # Slash dates with an English label have ambiguous ordering.
                parsed = date.fromisoformat(value)
        except ValueError:
            return None
        values.add(parsed.isoformat())
    return values.pop() if len(values) == 1 else None


def text_evidence(text, raw):
    lines = [normalized(line.replace("€", " EUR ")) for line in text.splitlines() if line.strip()]
    receipt = any(re.fullmatch(r"(?:recu de paiement|payment receipt|payment confirmation|confirmation de paiement)", line) for line in lines)
    invoice = any(re.fullmatch(r"(?:facture|invoice|tax invoice|avoir|credit note)", line) for line in lines)
    if receipt == invoice:
        return unknown(raw, "document_role_uncertain")
    if receipt and any(re.match(r"(?:invoices|factures|invoice references|additional invoice|autre facture)\s*:", line) for line in lines):
        return unknown(raw, "multiple_invoice_references")
    reference = _single(lines, r"(?:numero de facture|n[°o]? de facture|invoice (?:number|no\.?|#)|facture concernee|invoice reference|reference facture)\s*:\s*([a-z0-9][a-z0-9._/-]{0,79})")
    issuer = _single(lines, r"(?:fournisseur|supplier|issuer|beneficiaire|payee)\s*:\s*(.{2,160})")
    address = _single(lines, r"(?:adresse|address)\s*:\s*(.{8,240})")
    legal_id = _single(lines, r"(?:vat|tva|siret|supplier tax id|numero de tva)\s*:\s*([a-z0-9 .-]{6,40})")
    amount_label = r"(?:montant paye|amount paid)" if receipt else r"(?:total ttc|invoice total|total amount)"
    dates = _document_date(lines, receipt)
    amount = _single(lines, amount_label + r"\s*:\s*([0-9][0-9 .,]*\s+[a-z]{3})")
    if not reference or not issuer or not (legal_id or address) or not dates or not amount:
        return unknown(raw, "incomplete_document_identity")
    try:
        date.fromisoformat(dates)
    except ValueError:
        return unknown(raw, "invalid_document_date")
    number, currency = amount.rsplit(" ", 1)
    total = decimal_amount(number)
    if total is None or total <= 0:
        return unknown(raw, "invalid_document_amount")
    return {
        "role": "receipt" if receipt else "invoice", "reason": "text_identity",
        "sha256": hashlib.sha256(raw).hexdigest(), "reference": reference,
        "issuer": issuer, "address": address, "legal_id": normalized(legal_id),
        "date": dates, "currency": currency.upper(), "amount": format(total.normalize(), "f"),
        "kind": "refund" if any(line in {"avoir", "credit note"} for line in lines) else "invoice",
        "partial": receipt and any(line in {"paiement partiel", "partial payment"} for line in lines),
    }


def pdf_evidence(raw):
    if len(raw) > MAX_FILE_BYTES:
        return unknown(raw, "file_byte_limit")
    try:
        reader = OdooPdfFileReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted or len(reader.pages) > MAX_PAGES:
            return unknown(raw, "encrypted_or_page_limit")
        parts = []
        size = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            size += len(part)
            if size > MAX_TEXT_CHARS:
                return unknown(raw, "text_limit")
            parts.append(part)
        return text_evidence("\n".join(parts), raw)
    except Exception:
        # Invalid/unsupported PDFs stay intact. This is no OCR fallback and
        # byte/page limits do not claim a hard CPU deadline for PDF parsing.
        return unknown(raw, "unreadable_pdf")


def same_issuer(left, right):
    if left.get("legal_id") and right.get("legal_id"):
        return left["legal_id"] == right["legal_id"]
    return bool(left.get("issuer") and left.get("address") and left["issuer"] == right.get("issuer") and left["address"] == right.get("address"))


def same_invoice(left, right):
    return left.get("reference") == right.get("reference") and left.get("kind") == right.get("kind") and same_issuer(left, right)


def receipt_matches(receipt, invoice):
    if invoice.get("kind") != "invoice" or receipt.get("reference") != invoice.get("reference") or not same_issuer(receipt, invoice):
        return False
    if receipt["currency"] != invoice["currency"] or receipt["date"] < invoice["date"]:
        return False
    paid, total = Decimal(receipt["amount"]), Decimal(invoice["amount"])
    return paid == total or (receipt.get("partial") and 0 < paid < total)


def group_families(families):
    """Families contain native origin files and one conservative evidence dict.

    Distinct invoices split. Uncertain evidence stays on the first native
    draft; no receipt/unknown-only holding draft is manufactured.
    """
    groups = []
    leftovers = []
    for family in families:
        evidence = family["evidence"]
        if evidence["role"] != "invoice":
            leftovers.append(family)
            continue
        matches = [group for group in groups if same_invoice(evidence, group[0]["evidence"])]
        if matches:
            group = matches[0]
            anchor = group[0]["evidence"]
            if (evidence["currency"], evidence["amount"], evidence["date"]) != (anchor["currency"], anchor["amount"], anchor["date"]):
                family["uncertain"] = "conflicting_invoice_values"
                for previous in group:
                    previous["uncertain"] = "conflicting_invoice_values"
            group.append(family)
        else:
            groups.append([family])
    if not groups:
        for family in leftovers:
            family["uncertain"] = family["evidence"]["reason"] if family["evidence"]["role"] == "unknown" else "receipt_match_uncertain"
        return [leftovers] if leftovers else []
    for family in leftovers:
        evidence = family["evidence"]
        matches = [group for group in groups if evidence["role"] == "receipt" and receipt_matches(evidence, group[0]["evidence"])]
        if len(matches) == 1 and not any(item.get("uncertain") for item in matches[0]):
            matches[0].append(family)
        else:
            family["uncertain"] = "receipt_match_uncertain" if evidence["role"] == "receipt" else evidence["reason"]
            groups[0].append(family)
    return groups
