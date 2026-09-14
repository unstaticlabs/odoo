"""Fabricated exports in every shape a channel sends.

Every value here is invented.  Real orders, customers and prices belong to the
source package, never to this repository, so the fixtures are built from the
header constants themselves: a column the parser renames breaks the fixture at
the same moment it breaks the parser.
"""

import csv
import io

from odoo.addons.usl_b2c_ingest.parsers import (
    etsy,
    medusa,
    printful_transactions as printful_transactions_module,
    stripe,
)

BOM = "﻿"


def _csv(header, rows, delimiter=","):
    """Return the bytes of a CSV whose columns are given by name."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(header), delimiter=delimiter)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in header})
    return (BOM + buffer.getvalue()).encode()


def etsy_orders(rows=None):
    return _csv(etsy.ORDERS_HEADER, rows if rows is not None else ETSY_ORDER_ROWS)


def etsy_order_items(rows=None):
    return _csv(etsy.ITEMS_HEADER, rows if rows is not None else ETSY_ITEM_ROWS)


def medusa_orders(rows=None):
    return _csv(
        medusa.ORDERS_HEADER,
        rows if rows is not None else MEDUSA_WIDE_ROWS,
        delimiter=medusa.DELIMITER,
    )


def medusa_full_orders(rows=None):
    return _csv(
        medusa.FULL_ORDERS_HEADER,
        rows if rows is not None else MEDUSA_FULL_ROWS,
        delimiter=medusa.DELIMITER,
    )


def medusa_order_items(rows=None):
    return _csv(
        medusa.ITEMS_HEADER,
        rows if rows is not None else MEDUSA_ITEM_ROWS,
        delimiter=medusa.DELIMITER,
    )


def etsy_statement(rows=None):
    return _csv(etsy.STATEMENT_REQUIRED, rows if rows is not None else ETSY_STATEMENT_ROWS)


def stripe_balance_history(rows=None, *, metadata=True):
    """Return a Stripe balance history, with the metadata column an account adds.

    Stripe writes one column per metadata key the account uses, so an export
    that carries none and one that carries several are the same export. The
    fixture states one by default, because that is what a real account sends.
    """
    header = (*stripe.BALANCE_REQUIRED, "Description", "Transfer Date (UTC)")
    if metadata:
        header = (*header, "session_id (metadata)")
    return _csv(header, rows if rows is not None else STRIPE_BALANCE_ROWS)


def printful_transactions(rows=None):
    return _csv(
        printful_transactions_module.TRANSACTIONS_REQUIRED,
        rows if rows is not None else PRINTFUL_TRANSACTION_ROWS,
    )


#: What Etsy kept, paid out and collected on the shop's behalf, in the shape its
#: payment account states it: a deduction is negative, and a deposit states its
#: figure only in the sentence naming it.
ETSY_STATEMENT_ROWS = (
    {
        "Date": "March 5, 2026",
        "Type": "Sale",
        "Title": "Payment for Order #9000000001",
        "Info": "",
        "Currency": "EUR",
        "Amount": "€120.00",
        "Fees & Taxes": "--",
        "Net": "€120.00",
    },
    {
        "Date": "March 5, 2026",
        "Type": "Fee",
        "Title": "Processing fee",
        "Info": "Order #9000000001",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€5.00",
        "Net": "-€5.00",
    },
    {
        "Date": "March 5, 2026",
        "Type": "Fee",
        "Title": "Transaction fee: Jersey",
        "Info": "Order #9000000001",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€7.80",
        "Net": "-€7.80",
    },
    # Etsy writes hundreds of these, identical in every column. Each one is a
    # separate twenty cents and none of them may collapse into another.
    {
        "Date": "March 5, 2026",
        "Type": "Fee",
        "Title": "Listing fee ($0.20 USD)",
        "Info": "",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€0.20",
        "Net": "-€0.20",
    },
    {
        "Date": "March 5, 2026",
        "Type": "Fee",
        "Title": "Listing fee ($0.20 USD)",
        "Info": "",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€0.20",
        "Net": "-€0.20",
    },
    {
        "Date": "March 6, 2026",
        "Type": "Marketing",
        "Title": "Fee for sale made through Offsite Ads",
        "Info": "Order #9000000001",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€2.00",
        "Net": "-€2.00",
    },
    # A tax the marketplace collected is not a fee, and is never bought.
    {
        "Date": "March 6, 2026",
        "Type": "Tax",
        "Title": "Sales tax paid by buyer",
        "Info": "Order #9000000002",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "-€6.97",
        "Net": "-€6.97",
    },
    {
        "Date": "March 31, 2026",
        "Type": "Deposit",
        "Title": "€104.00 sent to your bank account",
        "Info": "",
        "Currency": "EUR",
        "Amount": "--",
        "Fees & Taxes": "--",
        "Net": "--",
    },
)

#: What Stripe kept and paid out, in the shape its balance history states it: a
#: payment's cost as a fee against a positive amount, and Stripe's own charge as
#: a negative amount with no fee at all.
STRIPE_BALANCE_ROWS = (
    {
        "id": "txn_9000000001",
        "Type": "charge",
        "Source": "ch_9000000001",
        "Amount": "60,00",
        "Fee": "1,04",
        "Net": "58,96",
        "Currency": "eur",
        "Created (UTC)": "2026-03-05 10:00",
        "Available On (UTC)": "2026-03-10 00:00",
        "Transfer": "po_9000000001",
        "Transfer Date (UTC)": "2026-03-31 00:00",
        "session_id (metadata)": "payses_9000000001",
    },
    {
        "id": "txn_9000000002",
        "Type": "stripe_fee",
        "Source": "",
        "Amount": "-0,30",
        "Fee": "0,00",
        "Net": "-0,30",
        "Currency": "eur",
        "Created (UTC)": "2026-03-20 09:00",
        "Available On (UTC)": "2026-03-20 09:00",
        "Transfer": "",
        "Description": "Billing",
        "session_id (metadata)": "",
    },
    {
        "id": "txn_9000000003",
        "Type": "payout",
        "Source": "po_9000000001",
        "Amount": "-58,66",
        "Fee": "0,00",
        "Net": "-58,66",
        "Currency": "eur",
        "Created (UTC)": "2026-03-31 00:30",
        "Available On (UTC)": "2026-03-31 00:30",
        "Transfer": "po_9000000001",
        "Transfer Date (UTC)": "2026-03-31 00:00",
        "Description": "STRIPE PAYOUT",
    },
)

#: What moved the supplier's wallet, in the shape Printful states it: the kind
#: of movement is the front of the payment's description and nowhere else.
PRINTFUL_TRANSACTION_ROWS = (
    {
        "Payment": "Deposit to Wallet 539044********1921",
        "Status": "Completed",
        "Amount": "€250.00",
        "Date": "2026-03-02T09:00",
        "ID": "900000001",
    },
    {
        "Payment": "Order #9000000001 wallet",
        "Status": "Completed",
        "Amount": "€20.00",
        "Date": "2026-03-05T12:00",
        "ID": "900000002",
    },
    # Charged to the card, never to the wallet.
    {
        "Payment": "Subscription payment 539044********1921",
        "Status": "Completed",
        "Amount": "€24.99",
        "Date": "2026-03-15T08:00",
        "ID": "900000003",
    },
    # A top-up that did not go through moved nothing.
    {
        "Payment": "Deposit to Wallet 539044********1921",
        "Status": "Failed",
        "Amount": "€100.00",
        "Date": "2026-03-16T08:00",
        "ID": "900000004",
    },
)


#: One EU destination where the shop owes the tax, and one where the
#: marketplace collects a tax of its own on top of what the shop charged.
ETSY_ORDER_ROWS = (
    {
        "Sale Date": "03/04/26",
        "Order ID": "9000000001",
        "Buyer User ID": "buyer-one",
        "Full Name": "Wilhelmina Fabricant",
        "Number of Items": "1",
        "Date Shipped": "03/06/26",
        "Street 1": "1 Invented Way",
        "Ship City": "Bremen",
        "Ship Zipcode": "28195",
        "Ship Country": "Germany",
        "Currency": "EUR",
        "Order Value": "40.00",
        "Discount Amount": "0.00",
        "Shipping Discount": "0.00",
        "Shipping": "5.00",
        "Sales Tax": "0",
        "Order Total": "45.00",
        "Card Processing Fees": "2.00",
        "Order Net": "43.00",
        "Adjusted Order Total": "0.00",
        "Adjusted Card Processing Fees": "0.00",
        "Adjusted Net Order Amount": "0.00",
        "Status": "Completed",
    },
    {
        "Sale Date": "03/09/26",
        "Order ID": "9000000002",
        "Full Name": "Cormac O&#39;Placeholder",
        "Number of Items": "2",
        "Date Shipped": "03/11/26",
        "Street 1": "2 Imaginary Street",
        "Ship City": "Springfield",
        "Ship State": "IL",
        "Ship Zipcode": "62704",
        "Ship Country": "United States",
        "Currency": "EUR",
        "Order Value": "70.00",
        "Coupon Code": "FICTION10",
        "Discount Amount": "7.00",
        "Shipping Discount": "0.00",
        "Shipping": "6.00",
        "Sales Tax": "0",
        # The buyer paid 4.00 of tax the marketplace collects and keeps.
        "Order Total": "73.00",
        "Card Processing Fees": "3.00",
        "Order Net": "66.00",
        "Adjusted Order Total": "0.00",
        "Adjusted Card Processing Fees": "0.00",
        "Adjusted Net Order Amount": "0.00",
        "Status": "Completed",
    },
)

ETSY_ITEM_ROWS = (
    {
        "Sale Date": "03/04/26",
        "Item Name": "Invented Jersey",
        "Quantity": "1",
        "Price": "40.00",
        "Discount Amount": "0.00",
        "Shipping Discount": "0.00",
        "Order Shipping": "5.00",
        "Order Sales Tax": "0",
        "Item Total": "40",
        "Currency": "EUR",
        "Transaction ID": "7000000001",
        "Listing ID": "6000000001",
        "Date Paid": "03/04/2026",
        "Date Shipped": "03/06/2026",
        "Ship Name": "Wilhelmina Fabricant",
        "Ship Address1": "1 Invented Way",
        "Ship City": "Bremen",
        "Ship Zipcode": "28195",
        "Ship Country": "Germany",
        "Order ID": "9000000001",
        "Variations": "Color:Black,Size:M",
        "VAT Paid by Buyer": "0",
        "SKU": "FICTION-JERSEY-BLACK-M",
    },
    {
        "Sale Date": "03/09/26",
        "Item Name": "Invented Cap",
        "Quantity": "2",
        "Price": "35.00",
        "Coupon Code": "FICTION10",
        "Discount Amount": "7.00",
        "Shipping Discount": "0.00",
        "Order Shipping": "6.00",
        "Order Sales Tax": "0",
        "Item Total": "70",
        "Currency": "EUR",
        "Transaction ID": "7000000002",
        "Listing ID": "6000000002",
        "Date Paid": "03/09/2026",
        "Date Shipped": "03/11/2026",
        "Ship Name": "Cormac O&#39;Placeholder",
        "Ship Address1": "2 Imaginary Street",
        "Ship City": "Springfield",
        "Ship Country": "United States",
        "Order ID": "9000000002",
        "Variations": "Color:White",
        "VAT Paid by Buyer": "0",
        "SKU": "FICTION-CAP-WHITE",
    },
)

#: One order with two different lines, and one that repeats the same line, so
#: the identity a layout gives an unnumbered line is exercised both ways.
_MEDUSA_ITEMS = {
    "8000000001": (
        {
            "sku": "FICTION-CHAIN-40",
            "product": "Invented Chain",
            "variant": "40 cm",
            "qty": "1",
            "unit_price": "55.00",
            "total": "55.00",
        },
        {
            "sku": "FICTION-LOCK",
            "product": "Invented Padlock",
            "variant": "Black",
            "qty": "1",
            "unit_price": "12.00",
            "total": "12.00",
        },
    ),
    "8000000002": (
        {
            "sku": "FICTION-CAP",
            "product": "Invented Cap",
            "variant": "White",
            "qty": "1",
            "unit_price": "30.00",
            "total": "30.00",
        },
        {
            "sku": "FICTION-CAP",
            "product": "Invented Cap",
            "variant": "White",
            "qty": "1",
            "unit_price": "30.00",
            "total": "30.00",
        },
    ),
}

_MEDUSA_HEADERS = {
    "8000000001": {
        "date": "2026-03-04",
        "order_status": "pending",
        "customer_email": "one@example.invalid",
        "currency": "EUR",
    },
    "8000000002": {
        "date": "2026-03-09",
        "order_status": "pending",
        "customer_email": "two@example.invalid",
        "currency": "GBP",
    },
}

MEDUSA_WIDE_ROWS = tuple(
    {"order_number": order_number}
    | _MEDUSA_HEADERS[order_number]
    | {
        f"item_{slot}_{column}": value
        for slot, item in enumerate(items, start=1)
        for column, value in item.items()
    }
    for order_number, items in _MEDUSA_ITEMS.items()
)

MEDUSA_ITEM_ROWS = tuple(
    {"order_number": order_number} | _MEDUSA_HEADERS[order_number] | item
    for order_number, items in _MEDUSA_ITEMS.items()
    for item in items
)

#: The export that states where the goods went and what was charged on top.
MEDUSA_FULL_ROWS = (
    {
        "Order_ID": "order_invented_one",
        "Display_ID": "8000000001",
        "Order status": "completed",
        "Date": "2026-03-04",
        "Customer First name": "Ingrid",
        "Customer Last name": "Notreal",
        "Customer Email": "one@example.invalid",
        "Customer ID": "cus_invented_one",
        "Shipping Address 1": "3 Fictional Quay",
        "Shipping Country Code": "DE",
        "Shipping City": "Rostock",
        "Shipping Postal Code": "18055",
        "Fulfillment Status": "fulfilled",
        "Payment Status": "captured",
        "Subtotal": "67.00",
        "Shipping Total": "8.00",
        "Discount Total": "0.00",
        "Tax Total": "0.00",
        "Total": "75.00",
        "Currency Code": "EUR",
    },
    {
        "Order_ID": "order_invented_two",
        "Display_ID": "8000000002",
        "Order status": "completed",
        "Date": "2026-03-09",
        "Customer First name": "Percival",
        "Customer Last name": "Madeup",
        "Customer Email": "two@example.invalid",
        "Customer ID": "cus_invented_two",
        "Shipping Address 1": "12 Notional Road",
        "Shipping Country Code": "US",
        "Shipping City": "Cheyenne",
        "Shipping Postal Code": "82001",
        "Fulfillment Status": "fulfilled",
        "Payment Status": "captured",
        "Subtotal": "60.00",
        "Shipping Total": "9.00",
        "Discount Total": "0.00",
        "Tax Total": "0.00",
        "Total": "69.00",
        "Currency Code": "GBP",
    },
)

#: One Printful fulfilment for the first invented Etsy order.
PRINTFUL_ORDERS = (
    {
        "id": 5000000001,
        "external_id": "9000000001",
        "store_id": 11111111,
        "status": "fulfilled",
        "created_at": "2026-03-05T09:00:00Z",
        "recipient": {
            "name": "Wilhelmina Fabricant",
            "address1": "1 Invented Way",
            "city": "Bremen",
            "zip": "28195",
            "country_code": "DE",
            "email": "one@example.invalid",
        },
        "costs": {
            "currency": "EUR",
            "subtotal": "16.00",
            "discount": "0.00",
            "shipping": "4.00",
            "tax": "0.00",
            "vat": "0.00",
            "total": "20.00",
        },
        "retail_costs": {
            "currency": "EUR",
            "subtotal": "40.00",
            "shipping": "5.00",
            "total": "45.00",
        },
        "order_items": [
            {
                "id": 4000000001,
                "external_id": "FICTION-JERSEY-BLACK-M",
                "sync_variant_id": 3000000001,
                "quantity": 1,
                "name": "Invented Jersey / Black / M",
                "price": "16.00",
                "retail_price": "40.00",
                "currency": "EUR",
                "retail_currency": "EUR",
            },
        ],
    },
)

#: The same order, refunded afterwards. Printful states a refund as its own
#: order record, so it is a second event against the same lines.
PRINTFUL_REFUNDED = (
    *PRINTFUL_ORDERS,
    {
        "id": 5000000009,
        "external_id": "9000000001",
        "store_id": 11111111,
        "status": "Refunded",
        "created_at": "2026-03-20T09:00:00Z",
        "recipient": {
            "name": "Wilhelmina Fabricant",
            "address1": "1 Invented Way",
            "city": "Bremen",
            "zip": "28195",
            "country_code": "DE",
            "email": "one@example.invalid",
        },
        "costs": {
            "currency": "EUR",
            "subtotal": "16.00",
            "discount": "0.00",
            "shipping": "4.00",
            "tax": "0.00",
            "vat": "0.00",
            "total": "20.00",
        },
        "order_items": [],
    },
)
