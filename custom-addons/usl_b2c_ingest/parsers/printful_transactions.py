"""Printful's own account of the wallet it is paid from.

Printful bills nothing.  It draws on a prepaid wallet, and the only place the
wallet's movements are stated is this export — the API this addon holds a token
for reads orders and nothing else.  Without it the wallet's balance in the
ledger is a number derived from fulfilments and answerable to nothing; with it,
it is a fact that can be disagreed with.

Three things move the wallet, and they are not the same kind of thing:

- an order draws on it, which is the cost of goods and is already billed from
  the fulfilment events;
- a deposit tops it up from a card, and is what meets the bank;
- a subscription is charged to the card directly and never touches the wallet
  at all, which is why it is read and then left alone.
"""

import re
from decimal import Decimal

from .common import CHARGE_GRAIN, ParsedRow, money, parsed_datetime, text

PROVIDER = "printful"

#: Printful writes the same five columns whichever period is asked for.
TRANSACTIONS_REQUIRED = ("Payment", "Status", "Amount", "Date", "ID")

#: What each transaction is.  The word is not stated in a column of its own:
#: Printful puts it at the front of the payment's description.
ENTRY_KINDS = (
    (re.compile(r"^deposit to wallet", re.IGNORECASE), "top_up"),
    (re.compile(r"^subscription payment", re.IGNORECASE), "subscription"),
    (re.compile(r"^refund\b", re.IGNORECASE), "supply_refund"),
    (re.compile(r"^order\b", re.IGNORECASE), "supply"),
)

#: The kinds that move the wallet, and by which sign.  A subscription is paid
#: by card and never reaches it.
WALLET_SIGN = {"top_up": Decimal("1"), "supply": Decimal("-1"), "supply_refund": Decimal("1")}

#: The kind that has to meet a line on the bank statement.
TRANSFER_KINDS = ("top_up",)

_ORDER_IN_TEXT = re.compile(r"#(\S+?)\s+wallet", re.IGNORECASE)


def parse_transactions(document):
    """Yield one row per wallet transaction.

    A transaction that did not complete moved no money.  It is read all the
    same, because a failed top-up is a thing an operator looks for when the
    wallet is lower than it should be, and it is what makes the difference
    between a missing export and a payment that did not go through.
    """
    for row in document.rows:
        description = text(row["Payment"])
        kind = _kind(description)
        status = text(row["Status"])
        amount = money(row["Amount"], default=Decimal("0"))
        settled = status.casefold() == "completed"
        yield ParsedRow(
            format_id="printful_transactions",
            provider=PROVIDER,
            grain=CHARGE_GRAIN,
            # Printful's own transaction identifier, unique across every export.
            external_order_id=text(row["ID"]),
            row_number=row["_row_number"],
            occurred_at=parsed_datetime(row["Date"]),
            payload=row,
            values={
                "entry_kind": kind,
                "stated_kind": description,
                "status": status,
                "settled": settled,
                "currency": _currency(row["Amount"]),
                "gross_amount": amount,
                "wallet_amount": (
                    WALLET_SIGN.get(kind, Decimal("0")) * amount if settled else Decimal("0")
                ),
                "description": description,
                "names_order_id": _named_order(description),
            },
        )


def _kind(description):
    for pattern, kind in ENTRY_KINDS:
        if pattern.search(description):
            return kind
    return "other"


def _named_order(description):
    """Return the order a draw answers for, when it names one."""
    found = _ORDER_IN_TEXT.search(description)
    return found.group(1) if found else ""


#: Printful states the currency as the symbol in front of the amount, and
#: nowhere else.
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}


def _currency(amount):
    for symbol, name in _SYMBOLS.items():
        if symbol in (amount or ""):
            return name
    return ""
