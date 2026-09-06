"""Shared value rules for proving a reconstruction against its evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo.addons.usl_b2c.models.native_history import (
    MATERIALIZATION_CONTEXT,
    MATERIALIZATION_TOKEN,
)
from odoo.addons.usl_b2c_restore.native_plan import acquisitions


def materialization_context():
    """Return the context that marks trusted, quiet importer ownership."""
    return {
        MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN,
        "tracking_disable": True,
        "mail_create_nosubscribe": True,
        "mail_notrack": True,
        "mail_notify_force_send": False,
    }


def bill_evidence_counts():
    """Return how many acquisition lines share one vendor-bill line."""
    return Counter(
        (line["bill_ref"], line["bill_label"])
        for acquisition in acquisitions()
        for line in acquisition["lines"]
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode(),
    ).hexdigest()


def _stored_value(record, field_name):
    """Return one stored value in a form that compares and prints cleanly."""
    field = record._fields[field_name]
    value = record[field_name]
    if field.type == "many2one":
        return value.id or False
    if field.type in {"many2many", "one2many"}:
        return set(value.ids)
    if field.type in {"date", "datetime"}:
        return fields.Datetime.to_datetime(value) if value else False
    return value


def _expected_value(record, field_name, expected):
    """Return one expectation in the same form as its stored counterpart.

    A relational expectation may be written as a record or as a stored id;
    both describe the same fact and must compare identically.
    """
    field = record._fields[field_name]
    if field.type == "many2one":
        if isinstance(expected, models.BaseModel):
            return expected.id or False
        return expected or False
    if field.type in {"many2many", "one2many"}:
        return set(expected.ids) if expected else set()
    if field.type in {"date", "datetime"}:
        return fields.Datetime.to_datetime(expected) if expected else False
    return expected


def value_differs(record, field_name, expected):
    """Compare one stored value with its expectation at the field's own precision.

    Odoo rounds every float to the precision its field declares, so comparing a
    stored amount against an exact source decimal with a tighter tolerance turns
    ordinary rounding into false drift and breaks an otherwise correct rerun.
    """
    field = record._fields[field_name]
    actual = _stored_value(record, field_name)
    expected = _expected_value(record, field_name, expected)
    if field.type == "monetary":
        currency = record[field.get_currency_field(record)]
        if currency:
            return bool(currency.compare_amounts(float(actual), float(expected or 0)))
    if field.type in {"float", "monetary"}:
        digits = field.get_digits(record.env) if hasattr(field, "get_digits") else None
        return float_compare(
            float(actual),
            float(expected or 0),
            precision_digits=digits[1] if digits else 6,
        ) != 0
    return actual != expected


def assert_values(record, expected, label):
    """Fail closed with concise evidence when a record drifted from its source."""
    drift = {
        field_name: {
            "actual": _stored_value(record, field_name),
            "expected": _expected_value(record, field_name, expected_value),
        }
        for field_name, expected_value in expected.items()
        if value_differs(record, field_name, expected_value)
    }
    if drift:
        raise UserError(f"{label} drifted from reviewed evidence: {drift!r}.")
