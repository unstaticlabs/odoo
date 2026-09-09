"""Etsy shop exports.

Etsy emails two files for the same period.  The order file is authoritative for
the money Etsy charged and kept; the item file is authoritative for what was
bought.  Neither is sufficient alone, so both are parsed into the same
canonical rows and reconciled by order identity.
"""

import re
from collections import Counter
from decimal import Decimal

from .common import (
    CHARGE_GRAIN,
    LINE_GRAIN,
    ORDER_GRAIN,
    ParsedRow,
    digest,
    money,
    parsed_datetime,
    quantity,
    reference,
    text,
)

PROVIDER = "etsy"

ORDERS_HEADER = (
    "Sale Date",
    "Order ID",
    "Buyer User ID",
    "Full Name",
    "First Name",
    "Last Name",
    "Number of Items",
    "Payment Method",
    "Date Shipped",
    "Street 1",
    "Street 2",
    "Ship City",
    "Ship State",
    "Ship Zipcode",
    "Ship Country",
    "Currency",
    "Order Value",
    "Coupon Code",
    "Coupon Details",
    "Discount Amount",
    "Shipping Discount",
    "Shipping",
    "Sales Tax",
    "Order Total",
    "Status",
    "Card Processing Fees",
    "Order Net",
    "Adjusted Order Total",
    "Adjusted Card Processing Fees",
    "Adjusted Net Order Amount",
    "Buyer",
    "Order Type",
    "Payment Type",
    "InPerson Discount",
    "InPerson Location",
    "SKU",
)

ITEMS_HEADER = (
    "Sale Date",
    "Item Name",
    "Buyer",
    "Quantity",
    "Price",
    "Coupon Code",
    "Coupon Details",
    "Discount Amount",
    "Shipping Discount",
    "Order Shipping",
    "Order Sales Tax",
    "Item Total",
    "Currency",
    "Transaction ID",
    "Listing ID",
    "Date Paid",
    "Date Shipped",
    "Ship Name",
    "Ship Address1",
    "Ship Address2",
    "Ship City",
    "Ship State",
    "Ship Zipcode",
    "Ship Country",
    "Order ID",
    "Variations",
    "Order Type",
    "Listings Type",
    "Payment Type",
    "InPerson Discount",
    "InPerson Location",
    "VAT Paid by Buyer",
    "SKU",
)


def _address(row, street1, street2):
    return {
        "shipping_name": text(row.get("Ship Name") or row.get("Full Name")),
        "shipping_street": text(row.get(street1)),
        "shipping_street2": text(row.get(street2)),
        "shipping_city": text(row.get("Ship City")),
        "shipping_state": text(row.get("Ship State")),
        "shipping_zip": text(row.get("Ship Zipcode")),
        "original_country": text(row.get("Ship Country")),
    }


def parse_orders(document):
    """Yield one canonical order row per Etsy sale.

    Two identities hold over every Etsy order file observed, and both are
    measured rather than assumed so a format change surfaces as an issue
    instead of a silent misstatement:

    ``Order Net = Order Value - Discount Amount + Shipping - Card Processing
    Fees`` is the money Etsy actually owes the shop.

    ``Order Total`` is what the buyer paid, and exceeds the shop's own gross by
    the tax Etsy collects and remits itself on US, UK and Australian
    destinations.  That excess never reaches the shop and is not revenue.
    """
    for row in document.rows:
        gross = money(row["Order Value"], default=Decimal("0"))
        discount = money(row["Discount Amount"], default=Decimal("0"))
        shipping_discount = money(row["Shipping Discount"], default=Decimal("0"))
        shipping = money(row["Shipping"], default=Decimal("0"))
        seller_tax = money(row["Sales Tax"], default=Decimal("0"))
        buyer_paid = money(row["Order Total"], default=Decimal("0"))
        fee = money(row["Card Processing Fees"], default=Decimal("0"))
        net = money(row["Order Net"], default=Decimal("0"))
        recognised = gross - discount - shipping_discount + shipping + seller_tax
        yield ParsedRow(
            format_id="etsy_orders",
            provider=PROVIDER,
            grain=ORDER_GRAIN,
            external_order_id=reference(row["Order ID"]),
            row_number=row["_row_number"],
            payload=_payload(row),
            occurred_at=parsed_datetime(row["Sale Date"]),
            values={
                "currency": text(row["Currency"]).upper() or "EUR",
                "subtotal_amount": gross,
                "discount_amount": discount + shipping_discount,
                "shipping_amount": shipping,
                "tax_amount": seller_tax,
                "fee_amount": fee,
                "total_amount": recognised,
                "net_amount": net,
                "marketplace_collected_tax": buyer_paid - recognised,
                "buyer_paid_amount": buyer_paid,
                "net_identity_residual": net - (recognised - fee),
                "adjusted_total_amount": money(row["Adjusted Order Total"], default=Decimal("0")),
                "adjusted_fee_amount": money(
                    row["Adjusted Card Processing Fees"], default=Decimal("0"),
                ),
                "adjusted_net_amount": money(
                    row["Adjusted Net Order Amount"], default=Decimal("0"),
                ),
                "line_count": quantity(row["Number of Items"]),
                "coupon_code": text(row["Coupon Code"]),
                "original_provider_state": text(row["Status"]),
                "customer_external_id": text(row["Buyer User ID"]),
                "customer_name": text(row["Full Name"]),
                "fulfilment_date": parsed_datetime(row["Date Shipped"]),
                **_address(row, "Street 1", "Street 2"),
            },
        )


def parse_order_items(document):
    """Yield one canonical order line per Etsy transaction.

    Etsy repeats the order-level shipping, discount and tax on the *first* item
    of a multi-item order and zeroes them on the rest, so those columns are
    read as order context and never summed across lines.
    """
    for row in document.rows:
        order_id = reference(row["Order ID"])
        unit_price = money(row["Price"], default=Decimal("0"))
        qty = quantity(row["Quantity"])
        yield ParsedRow(
            format_id="etsy_order_items",
            provider=PROVIDER,
            grain=LINE_GRAIN,
            external_order_id=order_id,
            external_line_id=reference(row["Transaction ID"]),
            row_number=row["_row_number"],
            payload=_payload(row),
            occurred_at=parsed_datetime(row["Date Paid"]) or parsed_datetime(row["Sale Date"]),
            values={
                "currency": text(row["Currency"]).upper() or "EUR",
                "original_name": text(row["Item Name"]),
                "original_variation": text(row["Variations"]),
                "original_sku": text(row["SKU"]),
                "external_listing_id": reference(row["Listing ID"]),
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal_amount": money(row["Item Total"], default=unit_price * qty),
                "vat_paid_by_buyer": money(row["VAT Paid by Buyer"], default=Decimal("0")),
                "fulfilment_date": parsed_datetime(row["Date Shipped"]),
                "customer_name": text(row["Ship Name"]),
                # Order context, carried for cross-checking only.
                "order_shipping_amount": money(row["Order Shipping"], default=Decimal("0")),
                "order_discount_amount": money(row["Discount Amount"], default=Decimal("0")),
                "order_tax_amount": money(row["Order Sales Tax"], default=Decimal("0")),
                **_address(row, "Ship Address1", "Ship Address2"),
            },
        )


def _payload(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


#: Etsy's payment account statement, which is the only export naming what Etsy
#: kept.  The sold-orders export states the card processing fee and nothing
#: else — not the transaction fee, the listing fee, the regulatory fee or the
#: advertising — so an account built from orders alone can never empty.
#:
#: Matched on the columns Etsy always writes rather than the whole set, because
#: a statement carried between tools tends to arrive with a column naming which
#: month it came from.
STATEMENT_REQUIRED = (
    "Date",
    "Type",
    "Title",
    "Info",
    "Currency",
    "Amount",
    "Fees & Taxes",
    "Net",
)

#: What each statement entry is, in the vocabulary the ledger cares about.
#: Etsy's own word is kept alongside, so a word this does not know stays
#: readable in the evidence instead of being read as something it is not.
STATEMENT_KINDS = {
    "fee": "fee",
    "marketing": "fee",
    "tax": "marketplace_tax",
    "sale": "sale",
    "refund": "refund",
    "deposit": "payout",
    "payment": "bill_payment",
}

#: The kinds whose money Etsy kept, and which therefore have to be bought.
FEE_KINDS = ("fee",)

_ORDER_IN_TEXT = re.compile(r"#(\d{6,})")

#: Etsy leaves a deposit's Amount column empty and states the figure only in the
#: sentence it writes as the entry's title.  Reading it there is the only way to
#: know what it paid out; a change of wording stops matching and is reported,
#: rather than being read as a payout of nothing.
_DEPOSIT_AMOUNT = re.compile(r"([€£$]\s*[\d.,]+)\s+sent to your bank", re.IGNORECASE)


def parse_statement(document):
    """Yield one row per statement entry.

    A statement entry carries no identifier of its own, and Etsy writes many
    that are identical — two hundred and sixty-eight listing fees of the same
    twenty cents.  So identity is what the entry says plus how many identical
    ones came before it on the same day, which makes a month re-exported inside
    a wider file resolve to the entries already read rather than to new ones.
    """
    seen = Counter()
    for row in document.rows:
        stated = text(row["Type"])
        kind = STATEMENT_KINDS.get(stated.lower(), "other")
        occurred_at = parsed_datetime(row["Date"])
        gross = money(row["Amount"], default=Decimal("0"))
        if kind == "payout" and not gross:
            gross = -_deposit_amount(text(row["Title"]))
        # Etsy states what it kept as a negative, being a deduction from the
        # balance. It is bought as a positive cost, and a credit as a negative.
        kept = -money(row["Fees & Taxes"], default=Decimal("0"))
        identity = digest(
            [stated, text(row["Title"]), text(row["Info"]), text(row["Currency"]),
             str(gross), str(kept)],
        )[:12]
        seen[occurred_at.date(), identity] += 1
        yield ParsedRow(
            format_id="etsy_statement",
            provider=PROVIDER,
            grain=CHARGE_GRAIN,
            external_order_id=f"{occurred_at.date():%Y-%m-%d}",
            external_line_id=f"{identity}:{seen[occurred_at.date(), identity]}",
            row_number=row["_row_number"],
            occurred_at=occurred_at,
            payload=row,
            values={
                "entry_kind": kind,
                "stated_kind": stated,
                "currency": text(row["Currency"]).upper(),
                "gross_amount": gross,
                "fee_amount": kept,
                "net_amount": money(row["Net"], default=Decimal("0")),
                "description": text(row["Title"]),
                "names_order_id": _named_order(row),
            },
        )


def _deposit_amount(title):
    """Return what a deposit paid out, read from the sentence stating it."""
    found = _DEPOSIT_AMOUNT.search(title)
    return money(found.group(1), default=Decimal("0")) if found else Decimal("0")


def _named_order(row):
    """Return the order a statement entry answers for, when it names one."""
    for column in ("Info", "Title"):
        found = _ORDER_IN_TEXT.search(text(row[column]))
        if found:
            return found.group(1)
    return ""
