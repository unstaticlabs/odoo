"""Content identities shared by reconstruction, candidates, and cohorts."""

from __future__ import annotations

import hashlib
from pathlib import Path


MIGRATION_INPUTS = (
    "Dockerfile",
    "accounting_compat",
    "compose.yaml",
    "compose.external-pocket-id.yaml",
    "compose.ollama-native.yaml",
    "compose.pocket-id.yaml",
    "compose.production.yaml",
    "custom-addons",
    "deploy/documents",
    "migration",
    "oca-patches",
    "scripts/odoo",
)
IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "private",
    "tests",
}
IGNORED_SUFFIXES = {".log", ".md", ".pyc"}


class DigestError(ValueError):
    """Raised when identity material is missing or unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(path: Path) -> list[Path]:
    if path.is_symlink():
        raise DigestError(f"identity paths may not be symlinks: {path}")
    if path.is_file():
        return [path]
    files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise DigestError(f"identity paths may not be symlinks: {item}")
        if item.is_file():
            files.append(item)
    return sorted(files, key=lambda item: item.relative_to(path).as_posix())


def tree_digest(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_size = 0
    files = iter_files(path)
    for item in files:
        relative = item.relative_to(path).as_posix().encode()
        content_sha = sha256_file(item).encode()
        size = item.stat().st_size
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        digest.update(content_sha)
        total_size += size
    return digest.hexdigest(), len(files), total_size


def migration_digest(root: Path) -> str:
    root = root.resolve()
    files: list[Path] = []
    for relative in MIGRATION_INPUTS:
        path = root / relative
        if not path.exists():
            raise DigestError(f"migration identity input is missing: {relative}")
        candidates = [path] if path.is_file() else path.rglob("*")
        files.extend(
            item
            for item in candidates
            if item.is_file()
            and not set(item.relative_to(root).parts).intersection(IGNORED_PARTS)
            and item.suffix not in IGNORED_SUFFIXES
        )
    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
