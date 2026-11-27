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
    SchemaError,
    digest,
    money,
    parsed_datetime,
    quantity,
    reference,
    text,
)

from .medusa_exchange import IDENTIFIED_ITEMS_HEADER

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


#: What the wide layout must state to be that export at all: the order, and a
#: first item to hang on it.  Medusa widens the layout as orders grow — a file
#: written when the largest order held six items states six slots, and one
#: written after a seventh was sold states seven — so demanding an exact width
#: refuses next month's export of the same thing.
ORDERS_REQUIRED = _HEADER_COLUMNS + tuple(f"item_1_{column}" for column in _ITEM_COLUMNS)

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
                "currency": text(row["Currency Code"]).upper() or "EUR",
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
                # Medusa states its subtotal before tax, where every other
                # export states what the customer paid. Publishing it under the
                # same name would price a sale at its own total less the VAT,
                # so the lines are left to say what the goods came to and only
                # the check that the export adds up is kept.
                "shipping_amount": shipping,
                "discount_amount": discount,
                "tax_amount": tax,
                "total_amount": total,
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
    return {
        "currency": currency,
        "original_sku": item["sku"],
        "original_name": item["product"],
        "original_variation": item["variant"],
        "quantity": item["quantity"],
        "unit_price": item["unit_price"],
        "subtotal_amount": item["total"],
    }


def _header_values(row):
    return {
        "currency": text(row["currency"]).upper() or "EUR",
        "original_provider_state": text(row["order_status"]),
        "customer_email": text(row["customer_email"]),
    }


def _read_item(row, prefix=""):
    sku = text(row.get(f"{prefix}sku"))
    product = text(row.get(f"{prefix}product"))
    if not sku and not product:
        return None
    qty = quantity(row.get(f"{prefix}qty"))
    unit_price = money(row.get(f"{prefix}unit_price"), default=Decimal("0"))
    stated_total = money(row.get(f"{prefix}total"))
    return {
        "sku": sku,
        "product": product,
        "variant": text(row.get(f"{prefix}variant")),
        "quantity": qty,
        "unit_price": unit_price,
        "total": stated_total if stated_total is not None else unit_price * qty,
    }


def _item_slots(row):
    """Return how many item slots this export states.

    Counting what the file declares reads every item of a six-slot export and
    of a seven-slot one; a fixed count reads the wider file and silently drops
    its last item, which is worse than refusing it.
    """
    slot = 1
    while f"item_{slot}_sku" in row:
        slot += 1
    return slot - 1


def parse_orders(document):
    """Yield the order and its lines from the wide, one-row-per-order layout."""
    for row in document.rows:
        order_id = reference(row["order_number"])
        occurred_at = parsed_datetime(row["date"])
        payload = _payload(row)
        currency = text(row["currency"]).upper() or "EUR"
        items = [
            item
            for item in (
                _read_item(row, prefix=f"item_{slot}_")
                for slot in range(1, _item_slots(row) + 1)
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
                "subtotal_amount": sum((item["total"] for item in items), Decimal("0")),
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


def parse_order_items(document, *, identified=False):
    """Yield the lines, and one order header per order, from the item layout."""
    seen_by_order = {}
    announced = set()
    if identified:
        line_ids = set()
        native_orders = {}
        display_orders = {}
        order_facts = {}
        for row in document.rows:
            native = reference(row["order_id"])
            display = reference(row["order_number"]) or native
            line = reference(row["line_id"])
            if not native or not line or (native, line) in line_ids:
                raise SchemaError("Each Medusa item must state a nonempty order_id and unique line_id.")
            if native_orders.setdefault(native, display) != display or display_orders.setdefault(display, native) != native:
                raise SchemaError("Medusa order_id and order_number disagree within this export.")
            facts = tuple(text(row[name]) for name in ("date", "currency", "order_status", "payment_status", "fulfillment_status"))
            if order_facts.setdefault(native, facts) != facts:
                raise SchemaError("Medusa rows disagree about one order's date, currency or source states.")
            line_ids.add((native, line))
    for row in document.rows:
        order_id = reference(row["order_number"]) or (reference(row["order_id"]) if identified else "")
        occurred_at = parsed_datetime(row["date"])
        payload = _payload(row)
        currency = text(row["currency"]).upper() or "EUR"
        header_values = _header_values(row) if not identified else {
            "currency": currency,
            "internal_order_id": reference(row["order_id"]),
            "original_provider_state": text(row["order_status"]),
            "source_payment_state": text(row["payment_status"]),
            "source_fulfilment_state": text(row["fulfillment_status"]),
        }
        format_id = "medusa_identified_items" if identified else "medusa_order_items"
        if order_id not in announced:
            announced.add(order_id)
            yield ParsedRow(
                format_id=format_id,
                provider=PROVIDER,
                grain=ORDER_GRAIN,
                external_order_id=order_id,
                row_number=row["_row_number"],
                payload=payload,
                occurred_at=occurred_at,
                values=header_values,
            )
        item = _read_item(row)
        if item is None:
            if identified:
                raise SchemaError("A native Medusa line must state a SKU or product.")
            continue
        seen = seen_by_order.setdefault(order_id, Counter())
        line_id = reference(row["line_id"]) if identified else _line_identity(order_id, item, seen)
        values = _item_values(item, currency)
        if identified:
            values["internal_order_id"] = reference(row["order_id"])
        yield ParsedRow(
            format_id=format_id,
            provider=PROVIDER,
            grain=LINE_GRAIN,
            external_order_id=order_id,
            external_line_id=line_id,
            row_number=row["_row_number"],
            payload=payload,
            occurred_at=occurred_at,
            values=values,
        )


def parse_identified_order_items(document):
    """Read explicit native IDs without treating product content as identity."""
    return parse_order_items(document, identified=True)


def _payload(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}
