"""Channel export parsers and the registry that recognises a dropped file.

A file is recognised by the set of column names it declares, not by its file
name or the order of its columns, so a renamed export or a channel that
reorders its columns still lands in the right parser.
"""

from . import common, etsy, medusa, printful, printful_transactions, stripe
from .common import (
    CHARGE_GRAIN,
    LINE_GRAIN,
    ORDER_GRAIN,
    ParsedRow,
    SchemaError,
    SourceDocument,
    amount,
    header_signature,
    jsonable,
    read_csv,
)


class CsvFormat:
    """One recognised CSV export shape."""

    def __init__(
        self,
        format_id,
        label,
        provider,
        header,
        parse,
        *,
        precedence,
        delimiter=",",
        exact=True,
    ):
        self.format_id = format_id
        self.label = label
        self.provider = provider
        self.header = tuple(header)
        self.parse = parse
        self.precedence = precedence
        self.delimiter = delimiter
        #: A shape recognised by the columns it must contain rather than by all
        #: of them.  Stripe appends one column per metadata key an account
        #: writes, and a statement carried between tools arrives with a column
        #: saying where it came from: demanding the whole set would refuse a
        #: file that is in every respect the export it claims to be.
        self.exact = exact
        self.signature = header_signature(self.header)

    def recognises(self, header):
        """Return whether a file's columns are this shape."""
        if self.exact:
            return header_signature(header) == self.signature
        return set(self.header) <= {name.strip() for name in header}


#: Lower precedence wins when two exports describe the same fact: an order file
#: outranks an item file on order money, and both outrank the supplier.
CSV_FORMATS = (
    CsvFormat(
        "etsy_orders",
        "Etsy — sold orders",
        etsy.PROVIDER,
        etsy.ORDERS_HEADER,
        etsy.parse_orders,
        precedence=10,
    ),
    CsvFormat(
        "etsy_order_items",
        "Etsy — sold order items",
        etsy.PROVIDER,
        etsy.ITEMS_HEADER,
        etsy.parse_order_items,
        precedence=20,
    ),
    CsvFormat(
        "medusa_orders",
        "Medusa — sold items per order",
        medusa.PROVIDER,
        medusa.ORDERS_HEADER,
        medusa.parse_orders,
        precedence=10,
        delimiter=medusa.DELIMITER,
    ),
    CsvFormat(
        "medusa_full_orders",
        "Medusa — orders",
        medusa.PROVIDER,
        medusa.FULL_ORDERS_HEADER,
        medusa.parse_full_orders,
        precedence=5,
        delimiter=medusa.DELIMITER,
    ),
    CsvFormat(
        "medusa_order_items",
        "Medusa — sold items",
        medusa.PROVIDER,
        medusa.ITEMS_HEADER,
        medusa.parse_order_items,
        precedence=20,
        delimiter=medusa.DELIMITER,
    ),
    CsvFormat(
        "etsy_statement",
        "Etsy — payment account statement",
        etsy.PROVIDER,
        etsy.STATEMENT_REQUIRED,
        etsy.parse_statement,
        precedence=30,
        exact=False,
    ),
    CsvFormat(
        "printful_transactions",
        "Printful — wallet transactions",
        printful_transactions.PROVIDER,
        printful_transactions.TRANSACTIONS_REQUIRED,
        printful_transactions.parse_transactions,
        precedence=30,
        exact=False,
    ),
    CsvFormat(
        "stripe_balance_history",
        "Stripe — balance history",
        stripe.PROVIDER,
        stripe.BALANCE_REQUIRED,
        stripe.parse_balance_history,
        precedence=30,
        exact=False,
    ),
)

FORMATS_BY_ID = {fmt.format_id: fmt for fmt in CSV_FORMATS}
_FORMATS_BY_SIGNATURE = {fmt.signature: fmt for fmt in CSV_FORMATS if fmt.exact}
_SUBSET_FORMATS = tuple(fmt for fmt in CSV_FORMATS if not fmt.exact)


def detect(content):
    """Return the CsvFormat for raw file bytes, or raise naming what differed.

    Every known delimiter is tried before giving up, because the delimiter is
    part of the export's shape rather than something an operator should have to
    declare.
    """
    attempts = {}
    for delimiter in sorted({fmt.delimiter for fmt in CSV_FORMATS}):
        try:
            document = read_csv("probe", content, delimiter=delimiter)
        except (UnicodeDecodeError, SchemaError):
            continue
        found = _FORMATS_BY_SIGNATURE.get(header_signature(document.header))
        if found is not None and found.delimiter == delimiter:
            return found
        attempts[delimiter] = document.header
    # Only once no shape claims the whole header: a file that is exactly one
    # export must never be read as another that merely fits inside it.
    for delimiter, header in attempts.items():
        matching = [
            fmt
            for fmt in _SUBSET_FORMATS
            if fmt.delimiter == delimiter and fmt.recognises(header)
        ]
        if len(matching) == 1:
            return matching[0]
        if matching:
            raise SchemaError(
                "These columns are read by more than one parser, so which "
                f"export this is cannot be told: {[fmt.label for fmt in matching]}",
            )
    raise SchemaError(_unrecognised(attempts))


def parse(fmt, name, content):
    """Return the parsed rows and the source document for a recognised file."""
    document = read_csv(name, content, delimiter=fmt.delimiter)
    if not fmt.recognises(document.header):
        raise SchemaError(
            f"{name} does not have the columns of {fmt.label}: "
            f"{_column_difference(fmt.header, document.header)}",
        )
    return tuple(fmt.parse(document)), document


def _column_difference(expected, actual):
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    parts = []
    if missing:
        parts.append(f"missing {missing}")
    if unexpected:
        parts.append(f"unexpected {unexpected}")
    return "; ".join(parts) or "the same columns in a different order"


def _unrecognised(attempts):
    if not attempts:
        message = "The file is not readable as CSV text."
        raise SchemaError(message)
    best = min(
        attempts.values(),
        key=lambda header: min(
            len(set(fmt.header) ^ set(header)) for fmt in CSV_FORMATS
        ),
    )
    closest = min(CSV_FORMATS, key=lambda fmt: len(set(fmt.header) ^ set(best)))
    return (
        f"No parser recognises these columns. Closest is {closest.label}: "
        f"{_column_difference(closest.header, best)}"
    )
