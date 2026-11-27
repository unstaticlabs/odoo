"""Customer-free Medusa v2 response projection, also usable as a local CLI.

This converts one already authorized response; it performs no network request.
"""

import argparse
import csv
import io
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

IDENTIFIED_ITEMS_HEADER = (
    "order_id", "order_number", "line_id", "date", "order_status",
    "payment_status", "fulfillment_status", "currency",
    "sku", "product", "variant", "qty", "unit_price", "total",
)


def _required(record, field):
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or not str(value).strip():
        raise ValueError(f"Medusa response requires a scalar {field}.")
    return str(value).strip()


def _number(record, field):
    value = _required(record, field)
    try:
        valid = Decimal(value).is_finite()
    except InvalidOperation:
        valid = False
    if not valid:
        raise ValueError(f"Medusa response requires a finite {field}.")
    return value


def export_items(response):
    """Project one GET order response/page; amounts stay in Medusa v2 units.

    Item total is required and copied verbatim, including zero. It is never
    replaced with unit price times quantity, or presented as an order total.
    """
    if not isinstance(response, dict) or ("order" in response) == ("orders" in response):
        raise ValueError("Expected one Medusa order response or orders page.")
    orders = [response["order"]] if "order" in response else response["orders"]
    if not isinstance(orders, list):
        raise ValueError("Medusa orders must be a list.")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=IDENTIFIED_ITEMS_HEADER, delimiter=";")
    writer.writeheader()
    for order in orders:
        if not isinstance(order, dict) or not isinstance(order.get("items"), list) or not order["items"]:
            raise ValueError("Each Medusa order must contain its selected items.")
        header = dict(order_id=_required(order, "id"), order_number=_required(order, "display_id"),
            date=_required(order, "created_at"), order_status=_required(order, "status"),
            payment_status=_required(order, "payment_status"), fulfillment_status=_required(order, "fulfillment_status"),
            currency=_required(order, "currency_code"))
        for item in order["items"]:
            if not isinstance(item, dict):
                raise ValueError("Each Medusa item must be an object.")
            writer.writerow(header | dict(line_id=_required(item, "id"),
                sku=item.get("variant_sku") or "", product=item.get("product_title") or _required(item, "title"),
                variant=item.get("variant_title") or "", qty=_number(item, "quantity"),
                unit_price=_number(item, "unit_price"), total=_number(item, "total")))
    return buffer.getvalue().encode("utf-8-sig")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", type=Path, help="Private JSON from an authorized Medusa GET order response/page")
    parser.add_argument("--output", required=True, type=Path, help="New private CSV for an ordinary B2C import batch")
    args = parser.parse_args()
    with args.response.open() as source:
        content = export_items(json.load(source, parse_float=Decimal))
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(content)


if __name__ == "__main__":
    main()
