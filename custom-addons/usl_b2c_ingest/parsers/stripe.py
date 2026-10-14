"""Stripe's balance history: what it collected, what it kept, what it paid out.

Stripe is not a channel.  It is how one channel's customers pay, and it keeps a
part of every payment plus a few charges of its own.  Until that is bought, the
clearing account holding the channel's receipts cannot come back to nothing:
the payout is always smaller than the sales it settles, by exactly what Stripe
kept.

The balance history is read rather than the payments export because it is the
only one that states all three facts.  A payments export names the fee on each
charge but never Stripe's own account charges, and never a payout — so it can
say what a month cost but not prove it against the balance.
"""

from decimal import Decimal

from .common import CHARGE_GRAIN, ParsedRow, money, parsed_datetime, text

PROVIDER = "stripe"

#: Stripe appends one column per metadata key the account writes, so a balance
#: history is recognised by the columns Stripe itself always states rather than
#: by the whole set.
BALANCE_REQUIRED = (
    "id",
    "Type",
    "Source",
    "Amount",
    "Fee",
    "Net",
    "Currency",
    "Created (UTC)",
    "Available On (UTC)",
    "Transfer",
)

#: What each balance transaction is, in the vocabulary the ledger cares about.
#: Anything Stripe invents that is not here is read as an adjustment, which is
#: reported and never billed: a word nobody recognises is not a fee.
ENTRY_KINDS = {
    "charge": "charge",
    "payment": "charge",
    "refund": "refund",
    "payment_refund": "refund",
    "payout": "payout",
    "transfer": "payout",
    "stripe_fee": "account_fee",
    "application_fee": "account_fee",
    "adjustment": "adjustment",
    "dispute": "adjustment",
}

#: The kinds whose money Stripe kept, and which therefore have to be bought.
FEE_KINDS = ("charge", "refund", "account_fee")


def parse_balance_history(document):
    """Yield one row per balance transaction.

    Stripe states its own account charges as a negative amount and no fee,
    while it states a payment's cost as a fee against a positive amount.  Both
    are money Stripe kept, so both are read into the same field and the
    difference between them stays visible in the kind.
    """
    for row in document.rows:
        kind = ENTRY_KINDS.get(text(row["Type"]).lower(), "adjustment")
        gross = money(row["Amount"], default=Decimal("0"))
        fee = money(row["Fee"], default=Decimal("0"))
        if kind == "account_fee":
            fee += -gross
        occurred_at = parsed_datetime(row["Created (UTC)"])
        yield ParsedRow(
            format_id="stripe_balance_history",
            provider=PROVIDER,
            grain=CHARGE_GRAIN,
            # A balance transaction id is Stripe's own, and unique across every
            # export it will ever write.
            external_order_id=text(row["id"]),
            row_number=row["_row_number"],
            occurred_at=occurred_at,
            payload=row,
            values={
                "entry_kind": kind,
                "stated_kind": text(row["Type"]),
                "currency": text(row["Currency"]).upper(),
                "gross_amount": gross,
                "fee_amount": fee,
                "net_amount": money(row["Net"], default=Decimal("0")),
                "source_reference": text(row["Source"]),
                "payout_reference": text(row["Transfer"]),
                "available_on": parsed_datetime(row["Available On (UTC)"]),
                "description": text(row.get("Description", "")),
            },
        )
