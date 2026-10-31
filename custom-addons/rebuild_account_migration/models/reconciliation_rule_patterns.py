"""Bounded label shapes, deliberately not an arbitrary regex solver."""
import re


def label_tokens(label):
    return re.findall(r"[0-9]+|[^0-9\s]+|\s+", (label or "").strip())


def shape_key(label):
    return tuple("#" if token.isascii() and token.isdigit() else " " if token.isspace() else token.casefold()
                 for token in label_tokens(label))


def infer_pattern(labels):
    """Only observed digit slots vary; stable words and punctuation stay literal."""
    rows = [label_tokens(label) for label in labels]
    if not rows or len({shape_key(label) for label in labels}) != 1:
        return False
    parts, shape = [], []
    for column in zip(*rows, strict=True):
        value = column[0]
        if value.isspace():
            parts.append(r"\s+")
            shape.append(["space"])
        elif value.isascii() and value.isdigit() and len(set(column)) > 1:
            low, high = min(map(len, column)), max(map(len, column))
            parts.append("[0-9]{%s,%s}" % (low, high))
            shape.append(["digits", low, high])
        else:
            parts.append(re.escape(value))
            shape.append(["literal", value])
    return "^" + "".join(parts) + "$", shape


def shape_witness(left, right):
    """A witness or disjointness for two generated token languages; else unknown."""
    if len(left) != len(right):
        return None, False
    result = []
    for a, b in zip(left, right, strict=True):
        if a[0] == b[0] == "space":
            result.append(" ")
        elif a[0] == b[0] == "literal":
            if not a[1].isascii() or not b[1].isascii():
                return None, False
            if a[1].casefold() != b[1].casefold():
                return None, True
            result.append(a[1])
        elif a[0] == b[0] == "digits":
            low, high = max(a[1], b[1]), min(a[2], b[2])
            if low > high:
                return None, True
            result.append("1" * low)
        elif {a[0], b[0]} == {"literal", "digits"}:
            literal, digits = (a, b) if a[0] == "literal" else (b, a)
            value = literal[1]
            if not (value.isascii() and value.isdigit() and digits[1] <= len(value) <= digits[2]):
                return None, True
            result.append(value)
        else:
            return None, False
    return "".join(result), True
