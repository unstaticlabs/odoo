"""Synthetic bank protocol invariants, without an Odoo registry or network."""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "custom-addons/usl_enablebanking/services/protocol.py"
spec = importlib.util.spec_from_file_location("usl_enablebanking_protocol", SOURCE)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)


def movement(reference="entry-a", **values):
    return {
        "entry_reference": reference,
        "status": "BOOK",
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "booking_date": "2026-10-01",
        "value_date": "2026-10-02",
        "remittance_information": ["Synthetic movement"],
        **values,
    }


class EnableBankingProtocolCase(unittest.TestCase):
    def test_two_equal_movements_with_distinct_bank_references_both_survive(self):
        for amount, date, count in itertools.product(("0.01", "10.00", "123.45"),
                                                     ("2026-10-01", "2026-10-02"), (2, 5)):
            rows = [movement(f"entry-{i}", booking_date=date,
                             transaction_amount={"amount": amount, "currency": "EUR"}) for i in range(count)]
            result = protocol.normalise_transactions(rows, "EUR")
            self.assertEqual(len(result.transactions), count)
            self.assertEqual({x["entry_reference"] for x in result.transactions}, {f"entry-{i}" for i in range(count)})

    def test_repeat_pages_deduplicate_only_the_immutable_reference(self):
        result = protocol.normalise_transactions([movement("a"), movement("b"), movement("a")], "EUR")
        self.assertEqual([x["entry_reference"] for x in result.transactions], ["a", "b"])
        self.assertEqual(result.duplicate_count, 1)

    def test_conflicting_facts_for_one_bank_reference_are_visible(self):
        with self.assertRaisesRegex(protocol.ProviderDataError, "identity_conflict"):
            protocol.normalise_transactions([movement(), movement(booking_date="2026-10-03")], "EUR")

    def test_session_scoped_transaction_id_is_never_a_deduplication_key(self):
        with self.assertRaisesRegex(protocol.ProviderDataError, "missing_entry_reference"):
            protocol.normalise_transactions([movement(None, transaction_id="session-detail-only")], "EUR")

    def test_malformed_amount_and_account_shapes_have_safe_codes(self):
        for amount in (None, [], "not-an-amount-object"):
            with self.subTest(amount=amount), self.assertRaisesRegex(protocol.ProviderDataError, "invalid_amount"):
                protocol.normalise_transactions([movement(transaction_amount=amount)], "EUR")
        for account in (None, [], "not-an-account-object"):
            with self.subTest(account=account), self.assertRaisesRegex(protocol.ProviderDataError, "invalid_account_identity"):
                protocol.account_identity(account)

    def test_unhashable_status_and_direction_have_stable_error_codes(self):
        for field, code in (("status", "invalid_transaction_status"), ("credit_debit_indicator", "invalid_direction")):
            for value in ([], {}, ["BOOK"]):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(protocol.ProviderDataError, code):
                    protocol.normalise_transactions([movement("valid-first"), movement("invalid-second", **{field: value})], "EUR")

    def test_pending_transactions_do_not_become_booked_lines(self):
        result = protocol.normalise_transactions([movement(), movement("pending", status="PDNG")], "EUR")
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.pending_count, 1)

    def test_native_booking_period_ignores_outside_rows_without_inventing_identities(self):
        rows = [movement(None, booking_date="2026-09-30"), movement("included"), movement(None, booking_date="2026-10-03")]
        result = protocol.normalise_transactions(rows, "EUR", date_since="2026-10-01", date_until="2026-10-02")
        self.assertEqual([row["entry_reference"] for row in result.transactions], ["included"])

    def test_bank_direction_controls_the_native_signed_amount(self):
        for amount in ("10.00", "-10.00"):
            debit = protocol.normalise_transactions([movement(transaction_amount={"amount": amount, "currency": "EUR"})], "EUR")
            credit = protocol.normalise_transactions([movement(credit_debit_indicator="CRDT", transaction_amount={"amount": amount, "currency": "EUR"})], "EUR")
            self.assertEqual(debit.transactions[0]["amount"], -10.0)
            self.assertEqual(credit.transactions[0]["amount"], 10.0)

    def test_currency_mismatch_and_nonfinite_amounts_never_become_lines(self):
        for amount, currency, code in [("NaN", "EUR", "invalid_amount"), ("Infinity", "EUR", "invalid_amount"), ("1.00", "USD", "currency_mismatch")]:
            with self.assertRaisesRegex(protocol.ProviderDataError, code):
                protocol.normalise_transactions([movement(transaction_amount={"amount": amount, "currency": currency})], "EUR")

    def test_no_booking_date_is_not_silently_replaced_by_a_value_date(self):
        with self.assertRaisesRegex(protocol.ProviderDataError, "missing_booking_date"):
            protocol.normalise_transactions([movement(booking_date=None)], "EUR")

    def test_import_identity_survives_session_renewal_and_scopes_the_account(self):
        self.assertEqual(protocol.import_identity(4, "account-a", "entry-a"), protocol.import_identity(4, "account-a", "entry-a"))
        self.assertNotEqual(protocol.import_identity(4, "account-a", "entry-a"), protocol.import_identity(4, "account-b", "entry-a"))
        self.assertNotEqual(protocol.import_identity(4, "account-a", "entry-a"), protocol.import_identity(5, "account-a", "entry-a"))

    def test_account_identification_preserves_all_provider_hash_aliases(self):
        value = protocol.account_identity({"uid": "session-account", "identification_hash": "hash-a", "identification_hashes": ["hash-a", "hash-b"]})
        self.assertEqual(value, ("session-account", ("hash-a", "hash-b")))

    def test_pagination_follows_every_page_and_refuses_repeated_cursors(self):
        calls = []
        def get(cursor):
            calls.append(cursor)
            return {"transactions": [movement("b" if cursor else "a")], "continuation_key": None if cursor else "page-2"}
        self.assertEqual(len(protocol.collect_transaction_pages(get)), 2)
        self.assertEqual(calls, [None, "page-2"])
        with self.assertRaisesRegex(protocol.ProviderDataError, "repeated_cursor"):
            protocol.collect_transaction_pages(lambda cursor: {"transactions": [], "continuation_key": "same-page"})

    def test_later_page_failure_exposes_no_partial_transaction_result(self):
        def get(cursor):
            if cursor:
                raise protocol.ProviderDataError("provider_unavailable")
            return {"transactions": [movement()], "continuation_key": "next"}
        with self.assertRaisesRegex(protocol.ProviderDataError, "provider_unavailable"):
            protocol.collect_transaction_pages(get)


if __name__ == "__main__":
    unittest.main()
