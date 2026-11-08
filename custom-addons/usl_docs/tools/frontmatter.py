"""Read and write the front matter every documentation page carries.

The block is a deliberately small subset of YAML: scalar values, block lists
and nothing else. That subset needs no YAML library (the runtime image ships
none), parses identically from the Odoo viewer and from the repository checks
in ``scripts/``, and refuses the ambiguities that make full YAML a source of
surprises in a public repository. The same module is loaded by path from
``scripts/check-docs`` so the two sides can never disagree.

The schema itself is documented in ``docs/README.md``.
"""

from __future__ import annotations

import re

DELIMITER = "---"
_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s+(.*))?$")
_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")

#: Diátaxis type per directory. ``decision`` lives under ``docs/product``.
TYPES = ("tutorial", "how-to", "reference", "explanation", "decision")
DIRECTORY_TYPES = {
    "how-to": "how-to",
    "reference": "reference",
    "explanation": "explanation",
    "decisions": "decision",
}
PERSONAS = ("everyone", "ceo", "accountant", "finance_operator", "employee", "manager", "administrator")
LANGUAGES = ("en", "fr")


class FrontMatterError(ValueError):
    """The front matter is present but not in the supported subset."""


def _scalar(raw):
    value = raw.strip()
    if not value:
        return ""
    if value[0] in "\"'" and len(value) >= 2 and value[-1] == value[0]:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def split(text):
    """Return ``(front_matter_dict, body)``; ``({}, text)`` without a block."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != DELIMITER:
        return {}, text
    data = {}
    current_list = None
    for index in range(1, len(lines)):
        line = lines[index].rstrip("\r")
        if line == DELIMITER:
            body = "\n".join(lines[index + 1:])
            return data, body.lstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _ITEM_RE.match(line)
        if item:
            if current_list is None:
                raise FrontMatterError(f"List item outside a list on line {index + 1}: {line!r}")
            current_list.append(_scalar(item.group(1)))
            continue
        match = _KEY_RE.match(line)
        if not match:
            raise FrontMatterError(f"Unsupported front matter line {index + 1}: {line!r}")
        key, raw = match.group(1), match.group(2)
        if key in data:
            raise FrontMatterError(f"Duplicate front matter key {key!r}")
        if raw is None or not raw.strip():
            current_list = []
            data[key] = current_list
        else:
            current_list = None
            data[key] = _scalar(raw)
    raise FrontMatterError("Front matter block is not closed")


def _dump_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "" or text != text.strip() or text[0] in "\"'[{#&*!|>%@`" or ": " in text or text.lower() in {"true", "false"}:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def dump(data):
    """Serialise ``data`` as a front matter block, keys in the given order."""
    lines = [DELIMITER]
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {_dump_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_dump_scalar(value)}")
    lines.append(DELIMITER)
    return "\n".join(lines) + "\n"


def expected_type(relative_path):
    """Return the Diátaxis type the page's location implies, or ``None``."""
    parts = relative_path.replace("\\", "/").split("/")
    if len(parts) == 1:
        if parts[0] == "TUTORIAL.md":
            return "tutorial"
        return None
    return DIRECTORY_TYPES.get(parts[0])


def validate(data, relative_path):
    """Return a list of problems with a page's front matter (empty when fine)."""
    problems = []
    if not data:
        return ["missing front matter"]
    for key in ("title", "type", "description"):
        if not isinstance(data.get(key), str) or not data.get(key).strip():
            problems.append(f"{key} is required")
    page_type = data.get("type")
    if page_type not in TYPES:
        problems.append(f"type must be one of {', '.join(TYPES)}")
    implied = expected_type(relative_path)
    if implied and page_type in TYPES and page_type != implied:
        problems.append(f"type {page_type!r} does not match the directory ({implied!r})")
    if "lang" in data and data["lang"] not in LANGUAGES:
        problems.append(f"lang must be one of {', '.join(LANGUAGES)}")
    if "persona" in data and data["persona"] not in PERSONAS:
        problems.append(f"persona must be one of {', '.join(PERSONAS)}")
    if "generated" in data and not isinstance(data["generated"], bool):
        problems.append("generated must be true or false")
    if data.get("generated") and not data.get("journey") and page_type in {"tutorial", "how-to"}:
        problems.append("a generated tutorial or how-to names its journey")
    if "source" in data and (
        not isinstance(data["source"], list)
        or not all(isinstance(item, str) and item for item in data["source"])
    ):
        problems.append("source is a list of repository paths")
    return problems
