"""Fabricated exports in every shape a channel sends.

Every value here is invented.  Real orders, customers and prices belong to the
source package, never to this repository, so the fixtures are built from the
header constants themselves: a column the parser renames breaks the fixture at
the same moment it breaks the parser.
"""

import csv
import io

from odoo.addons.usl_b2c_ingest.parsers import etsy, medusa

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
