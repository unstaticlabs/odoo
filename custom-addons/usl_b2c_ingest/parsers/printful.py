"""Printful fulfilment records, as returned by the v2 orders API.

Printful is the supplier, not a sales channel: its records carry what
fulfilment cost, and its ``external_id`` is the marketplace order the cost
belongs to.  Which storefront an order came from is configuration rather than
code, so the store-to-channel mapping is supplied by the caller from the
configured B2C channels.
"""

from decimal import Decimal

from .common import (
    LINE_GRAIN,
    ORDER_GRAIN,
    ParsedRow,
    money,
    parsed_datetime,
    reference,
    text,
)

PROVIDER = "printful"

#: Printful reports every cost in the store's settlement currency.
COST_FIELDS = (
    "subtotal",
    "discount",
    "shipping",
    "digitization",
    "additional_fee",
    "fulfillment_fee",
    "retail_delivery_fee",
    "tax",
    "vat",
    "total",
)


def _costs(payload, key):
    costs = payload.get(key) or {}
    return {
        f"{key}_{field}": money(str(costs.get(field, "")), default=Decimal("0"))
        for field in COST_FIELDS
        if field in costs
    } | {f"{key}_currency": text(costs.get("currency"))}


def parse_orders(payload, store_channels):
    """Yield fulfilment rows for a page of Printful orders.

    ``store_channels`` maps a Printful store id to the ``(provider, purpose)``
    it fulfils for.  An unmapped store yields rows whose provider is Printful
    itself, so the batch reports it rather than guessing a channel.
    """
    for order in payload:
        store_id = str(order.get("store_id") or "")
        provider, purpose = store_channels.get(store_id, (PROVIDER, ""))
        external_order_id = reference(str(order.get("external_id") or ""))
        printful_id = reference(str(order.get("id") or ""))
        occurred_at = parsed_datetime(order.get("created_at"))
        base = {
            "printful_order_id": printful_id,
            "printful_store_id": store_id,
            "business_purpose": purpose,
            "original_provider_state": text(order.get("status")),
        }
        yield ParsedRow(
            format_id="printful_orders",
            provider=provider,
            grain=ORDER_GRAIN,
            external_order_id=external_order_id or printful_id,
            row_number=0,
            payload=order,
            occurred_at=occurred_at,
            values=base
            | _costs(order, "costs")
            | _costs(order, "retail_costs")
            | _recipient(order.get("recipient") or {}),
        )
        for item in order.get("order_items") or ():
            yield ParsedRow(
                format_id="printful_orders",
                provider=provider,
                grain=LINE_GRAIN,
                external_order_id=external_order_id or printful_id,
                external_line_id=reference(str(item.get("id") or "")),
                row_number=0,
                payload=item,
                occurred_at=occurred_at,
                values=base
                | {
                    "original_name": text(item.get("name")),
                    "original_sku": text(item.get("external_id")),
                    "printful_variant_id": reference(str(item.get("sync_variant_id") or "")),
                    "quantity": Decimal(str(item.get("quantity") or 0)),
                    "supplier_unit_cost": money(str(item.get("price", "")), default=Decimal("0")),
                    "unit_price": money(str(item.get("retail_price", "")), default=Decimal("0")),
                    "currency": text(item.get("currency")) or "EUR",
                },
            )


def _recipient(recipient):
    return {
        "shipping_name": text(recipient.get("name")),
        "shipping_street": text(recipient.get("address1")),
        "shipping_street2": text(recipient.get("address2")),
        "shipping_city": text(recipient.get("city")),
        "shipping_state": text(recipient.get("state_code")),
        "shipping_zip": text(recipient.get("zip")),
        "original_country": text(recipient.get("country_code")),
        "customer_email": text(recipient.get("email")),
    }
