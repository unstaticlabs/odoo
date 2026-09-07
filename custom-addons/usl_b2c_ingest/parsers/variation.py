"""Reading the variant a channel describes in prose.

Channels do not export attribute identifiers.  Etsy writes named pairs
(``Color:Black,Size:M``); Medusa writes bare values in order
(``M (46cm) / 3mm (11x17mm links) / Two Chains``).  Neither uses the spacing
Odoo does.  What both really state is a set of values, so both are read into
one, and matching a variant is then set comparison rather than parsing.
"""

import re

_SEPARATORS = re.compile(r"\s+/\s+|,(?![^(]*\))")
_WHITESPACE = re.compile(r"\s+")


def normalise(value):
    """Return a value with the spacing a channel and Odoo disagree about removed."""
    return _WHITESPACE.sub("", (value or "").strip().casefold())


def tokens(variation):
    """Return the values a channel's variation text states, in order.

    A named pair keeps only its value: which attribute a channel thinks it is
    naming carries no weight, because the same value is spelled differently by
    each channel while the value itself is what identifies the variant.
    """
    raw = (variation or "").strip()
    if not raw:
        return ()
    found = []
    for part in _SEPARATORS.split(raw):
        part = part.strip()
        if not part:
            continue
        _name, separator, value = part.partition(":")
        found.append((value if separator else part).strip())
    return tuple(found)


def match(values, token):
    """Return the one attribute value a channel's token states, or nothing.

    A token carries detail Odoo does not keep — ``4mm (14x21mm links)`` for a
    ``4 mm`` diameter — so a value is accepted when the token begins with it.
    Where several values qualify, the longest is the one the token states:
    ``One Chain With Padlock`` also begins with ``One Chain``, and only the
    former is what was sold.
    """
    normalised = normalise(token)
    if not normalised:
        return None
    candidates = [
        value
        for value in values
        if normalised.startswith(normalise(value)) and normalise(value)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda value: len(normalise(value)))
