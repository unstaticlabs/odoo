"""Bounded visible email evidence, without filename guesses or remote resources."""

import html
import re
import unicodedata
from html.parser import HTMLParser

MAX_SOURCE_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 65536
REFERENCE = re.compile(r"(?<![A-Za-z0-9])GPA\.\d{4}-\d{4}-\d{4}-\d{5}(?:\.\.\d+)?(?![A-Za-z0-9.])")
BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "table"}


def _fold(value):
    return " ".join(unicodedata.normalize("NFKD", value.replace("’", "'")).encode("ascii", "ignore").decode().lower().split())


class _VisibleEmail(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.chunks = []
        self.headings = []
        self.hidden = 0
        self.heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag in {"title", "h1", "h2"}:
            self.heading += 1
        if tag in BLOCKS:
            self.parts.append("\n")
        if tag == "img" and not self.hidden:
            alternate = dict(attrs).get("alt")
            if alternate:
                self.handle_data(alternate)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"title", "h1", "h2"}:
            self.heading = max(0, self.heading - 1)
        if tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        self.parts.append(data)
        self.chunks.append(data)
        if self.heading:
            self.headings.append(data)


def _visible(value):
    if isinstance(value, bytes):
        if len(value) > MAX_SOURCE_BYTES:
            return None
        value = value.decode("utf-8-sig", errors="replace")
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
        return None
    original = value
    if "&lt;html" in value.lower() or "&lt;!doctype" in value.lower():
        value = html.unescape(value)
    parser = _VisibleEmail()
    parser.feed(value)
    parser.close()
    text = "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines()).strip()
    if len(text) > MAX_TEXT_CHARS:
        return None
    # An intact literal reference also supplies an exact native search term.
    # Fragments across markup are unknown, never reconstructed by guessing.
    references = {m.group() for chunk in parser.chunks for m in REFERENCE.finditer(chunk) if m.group() in original}
    return text, references, " ".join(parser.headings)


def receipt_email(value):
    """Return one complete Google Play receipt identity, or no assertion."""
    parsed = _visible(str(value or ""))
    if not parsed:
        return None
    text, references, headings = parsed
    folded = _fold(text + " " + headings)
    if (
        "google play" not in folded
        or not any(label in folded for label in ("order number", "numero de commande"))
        or len(references) != 1
    ):
        return None
    return {"provider": "google_play", "reference": references.pop(), "text": text}


def incidental_terms(raw):
    """Recognize only content-confirmed Google Play terms, even as text/plain."""
    if isinstance(raw, bytes) and raw.startswith(b"%PDF"):
        return False
    parsed = _visible(raw)
    if not parsed:
        return False
    _text, references, headings = parsed
    heading = _fold(headings)
    return bool(
        not references and "google play" in heading
        and any(label in heading for label in ("terms of service", "terms of use", "conditions d'utilisation"))
    )
