"""Value normalisation and document types shared by every channel parser.

Deliberately free of Odoo imports: a parser is pure data handling, so it can be
exercised by a plain unit test without a database.
"""

import csv
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

#: Grain of a parsed row. An order row carries the money the channel charged;
#: a line row carries what was actually bought.
ORDER_GRAIN = "order"
LINE_GRAIN = "line"

_CURRENCY_SYMBOLS = str.maketrans("", "", "€£$")
_NON_BREAKING_SPACE = " "
_EMPTY_MONEY = {"", "-", "--", "—"}


def digest(value):
    """Return a stable SHA-256 over any JSON-representable value."""
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode(),
    ).hexdigest()


def header_signature(header):
    """Return the order-insensitive identity of a CSV header.

    Channels reorder and rename columns between exports.  Matching on the set
    of column names rather than their sequence keeps a reordered export
    recognisable, while a renamed or dropped column still changes the
    signature and is reported instead of silently mis-parsed.
    """
    return digest(sorted(name.strip() for name in header))


def money(value, *, default=None):
    """Return a Decimal for a channel monetary field, or ``default`` when blank."""
    raw = (value or "").strip().replace(_NON_BREAKING_SPACE, " ")
    if raw in _EMPTY_MONEY:
        return default
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").translate(_CURRENCY_SYMBOLS)
    raw = re.sub(r"(?:EUR|GBP|USD)", "", raw, flags=re.IGNORECASE).replace(" ", "")
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


def amount(value, *, default=None):
    """Return a Decimal for a value a JSON API states as a string, or not at all.

    An API that omits a price sends ``null``, which is not the same as zero and
    must not be read as the text "None".
    """
    if value is None:
        return default
    return money(str(value), default=default)


def quantity(value):
    """Return a Decimal quantity, treating a blank cell as zero."""
    return money(value, default=Decimal("0"))


_DATE_PATTERNS = (
    "%m/%d/%y",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%B %d, %Y",
)


def parsed_datetime(value):
    """Return a naive UTC datetime for any date shape a channel exports."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    else:
        if parsed.tzinfo:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date/time {value!r}")


def text(value):
    """Return a channel text field with its HTML escaping resolved."""
    raw = (value or "").strip()
    return html.unescape(raw) if "&" in raw else raw


def reference(value):
    """Return an external identifier without its decorative prefix."""
    return (value or "").strip().lstrip("#").strip()


def jsonable(value):
    """Return a value stored losslessly in a JSON field.

    Decimals become strings rather than floats so a cent never rounds away
    between a parsed row and the record it becomes.
    """
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


@dataclass(frozen=True)
class SourceDocument:
    """One uploaded file, read once and never re-read from disk."""

    name: str
    checksum: str
    header: tuple
    rows: tuple


@dataclass(frozen=True)
class ParsedRow:
    """One normalised source row, ready to become canonical commerce."""

    format_id: str
    provider: str
    grain: str
    external_order_id: str
    row_number: int
    payload: dict
    values: dict = field(default_factory=dict)
    external_line_id: str = ""
    occurred_at: object = None

    @property
    def payload_digest(self):
        return digest(self.payload)


class SchemaError(ValueError):
    """A file whose columns no parser recognises, named precisely."""


def read_csv(name, content, *, delimiter=","):
    """Return a SourceDocument for raw CSV bytes.

    Channels export UTF-8 with a byte-order mark often enough that stripping it
    belongs here rather than in every caller.
    """
    checksum = hashlib.sha256(content).hexdigest()
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig"), newline=""),
        delimiter=delimiter,
    )
    header = tuple(name.strip() for name in (reader.fieldnames or ()))
    rows = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise SchemaError(f"{name}:{row_number} has more cells than the header declares")
        rows.append(
            {
                (key or "").strip(): (value if value is not None else "")
                for key, value in row.items()
            }
            | {"_row_number": row_number},
        )
    return SourceDocument(name=name, checksum=checksum, header=header, rows=tuple(rows))
