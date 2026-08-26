from collections import Counter
from decimal import Decimal

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_b2c_restore.parsers import (
    ETSY_STATEMENT_HEADER,
    MEDUSA_ITEMS_HEADER,
    archive_baseline,
    build_canonical_orders,
    load_csv,
    parse_etsy_statement_events,
    parse_printful_pdf,
    parse_revolut_events,
    parse_stripe_events,
)
from odoo.addons.usl_b2c_restore.source import B2cSourceReader


@tagged("b2c_source", "-standard")
class TestB2cSourceParsers(BaseCase):
    """Qualification tests that require the isolated, read-only source service."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = B2cSourceReader().read()
        cls.documents = cls.source["documents"]
        cls.canonical = build_canonical_orders(
            cls.documents["etsy_items"],
            cls.documents["medusa_legacy"][0],
            cls.documents["medusa"][0],
            cls.documents["medusa_items"][0],
        )
        cls.etsy_events = parse_etsy_statement_events(
            cls.documents["etsy_statement"],
        )
        cls.stripe_events = parse_stripe_events(
            cls.documents["stripe_payment"][0],
            cls.documents["stripe_payout"][0],
        )
        cls.revolut_events = parse_revolut_events(cls.documents["revolut"][0])
        cls.printful_rows = parse_printful_pdf(
            next(
                item["content"]
                for item in cls.source["files"]
                if item["source"].kind == "printful"
            ),
        )

    def test_changed_csv_schema_is_blocking(self):
        changed_header = ",".join((*ETSY_STATEMENT_HEADER[:-1], "Renamed Tax Details"))
        with self.assertRaisesRegex(ValueError, "schema changed"):
            load_csv(
                "changed.csv",
                "synthetic",
                f"{changed_header}\n".encode(),
                ETSY_STATEMENT_HEADER,
            )
        changed_medusa_header = ";".join(
            (*MEDUSA_ITEMS_HEADER[:-1], "renamed_line_total"),
        )
        with self.assertRaisesRegex(ValueError, "schema changed"):
            load_csv(
                "changed-medusa-items.csv",
                "synthetic",
                f"{changed_medusa_header}\n".encode(),
                MEDUSA_ITEMS_HEADER,
                delimiter=";",
            )

    def test_archive_baseline_and_multicurrency(self):
        self.assertEqual(
            self.source["source_company"],
            {
                "id": 1,
                "name": "Unstatic Labs",
                "vat": "FR48983982950",
                "company_registry": "98398295000021",
            },
        )
        baseline = archive_baseline(
            self.canonical,
            self.documents["etsy_statement"],
            self.stripe_events,
            self.revolut_events,
            self.printful_rows,
            self.documents["stripe_payout"][0],
            self.source["catalog_skus"],
        )
        self.assertEqual(baseline["canonical_orders"], 304)
        self.assertEqual(
            baseline["medusa_current_currencies"],
            {"EUR": 67, "GBP": 16, "USD": 13},
        )
        self.assertEqual(
            baseline["revolut_currencies"],
            {"EUR": 35, "GBP": 13, "USD": 270},
        )

    def test_etsy_order_statement_linkage_and_sku_mapping_stays_pending(self):
        etsy_lines = [
            line for line in self.canonical["lines"] if line["provider"] == "etsy"
        ]
        self.assertEqual(len(etsy_lines), 235)
        self.assertEqual(len(self.canonical["etsy_ids"]), 173)
        self.assertEqual(
            sum((line["quantity"] for line in etsy_lines), Decimal()),
            237,
        )
        original_skus = {
            line["original_sku"]
            for line in etsy_lines
            if line["original_sku"]
        }
        self.assertEqual(len(original_skus), 56)
        self.assertFalse(original_skus & set(self.source["catalog_skus"]))

        commerce_events = [
            event
            for event in self.etsy_events
            if event["original_state"] in {"Sale", "Refund"}
        ]
        self.assertEqual(len(commerce_events), 179)
        self.assertTrue(all(event["external_order_id"] for event in commerce_events))
        self.assertTrue(
            all(
                event["external_order_id"] in self.canonical["orders"]
                for event in commerce_events
            ),
        )

    def test_medusa_precedence_and_supplemental_line_coverage(self):
        self.assertEqual(len(self.canonical["legacy_ids"]), 249)
        self.assertEqual(len(self.canonical["current_ids"]), 96)
        overlap = self.canonical["legacy_ids"] & self.canonical["current_ids"]
        self.assertEqual(len(overlap), 41)
        self.assertTrue(
            all(
                self.canonical["orders"][external_id]["source_provider"] == "medusa"
                for external_id in overlap - self.canonical["etsy_ids"]
            ),
        )
        current_only = self.canonical["current_ids"] - self.canonical["etsy_ids"]
        self.assertTrue(
            all(
                self.canonical["orders"][external_id]["amount_completeness"]
                == "partial"
                for external_id in current_only
            ),
        )
        medusa_lines = [
            line for line in self.canonical["lines"] if line["provider"] == "medusa"
        ]
        self.assertEqual(len(medusa_lines), 222)
        self.assertEqual(
            sum((line["quantity"] for line in medusa_lines), Decimal()),
            225,
        )
        self.assertEqual(
            len({line["external_order_id"] for line in medusa_lines}),
            96,
        )
        self.assertTrue(all(not line["external_line_id"] for line in medusa_lines))
        self.assertEqual(sum(bool(line["original_sku"]) for line in medusa_lines), 138)
        self.assertEqual(
            len({line["original_sku"] for line in medusa_lines if line["original_sku"]}),
            50,
        )
        example = [
            line
            for line in medusa_lines
            if line["external_order_id"] == "order_01KWQF6Y14CCS8WSGGDNFFBXT1"
        ]
        self.assertEqual(len(example), 2)
        self.assertEqual(sum((line["quantity"] for line in example), Decimal()), 3)
        self.assertEqual(sum((line["revenue"] for line in example), Decimal()), 111)
        legacy_only = (
            self.canonical["legacy_ids"]
            - self.canonical["current_ids"]
            - self.canonical["etsy_ids"]
        )
        self.assertEqual(len(legacy_only), 35)
        self.assertTrue(
            all(
                self.canonical["orders"][external_id]["amount_completeness"]
                == "header_only"
                for external_id in legacy_only
            ),
        )

    def test_stripe_blank_ids_have_row_keys(self):
        payments = [
            event
            for event in self.stripe_events
            if event["document"].name != self.documents["stripe_payout"][0].name
        ]
        self.assertEqual(len(payments), 149)
        self.assertEqual(
            sum(not event["external_transaction_id"] for event in payments),
            72,
        )
        self.assertEqual(
            len({event["provider_event_key"] for event in self.stripe_events}),
            157,
        )
        self.assertEqual(
            len(
                {
                    event["external_payment_intent_id"]
                    for event in payments
                    if event["external_payment_intent_id"]
                },
            ),
            134,
        )
        self.assertEqual(
            len(
                {
                    event["external_session_id"]
                    for event in payments
                    if event["external_session_id"]
                },
            ),
            117,
        )
        self.assertEqual(
            len(
                {
                    event["external_checkout_session_id"]
                    for event in payments
                    if event["external_checkout_session_id"]
                },
            ),
            4,
        )
        refunded = [event for event in payments if event["refund"]]
        self.assertTrue(refunded)
        self.assertTrue(all(event["refund"] < 0 for event in refunded))
        self.assertTrue(
            all(
                event["event_type"] != "refund" or event["amount"] <= 0
                for event in payments
            ),
        )

    def test_revolut_refunds_link_to_original_payments(self):
        by_id = {
            event["external_transaction_id"]: event
            for event in self.revolut_events
        }
        refunds = [
            event
            for event in self.revolut_events
            if event["event_type"] == "refund"
        ]
        self.assertEqual(len(refunds), 7)
        self.assertTrue(
            all(event["external_original_payment_id"] in by_id for event in refunds),
        )
        self.assertTrue(all(event["amount"] < 0 for event in refunds))

    def test_printful_refunds_and_vat(self):
        self.assertEqual(Counter(row["status"] for row in self.printful_rows), {
            "Completed": 247,
            "Refunded": 14,
        })
        refunds = [row for row in self.printful_rows if row["status"] == "Refunded"]
        self.assertTrue(all(row["total"] < 0 for row in refunds))
        self.assertEqual(sum(bool(row["vat"]) for row in self.printful_rows), 158)
