"""Reviewed, deterministic plan for promoting USL B2C history into native Odoo.

Only source-specific evidence belongs here.  The delivered ``usl_b2c`` module
owns the durable fields and safety guards; it does not know these one-off
supplier references or historical quantities.
"""

from __future__ import annotations

import re
from decimal import Decimal


EXPECTED_NATIVE_COUNTS = {
    "orders": 304,
    "detailed_lines": 457,
    "etsy_orders": 173,
    "medusa_orders": 96,
    "legacy_orders": 35,
    "printful_events": 261,
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


def _line(code, quantity, price, bill_ref, bill_label):
    return {
        "code": code,
        "quantity": Decimal(str(quantity)),
        "price": Decimal(str(price)),
        "bill_ref": bill_ref,
        "bill_label": bill_label,
    }


ACQUISITIONS = (
    {
        "key": "quandun-samples-25101809001609508931",
        "date": "2025-10-01",
        "partner": "Zhejiang Quandun Import & Export Co., Ltd.",
        "currency": "USD",
        "lines": (
            _line(
                "B2C-SAMPLE-QD40-2025",
                8,
                "3.60",
                "ORDER 25101809001609508931 — INVOICE PENDING",
                "Premium Qvand aluminum padlocks — 8 sample units @ $3.60",
            ),
        ),
        "internal_consumption": True,
    },
    {
        "key": "chonghong-vv2025001",
        "date": "2025-10-31",
        "partner": "Chonghong Industries Ltd",
        "currency": "USD",
        "lines": (
            _line(
                "CHAIN_CM_4MM_AISI404_CNCHO10CHO",
                50,
                "3.61",
                "VV2025001-2",
                "AISI304 stainless chain 4 mm — 50 m",
            ),
            _line(
                "CHAIN_CM_3MM_AISI404_CNCHO10CHO",
                50,
                "1.62",
                "VV2025001-2",
                "AISI304 stainless chain 3 mm — 50 m",
            ),
        ),
        "landed_cost": {
            "key": "chonghong-vv2025001-freight",
            "amount": Decimal("69.31"),
            "split_method": "by_current_cost_price",
            "label": "DDP inbound freight by sea — order 12786708501028141",
        },
    },
    {
        "key": "amazon-406-0891968-6315562-italy",
        "date": "2025-10-27",
        "partner": "Amazon EU S.à r.l., Succursale Italiana",
        "currency": "EUR",
        "lines": (
            _line("GBC-ML-9120-TBLK", 2, "7.79", "IT25-AEUI-14889679", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),
            _line("GBC-ML-9120-QCOLNOP", 1, "12.93", "IT25-AEUI-14889679", "Master Lock 9120EURQCOLNOP — ASIN B001OXDCOI"),
        ),
    },
    {
        "key": "amazon-406-0891968-6315562-france",
        "date": "2025-10-27",
        "partner": "Amazon EU S.à r.l., Succursale Française",
        "currency": "EUR",
        "lines": (
            _line("GBC-ML-9120-QCOLNOP", 1, "15.52", "FR51VOGK5AEUI", "Master Lock 9120EURQCOLNOP — ASIN B001OXDCOI"),
        ),
    },
    {
        "key": "quandun-qd251110",
        "date": "2025-11-10",
        "partner": "Zhejiang Quandun Import & Export Co., Ltd.",
        "currency": "USD",
        "lines": tuple(
            _line(
                f"PADLOCK_{colour.upper()}",
                25,
                "3.42",
                "QD251110",
                f"40 mm padlock — {colour} — quantity 25",
            )
            for colour in ("Red", "Blue", "Orange", "Purple", "Green", "Brown", "Black", "Gold")
        ),
        "landed_cost": {
            "key": "quandun-qd251110-ddp",
            "amount": Decimal("303.23"),
            "split_method": "by_quantity",
            "label": "DDP inbound freight for 200 padlocks",
        },
    },
    {
        "key": "amazon-406-7735380-7669119-406-4629097-6281925",
        "date": "2025-12-09",
        "partner": "Amazon EU S.à r.l., Sucursal en España",
        "currency": "EUR",
        "lines": (
            _line("GBC-ML-9120-TBLK", 8, "7.79", "ES53QZJ3AEUI", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),
            _line("GBC-ML-9120-QCOLNOP", 1, "16.09", "ES53QZXLAEUI", "Master Lock 9120EURQCOLNOP — ASIN B001OXDCOI"),
        ),
    },
    {
        "key": "heinle-fr50072hnjb3xi",
        "date": "2025-12-10",
        "partner": "Heinle Solution GmbH — immatriculation TVA FR",
        "currency": "EUR",
        "lines": (_line("CHAIN_CM_6MM_LEROYMERLIN", 9, "9.324444444444445", "FR50072HNJB3XI", "Stainless chain A4/316, 6 mm — quantity 9 m"),),
    },
    {
        "key": "amazon-406-1468164-1211513",
        "date": "2026-01-04",
        "partner": "Amazon EU S.à r.l., Succursale Italiana",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-QBLKNOP", 5, "14.92", "IT26-AEUI-104861", "Master Lock 9120EURQBLKNOP, pack familial — ASIN B001MTEROI"),),
    },
    {
        "key": "heinle-it60008znjb3xi",
        "date": "2026-03-18",
        "partner": "Heinle Solution GmbH — immatriculation TVA IT",
        "currency": "EUR",
        "lines": (_line("CHAIN_CM_6MM_LEROYMERLIN", 9, "9.052222222222222", "IT60008ZNJB3XI", "Stainless chain A4/316, 6 mm — quantity 9 m"),),
    },
    {
        "key": "amazon-406-9007321-8629100",
        "date": "2026-03-22",
        "partner": "Amazon EU S.à r.l., Succursale Italiana",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-QBLKNOP", 2, "14.92", "IT26-AEUI-4476677", "Master Lock 9120EURQBLKNOP, pack familial — ASIN B001MTEROI"),),
    },
    {
        "key": "quandun-qd01-040526",
        "date": "2026-05-05",
        "partner": "Zhejiang Quandun Import & Export Co., Ltd.",
        "currency": "USD",
        "lines": (
            _line("PADLOCK_PURPLE", 25, "3.42", "QD01-040526", "40 mm padlocks — 90 units — QD01-050526"),
            _line("PADLOCK_BLACK", 25, "3.42", "QD01-040526", "40 mm padlocks — 90 units — QD01-050526"),
            _line("PADLOCK_RED", 20, "3.42", "QD01-040526", "40 mm padlocks — 90 units — QD01-050526"),
            _line("PADLOCK_BLUE", 20, "3.42", "QD01-040526", "40 mm padlocks — 90 units — QD01-050526"),
        ),
        "landed_cost": {
            "key": "quandun-qd01-040526-freight-duty",
            "amount": Decimal("132.64"),
            "split_method": "by_quantity",
            "label": "Inbound freight — QD01-040526 (DDP freight and duty)",
        },
    },
    {
        "key": "heinle-de600axanjb3xi",
        "date": "2026-05-16",
        "partner": "Heinle Solution GmbH",
        "currency": "EUR",
        "lines": (_line("CHAIN_CM_6MM_LEROYMERLIN", 9, "8.976666666666667", "DE600AXANJB3XI", "Stainless chain A4/316, 6 mm — quantity 9 m"),),
    },
    {
        "key": "amazon-master-2026-05-17",
        "date": "2026-05-17",
        "partner": "Amazon EU S.à r.l., Sucursal en España",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-TBLK", 15, "6.66", "ES61HLK3AEUI", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),),
    },
    {
        "key": "amazon-master-2026-05-18",
        "date": "2026-05-18",
        "partner": "Amazon EU S.à r.l., Sucursal en España",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-QBLKNOP", 11, "19.00", "ES61I2E6AEUI", "Master Lock 9120EURQBLKNOP, pack familial — ASIN B001MTEROI"),),
    },
    {
        "key": "amazon-master-2026-05-27",
        "date": "2026-05-27",
        "partner": "Amazon EU S.à r.l., Sucursal en España",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-TBLK", 2, "7.34", "ES61LK20AEUI", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),),
    },
    {
        "key": "amazon-master-2026-08-01",
        "date": "2026-08-01",
        "partner": "Amazon EU S.à r.l., Succursale Italiana",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-TBLK", 1, "7.79", "IT26-AEUI-12045143", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),),
    },
    {
        "key": "amazon-master-2026-08-04",
        "date": "2026-08-04",
        "partner": "Amazon EU S.à r.l., Succursale Italiana",
        "currency": "EUR",
        "lines": (_line("GBC-ML-9120-TBLK", 1, "7.79", "IT26-AEUI-12166630", "Master Lock 9120EURTBLK, lot de 2 — ASIN B001MTEROS"),),
    },
)

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
