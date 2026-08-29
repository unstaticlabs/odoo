#!/usr/bin/env python3
"""Build and verify the non-secret identity of a USL Odoo release."""

# This is an operator CLI with concise literal failures and intentional output.
# ruff: noqa: EM101, T201

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC_REPOSITORY = re.compile(
    r'^sync_repo "(?P<name>[^"]+)" "(?P<url>[^"]+)" '
    r'"(?P<branch>[^"]+)" "(?P<commit>[0-9a-f]{40})"$',
)
PRODUCT_MODULES = {
    "rebuild_account_migration",
    "usl_access_control",
    "usl_accounting",
    "usl_b2c",
    "usl_documents",
    "usl_documents_accounting",
    "usl_documents_b2c",
    "usl_expense_batch",
    "usl_feedback",
    "usl_home",
    "usl_locale",
    "usl_platform_billing",
    "usl_platform_billing_pocketid",
    "usl_pocketid",
    "usl_project",
    "usl_tese_accounting",
    "usl_tese_payroll",
}
ACTION_RISK_POLICY_DIRECTORY = ROOT / "custom-addons/usl_access_control/policy"


class ReleaseIdentityError(RuntimeError):
    """A release input is absent, dirty, or inconsistent."""


def run(*command: str) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseIdentityError(
            f"Command failed ({' '.join(command)}): {detail}",
        )
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def action_risk_policy_sha256() -> str:
    """Return the canonical digest of the reviewed action surface and policy."""
    payload = {}
    for key, filename in (
        ("action_surface", "action_surface.json"),
        ("action_policy", "action_policy.json"),
    ):
        path = ACTION_RISK_POLICY_DIRECTORY / filename
        if not path.is_file():
            raise ReleaseIdentityError(
                f"Action-risk policy artifact is missing: {path}",
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ReleaseIdentityError(
                f"Action-risk policy artifact is invalid JSON: {path}: {error}",
            ) from error
        if key == "action_policy":
            if not isinstance(value, dict):
                raise ReleaseIdentityError(
                    f"Action-risk policy artifact must be an object: {path}",
                )
            value = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key != "qualified_policy_digest"
            }
        payload[key] = value
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def manifest(path: Path) -> dict[str, object]:
    return ast.literal_eval(path.read_text(encoding="utf-8"))


def product_module_versions() -> dict[str, str]:
    versions = {}
    for module_name in sorted(PRODUCT_MODULES):
        manifest_path = ROOT / "custom-addons" / module_name / "__manifest__.py"
        if not manifest_path.is_file():
            raise ReleaseIdentityError(
                f"Product module manifest is missing: {manifest_path}",
            )
        version = manifest(manifest_path).get("version")
        if not isinstance(version, str) or not version:
            raise ReleaseIdentityError(
                f"Product module {module_name} has no release version.",
            )
        versions[module_name] = version
    return versions


def expected_oca_pins() -> list[dict[str, str]]:
    pins = []
    sync_script = ROOT / "scripts" / "sync-oca-addons"
    for line in sync_script.read_text(encoding="utf-8").splitlines():
        match = SYNC_REPOSITORY.fullmatch(line.strip())
        if match:
            pins.append(match.groupdict())
    if not pins:
        raise ReleaseIdentityError("No OCA pins were found in sync-oca-addons.")
    return pins


def verified_oca_pins() -> list[dict[str, str]]:
    verified = []
    for pin in expected_oca_pins():
        checkout = ROOT / "oca-src" / pin["name"]
        if not (checkout / ".git").exists():
            raise ReleaseIdentityError(
                f"OCA checkout {pin['name']} is missing; run make oca-addons-sync.",
            )
        actual_commit = run("git", "-C", str(checkout), "rev-parse", "HEAD")
        if actual_commit != pin["commit"]:
            raise ReleaseIdentityError(
                f"OCA checkout {pin['name']} is {actual_commit}, expected {pin['commit']}.",
            )
        verified.append({**pin, "actual_commit": actual_commit})
    return verified


def oca_bundle_sha256() -> str:
    addons = ROOT / "oca-addons"
    source_root = (ROOT / "oca-src").resolve()
    digest = hashlib.sha256()
    modules = [path for path in addons.iterdir() if path.name != "README.md"]
    if not modules:
        raise ReleaseIdentityError(
            "The OCA runtime bundle is empty; run make oca-addons-sync.",
        )
    for module in sorted(modules, key=lambda path: path.name):
        resolved = module.resolve()
        if not resolved.is_dir() or not resolved.is_relative_to(source_root):
            raise ReleaseIdentityError(
                f"OCA runtime module {module.name} has an unsafe target.",
            )
        for path in sorted(resolved.rglob("*")):
            if (
                not path.is_file()
                or ".git" in path.parts
                or "__pycache__" in path.parts
                or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            relative = f"{module.name}/{path.relative_to(resolved).as_posix()}"
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def image_identity(
    reference: str,
    commit: str,
    oca_digest: str,
    action_risk_digest: str,
) -> dict[str, object]:
    raw = run("docker", "image", "inspect", reference)
    payload = json.loads(raw)
    if not isinstance(payload, list) or len(payload) != 1:
        raise ReleaseIdentityError(f"Docker returned an ambiguous image: {reference}")
    image = payload[0]
    labels = image.get("Config", {}).get("Labels") or {}
    expected_labels = {
        "org.opencontainers.image.revision": commit,
        "com.unstaticlabs.odoo.oca-bundle-sha256": oca_digest,
        "com.unstaticlabs.odoo.action-risk-policy-sha256": action_risk_digest,
        "com.unstaticlabs.odoo.runtime": "distribution",
    }
    for key, expected in expected_labels.items():
        if labels.get(key) != expected:
            raise ReleaseIdentityError(
                f"Image {reference} label {key} is {labels.get(key)!r}, expected {expected!r}.",
            )
    if reference.endswith(":latest") or ":" not in reference:
        raise ReleaseIdentityError("The release image must use an immutable commit tag.")
    return {
        "reference": reference,
        "id": image.get("Id"),
        "repo_digests": sorted(image.get("RepoDigests") or []),
        "labels": expected_labels,
    }


def build_identity(
    source_directory: Path,
    *,
    image: str | None = None,
    require_clean: bool = False,
) -> dict[str, object]:
    source_directory = source_directory.expanduser().resolve()
    dump = source_directory / "dump.sql"
    filestore = source_directory / "filestore"
    if not dump.is_file() or not filestore.is_dir():
        raise ReleaseIdentityError(
            f"Source package is incomplete under {source_directory}.",
        )
    status = run("git", "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise ReleaseIdentityError(
            "Release identity requires a clean tracked checkout.",
        )
    commit = run("git", "rev-parse", "HEAD")
    oca_pins = verified_oca_pins()
    oca_digest = oca_bundle_sha256()
    action_risk_digest = action_risk_policy_sha256()
    dump_sha256 = sha256_file(dump)
    try:
        upstream_commit = run(
            "git",
            "merge-base",
            "HEAD",
            "upstream/saas-19.3",
        )
    except ReleaseIdentityError:
        upstream_commit = None
    identity: dict[str, object] = {
        "schema": "usl-release-identity-v1",
        "release_commit": commit,
        "release_ref": run("git", "branch", "--show-current") or "detached",
        "tree_clean": not bool(status),
        "upstream_saas_19_3_commit": upstream_commit,
        "source": {
            "snapshot": f"source-{dump_sha256[:12]}",
            "dump_sha256": dump_sha256,
            "dump_size": dump.stat().st_size,
        },
        "oca": {
            "bundle_sha256": oca_digest,
            "repositories": oca_pins,
        },
        "action_risk_policy_sha256": action_risk_digest,
        "product_module_versions": product_module_versions(),
    }
    if image:
        identity["image"] = image_identity(
            image,
            commit,
            oca_digest,
            action_risk_digest,
        )
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    identity["identity_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--image")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--field")
    args = parser.parse_args()
    try:
        identity = build_identity(
            args.source_dir,
            image=args.image,
            require_clean=args.require_clean,
        )
    except (ReleaseIdentityError, OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(identity, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.field:
        value: object = identity
        for component in args.field.split("."):
            if not isinstance(value, dict) or component not in value:
                parser.error(f"Unknown identity field: {args.field}")
            value = value[component]
        if not isinstance(value, (str, int, float, bool)):
            parser.error(f"Identity field is not scalar: {args.field}")
        print(value)
    else:
        print(json.dumps(identity, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
