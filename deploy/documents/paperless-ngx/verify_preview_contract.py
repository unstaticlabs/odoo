"""Fail the Paperless image build when preview call sites drift from serve_file."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def verify(path: Path) -> tuple[bool, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "serve_file"
    ]
    if len(definitions) != 1:
        raise RuntimeError("Paperless preview contract requires one serve_file definition")
    accepts_request = any(
        argument.arg == "request" for argument in definitions[0].args.kwonlyargs
    )
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "serve_file"
    ]
    if len(calls) < 3:
        raise RuntimeError("Paperless preview contract found too few serve_file call sites")
    for call in calls:
        passes_request = any(keyword.arg == "request" for keyword in call.keywords)
        if passes_request != accepts_request:
            raise RuntimeError(
                "Paperless serve_file request signature and call sites differ"
            )
    return accepts_request, len(calls)


if __name__ == "__main__":
    source = Path(
        sys.argv[1] if len(sys.argv) > 1 else "/usr/src/paperless/src/documents/views.py"
    )
    accepts_request, call_count = verify(source)
    print(
        "Paperless preview contract passed: "
        f"request={accepts_request} call_sites={call_count}"
    )
