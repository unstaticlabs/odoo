import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

try:
    from pypdf import PdfReader
except ImportError:
    from PyPDF2 import PdfReader

ETSY_STATEMENT_HEADER = (
    "Date",
    "Type",
    "Title",
    "Info",
    "Currency",
    "Amount",
    "Fees & Taxes",
    "Net",
    "Tax Details",
)
ETSY_ITEMS_HEADER = (
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
LEGACY_MEDUSA_HEADER = ("Store", "Order", "Status", "Total", "Date", "Address")
MEDUSA_HEADER = (
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
MEDUSA_ITEMS_HEADER = (
    "order_number",
    "date",
    "order_status",
    "customer_email",
    "currency",
    "sku",
    "product",
    "variant",
    "quantity",
    "unit_price",
    "line_total",
)
REVOLUT_HEADER = (
    "payment_id",
    "type",
    "description",
    "original_payment_id",
    "order_id",
    "state",
    "reason",
    "amount",
    "currency",
    "surcharge_amount",
    "tip_amount",
    "refunded_amount",
    "created_date",
    "merchant_order_ext_ref",
    "payment_method",
    "location_id",
    "customer_id",
    "customer_card_number",
    "customer_card_country",
    "customer_card_brand",
    "customer_card_type",
    "customer_card_category",
    "customer_email",
    "fee_amount",
    "fee_currency",
    "captured_date",
)
STRIPE_PAYOUT_HEADER = (
    "id",
    "Amount",
    "Created (UTC)",
    "Currency",
    "Livemode",
    "Arrival Date (UTC)",
    "Source Type",
    "Destination",
    "Status",
    "Type",
    "Method",
    "Description",
    "Balance Transaction",
    "Failure Balance Transaction",
    "Failure Message",
    "Failure Code",
    "Statement Descriptor",
    "Trace ID",
    "Trace ID Status",
    "Destination Name",
    "Destination Country",
    "Destination Last 4",
)
STRIPE_PAYMENT_HEADER = (
    "id",
    "Created date (UTC)",
    "Amount",
    "Amount Refunded",
    "Currency",
    "Captured",
    "Converted Amount",
    "Converted Amount Refunded",
    "Converted Currency",
    "Decline Reason",
    "Description",
    "Fee",
    "Is Link",
    "Link Funding",
    "Mode",
    "PaymentIntent ID",
    "Payment Source Type",
    "Refunded date (UTC)",
    "Statement Descriptor",
    "Status",
    "Seller Message",
    "Taxes On Fee",
    "Interchange Costs",
    "Merchant Service Charge",
    "Card ID",
    "Card Name",
    "Card Address Line1",
    "Card Address Line2",
    "Card Address City",
    "Card Address State",
    "Card Address Country",
    "Card Address Zip",
    "Card AVS Line1 Status",
    "Card AVS Zip Status",
    "Card Brand",
    "Card CVC Status",
    "Card Exp Month",
    "Card Exp Year",
    "Card Fingerprint",
    "Card Funding",
    "Card Issue Country",
    "Card Last4",
    "Card Tokenization Method",
    "Customer ID",
    "Customer Description",
    "Customer Email",
    "Customer Phone",
    "Shipping Name",
    "Shipping Address Line1",
    "Shipping Address Line2",
    "Shipping Address City",
    "Shipping Address State",
    "Shipping Address Country",
    "Shipping Address Postal Code",
    "Disputed Amount",
    "Dispute Date (UTC)",
    "Dispute Evidence Due (UTC)",
    "Dispute Reason",
    "Dispute Status",
    "Invoice ID",
    "Invoice Number",
    "Checkout Session ID",
    "Checkout Custom Field 1 Key",
    "Checkout Custom Field 1 Value",
    "Checkout Custom Field 2 Key",
    "Checkout Custom Field 2 Value",
    "Checkout Custom Field 3 Key",
    "Checkout Custom Field 3 Value",
    "Checkout Line Item Summary",
    "Checkout Promotional Consent",
    "Checkout Terms of Service Consent",
    "Client Reference ID",
    "Payment Link ID",
    "UTM Campaign",
    "UTM Content",
    "UTM Medium",
    "UTM Source",
    "UTM Term",
    "Terminal Location ID",
    "Terminal Reader ID",
    "Application Fee",
    "Application ID",
    "Destination",
    "Transfer",
    "Transfer Group",
    "session_id (metadata)",
)

EXPECTED_ARCHIVE_BASELINE = {
    "canonical_orders": 304,
    "etsy_item_rows": 235,
    "etsy_order_units": "237",
    "etsy_orders": 173,
    "etsy_sku_exact_catalog_matches": 0,
    "etsy_skus": 56,
    "etsy_statement_rows": 1346,
    "etsy_statement_types": {
        "Deposit": 20,
        "Fee": 1062,
        "Marketing": 2,
        "Payment": 1,
        "Refund": 4,
        "Sale": 175,
        "Tax": 82,
    },
    "legacy_medusa_orders": 249,
    "medusa_current_currencies": {"EUR": 67, "GBP": 16, "USD": 13},
    "medusa_current_legacy_overlap": 41,
    "medusa_current_orders": 96,
    "medusa_item_exact_catalog_matches": 9,
    "medusa_item_nonblank_sku_rows": 138,
    "medusa_item_orders": 96,
    "medusa_item_rows": 222,
    "medusa_item_skus": 50,
    "medusa_item_units": "225",
    "printful_completed_rows": 247,
    "printful_refund_rows": 14,
    "printful_rows": 261,
    "revolut_currencies": {"EUR": 35, "GBP": 13, "USD": 270},
    "revolut_payments": 311,
    "revolut_refunds": 7,
    "revolut_rows": 318,
    "stripe_blank_ids": 72,
    "stripe_payment_intents": 134,
    "stripe_payout_rows": 8,
    "stripe_session_metadata_ids": 117,
    "stripe_unified_rows": 149,
}


@dataclass(frozen=True)
class CsvDocument:
    name: str
    checksum: str
    schema_digest: str
    rows: tuple


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode(),
    ).hexdigest()


def load_csv(name, checksum, content, expected_header, *, delimiter=","):
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    actual = tuple(reader.fieldnames or ())
    if actual != tuple(expected_header):
        raise ValueError(f"{name} schema changed: {actual!r} != {tuple(expected_header)!r}")
    rows = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"{name}:{row_number} contains fields outside the schema")
        normalized = {key: value if value is not None else "" for key, value in row.items()}
        normalized["_row_number"] = row_number
        normalized["_row_digest"] = digest([row_number, normalized])
        rows.append(normalized)
    return CsvDocument(name, checksum, digest(actual), tuple(rows))


def money(value, *, default=None):
    raw = (value or "").strip().replace("\u00a0", " ")
    if not raw or raw in {"-", "--", "—"}:
        return default
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace("€", "").replace("£", "").replace("$", "")
    raw = re.sub(r"(?:EUR|GBP|USD)", "", raw, flags=re.IGNORECASE)
    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        # The final separator is the decimal separator. This accepts both
        # 1,234.56 and 1.234,56 without guessing from the caller's locale.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        result = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"Invalid monetary value {value!r}") from error
    return -result if negative else result


def quantity(value):
    return money(value, default=Decimal("0"))


def parsed_datetime(value):
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for pattern in (
        "%m/%d/%y",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date/time {value!r}")


def evidence_payload(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def evidence_key(provider, document, row):
    return f"{provider}:{document.checksum}:{row['_row_number']}:{row['_row_digest']}"


def _first_consistent(rows, key):
    values = {(row.get(key) or "").strip() for row in rows if (row.get(key) or "").strip()}
    return next(iter(values)) if len(values) == 1 else ""


def _external_order_reference(row):
    candidates = f"{row.get('Title', '')} {row.get('Info', '')}"
    match = re.search(r"(?<!\d)(\d{8,})(?!\d)", candidates)
    return normalize_order_id(match.group(1)) if match else ""


def normalize_order_id(value):
    return (value or "").strip().lstrip("#").strip()


def normalize_printful_order_reference(value):
    """Return the immutable merchant order identifier from Printful's label."""
    normalized = re.sub(
        r"^(?:refund\s+to\s+wallet\s+|order\s+)",
        "",
        (value or "").strip(),
        flags=re.IGNORECASE,
    )
    # Printful's CSV export inserts a display space before the final ULID
    # segment.  Merchant order references are identifiers, so whitespace is
    # never significant and must not prevent an exact Medusa relationship.
    return re.sub(r"\s+", "", normalize_order_id(normalized))


def parse_legacy_delivery_address(value):
    """Split Printful's redacted legacy address without inventing missing data."""
    raw = (value or "").strip()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    result = {
        "shipping_name": parts[0] if parts else "",
        "shipping_street": "",
        "shipping_street2": "",
        "shipping_city": "",
        "shipping_state": "",
        "shipping_zip": "",
        "country": parts[-1].upper() if len(parts) >= 2 else "",
        "shipping_address_raw": raw,
    }
    body = parts[1:-1]
    if not body:
        return result
    postal = body[-1]
    us_postal = re.fullmatch(r"([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", postal, re.I)
    if us_postal:
        result["shipping_state"] = us_postal.group(1).upper()
        result["shipping_zip"] = us_postal.group(2)
    elif any(character.isdigit() for character in postal):
        result["shipping_zip"] = postal
    else:
        postal = ""
    if postal:
        body = body[:-1]
    if body:
        result["shipping_city"] = body[-1]
        body = body[:-1]
    if body:
        result["shipping_street"] = body[0]
    if len(body) > 1:
        result["shipping_street2"] = ", ".join(body[1:])
    return result


def build_canonical_orders(
    etsy_documents,
    legacy_document,
    medusa_document,
    medusa_items_document,
):
    orders = {}
    sources = []
    lines = []

    def ensure(external_id, provider, channel, date, state, precedence):
        key = (external_id or "").strip()
        if not key:
            raise ValueError(f"{provider} order has a blank business identifier")
        existing = orders.get(key)
        if not existing:
            existing = {
                "external_order_id": key,
                "external_display_id": "",
                "source_provider": provider,
                "channel_code": channel,
                "order_date": date,
                "original_provider_state": state or "",
                "state": "unknown",
                "source_payment_state": "",
                "source_fulfilment_state": "",
                "payment_date": None,
                "fulfilment_date": None,
                "customer_external_id": "",
                "customer_name": "",
                "customer_email": "",
                "shipping_name": "",
                "shipping_street": "",
                "shipping_street2": "",
                "shipping_city": "",
                "shipping_state": "",
                "shipping_zip": "",
                "shipping_address_raw": "",
                "country": "",
                "currency": "",
                "subtotal": None,
                "shipping": None,
                "discount": None,
                "tax": None,
                "total": None,
                "revenue": None,
                "amount_completeness": "header_only",
                "fulfilment_mode": "unknown",
                "precedence": precedence,
            }
            orders[key] = existing
        elif precedence > existing["precedence"]:
            existing.update(
                {
                    "source_provider": provider,
                    "channel_code": channel,
                    "order_date": date or existing["order_date"],
                    "original_provider_state": state or existing["original_provider_state"],
                    "precedence": precedence,
                },
            )
        return existing

    for row in legacy_document.rows:
        original_id = row["Order"].strip()
        external_id = normalize_order_id(original_id)
        order = ensure(
            external_id,
            "medusa_legacy",
            "medusa",
            parsed_datetime(row["Date"]),
            row["Status"],
            10,
        )
        order["total"] = money(row["Total"])
        order["revenue"] = order["total"]
        order.update(
            {
                "currency": "EUR",
                "state": (
                    "cancelled"
                    if "cancel" in row["Status"].strip().lower()
                    else "fulfilled"
                ),
                "source_payment_state": "unavailable",
                "source_fulfilment_state": row["Status"].strip(),
                "fulfilment_date": parsed_datetime(row["Date"]),
                **parse_legacy_delivery_address(row["Address"]),
            },
        )
        sources.append(
            (external_id, "medusa_legacy", legacy_document, (row,), original_id),
        )

    legacy_ids = set(orders)
    current_ids = set()
    medusa_display_ids = {}
    for row in medusa_document.rows:
        original_id = row["Order_ID"].strip()
        external_id = normalize_order_id(original_id)
        current_ids.add(external_id)
        display_id = row["Display_ID"].strip()
        if not display_id:
            raise ValueError(f"Medusa order {external_id} has a blank display identifier")
        if display_id in medusa_display_ids:
            raise ValueError(f"Duplicate Medusa display identifier {display_id}")
        medusa_display_ids[display_id] = external_id
        order = ensure(
            external_id,
            "medusa",
            "medusa",
            parsed_datetime(row["Date"]),
            row["Order status"],
            30,
        )
        order.update(
            {
                "external_display_id": row["Display_ID"].strip(),
                "country": row["Shipping Country Code"].strip(),
                "currency": row["Currency Code"].strip().upper(),
                "subtotal": money(row["Subtotal"], default=Decimal("0")),
                "shipping": money(row["Shipping Total"], default=Decimal("0")),
                "discount": -abs(
                    money(row["Discount Total"], default=Decimal("0")),
                ),
                "tax": money(row["Tax Total"], default=Decimal("0")),
                "total": money(row["Total"], default=Decimal("0")),
                "revenue": money(row["Total"], default=Decimal("0")),
                "amount_completeness": "header_only",
                "source_payment_state": row["Payment Status"].strip(),
                "source_fulfilment_state": row["Fulfillment Status"].strip(),
                "state": {
                    "delivered": "fulfilled",
                    "partially_delivered": "partially_fulfilled",
                    "not_fulfilled": (
                        "cancelled"
                        if row["Payment Status"].strip().lower() == "canceled"
                        else "confirmed"
                    ),
                }.get(row["Fulfillment Status"].strip().lower(), "unknown"),
                "customer_external_id": row["Customer ID"].strip(),
                "customer_name": " ".join(
                    value
                    for value in (
                        row["Customer First name"].strip(),
                        row["Customer Last name"].strip(),
                    )
                    if value
                ),
                "customer_email": row["Customer Email"].strip(),
                "shipping_name": " ".join(
                    value
                    for value in (
                        row["Customer First name"].strip(),
                        row["Customer Last name"].strip(),
                    )
                    if value
                ),
                "shipping_street": row["Shipping Address 1"].strip(),
                "shipping_street2": row["Shipping Address 2"].strip(),
                "shipping_city": row["Shipping City"].strip(),
                "shipping_state": row["Shipping Region ID"].strip(),
                "shipping_zip": row["Shipping Postal Code"].strip(),
                "shipping_address_raw": ", ".join(
                    value
                    for value in (
                        row["Customer First name"].strip(),
                        row["Customer Last name"].strip(),
                        row["Shipping Address 1"].strip(),
                        row["Shipping Address 2"].strip(),
                        row["Shipping City"].strip(),
                        row["Shipping Region ID"].strip(),
                        row["Shipping Postal Code"].strip(),
                        row["Shipping Country Code"].strip(),
                    )
                    if value
                ),
            },
        )
        sources.append((external_id, "medusa", medusa_document, (row,), original_id))

    medusa_item_groups = defaultdict(list)
    for row in medusa_items_document.rows:
        display_id = row["order_number"].strip()
        external_id = medusa_display_ids.get(display_id)
        if not external_id:
            raise ValueError(
                f"Medusa sold item references unknown display identifier {display_id!r}",
            )
        order = orders[external_id]
        line_currency = row["currency"].strip().upper()
        if line_currency != order["currency"]:
            raise ValueError(
                f"Medusa sold item currency changed for display identifier {display_id}",
            )
        line_date = parsed_datetime(row["date"])
        if not line_date or line_date.date() != order["order_date"].date():
            raise ValueError(
                f"Medusa sold item date changed for display identifier {display_id}",
            )
        order["amount_completeness"] = "partial"
        medusa_item_groups[external_id].append(row)
        lines.append(
            {
                "provider": "medusa",
                "external_order_id": external_id,
                "external_line_id": "",
                "external_transaction_id": "",
                "external_listing_id": "",
                "original_sku": row["sku"].strip(),
                "original_name": row["product"].strip() or "Unnamed Medusa item",
                "original_variation": row["variant"],
                "quantity": quantity(row["quantity"]),
                "unit_price": money(row["unit_price"], default=Decimal("0")),
                "discount": Decimal("0"),
                "shipping": Decimal("0"),
                "tax": Decimal("0"),
                "revenue": money(row["line_total"], default=Decimal("0")),
                "document": medusa_items_document,
                "row": row,
            },
        )
    missing_item_orders = set(medusa_display_ids.values()) - set(medusa_item_groups)
    if missing_item_orders:
        raise ValueError(
            "Medusa sold items do not cover every current order: "
            f"{sorted(missing_item_orders)!r}",
        )
    for external_id, rows in sorted(medusa_item_groups.items()):
        sources.append(
            (
                external_id,
                "medusa",
                medusa_items_document,
                tuple(rows),
                external_id,
            ),
        )

    etsy_groups = defaultdict(list)
    for document in etsy_documents:
        for row in document.rows:
            etsy_groups[normalize_order_id(row["Order ID"])].append((document, row))
    if set(etsy_groups) - legacy_ids:
        message = "The Etsy item exports contain orders outside the legacy umbrella"
        raise ValueError(message)
    for external_id, document_rows in sorted(etsy_groups.items()):
        rows = [row for _document, row in document_rows]
        date = min(parsed_datetime(row["Sale Date"]) for row in rows)
        order = ensure(external_id, "etsy", "etsy", date, "Sale", 50)
        currency = _first_consistent(rows, "Currency").upper()
        country = _first_consistent(rows, "Ship Country")
        line_revenue = sum(
            (money(row["Item Total"], default=Decimal("0")) for row in rows),
            Decimal("0"),
        )
        gross = sum(
            (
                money(row["Price"], default=Decimal("0"))
                * quantity(row["Quantity"])
                for row in rows
            ),
            Decimal("0"),
        )
        discount = -sum(
            (abs(money(row["Discount Amount"], default=Decimal("0"))) for row in rows),
            Decimal("0"),
        )
        shipping = sum(
            (money(row["Order Shipping"], default=Decimal("0")) for row in rows),
            Decimal("0"),
        )
        tax = sum(
            (money(row["Order Sales Tax"], default=Decimal("0")) for row in rows),
            Decimal("0"),
        )
        payment_dates = [parsed_datetime(row["Date Paid"]) for row in rows if row["Date Paid"].strip()]
        fulfilment_dates = [
            parsed_datetime(row["Date Shipped"])
            for row in rows
            if row["Date Shipped"].strip()
        ]
        order.update(
            {
                "currency": currency,
                "country": country,
                "subtotal": gross,
                "shipping": shipping,
                "discount": discount,
                "tax": tax,
                "total": line_revenue + discount + shipping + tax,
                "revenue": line_revenue + discount + shipping + tax,
                "amount_completeness": "complete",
                "fulfilment_mode": "unknown",
                "state": "fulfilled" if fulfilment_dates else "confirmed",
                "source_payment_state": "paid" if payment_dates else "unavailable",
                "source_fulfilment_state": "shipped" if fulfilment_dates else "unavailable",
                "payment_date": min(payment_dates) if payment_dates else None,
                "fulfilment_date": max(fulfilment_dates) if fulfilment_dates else None,
                "customer_name": _first_consistent(rows, "Buyer"),
                "shipping_name": _first_consistent(rows, "Ship Name"),
                "shipping_street": _first_consistent(rows, "Ship Address1"),
                "shipping_street2": _first_consistent(rows, "Ship Address2"),
                "shipping_city": _first_consistent(rows, "Ship City"),
                "shipping_state": _first_consistent(rows, "Ship State"),
                "shipping_zip": _first_consistent(rows, "Ship Zipcode"),
                "shipping_address_raw": ", ".join(
                    value
                    for value in (
                        _first_consistent(rows, "Ship Name"),
                        _first_consistent(rows, "Ship Address1"),
                        _first_consistent(rows, "Ship Address2"),
                        _first_consistent(rows, "Ship City"),
                        _first_consistent(rows, "Ship State"),
                        _first_consistent(rows, "Ship Zipcode"),
                        country,
                    )
                    if value
                ),
            },
        )
        source_groups = {}
        for document, row in document_rows:
            key = (document.name, document.checksum)
            if key not in source_groups:
                source_groups[key] = [document, []]
            source_groups[key][1].append(row)
            lines.append(
                {
                    "provider": "etsy",
                    "external_order_id": external_id,
                    "external_line_id": row["Transaction ID"].strip(),
                    "external_transaction_id": row["Transaction ID"].strip(),
                    "external_listing_id": row["Listing ID"].strip(),
                    "original_sku": row["SKU"].strip(),
                    "original_name": row["Item Name"].strip() or "Unnamed Etsy item",
                    "original_variation": row["Variations"],
                    "quantity": quantity(row["Quantity"]),
                    "unit_price": money(row["Price"], default=Decimal("0")),
                    "discount": -abs(
                        money(row["Discount Amount"], default=Decimal("0")),
                    ),
                    "shipping": money(row["Order Shipping"], default=Decimal("0")),
                    "tax": money(row["Order Sales Tax"], default=Decimal("0")),
                    "revenue": money(row["Item Total"], default=Decimal("0")),
                    "document": document,
                    "row": row,
                },
            )
        for document, grouped_rows in source_groups.values():
            sources.append(
                (
                    external_id,
                    "etsy",
                    document,
                    tuple(grouped_rows),
                    grouped_rows[0]["Order ID"].strip(),
                ),
            )

    return {
        "orders": orders,
        "sources": tuple(sources),
        "lines": tuple(lines),
        "legacy_ids": legacy_ids,
        "current_ids": current_ids,
        "etsy_ids": set(etsy_groups),
    }


def parse_etsy_statement_events(documents):
    events = []
    for document in documents:
        for row in document.rows:
            kind = row["Type"].strip()
            amount = money(row["Amount"], default=Decimal("0"))
            fees = money(row["Fees & Taxes"], default=Decimal("0"))
            net = money(row["Net"], default=Decimal("0"))
            refund = -abs(amount) if kind == "Refund" else Decimal("0")
            events.append(
                {
                    "provider_event_key": evidence_key("etsy", document, row),
                    "event_type": {
                        "Deposit": "deposit",
                        "Fee": "fee",
                        "Marketing": "fee",
                        "Payment": "payment",
                        "Refund": "refund",
                        "Sale": "payment",
                        "Tax": "tax",
                    }.get(kind, "adjustment"),
                    "external_order_id": _external_order_reference(row),
                    "event_date": parsed_datetime(row["Date"]),
                    "currency": row["Currency"].strip().upper(),
                    "amount": refund if kind == "Refund" else amount,
                    "refund": refund,
                    "fee": abs(fees),
                    "net": net,
                    "original_state": kind,
                    "state": "refunded" if kind == "Refund" else "settled",
                    "document": document,
                    "row": row,
                },
            )
    return tuple(events)


def apply_etsy_refunds(canonical, events):
    """Attach order-level Etsy refund evidence without changing gross order totals."""
    refunds_by_order = defaultdict(list)
    for event in events:
        if event["event_type"] == "refund" and event["external_order_id"]:
            refunds_by_order[event["external_order_id"]].append(event)
    linked_orders = set()
    for external_id, refunds in refunds_by_order.items():
        order = canonical["orders"].get(external_id)
        if not order:
            continue
        refund_amount = sum(
            (event["refund"] for event in refunds),
            Decimal("0"),
        )
        order["refund"] = refund_amount
        order["net"] = (order["revenue"] or Decimal("0")) + refund_amount
        order["refund_date"] = max(event["event_date"] for event in refunds)
        order["state"] = "partially_refunded"
        linked_orders.add(external_id)
    return linked_orders


def parse_stripe_events(payment_document, payout_document):
    events = []
    for row in payment_document.rows:
        external_id = row["id"].strip()
        amount = money(row["Amount"], default=Decimal("0"))
        key = (
            f"stripe:id:{external_id}"
            if external_id
            else evidence_key("stripe-blank", payment_document, row)
        )
        refunded = -abs(
            money(row["Amount Refunded"], default=Decimal("0")),
        )
        converted_refunded = -abs(
            money(row["Converted Amount Refunded"], default=Decimal("0")),
        )
        events.append(
            {
                "provider_event_key": key,
                # Stripe's unified export keeps the original payment and its
                # later refund on one row.  Preserve that mixed evidence as a
                # payment with a negative refund component; treating the
                # positive original amount as a refund would reverse its sign.
                "event_type": "refund" if refunded and amount <= 0 else "payment",
                "external_transaction_id": external_id,
                "external_payment_intent_id": row["PaymentIntent ID"].strip(),
                "external_session_id": row["session_id (metadata)"].strip(),
                "external_checkout_session_id": row["Checkout Session ID"].strip(),
                "event_date": parsed_datetime(row["Created date (UTC)"]),
                "currency": row["Currency"].strip().upper(),
                "amount": amount,
                "refund": refunded,
                "fee": abs(money(row["Fee"], default=Decimal("0"))),
                "net": (
                    amount
                    + refunded
                    - abs(money(row["Fee"], default=Decimal("0")))
                ),
                "converted_amount": money(row["Converted Amount"]),
                "converted_refund": converted_refunded,
                "converted_currency": row["Converted Currency"].strip().upper(),
                "original_state": row["Status"],
                "state": "settled" if row["Status"].lower() == "succeeded" else "unknown",
                "document": payment_document,
                "row": row,
            },
        )
    for row in payout_document.rows:
        external_id = row["id"].strip()
        if not external_id:
            message = "Stripe payout identifier is blank"
            raise ValueError(message)
        events.append(
            {
                "provider_event_key": f"stripe:payout:{external_id}",
                "event_type": "payout",
                "external_payout_id": external_id,
                "external_transaction_id": external_id,
                "event_date": parsed_datetime(row["Created (UTC)"]),
                "currency": row["Currency"].strip().upper(),
                "amount": money(row["Amount"], default=Decimal("0")),
                "refund": Decimal("0"),
                "fee": Decimal("0"),
                "net": money(row["Amount"], default=Decimal("0")),
                "converted_amount": None,
                "converted_refund": None,
                "converted_currency": "",
                "original_state": row["Status"],
                "state": "settled" if row["Status"].lower() == "paid" else "unknown",
                "document": payout_document,
                "row": row,
            },
        )
    return tuple(events)


def parse_revolut_events(document):
    events = []
    for row in document.rows:
        external_id = row["payment_id"].strip()
        if not external_id:
            message = "Revolut payment identifier is blank"
            raise ValueError(message)
        is_refund = row["type"].strip().lower() == "refund"
        amount = money(row["amount"], default=Decimal("0"))
        if is_refund:
            amount = -abs(amount)
        refunded = money(row["refunded_amount"], default=Decimal("0"))
        refunded = -abs(refunded) if refunded else Decimal("0")
        events.append(
            {
                "provider_event_key": f"revolut:{external_id}",
                "event_type": "refund" if is_refund else "payment",
                "external_transaction_id": external_id,
                "external_original_payment_id": row["original_payment_id"].strip(),
                "external_order_id": (
                    normalize_order_id(
                        row["order_id"] or row["merchant_order_ext_ref"],
                    )
                ),
                "event_date": parsed_datetime(row["created_date"]),
                "currency": row["currency"].strip().upper(),
                "amount": amount,
                "refund": amount if is_refund else refunded,
                "fee": abs(money(row["fee_amount"], default=Decimal("0"))),
                "net": amount - abs(
                    money(row["fee_amount"], default=Decimal("0")),
                ),
                "original_state": row["state"],
                "state": "refunded" if is_refund else "settled",
                "document": document,
                "row": row,
            },
        )
    by_id = {event["external_transaction_id"]: event for event in events}
    for event in events:
        original = event.get("external_original_payment_id")
        if original and original not in by_id:
            raise ValueError(f"Revolut refund original payment is missing: {original}")
    return tuple(events)


def parse_printful_pdf(content):
    pages = PdfReader(io.BytesIO(content)).pages
    tokens = []
    for page in pages:
        tokens.extend(
            line.strip()
            for line in (page.extract_text() or "").splitlines()
            if line.strip()
        )
    rows = []
    date_pattern = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
    index = 0
    while index + 13 < len(tokens):
        if date_pattern.match(tokens[index]) and tokens[index + 1] in {
            "Completed",
            "Refunded",
        }:
            printful_index = next(
                (
                    position
                    for position in range(index + 3, min(index + 7, len(tokens)))
                    if re.match(r"^\d{8,}$", tokens[position])
                ),
                None,
            )
            if printful_index is None or printful_index + 10 >= len(tokens):
                raise ValueError(f"Invalid Printful identifier near {tokens[index]}")
            amounts = [
                money(value)
                for value in tokens[printful_index + 3 : printful_index + 10]
            ]
            if any(value is None for value in amounts):
                raise ValueError(f"Incomplete Printful monetary row near {tokens[index]}")
            rows.append(
                {
                    "date": parsed_datetime(tokens[index]),
                    "status": tokens[index + 1],
                    "order": " ".join(tokens[index + 2 : printful_index]),
                    "printful_id": tokens[printful_index],
                    "origin_country_codes": tokens[printful_index + 1],
                    "destination": tokens[printful_index + 2],
                    "products": amounts[0],
                    "discount": amounts[1],
                    "shipping": amounts[2],
                    "digitalization": amounts[3],
                    "tax": amounts[4],
                    "vat": amounts[5],
                    "total": amounts[6],
                    "review": tokens[printful_index + 10],
                    "_row_number": len(rows) + 1,
                    "_row_digest": digest(
                        tokens[index : printful_index + 11],
                    ),
                },
            )
            index = printful_index + 11
        else:
            index += 1
    return tuple(rows)


def archive_baseline(
    canonical,
    statement_documents,
    stripe_events,
    revolut_events,
    printful_rows,
    payout_document,
    catalog_skus,
):
    etsy_lines = [line for line in canonical["lines"] if line["provider"] == "etsy"]
    medusa_lines = [
        line for line in canonical["lines"] if line["provider"] == "medusa"
    ]
    etsy_skus = {line["original_sku"] for line in etsy_lines if line["original_sku"]}
    medusa_skus = {
        line["original_sku"] for line in medusa_lines if line["original_sku"]
    }
    statement_types = Counter(
        row["Type"]
        for document in statement_documents
        for row in document.rows
    )
    stripe_payments = [
        event
        for event in stripe_events
        if event["document"].name != payout_document.name
    ]
    baseline = {
        "canonical_orders": len(canonical["orders"]),
        "etsy_item_rows": len(etsy_lines),
        "etsy_order_units": str(
            sum((line["quantity"] for line in etsy_lines), Decimal("0")),
        ),
        "etsy_orders": len(canonical["etsy_ids"]),
        "etsy_sku_exact_catalog_matches": len(etsy_skus & set(catalog_skus)),
        "etsy_skus": len(etsy_skus),
        "etsy_statement_rows": sum(len(document.rows) for document in statement_documents),
        "etsy_statement_types": dict(sorted(statement_types.items())),
        "legacy_medusa_orders": len(canonical["legacy_ids"]),
        "medusa_current_currencies": dict(
            sorted(Counter(
                order["currency"]
                for key, order in canonical["orders"].items()
                if key in canonical["current_ids"]
            ).items()),
        ),
        "medusa_current_legacy_overlap": len(
            canonical["current_ids"] & canonical["legacy_ids"],
        ),
        "medusa_current_orders": len(canonical["current_ids"]),
        "medusa_item_exact_catalog_matches": len(medusa_skus & set(catalog_skus)),
        "medusa_item_nonblank_sku_rows": sum(
            bool(line["original_sku"]) for line in medusa_lines
        ),
        "medusa_item_orders": len(
            {line["external_order_id"] for line in medusa_lines},
        ),
        "medusa_item_rows": len(medusa_lines),
        "medusa_item_skus": len(medusa_skus),
        "medusa_item_units": str(
            sum((line["quantity"] for line in medusa_lines), Decimal("0")),
        ),
        "printful_completed_rows": sum(
            row["status"] == "Completed" for row in printful_rows
        ),
        "printful_refund_rows": sum(
            row["status"] == "Refunded" for row in printful_rows
        ),
        "printful_rows": len(printful_rows),
        "revolut_currencies": dict(
            sorted(Counter(event["currency"] for event in revolut_events).items()),
        ),
        "revolut_payments": sum(
            event["event_type"] == "payment" for event in revolut_events
        ),
        "revolut_refunds": sum(
            event["event_type"] == "refund" for event in revolut_events
        ),
        "revolut_rows": len(revolut_events),
        "stripe_blank_ids": sum(
            not event["external_transaction_id"] for event in stripe_payments
        ),
        "stripe_payment_intents": len(
            {
                event["external_payment_intent_id"]
                for event in stripe_payments
                if event["external_payment_intent_id"]
            },
        ),
        "stripe_payout_rows": len(payout_document.rows),
        "stripe_session_metadata_ids": len(
            {
                event["row"]["session_id (metadata)"].strip()
                for event in stripe_payments
                if event["row"]["session_id (metadata)"].strip()
            },
        ),
        "stripe_unified_rows": len(stripe_payments),
    }
    if baseline != EXPECTED_ARCHIVE_BASELINE:
        raise ValueError(
            f"B2C archive baseline changed: {baseline} != {EXPECTED_ARCHIVE_BASELINE}",
        )
    return baseline
