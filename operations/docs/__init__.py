"""The documentation tooling behind ``scripts/docs``.

Every page the user guide ships is either written by hand or derived from
something the product already knows: a browser journey, the registry of a
built database, the catalogue of the pinned MCP server. One package holds
the derivations and the check that proves the committed pages are current,
so the rules live in one place and ``scripts/docs`` is the one entry point.

- ``check``: well-formed pages, resolvable links, numbered decision records.
- ``generate``: journey records into how-to pages and screenshots.
- ``reference``: the registry facts into the generated reference pages.
- ``mcp``: the pinned odoo-mcp tool catalogue into one reference page.
- ``evidence``: the docs evidence a qualification run produces.
- ``cli``: the argparse dispatcher ``scripts/docs`` wraps.

The front matter parser is the module the Odoo viewer uses, loaded by path
so the runtime and the repository share one definition.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
USERS = DOCS / "users"
DECISIONS = DOCS / "product" / "decisions"
FRONTMATTER_MODULE = ROOT / "custom-addons" / "usl_docs" / "tools" / "frontmatter.py"


def frontmatter():
    spec = importlib.util.spec_from_file_location("usl_docs_frontmatter", FRONTMATTER_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
