"""Commercial evidence that belongs with the source, not in the repository.

Supplier identities, order references, unit prices and freight are business
facts. This repository is public, so it carries only the contract and the exact
digest of each evidence file; the values themselves stay in the frozen source
package beside the other supplemental evidence.

Loading is lazy and cached: a module that merely imports this one still imports
cleanly on a machine without the source package, and only fails when it actually
asks for evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

EVIDENCE_DIR = Path(
    os.getenv(
        "B2C_SUPPLEMENTAL_EVIDENCE_DIR",
        "/mnt/accounting-source/supplemental/b2c",
    ),
).resolve()

# Every accepted file, pinned exactly. A changed digest stops the run.
PINNED_EVIDENCE = {
    "supplier-acquisitions-2026-09-06.json": (
        "2e5eadb40d2ee6e7f7c8905e2da8b6726db5b4835fca87cf576ef8d80dec98a7"
    ),
    "supplier-identities-2026-09-06.json": (
        "46566ff9837ac29749834179721a4b03bca74e374e54e5a5f04e1b66fdf4f7f0"
    ),
    "printful-order-items-2026-09-06.json": (
        "7f22ff667369a7622a018c2ec612d107488382e79d6e02112699bfe6b974c8e7"
    ),
}

MONEY_FIELDS = frozenset({"quantity", "price", "amount"})


class MissingEvidenceError(RuntimeError):
    """The evidence file is absent, unreadable, or not the reviewed one."""


@lru_cache(maxsize=None)
def load(name):
    """Return one pinned evidence document."""
    expected = PINNED_EVIDENCE.get(name)
    if expected is None:
        raise MissingEvidenceError(f"{name} is not reviewed evidence.")
    path = (EVIDENCE_DIR / name).resolve()
    if EVIDENCE_DIR not in path.parents:
        raise MissingEvidenceError(f"{name} resolves outside the evidence directory.")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise MissingEvidenceError(
            f"Reviewed B2C evidence {name} is missing from {EVIDENCE_DIR}: {error}",
        ) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected:
        raise MissingEvidenceError(
            f"Reviewed B2C evidence {name} changed: {digest} is not {expected}.",
        )
    return json.loads(content)


def available(name):
    """Return whether one pinned document can be read right now."""
    try:
        load(name)
    except MissingEvidenceError:
        return False
    return True


def _decimals(value, money=False):
    if isinstance(value, dict):
        return {
            key: _decimals(item, money=money or key in MONEY_FIELDS)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return tuple(_decimals(item, money=money) for item in value)
    if money and isinstance(value, str):
        return Decimal(value)
    return value


@lru_cache(maxsize=1)
def acquisitions():
    """Return the reviewed supplier acquisitions, with exact decimal amounts."""
    document = load("supplier-acquisitions-2026-09-06.json")
    return tuple(_decimals(entry) for entry in document["acquisitions"])


@lru_cache(maxsize=1)
def printful_orders():
    """Return the recovered Printful orders, keyed by their merchant reference.

    A Printful order with no `external_id` has no store order behind it: it was
    raised by hand for marketing or prototyping and was never a customer sale.
    """
    document = load("printful-order-items-2026-09-06.json")
    return {entry["legacy_order_id"]: entry for entry in document["orders"]}


@lru_cache(maxsize=1)
def supplier_identities():
    """Return the source supplier name to canonical legal identity mapping."""
    return dict(load("supplier-identities-2026-09-06.json")["identities"])
