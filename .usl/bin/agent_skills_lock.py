#!/usr/bin/env python3
"""Define the agent skills manifest and lock, and verify a repository against its lock.

Consumer repositories carry a verbatim copy of this file at .usl/bin/agent_skills_lock.py.
It uses only the standard library and needs neither the network nor the hub.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = 1
MANIFEST_PATH = ".usl/agent-skills.json"
LOCK_PATH = ".usl/agent-skills.lock.json"
VERIFIER_PATH = ".usl/bin/agent_skills_lock.py"
AGENTS = ("claude", "codex")
VISIBILITIES = ("private", "public")
FINDING_CODES = ("manifest-drift", "missing", "extra", "hash", "link", "verifier")
IGNORED_NAMES = frozenset({".DS_Store"})

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
REVISION = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+")

MANIFEST_KEYS = ("schema", "hub", "visibility", "agents", "skills", "bundles")
LOCK_KEYS = ("schema", "hub", "manifest_sha256", "verifier_sha256", "skills", "links")
LOCK_HUB_KEYS = ("repository", "revision")
LOCKED_SKILL_KEYS = ("path", "version", "risk", "status", "agents", "files")


class FormatError(ValueError):
    """A manifest or lock that is not valid schema 1."""


@dataclass(frozen=True)
class Manifest:
    hub: str
    visibility: str
    agents: tuple[str, ...]
    skills: tuple[str, ...]
    bundles: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.to_data())).hexdigest()

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "hub": self.hub,
            "visibility": self.visibility,
            "agents": list(self.agents),
            "skills": list(self.skills),
            "bundles": list(self.bundles),
        }


@dataclass(frozen=True)
class LockedSkill:
    path: str
    version: str
    risk: str
    status: str
    agents: tuple[str, ...]
    files: dict[str, str]

    def to_data(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "version": self.version,
            "risk": self.risk,
            "status": self.status,
            "agents": list(self.agents),
            "files": dict(self.files),
        }


@dataclass(frozen=True)
class Lock:
    repository: str
    revision: str
    manifest_sha256: str
    verifier_sha256: str
    skills: dict[str, LockedSkill]
    links: dict[str, str]

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "hub": {"repository": self.repository, "revision": self.revision},
            "manifest_sha256": self.manifest_sha256,
            "verifier_sha256": self.verifier_sha256,
            "skills": {name: skill.to_data() for name, skill in self.skills.items()},
            "links": dict(self.links),
        }


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.path}: {self.message}"


def canonical_json(value: Any) -> bytes:
    """Serialize with sorted keys, two-space indent, and a trailing newline."""

    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> Manifest:
    data = _read_schema_object(path, MANIFEST_KEYS, "manifest")
    if data["visibility"] not in VISIBILITIES:
        raise _error(path, f"visibility must be one of {', '.join(VISIBILITIES)}")
    return Manifest(
        hub=_string(path, "hub", data["hub"], REPOSITORY),
        visibility=data["visibility"],
        agents=_names(path, "agents", data["agents"], allowed=AGENTS),
        skills=_names(path, "skills", data["skills"]),
        bundles=_names(path, "bundles", data["bundles"]),
    )


def load_lock(path: Path) -> Lock:
    data = _read_schema_object(path, LOCK_KEYS, "lock")
    hub = _keys(path, "hub", data["hub"], LOCK_HUB_KEYS)
    skills = {name: _locked_skill(path, name, entry) for name, entry in _mapping(path, "skills", data["skills"]).items()}
    links = {
        _relative_path(path, "links", link): _link_target(path, link, target)
        for link, target in _mapping(path, "links", data["links"]).items()
    }
    _require_disjoint(path, [skill.path for skill in skills.values()] + list(links))
    return Lock(
        repository=_string(path, "hub.repository", hub["repository"], REPOSITORY),
        revision=_string(path, "hub.revision", hub["revision"], REVISION),
        manifest_sha256=_string(path, "manifest_sha256", data["manifest_sha256"], SHA256),
        verifier_sha256=_string(path, "verifier_sha256", data["verifier_sha256"], SHA256),
        skills=skills,
        links=links,
    )


def verify(root: Path) -> list[Finding]:
    """Compare the tree under root with its lock.

    Raises FormatError for an invalid manifest or lock, and OSError when either is unreadable.
    """

    lock = load_lock(root / LOCK_PATH)
    manifest = load_manifest(root / MANIFEST_PATH)
    findings: list[Finding] = []
    if manifest.sha256 != lock.manifest_sha256:
        findings.append(Finding("manifest-drift", MANIFEST_PATH, "changed since the lock was written"))
    findings.extend(_verify_verifier(root, lock))
    for skill in lock.skills.values():
        findings.extend(_verify_skill(root, skill))
    for link, target in lock.links.items():
        findings.extend(_verify_link(root, link, target))
    return sorted(findings, key=lambda finding: (finding.path, finding.code))


def _verify_verifier(root: Path, lock: Lock) -> list[Finding]:
    path = root / VERIFIER_PATH
    if path.is_symlink() or not path.is_file():
        return [Finding("verifier", VERIFIER_PATH, "missing; expected a verbatim copy of the hub's verifier")]
    if hash_file(path) != lock.verifier_sha256:
        return [Finding("verifier", VERIFIER_PATH, "differs from the copy the lock pins")]
    return []


def _verify_skill(root: Path, skill: LockedSkill) -> list[Finding]:
    parts = skill.path.split("/")
    for depth in range(1, len(parts) + 1):
        prefix = "/".join(parts[:depth])
        if (root / prefix).is_symlink():
            return [Finding("missing", skill.path, f"expected real directories, found a link at {prefix}")]
    directory = root / skill.path
    if not directory.is_dir():
        return [Finding("missing", skill.path, "locked skill directory is missing")]

    entries = _entries(directory)
    findings: list[Finding] = []
    for relative, digest in skill.files.items():
        location = f"{skill.path}/{relative}"
        regular = entries.pop(relative, None)
        if regular is None:
            findings.append(Finding("missing", location, "locked file is missing"))
        elif not regular:
            findings.append(Finding("missing", location, "expected a regular file"))
        elif hash_file(directory / relative) != digest:
            findings.append(Finding("hash", location, "content differs from the lock"))
    findings.extend(Finding("extra", f"{skill.path}/{relative}", "not listed in the lock") for relative in entries)
    return findings


def _verify_link(root: Path, link: str, target: str) -> list[Finding]:
    path = root / link
    if not path.is_symlink():
        found = "nothing" if not os.path.lexists(path) else "a directory" if path.is_dir() else "a file"
        return [Finding("link", link, f"expected a link to {target}, found {found}")]
    actual = os.readlink(path)
    if actual != target:
        return [Finding("link", link, f"points to {actual}, expected {target}")]
    if not path.is_dir():
        return [Finding("link", link, f"target {target} is not a directory")]
    return []


def _entries(directory: Path) -> dict[str, bool]:
    """Map each non-directory entry under directory to whether it is a regular file."""

    entries: dict[str, bool] = {}
    for current, dirnames, filenames in os.walk(directory):
        base = Path(current)
        for name in list(dirnames):
            if (base / name).is_symlink():
                dirnames.remove(name)
                entries[(base / name).relative_to(directory).as_posix()] = False
        for name in filenames:
            if name not in IGNORED_NAMES:
                path = base / name
                entries[path.relative_to(directory).as_posix()] = path.is_file() and not path.is_symlink()
    return entries


def _error(path: Path, message: str) -> FormatError:
    return FormatError(f"{path}: {message}")


def _read_schema_object(path: Path, keys: tuple[str, ...], kind: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except json.JSONDecodeError as exc:
        raise _error(path, f"invalid JSON: {exc}") from None
    except ValueError as exc:
        raise _error(path, str(exc)) from None
    schema = data.get("schema") if isinstance(data, dict) else None
    if type(schema) is not int or schema != SCHEMA:
        raise _error(path, f'not a schema {SCHEMA} agent skills {kind}; expected "schema": {SCHEMA}')
    return _keys(path, kind, data, keys)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _keys(path: Path, where: str, value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, f"{where} must be an object")
    missing = [key for key in keys if key not in value]
    unknown = sorted(set(value) - set(keys))
    if missing:
        raise _error(path, f"{where} is missing {', '.join(missing)}")
    if unknown:
        raise _error(path, f"{where} has unknown keys {', '.join(unknown)}")
    return value


def _mapping(path: Path, where: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, f"{where} must be an object")
    return value


def _string(path: Path, where: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _error(path, f"{where} is invalid: {value!r}")
    return value


def _names(path: Path, where: str, value: Any, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _error(path, f"{where} must be a list of strings")
    if allowed is not None and not value:
        raise _error(path, f"{where} must not be empty")
    if len(set(value)) != len(value):
        raise _error(path, f"{where} has duplicates")
    for item in value:
        valid = item in allowed if allowed is not None else NAME.fullmatch(item) is not None
        if not valid:
            raise _error(path, f"{where} has an invalid entry: {item!r}")
    return tuple(value)


def _relative_path(path: Path, where: str, value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise _error(path, f"{where} has an unsafe path: {value!r}")
    return value


def _link_target(path: Path, link: str, target: Any) -> str:
    if not isinstance(target, str) or not target or target.startswith("/") or "\\" in target:
        raise _error(path, f"links.{link} must be a relative link target")
    return target


def _locked_skill(path: Path, name: str, entry: Any) -> LockedSkill:
    if not NAME.fullmatch(name):
        raise _error(path, f"skills has an invalid name: {name!r}")
    where = f"skills.{name}"
    entry = _keys(path, where, entry, LOCKED_SKILL_KEYS)
    files = _mapping(path, f"{where}.files", entry["files"])
    if not files:
        raise _error(path, f"{where}.files must not be empty")
    return LockedSkill(
        path=_relative_path(path, f"{where}.path", entry["path"]),
        version=_string(path, f"{where}.version", entry["version"], VERSION),
        risk=_string(path, f"{where}.risk", entry["risk"], NAME),
        status=_string(path, f"{where}.status", entry["status"], NAME),
        agents=_names(path, f"{where}.agents", entry["agents"], allowed=AGENTS),
        files={
            _relative_path(path, f"{where}.files", relative): _string(path, f"{where}.files.{relative}", digest, SHA256)
            for relative, digest in files.items()
        },
    )


def _require_disjoint(path: Path, locations: list[str]) -> None:
    ordered = sorted(locations, key=lambda location: location.split("/"))
    for first, second in zip(ordered, ordered[1:]):
        if second == first or second.startswith(f"{first}/"):
            raise _error(path, f"locked paths overlap: {first} and {second}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    verify_command = commands.add_parser("verify", help="check hub skills against the lock")
    verify_command.add_argument("--root", type=Path, default=Path("."), help="repository root (default: current directory)")
    args = parser.parse_args(argv)

    try:
        lock = load_lock(args.root / LOCK_PATH)
        findings = verify(args.root)
    except FileNotFoundError as exc:
        print(f"agent skills: {exc.filename}: not found", file=sys.stderr)
        return 1
    except (OSError, FormatError) as exc:
        print(f"agent skills: {exc}", file=sys.stderr)
        return 1

    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return 1
    print(f"agent skills OK: {len(lock.skills)} skills at {lock.revision[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
