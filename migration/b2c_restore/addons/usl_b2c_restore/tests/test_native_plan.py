from collections import defaultdict
from decimal import Decimal

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_b2c_restore.native_plan import (
    ACQUISITIONS,
    EXPECTED_SOURCE_FINGERPRINTS,
    EXPECTED_THEORETICAL_STOCK,
    PACK_COMPONENTS,
    accepted_finalization_run,
    source_line_components,
    source_fingerprint_mismatches,
    stock_disposition,
)


@tagged("post_install", "-at_install")
class TestNativeHistoryPlan(BaseCase):
    def test_frozen_source_fingerprint_fails_closed_on_same_count_drift(self):
        self.assertFalse(source_fingerprint_mismatches(EXPECTED_SOURCE_FINGERPRINTS))

        drifted = {
            key: dict(value)
            for key, value in EXPECTED_SOURCE_FINGERPRINTS.items()
        }
        drifted["evidence"]["digest"] = "changed-with-the-same-count"
        self.assertEqual(
            source_fingerprint_mismatches(drifted),
            {
                "evidence": {
                    "actual": {
                        "count": 2893,
                        "digest": "changed-with-the-same-count",
                    },
                    "expected": EXPECTED_SOURCE_FINGERPRINTS["evidence"],
                },
            },
        )

    def test_finalization_accepts_source_or_applied_native_history(self):
        self.assertEqual(
            accepted_finalization_run(restore_status="passed"),
            "source_restore",
        )
        self.assertEqual(
            accepted_finalization_run(native_mode="apply", native_state="passed"),
            "native_history",
        )
        with self.assertRaises(RuntimeError):
            accepted_finalization_run(native_mode="dry_run", native_state="passed")

    def test_documented_acquisitions_have_the_reviewed_physical_totals(self):
        acquired = defaultdict(Decimal)
        samples = Decimal()
        for acquisition in ACQUISITIONS:
            for line in acquisition["lines"]:
                if acquisition.get("internal_consumption"):
                    samples += line["quantity"]
                    continue
                components = PACK_COMPONENTS.get(line["code"], {line["code"]: Decimal("1")})
                for code, quantity in components.items():
                    acquired[code] += line["quantity"] * quantity

        self.assertEqual(samples, 8)
        self.assertEqual(acquired["CHAIN_CM_3MM_AISI404_CNCHO10CHO"], 50)
        self.assertEqual(acquired["CHAIN_CM_4MM_AISI404_CNCHO10CHO"], 50)
        self.assertEqual(acquired["CHAIN_CM_6MM_LEROYMERLIN"], 27)
        self.assertEqual(acquired["PADLOCK_MASTER_9120EUR_BLACK"], 130)
        for colour in ("BLUE", "GREEN", "PINK", "PURPLE"):
            self.assertEqual(acquired[f"PADLOCK_MASTER_9120EUR_{colour}"], 3)
        self.assertEqual(acquired["PADLOCK_PURPLE"], 50)
        self.assertEqual(acquired["PADLOCK_BLACK"], 50)
        self.assertEqual(acquired["PADLOCK_RED"], 45)
        self.assertEqual(acquired["PADLOCK_BLUE"], 45)
        for colour in ("BROWN", "GOLD", "GREEN", "ORANGE"):
            self.assertEqual(acquired[f"PADLOCK_{colour}"], 25)

    def test_component_and_disposition_rules_are_exact(self):
        self.assertEqual(
            source_line_components(
                "Ankle chains",
                "M (26cm) / 4mm (14x21mm links) / Two Chains With Padlocks",
                2,
            ),
            {
                "CHAIN_CM_4MM_AISI404_CNCHO10CHO": Decimal("1.04"),
                "PADLOCK_MASTER_9120EUR_BLACK": Decimal("4"),
            },
        )
        self.assertEqual(
            stock_disposition("fulfilled", "delivered", "42", "own_stock"),
            "delivered",
        )
        self.assertEqual(
            stock_disposition("confirmed", "not_fulfilled", "42", "own_stock"),
            "reserved",
        )
        self.assertEqual(
            stock_disposition("fulfilled", "delivered", "42", "printful"),
            "pod",
        )
        self.assertEqual(
            stock_disposition("fulfilled", "delivered", "1617586251", "own_stock"),
            "internal_consumption",
        )

    def test_expected_stock_contract_keeps_reservations_separate(self):
        self.assertEqual(
            EXPECTED_THEORETICAL_STOCK["CHAIN_CM_3MM_AISI404_CNCHO10CHO"],
            (Decimal("32.24"), Decimal("3.06")),
        )
        self.assertEqual(
            EXPECTED_THEORETICAL_STOCK["PADLOCK_MASTER_9120EUR_BLACK"],
            (Decimal("81"), Decimal("14")),
        )
        self.assertEqual(
            EXPECTED_THEORETICAL_STOCK["PADLOCK_ORANGE"],
            (Decimal("21"), Decimal("1")),
        )
