"""Medusa storefront exports.

Medusa emails the same sale in two layouts: one row per order with up to six
items spread across columns, and one row per item.  Both carry the identical
facts, and neither carries the order-level shipping, discount or tax, so the
order total has to come from the payment processor.  Either file alone is
enough to reconstruct the lines; both are accepted so an operator never has to
remember which one to keep.
"""

from collections import Counter
from decimal import Decimal

from .common import (
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

PROVIDER = "medusa"

#: Medusa spreads at most six items across the wide layout's columns.
WIDE_ITEM_SLOTS = 6

_HEADER_COLUMNS = (
    "order_number",
    "date",
    "order_status",
    "customer_email",
    "currency",
)
_ITEM_COLUMNS = ("sku", "product", "variant", "qty", "unit_price", "total")

ORDERS_HEADER = _HEADER_COLUMNS + tuple(
    f"item_{slot}_{column}"
    for slot in range(1, WIDE_ITEM_SLOTS + 1)
    for column in _ITEM_COLUMNS
)
ITEMS_HEADER = _HEADER_COLUMNS + _ITEM_COLUMNS

DELIMITER = ";"

#: Medusa's full order export, the only one of its exports that carries the
#: destination and the money charged on top of the items.
FULL_ORDERS_HEADER = (
    "Order_ID",
    "Display_ID",
    "Order status",
    "Date",
    "Customer First name",
    "Customer Last name",
    "Customer Email",
    "Customer ID",
    "Shipping Address 1",
    "Shipping Address 2",
    "Shipping Country Code",
    "Shipping City",
    "Shipping Postal Code",
    "Shipping Region ID",
    "Fulfillment Status",
    "Payment Status",
    "Subtotal",
    "Shipping Total",
    "Discount Total",
    "Tax Total",
    "Total",
    "Currency Code",
)


def parse_full_orders(document):
    """Yield the order a Medusa export states in full: destination and money.

    This is the export that makes a Medusa sale taxable: the item exports name
    neither the country the goods went to nor the carriage the customer paid,
    and without the country there is no rate to charge.
    """
    for row in document.rows:
        subtotal = money(row["Subtotal"], default=Decimal("0"))
        shipping = money(row["Shipping Total"], default=Decimal("0"))
        discount = money(row["Discount Total"], default=Decimal("0"))
        tax = money(row["Tax Total"], default=Decimal("0"))
        total = money(row["Total"], default=Decimal("0"))
        first = text(row["Customer First name"])
        last = text(row["Customer Last name"])
        yield ParsedRow(
            format_id="medusa_full_orders",
            provider=PROVIDER,
            grain=ORDER_GRAIN,
            external_order_id=reference(row["Display_ID"]) or reference(row["Order_ID"]),
            row_number=row["_row_number"],
            payload=_payload(row),
            occurred_at=parsed_datetime(row["Date"]),
            values={
                "currency": text(row["Currency Code"]) or "EUR",
                "internal_order_id": reference(row["Order_ID"]),
                "original_provider_state": text(row["Order status"]),
                "source_payment_state": text(row["Payment Status"]),
                "source_fulfilment_state": text(row["Fulfillment Status"]),
                "customer_external_id": text(row["Customer ID"]),
                "customer_email": text(row["Customer Email"]),
                "customer_name": " ".join(part for part in (first, last) if part),
                "shipping_name": " ".join(part for part in (first, last) if part),
                "shipping_street": text(row["Shipping Address 1"]),
                "shipping_street2": text(row["Shipping Address 2"]),
                "shipping_city": text(row["Shipping City"]),
                "shipping_zip": text(row["Shipping Postal Code"]),
                "original_country": text(row["Shipping Country Code"]),
                "subtotal_amount": subtotal,
                "shipping_amount": shipping,
                "discount_amount": discount,
                "tax_amount": tax,
                "total_amount": total,
                "line_amount_residual": total - (subtotal - discount + shipping + tax),
            },
        )


def _line_identity(order_id, item, seen):
    """Return a line identifier that both Medusa layouts agree on.

    Medusa numbers nothing, so identity has to come from the line's own
    content.  Two identical lines in one order are told apart by their
    occurrence, which is stable as long as the content is.
    """
    content = digest([item["sku"], item["product"], item["variant"], str(item["unit_price"])])[:12]
    seen[content] += 1
    occurrence = seen[content]
    return f"{content}#{occurrence}" if occurrence > 1 else content


def _item_values(item, currency):
    unit_price = item["unit_price"]
    qty = item["quantity"]
    return {
        "currency": currency,
        "original_sku": item["sku"],
        "original_name": item["product"],
        "original_variation": item["variant"],
        "quantity": qty,
        "unit_price": unit_price,
        "subtotal_amount": item["total"] if item["total"] is not None else unit_price * qty,
    }


def _header_values(row):
    return {
        "currency": text(row["currency"]) or "EUR",
        "original_provider_state": text(row["order_status"]),
        "customer_email": text(row["customer_email"]),
    }


def _read_item(row, prefix=""):
    sku = text(row.get(f"{prefix}sku"))
    product = text(row.get(f"{prefix}product"))
    if not sku and not product:
        return None
    return {
        "sku": sku,
        "product": product,
        "variant": text(row.get(f"{prefix}variant")),
        "quantity": quantity(row.get(f"{prefix}qty")),
        "unit_price": money(row.get(f"{prefix}unit_price"), default=Decimal("0")),
        "total": money(row.get(f"{prefix}total")),
    }


def parse_orders(document):
    """Yield the order and its lines from the wide, one-row-per-order layout."""
    for row in document.rows:
        order_id = reference(row["order_number"])
        occurred_at = parsed_datetime(row["date"])
        payload = _payload(row)
        currency = text(row["currency"]) or "EUR"
        items = [
            item
            for item in (
                _read_item(row, prefix=f"item_{slot}_")
                for slot in range(1, WIDE_ITEM_SLOTS + 1)
            )
            if item is not None
        ]
        yield ParsedRow(
            format_id="medusa_orders",
            provider=PROVIDER,
            grain=ORDER_GRAIN,
            external_order_id=order_id,
            row_number=row["_row_number"],
            payload=payload,
            occurred_at=occurred_at,
            values=_header_values(row)
            | {
                "subtotal_amount": sum(
                    (item["total"] or item["unit_price"] * item["quantity"] for item in items),
                    Decimal("0"),
                ),
                "line_count": Decimal(len(items)),
            },
        )
        seen = Counter()
        for item in items:
            yield ParsedRow(
                format_id="medusa_orders",
                provider=PROVIDER,
                grain=LINE_GRAIN,
                external_order_id=order_id,
                external_line_id=_line_identity(order_id, item, seen),
                row_number=row["_row_number"],
                payload=payload,
                occurred_at=occurred_at,
                values=_item_values(item, currency),
            )


def parse_order_items(document):
    """Yield the lines, and one order header per order, from the item layout."""
    seen_by_order = {}
    announced = set()
    for row in document.rows:
        order_id = reference(row["order_number"])
        occurred_at = parsed_datetime(row["date"])
        payload = _payload(row)
        currency = text(row["currency"]) or "EUR"
        if order_id not in announced:
            announced.add(order_id)
            yield ParsedRow(
                format_id="medusa_order_items",
                provider=PROVIDER,
                grain=ORDER_GRAIN,
                external_order_id=order_id,
                row_number=row["_row_number"],
                payload=payload,
                occurred_at=occurred_at,
                values=_header_values(row),
            )
        item = _read_item(row)
        if item is None:
            continue
        seen = seen_by_order.setdefault(order_id, Counter())
        yield ParsedRow(
            format_id="medusa_order_items",
            provider=PROVIDER,
            grain=LINE_GRAIN,
            external_order_id=order_id,
            external_line_id=_line_identity(order_id, item, seen),
            row_number=row["_row_number"],
            payload=payload,
            occurred_at=occurred_at,
            values=_item_values(item, currency),
        )


def _payload(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}
