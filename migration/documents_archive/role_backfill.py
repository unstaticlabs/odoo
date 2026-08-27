"""Deterministic archive-role decisions for reconstructed Documents links.

The source Documents application did not distinguish evidence, library, and
background presentation.  Keep this translation in the one-shot migration
perimeter so the delivered registry contains only the resulting product
fields, never source provenance or migration controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

EVIDENCE_MODELS = frozenset(
    {
        "account.asset",
        "account.move",
        "account.payment",
        "hr.employee",
        "hr.expense",
        "usl.expense.batch",
        "usl.platform.billing.payout",
        "usl.platform.billing.session",
        "usl.tese.payslip",
    },
)
BACKGROUND_MODELS = frozenset(
    {
        "project.project",
        "project.task",
        "res.partner",
    },
)
CURATED_LIBRARY_TAGS = frozenset(
    {
        "company records",
        "contracts",
        "contracts & legal",
        "corporate records",
        "product",
    },
)


def normalized_values(values: Iterable[str | None]) -> set[str]:
    """Return non-empty case-insensitive values without locale assumptions."""
    return {str(value).strip().casefold() for value in values if str(value or "").strip()}


def resolve_root_role(
    *,
    record_models: Iterable[str | None],
    tags: Iterable[str | None] = (),
    accounting_evidence: bool = False,
    confidentiality: str | None = None,
    explicit_documents_record: bool = True,
    existing_role: str | None = None,
) -> dict[str, str]:
    """Resolve one source root without relying on mutable Paperless metadata.

    Accounting/HR relationships are authoritative evidence.  Curated legal,
    company, and product classifications remain library material.  Historical
    project/task/contact relationships are background unless one of those
    stronger facts exists.  An otherwise unlinked source Documents record is
    an explicit library upload; an external Paperless intake is background.
    """
    models = normalized_values(record_models)
    normalized_tags = normalized_values(tags)
    if existing_role == "evidence":
        return {
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "migration_preserved_evidence",
        }
    if (
        accounting_evidence
        or confidentiality in {"accounting", "hr"}
        or bool(models.intersection(EVIDENCE_MODELS))
    ):
        return {
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "migration_business_evidence",
        }
    if normalized_tags.intersection(CURATED_LIBRARY_TAGS):
        return {
            "archive_mode": "automatic",
            "document_role": "library",
            "policy_reason": "migration_curated_library",
        }
    if models and models.issubset(BACKGROUND_MODELS):
        return {
            "archive_mode": "automatic",
            "document_role": "background",
            "policy_reason": "migration_historical_record_context",
        }
    if explicit_documents_record:
        return {
            "archive_mode": "automatic",
            "document_role": "library",
            "policy_reason": "migration_explicit_documents_upload",
        }
    return {
        "archive_mode": "automatic",
        "document_role": "background",
        "policy_reason": "migration_external_archive_intake",
    }


def resolve_link_role(
    *,
    res_model: str,
    root_policy: dict[str, str],
) -> dict[str, str]:
    """Resolve one relationship while preserving a stronger root decision."""
    if root_policy["document_role"] == "evidence" or res_model in EVIDENCE_MODELS:
        return {
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "migration_business_evidence",
        }
    if (
        root_policy["document_role"] == "background"
        or res_model in BACKGROUND_MODELS
    ):
        return {
            "archive_mode": "automatic",
            "document_role": "background",
            "policy_reason": "migration_historical_record_context",
        }
    return dict(root_policy)
