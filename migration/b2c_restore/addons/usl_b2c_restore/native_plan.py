"""Reviewed, deterministic plan for promoting USL B2C history into native Odoo.

Only source-specific evidence belongs here.  The delivered ``usl_b2c`` module
owns the durable fields and safety guards; it does not know these one-off
supplier references or historical quantities.
"""

from __future__ import annotations

import re
from decimal import Decimal

from odoo.addons.usl_b2c_restore import private_evidence


EXPECTED_NATIVE_COUNTS = {
    "orders": 304,
    "detailed_lines": 457,
    "etsy_orders": 173,
    "medusa_orders": 96,
    "legacy_orders": 35,
    "printful_events": 261,
    "aliases": 174,
    "provider_evidence": 2893,
    "order_sources": 614,
    "source_documents": 40,
    "sale_lines": 940,
    "contacts": 365,
    "partner_identities": 365,
    "purchases": 17,
    "receipts": 17,
    "unbuilds": 12,
    "landed_costs": 3,
    "productions": 81,
    "productions_done": 69,
    "productions_open": 12,
    "deliveries": 85,
    "deliveries_done": 77,
    "deliveries_open": 8,
    "internal_order_consumption": 1,
    "internal_pickings": 2,
}


# Accepted from the independently qualified, finalized production clone. Counts
# alone are insufficient here: a provider payload could drift while preserving
# the number of records. These digests intentionally belong to migration code,
# not the delivered product module.
EXPECTED_SOURCE_FINGERPRINTS = {
    "aliases": {
        "count": 174,
        "digest": "185cf839848fdb6c35fd6892547384f511f1909297f69285ee07b721985be498",
    },
    "evidence": {
        "count": 2893,
        "digest": "bfdc75f35ee2bc36193e5b38fe207800944a387163d015c5fdac0cc9041b9e8a",
    },
    "fulfilment_events": {
        "count": 261,
        "digest": "18302ef601aabc8d5f84c208b179a211ae34ab100e0ea673f20128b193c08f7b",
    },
    "lines": {
        "count": 457,
        "digest": "38d9f2b29fa7bce19873df057be26568d80650a1c7a8b14b13806612f99f29c9",
    },
    "order_sources": {
        "count": 614,
        "digest": "6a17fe80ab9197b95c4fd1361c9fbb700061bb5a8eb7038582de084849c2b99c",
    },
    "orders": {
        "count": 304,
        "digest": "8407c1d7f82683226cc7ca2f4c06fa8c01f2b98bdd801e837386aa3a183b2bc8",
    },
    "source_documents": {
        "count": 40,
        "digest": "fde60c95419e1fb0da3de42b38f352831730dc71b0188dd46f718f0b6082ca85",
    },
}


def source_fingerprint_mismatches(actual):
    """Return concise drift evidence against the qualified frozen source."""
    keys = set(actual) | set(EXPECTED_SOURCE_FINGERPRINTS)
    return {
        key: {
            "actual": actual.get(key),
            "expected": EXPECTED_SOURCE_FINGERPRINTS.get(key),
        }
        for key in sorted(keys)
        if actual.get(key) != EXPECTED_SOURCE_FINGERPRINTS.get(key)
    }


def accepted_finalization_run(*, restore_status=None, native_mode=None, native_state=None):
    """Return the independently passed reconstruction that permits finalization."""
    if native_mode == "apply" and native_state == "passed":
        return "native_history"
    if restore_status == "passed":
        return "source_restore"
    raise RuntimeError(
        "B2C finalization requires either a passed source restoration or a "
        "passed native-history materialization.",
    )

EXPECTED_THEORETICAL_STOCK = {
    "CHAIN_CM_3MM_AISI404_CNCHO10CHO": (Decimal("32.24"), Decimal("3.06")),
    "CHAIN_CM_4MM_AISI404_CNCHO10CHO": (Decimal("43.98"), Decimal("2.34")),
    "CHAIN_CM_6MM_LEROYMERLIN": (Decimal("18.29"), Decimal("0.50")),
    "PADLOCK_MASTER_9120EUR_BLACK": (Decimal("81"), Decimal("14")),
    "PADLOCK_MASTER_9120EUR_BLUE": (Decimal("2"), Decimal("0")),
    "PADLOCK_MASTER_9120EUR_GREEN": (Decimal("3"), Decimal("0")),
    "PADLOCK_MASTER_9120EUR_PINK": (Decimal("2"), Decimal("0")),
    "PADLOCK_MASTER_9120EUR_PURPLE": (Decimal("3"), Decimal("0")),
    "PADLOCK_BLACK": (Decimal("35"), Decimal("0")),
    "PADLOCK_BLUE": (Decimal("35"), Decimal("0")),
    "PADLOCK_BROWN": (Decimal("24"), Decimal("0")),
    "PADLOCK_GOLD": (Decimal("20"), Decimal("0")),
    "PADLOCK_GREEN": (Decimal("23"), Decimal("0")),
    "PADLOCK_ORANGE": (Decimal("21"), Decimal("1")),
    "PADLOCK_PURPLE": (Decimal("35"), Decimal("1")),
    "PADLOCK_RED": (Decimal("40"), Decimal("0")),
}


def acquisitions():
    """Return the reviewed supplier acquisitions from pinned evidence.

    Supplier identities, order references and unit prices are commercial facts
    kept with the source package, never in this public repository.
    """
    return private_evidence.acquisitions()


PACK_COMPONENTS = {
    "GBC-ML-9120-TBLK": {"PADLOCK_MASTER_9120EUR_BLACK": Decimal("2")},
    "GBC-ML-9120-QBLKNOP": {"PADLOCK_MASTER_9120EUR_BLACK": Decimal("4")},
    "GBC-ML-9120-QCOLNOP": {
        "PADLOCK_MASTER_9120EUR_BLUE": Decimal("1"),
        "PADLOCK_MASTER_9120EUR_GREEN": Decimal("1"),
        "PADLOCK_MASTER_9120EUR_PINK": Decimal("1"),
        "PADLOCK_MASTER_9120EUR_PURPLE": Decimal("1"),
    },
}


def source_line_components(name, variation, quantity):
    """Return exact raw-material demand for one configured stock line."""
    label = f"{name or ''} {variation or ''}"
    if not re.search(r"\b(chain|chains|collar)\b", label, flags=re.IGNORECASE):
        return {}
    length = re.search(r"(?<!\d)(\d+)\s*cm\b", label, flags=re.IGNORECASE)
    if not length:
        raise ValueError(f"Configured stock line has no exact chain length: {label!r}")
    diameter = re.search(r"(?<!\d)([346])\s*mm\b", label, flags=re.IGNORECASE)
    if not diameter:
        raise ValueError(f"Configured stock line has no exact chain diameter: {label!r}")
    chain_code = {
        "3": "CHAIN_CM_3MM_AISI404_CNCHO10CHO",
        "4": "CHAIN_CM_4MM_AISI404_CNCHO10CHO",
        "6": "CHAIN_CM_6MM_LEROYMERLIN",
    }[diameter.group(1)]
    chain_count = Decimal("2") if re.search(r"\btwo chains\b", label, re.I) else Decimal("1")
    qty = Decimal(str(quantity))
    components = {
        chain_code: qty * Decimal(length.group(1)) / Decimal("100") * chain_count,
    }
    if re.search(r"\btwo chains with padlocks\b", label, re.I):
        components["PADLOCK_MASTER_9120EUR_BLACK"] = qty * Decimal("2")
    elif re.search(r"\b(one chain with padlock|with engraved padlock)\b", label, re.I):
        components["PADLOCK_MASTER_9120EUR_BLACK"] = qty
    return components


def stock_disposition(order_state, source_fulfilment_state, external_display_id, fulfilment_mode):
    """Classify a source line without consulting provider inventory quantities."""
    if fulfilment_mode == "printful":
        return "pod"
    if order_state == "cancelled":
        return "cancelled"
    if external_display_id == "1617586251":
        return "internal_consumption"
    if source_fulfilment_state == "delivered" or order_state in {
        "fulfilled",
        "partially_refunded",
        "refunded",
    }:
        return "delivered"
    if source_fulfilment_state in {"partially_delivered", "not_fulfilled"}:
        return "reserved"
    raise ValueError(
        "Stock-bearing B2C line has no reviewed fulfilment disposition: "
        f"{external_display_id!r} / {order_state!r} / {source_fulfilment_state!r}",
    )
