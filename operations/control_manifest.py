"""Classify and compare recovery controls without hiding release changes."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA = "usl-control-manifest/v1"

# These records express business history. A module upgrade may extend their
# schema, but it may not silently add, remove or rewrite the captured records.
ODOO_PRESERVATION_KEYS = frozenset(
    {
        "companies",
        "users",
        "employees",
        "agents",
        "moves",
        "move_lines",
        "posted_move_fingerprint",
        "posted_line_fingerprint",
        "partial_reconcile_fingerprint",
        "currency_rate_fingerprint",
        "attachments",
        "messages",
        "activities",
        "stored_attachments",
        "projects",
        "tasks",
        "expenses",
        "assets",
        "analytics",
        "taxes",
        "platform_sessions",
        "platform_payouts",
        "tese_payslips",
        "ledger_delta",
    },
)

# These values are owned by the candidate release. They may legitimately
# change during an upgrade, but production must match what staging signed.
ODOO_RELEASE_KEYS = frozenset(
    {
        "acl_fingerprint",
        "record_rule_fingerprint",
    },
)

# Pending work may drain during maintenance. It may not grow while all writers
# are quiesced. Failed work and cron failures are rejected by smoke admission.
ODOO_QUEUE_KEYS = frozenset(
    {
        "queued_mail",
        "failed_mail",
        "pending_documents",
        "failed_documents",
        "bank_pending",
        "bank_failed",
        "payment_pending",
        "payment_failed",
        "sign_archive_pending",
        "sign_archive_failed",
        "cron_failures",
    },
)

PAPERLESS_PRESERVATION_KEYS = frozenset(
    {
        "documents",
        "with_ocr",
        "missing_original_name",
    },
)

PENDING_QUEUE_KEYS = frozenset(
    {
        "queued_mail",
        "pending_documents",
        "bank_pending",
        "payment_pending",
        "sign_archive_pending",
    },
)

FAILED_QUEUE_KEYS = frozenset(
    {
        "failed_mail",
        "failed_documents",
        "bank_failed",
        "payment_failed",
        "sign_archive_failed",
        "cron_failures",
    },
)


class ControlManifestError(ValueError):
    """A control set is incomplete, unknown, or indicates data drift."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


def _exact_section(
    values: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> dict[str, Any]:
    actual = set(values)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ControlManifestError(
            f"{label} control fields differ: missing={missing}, unknown={unknown}",
        )
    return {key: values[key] for key in sorted(expected)}


def classify(controls: object) -> dict[str, Any]:
    """Return fail-closed business, release, and queue control sections."""
    if not isinstance(controls, dict) or set(controls) != {"odoo", "paperless"}:
        raise ControlManifestError("control roots must be exactly Odoo and Paperless")
    odoo = controls["odoo"]
    paperless = controls["paperless"]
    if not isinstance(odoo, dict) or not isinstance(paperless, dict):
        raise ControlManifestError("control roots must be objects")
    expected_odoo = ODOO_PRESERVATION_KEYS | ODOO_RELEASE_KEYS | ODOO_QUEUE_KEYS
    _exact_section(odoo, expected_odoo, "Odoo")
    paperless_values = _exact_section(
        paperless,
        PAPERLESS_PRESERVATION_KEYS,
        "Paperless",
    )
    return {
        "schema": SCHEMA,
        "preservation": {
            "odoo": {key: odoo[key] for key in sorted(ODOO_PRESERVATION_KEYS)},
            "paperless": paperless_values,
        },
        "release": {
            "odoo": {key: odoo[key] for key in sorted(ODOO_RELEASE_KEYS)},
        },
        "queues": {
            "odoo": {key: odoo[key] for key in sorted(ODOO_QUEUE_KEYS)},
        },
    }


def release_digest(controls: object) -> str:
    return _digest(classify(controls)["release"])


def validate_restore(
    before: object,
    after: object,
    *,
    expected_release_sha256: str | None = None,
    require_unchanged_release: bool = False,
) -> dict[str, Any]:
    """Validate preserved data, queue monotonicity, and release-owned policy."""
    baseline = classify(before)
    candidate = classify(after)
    if baseline["preservation"] != candidate["preservation"]:
        raise ControlManifestError("restored business controls differ from the source cohort")

    baseline_queues = baseline["queues"]["odoo"]
    candidate_queues = candidate["queues"]["odoo"]
    regressions = {
        key: {"before": baseline_queues[key], "after": candidate_queues[key]}
        for key in sorted(PENDING_QUEUE_KEYS)
        if candidate_queues[key] > baseline_queues[key]
    }
    if regressions:
        raise ControlManifestError(
            "pending queues grew while writers were quiesced: "
            + json.dumps(regressions, sort_keys=True),
        )
    failed = {
        key: candidate_queues[key]
        for key in sorted(FAILED_QUEUE_KEYS)
        if candidate_queues[key]
    }
    if failed:
        raise ControlManifestError(
            "restored runtime has failed queues or crons: "
            + json.dumps(failed, sort_keys=True),
        )

    candidate_release_sha256 = _digest(candidate["release"])
    if require_unchanged_release:
        baseline_release_sha256 = _digest(baseline["release"])
        if candidate_release_sha256 != baseline_release_sha256:
            raise ControlManifestError("same-release restore changed release-owned controls")
    if expected_release_sha256 is not None:
        if candidate_release_sha256 != expected_release_sha256:
            raise ControlManifestError(
                "production release controls differ from staging-qualified evidence",
            )

    return {
        "schema": "usl-control-validation/v1",
        "preservation_sha256": _digest(candidate["preservation"]),
        "release_sha256": candidate_release_sha256,
        "queues": candidate["queues"],
        "status": "passed",
    }
