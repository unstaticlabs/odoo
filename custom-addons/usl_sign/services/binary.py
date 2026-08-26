import base64
import binascii

from odoo.tools.binary import BinaryBytes, BinaryValue


def field_content(value, *, validate=True):
    """Return exact bytes from an Odoo Binary field or an RPC base64 value.

    saas-19.3 exposes binary fields as ``BinaryValue`` objects and rejects
    ambiguous byte writes. Public RPC payloads remain base64 text. Keeping the
    conversion at this boundary prevents accidentally hashing base64 text or
    trying to decode already-persisted PDF bytes.
    """
    if not value:
        return b""
    if isinstance(value, BinaryValue):
        return value.content
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return base64.b64decode(value, validate=validate)
        except (binascii.Error, ValueError) as error:
            msg = "Binary RPC values must be valid base64 data."
            raise ValueError(msg) from error
    raise TypeError(f"Unsupported binary value: {type(value).__name__}")


def field_value(raw):
    """Create the unambiguous value required for an Odoo Binary field write."""
    if not raw:
        return False
    if isinstance(raw, BinaryValue):
        return raw
    return BinaryBytes(raw)


def base64_text(raw):
    """Encode raw bytes for a JSON or RPC interface, never for an ORM write."""
    return base64.b64encode(raw).decode()
