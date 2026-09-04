"""Deterministic business-metadata translation for the Documents migration.

The source folder tree is evidence, not a product navigation model.  These
helpers translate the useful meaning in that tree into ordinary Paperless
tags, document types, correspondents, dates, and Odoo-link hints.  Technical
source identities remain in the sealed run manifest instead of live metadata.
"""

from __future__ import annotations

import calendar
import re
from contextlib import suppress
from datetime import date, datetime

SOURCE_TAG_ALIASES = {
    "banque revolut": "Revolut",
    "notes de frais employés": "Expenses",
    "notes de frais employés (copie)": "Expenses",
    "social > paie": "Payroll",
}

TAG_COLORS = {
    "Accounting": "#4e79a7",
    "Banking": "#2b8cbe",
    "Contracts & legal": "#7b5ea7",
    "HR": "#b23a5b",
    "Product": "#59a14f",
    "Tax & reporting": "#31a354",
    "Revolut": "#0666eb",
    "Revolut Business": "#0666eb",
    "Revolut Savings EUR": "#0666eb",
    "Revolut Flexible Cash Funds": "#0666eb",
    "Shine": "#f28e2b",
    "Wise": "#1f8f6a",
}

INSTITUTION_PARTNERS = {
    "legalplace": "LEGALPLACE",
    "revolut business": "REVOLUT BANK UAB",
    "shine": "SHINE FRANCE",
    "urssaf": "URSSAF ILE DE FRANCE",
}

MONTHS = {
    name.casefold(): number
    for number, name in enumerate(calendar.month_abbr)
    if name
}


def source_text(value) -> str:
    """Resolve translated source values without depending on the migration ORM."""
    if isinstance(value, dict):
        return (
            value.get("en_US")
            or value.get("fr_FR")
            or next(iter(value.values()), "")
        )
    return value or ""


def source_search_text(value) -> str:
    """Expose every translation when a source label carries business meaning."""
    if isinstance(value, dict):
        return " / ".join(
            str(value[key]) for key in sorted(value) if value.get(key)
        )
    return value or ""


def folder_contexts(folders: list[dict]) -> tuple[dict[int, str], dict[int, int]]:
    """Return deterministic display paths and nearest inherited companies."""
    by_id = {row["id"]: row for row in folders}
    paths = {}
    companies = {}
    for folder_id, row in by_id.items():
        source_ids = [
            int(part)
            for part in (row.get("parent_path") or "").split("/")
            if part
        ]
        paths[folder_id] = " / ".join(
            source_text(by_id[source_id]["name"])
            for source_id in source_ids
            if source_id in by_id and source_text(by_id[source_id]["name"])
        )
        company_id = next(
            (
                by_id[source_id].get("company_id")
                for source_id in reversed(source_ids)
                if source_id in by_id and by_id[source_id].get("company_id")
            ),
            None,
        )
        if company_id:
            companies[folder_id] = company_id
    return paths, companies


def normalized_source_tag(name: str) -> str:
    """Return the clean user-facing name for one source Documents tag."""
    clean = (name or "").strip()
    return SOURCE_TAG_ALIASES.get(clean.casefold(), clean)


def _segments(folder_path: str) -> list[str]:
    return [part.strip() for part in (folder_path or "").split("/") if part.strip()]


def _contains(segments: list[str], value: str) -> bool:
    needle = value.casefold()
    return any(segment.casefold() == needle for segment in segments)


def _filename_date(filename: str) -> date | None:
    """Read only unambiguous dates from filenames, preferring a range end."""
    value = filename or ""
    candidates: list[tuple[int, date]] = []
    for match in re.finditer(
        r"(?<!\d)(20\d{2})[-_](0[1-9]|1[0-2])[-_](0[1-9]|[12]\d|3[01])(?!\d)",
        value,
    ):
        with suppress(ValueError):
            candidates.append((match.start(), date(*map(int, match.groups()))))
    for match in re.finditer(r"(?<!\d)(20\d{6})(?!\d)", value):
        with suppress(ValueError):
            candidates.append(
                (match.start(), datetime.strptime(match.group(1), "%Y%m%d").date()),
            )
    for match in re.finditer(
        r"(?<!\d)(0[1-9]|[12]\d|3[01])-([A-Za-z]{3})-(20\d{2})(?!\d)",
        value,
    ):
        month = MONTHS.get(match.group(2).casefold())
        if month:
            with suppress(ValueError):
                candidates.append(
                    (match.start(), date(int(match.group(3)), month, int(match.group(1)))),
                )
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    month_matches = list(
        re.finditer(r"(?<!\d)(20\d{2})[-_](0[1-9]|1[0-2])(?![-_]?\d)", value),
    )
    if month_matches:
        match = month_matches[-1]
        year, month = map(int, match.groups())
        return date(year, month, calendar.monthrange(year, month)[1])
    return None


def document_date(item: dict) -> date:
    """Prefer authoritative record dates, then filename evidence, then source add date."""
    for key in ("invoice_date", "move_date"):
        value = item.get(key)
        if value:
            return value if isinstance(value, date) else date.fromisoformat(str(value))
    inferred = _filename_date(item.get("filename") or "")
    if inferred:
        return inferred
    added = item["create_date"]
    if isinstance(added, datetime):
        return added.date()
    return datetime.fromisoformat(str(added)).date()


def classify_item(item: dict) -> dict:
    """Translate one source identity into business-facing archive metadata."""
    segments = _segments(item.get("folder_path") or "")
    folded = {segment.casefold() for segment in segments}
    filename = (item.get("filename") or "").casefold()
    tags = {
        normalized_source_tag(name)
        for name in item.get("source_tag_names", [])
        if normalized_source_tag(name)
    }
    document_type = None
    type_priority = 0
    accounting_evidence = False
    hr_restricted = False
    institution_partner_name = None
    archive_correspondent_name = None

    def set_type(name: str, priority: int) -> None:
        nonlocal document_type, type_priority
        if priority > type_priority:
            document_type = name
            type_priority = priority

    if "finance" in folded or item.get("res_model") == "account.move":
        tags.add("Accounting")
        accounting_evidence = True

    move_type = item.get("move_type")
    journal_name = source_search_text(item.get("journal_name")).casefold()
    journal_type = (item.get("journal_type") or "").casefold()
    if item.get("res_model") == "account.move":
        if move_type in {"in_invoice", "in_refund", "in_receipt"}:
            set_type("Supplier invoice", 100)
        elif move_type in {"out_invoice", "out_refund", "out_receipt"}:
            set_type("Customer invoice", 100)
        elif "notes de frais" in journal_name:
            tags.add("Expenses")
            set_type("Expense receipt", 100)
        elif journal_type in {"bank", "cash"}:
            tags.add("Banking")
            set_type("Bank transaction evidence", 100)
        elif "paie" in journal_name:
            tags.update({"HR", "Payroll"})
            hr_restricted = True
            set_type("Payroll record", 100)
        else:
            set_type("Journal entry evidence", 90)

    if "bank" in folded:
        tags.update({"Accounting", "Banking"})
        accounting_evidence = True
        if "relevés" in folded or "savings" in folded or "statement" in filename:
            set_type("Bank statement", 80)
        else:
            set_type("Banking document", 60)

    if "revolut business" in folded:
        tags.update({"Revolut", "Revolut Business"})
        institution_partner_name = INSTITUTION_PARTNERS["revolut business"]
    if "savings" in folded and "savings" in filename and "flexible-cash" not in filename:
        tags.add("Revolut Savings EUR")
    if "savings" in folded and "flexible-cash-funds" in filename:
        tags.add("Revolut Flexible Cash Funds")
    if "shine" in folded:
        tags.add("Shine")
        institution_partner_name = INSTITUTION_PARTNERS["shine"]
    if "wise" in folded:
        tags.add("Wise")
        archive_correspondent_name = "Wise"

    tax_segments = {
        "taxes",
        "is",
        "tva",
        "cfe",
        "sie messagerie",
        "eori douanes",
    }
    if folded.intersection(tax_segments):
        tags.update({"Accounting", "Tax & reporting"})
        accounting_evidence = True
        set_type("Tax document", 80)
    if "tva" in folded:
        tags.add("VAT")
        set_type("VAT filing", 85)
    if "is" in folded:
        tags.add("Corporate income tax")
        set_type("Corporate tax filing", 85)
    if "cfe" in folded:
        tags.add("CFE")
    if "eori douanes" in folded:
        tags.add("Customs")

    if folded.intersection({"social", "urssaf", "dsn", "adhésion"}):
        tags.update({"HR", "Payroll"})
        hr_restricted = True
        if "urssaf" in folded:
            tags.add("Social contributions")
            institution_partner_name = INSTITUTION_PARTNERS["urssaf"]
        if "dsn" in folded:
            tags.add("Payroll declarations")
        set_type("Payroll record", 85)

    if "annual closing" in folded or "comptabilité" in folded:
        tags.update({"Accounting", "Tax & reporting"})
        accounting_evidence = True
        set_type("Annual accounts", 75)

    if "legal" in folded:
        tags.add("Contracts & legal")
        set_type("Legal document", 50)
        if "contracts" in folded or "contrats" in folded:
            tags.add("Contracts")
            set_type("Contract", 80)
        if folded.intersection({"registrations", "kbis"}):
            tags.add("Company records")
            set_type("Company registration", 85)
        if folded.intersection({"dau", "pvs", "notes de gestion", "gestion"}):
            tags.add("Corporate records")
            set_type("Corporate record", 80)
        if "legalplace" in folded:
            tags.add("LegalPlace")
            institution_partner_name = INSTITUTION_PARTNERS["legalplace"]

    if any(segment.startswith("employees -") for segment in folded):
        tags.add("HR")
        hr_restricted = True
        set_type("Employee document", 75)

    if "products" in folded or "design thinking" in folded:
        tags.add("Product")
        set_type("Product document", 70)

    if item.get("kind") == "unassigned_evidence":
        bank_export_markers = (
            "account-statement",
            "monthly-statement",
            "flexible-cash-funds",
        )
        if filename.endswith(".csv") and any(
            marker in filename for marker in bank_export_markers
        ):
            tags.update({"Accounting", "Banking"})
            accounting_evidence = True
            set_type("Bank statement", 80)

    if (
        item.get("kind") == "restricted_unassigned_evidence"
        and item.get("attachment_id") == 1611
    ):
        tags.add("Product")
        set_type("Product document", 70)

    return {
        "tags": sorted(tags, key=str.casefold),
        "document_type": document_type,
        "document_type_priority": type_priority,
        "accounting_evidence": accounting_evidence,
        "hr_restricted": hr_restricted,
        "institution_partner_name": institution_partner_name,
        "archive_correspondent_name": archive_correspondent_name,
        "document_date": document_date(item),
        "needs_attention": not bool(tags or document_type),
    }


def classify_group(group: list[dict]) -> dict:
    """Merge exact-binary duplicates without discarding any source context."""
    classified = [classify_item(item) for item in group]
    best_type = max(
        classified,
        key=lambda item: (
            item["document_type_priority"],
            item["document_type"] or "",
        ),
    )
    representative = min(
        zip(group, classified, strict=True),
        key=lambda pair: (
            not bool(pair[0].get("active")),
            pair[0].get("document_id") or 1_000_000_000,
            pair[0]["attachment_id"],
        ),
    )
    return {
        "tags": sorted(
            {tag for item in classified for tag in item["tags"]},
            key=str.casefold,
        ),
        "document_type": best_type["document_type"],
        "accounting_evidence": any(item["accounting_evidence"] for item in classified),
        "hr_restricted": any(item["hr_restricted"] for item in classified),
        "institution_partner_names": sorted(
            {
                item["institution_partner_name"]
                for item in classified
                if item["institution_partner_name"]
            },
            key=str.casefold,
        ),
        "archive_correspondent_names": sorted(
            {
                item["archive_correspondent_name"]
                for item in classified
                if item["archive_correspondent_name"]
            },
            key=str.casefold,
        ),
        "document_date": representative[1]["document_date"],
        "added_at": min(item["create_date"] for item in group),
        "modified_at": max(
            item.get("write_date") or item["create_date"] for item in group
        ),
        "needs_attention": all(item["needs_attention"] for item in classified),
    }
