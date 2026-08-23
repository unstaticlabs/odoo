"""Deterministic semantic selection for source Documents checksum groups."""

from __future__ import annotations

from classification import classify_group

PROFILES = {"full", "accounting", "hr", "smoke"}


def _source_order(group):
    return min(
        item.get("document_id") or (1_000_000_000 + item["attachment_id"])
        for item in group
    )


def smoke_groups(groups):
    """Return a compact relationship-complete cross-section of source truth."""
    selected = {}
    classifications = {
        group[0]["checksum"]: classify_group(group)
        for group in groups
    }

    def include_first(name, predicate):
        match = next((group for group in groups if predicate(group)), None)
        if match:
            selected.setdefault(match[0]["checksum"], (name, match))

    companies = sorted(
        {
            item.get("company_id") or item.get("folder_company_id")
            for group in groups
            for item in group
            if item.get("company_id") or item.get("folder_company_id")
        },
    )
    for company_id in companies:
        include_first(
            f"company:{company_id}",
            lambda group, company_id=company_id: any(
                (item.get("company_id") or item.get("folder_company_id"))
                == company_id
                for item in group
            ),
        )
    include_first(
        "accounting",
        lambda group: classifications[group[0]["checksum"]]["accounting_evidence"],
    )
    include_first(
        "hr",
        lambda group: classifications[group[0]["checksum"]]["hr_restricted"],
    )
    include_first(
        "needs_attention",
        lambda group: classifications[group[0]["checksum"]]["needs_attention"],
    )
    include_first("trash", lambda group: all(not item["active"] for item in group))
    include_first("duplicate", lambda group: len(group) > 1)
    include_first(
        "unassigned",
        lambda group: any(item["kind"] == "unassigned_evidence" for item in group),
    )
    include_first(
        "permissions",
        lambda group: any(item.get("access_rows") for item in group),
    )
    include_first(
        "pdf",
        lambda group: any(item.get("mimetype") == "application/pdf" for item in group),
    )
    include_first(
        "image",
        lambda group: any(
            (item.get("mimetype") or "").startswith("image/") for item in group
        ),
    )
    include_first(
        "tika",
        lambda group: any(
            item.get("mimetype") != "application/pdf"
            and not (item.get("mimetype") or "").startswith("image/")
            for item in group
        ),
    )
    return [entry[1] for entry in sorted(selected.values(), key=lambda item: _source_order(item[1]))]


def select_groups(groups, profile="full", limit=0):
    """Select checksum groups without splitting their relationships."""
    if profile not in PROFILES:
        raise ValueError(f"unknown Documents restore profile: {profile}")
    if limit < 0:
        message = "Documents restore limit cannot be negative"
        raise ValueError(message)
    if profile == "accounting":
        selected = [
            group
            for group in groups
            if classify_group(group)["accounting_evidence"]
        ]
    elif profile == "hr":
        selected = [group for group in groups if classify_group(group)["hr_restricted"]]
    elif profile == "smoke":
        selected = smoke_groups(groups)
    else:
        selected = list(groups)
    return selected[:limit] if limit else selected
