"""Validate an untrusted PDF in a resource-constrained subprocess."""

from __future__ import annotations

import json
import resource
import sys

import pikepdf


FORBIDDEN_KEYS = {
    "/AA",
    "/EmbeddedFiles",
    "/JavaScript",
    "/JS",
    "/Launch",
    "/OpenAction",
    "/RichMedia",
    "/XFA",
}
FORBIDDEN_ACTION_TYPES = {
    "/GoToE",
    "/GoToR",
    "/ImportData",
    "/JavaScript",
    "/Launch",
    "/Movie",
    "/Rendition",
    "/Sound",
    "/SubmitForm",
}


def _limit_process(max_bytes: int) -> None:
    memory = max(256 * 1024 * 1024, max_bytes * 16)
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _inspect(path: str, max_bytes: int) -> None:
    _limit_process(max_bytes)
    decompressed = 0
    visited = set()

    def inspect_object(obj) -> None:
        nonlocal decompressed
        objgen = getattr(obj, "objgen", (0, 0))
        marker = ("indirect", objgen) if objgen != (0, 0) else ("direct", id(obj))
        if marker in visited:
            return
        visited.add(marker)

        if isinstance(obj, (pikepdf.Stream, pikepdf.Dictionary)):
            keys = {str(key) for key in obj.keys()}
            action_type = str(obj.get("/S", ""))
            if keys & FORBIDDEN_KEYS or action_type in FORBIDDEN_ACTION_TYPES:
                raise ValueError("active_content")

        if isinstance(obj, pikepdf.Stream):
            decompressed += len(obj.read_bytes())
            if decompressed > max_bytes:
                raise ValueError("decompressed_size")
            for _key, value in obj.items():
                inspect_object(value)
        elif isinstance(obj, pikepdf.Dictionary):
            for _key, value in obj.items():
                inspect_object(value)
        elif isinstance(obj, pikepdf.Array):
            for value in obj:
                inspect_object(value)

    with pikepdf.open(path) as pdf:
        if pdf.is_encrypted:
            raise ValueError("encrypted_pdf")
        for obj in pdf.objects:
            inspect_object(obj)
        inspect_object(pdf.trailer)


def main() -> int:
    try:
        path, raw_max = sys.argv[1:3]
        _inspect(path, int(raw_max))
    except (IndexError, ValueError, pikepdf.PdfError) as error:
        code = str(error) if str(error) in {
            "active_content",
            "decompressed_size",
            "encrypted_pdf",
        } else "invalid_pdf"
        print(json.dumps({"ok": False, "code": code}))
        return 2
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
