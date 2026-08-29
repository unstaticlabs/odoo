#!/usr/bin/env python3
"""Create and validate the immutable multi-artifact USL release contract."""

# ruff: noqa: EM101, T201 - release CLI reports concise literal failures.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_BUILD_PLAN_SCHEMA,
    ARTIFACT_ROLES,
    DIGEST,
    RELEASE_SCHEMA,
    UPGRADE_SCHEMA,
    ContractError,
    canonical_sha256,
    exact_keys,
    validate_commit,
    validate_sha256,
    verify_checksum,
    with_checksum,
)
from release_identity import (  # noqa: E402
    PRODUCT_MODULES,
    expected_oca_pins,
    product_module_versions,
)

SCHEMA = RELEASE_SCHEMA
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._/-]*")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ARTIFACT_NAMES = {
    "odoo_distribution": "ghcr.io/unstaticlabs/usl-odoo",
    "operations_tool": "ghcr.io/unstaticlabs/usl-odoo-operations",
    "paperless_overlay": "ghcr.io/unstaticlabs/usl-paperless-ngx",
    "document_renderer": "ghcr.io/unstaticlabs/usl-document-renderer",
    "native_sign_dss": "ghcr.io/unstaticlabs/usl-sign-dss",
}


class ReleaseArtifactError(ContractError):
    """The release artifact is malformed or refers to another build."""


def _release_error(error: ContractError) -> ReleaseArtifactError:
    return ReleaseArtifactError(str(error))


def _identity(image: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            key: image[key]
            for key in (
                "name",
                "tag",
                "digest",
                "digest_reference",
                "source_commit_sha",
                "attestations",
            )
        },
    )


def _validate_image(
    value: object,
    *,
    role: str,
    commit: str,
    prior_release: dict[str, Any] | None,
    prior_identity: dict[str, Any] | None,
    historical: bool,
) -> dict[str, Any]:
    try:
        image = exact_keys(
            value,
            {
                "name",
                "tag",
                "digest",
                "digest_reference",
                "source_commit_sha",
                "identity_sha256",
                "origin",
                "attestations",
            },
            f"artifacts.{role}",
        )
    except ContractError as error:
        raise _release_error(error) from error
    expected_name = ARTIFACT_NAMES[role]
    if image["name"] != expected_name or not IMAGE.fullmatch(str(image["name"])):
        raise ReleaseArtifactError(f"artifacts.{role}.name must be {expected_name!r}")
    try:
        artifact_commit = validate_commit(
            image["source_commit_sha"], f"artifacts.{role}.source_commit_sha",
        )
    except ContractError as error:
        raise _release_error(error) from error
    expected_tag = f"sha-{artifact_commit}"
    if image["tag"] != expected_tag:
        raise ReleaseArtifactError(f"artifacts.{role}.tag must be {expected_tag!r}")
    if not isinstance(image["digest"], str) or not DIGEST.fullmatch(image["digest"]):
        raise ReleaseArtifactError(
            f"artifacts.{role}.digest must be a lowercase sha256 digest",
        )
    expected_reference = f"{expected_name}@{image['digest']}"
    if image["digest_reference"] != expected_reference:
        raise ReleaseArtifactError(
            f"artifacts.{role}.digest_reference must be {expected_reference!r}",
        )
    try:
        attestations = exact_keys(
            image["attestations"],
            {"oci_sbom", "buildkit_provenance", "github_provenance"},
            f"artifacts.{role}.attestations",
        )
    except ContractError as error:
        raise _release_error(error) from error
    for key, attestation in attestations.items():
        try:
            attestation = exact_keys(
                attestation,
                {"status", "subject_digest"},
                f"artifacts.{role}.attestations.{key}",
            )
        except ContractError as error:
            raise _release_error(error) from error
        if attestation != {"status": "generated", "subject_digest": image["digest"]}:
            raise ReleaseArtifactError(
                f"artifacts.{role}.attestations.{key} must bind the artifact digest",
            )
    try:
        validate_sha256(image["identity_sha256"], f"artifacts.{role}.identity_sha256")
    except ContractError as error:
        raise _release_error(error) from error
    if image["identity_sha256"] != _identity(image):
        raise ReleaseArtifactError(f"artifacts.{role}.identity_sha256 does not match")

    if not isinstance(image["origin"], dict):
        raise ReleaseArtifactError(f"artifacts.{role}.origin must be an object")
    kind = image["origin"].get("kind")
    try:
        if kind == "built_for_release":
            origin = exact_keys(
                image["origin"],
                {"kind", "release_commit_sha"},
                f"artifacts.{role}.origin",
            )
            if origin["release_commit_sha"] != commit or artifact_commit != commit:
                raise ReleaseArtifactError(
                    f"artifacts.{role} built origin must match the release source",
                )
        elif kind == "reused_from_release":
            origin = exact_keys(
                image["origin"],
                {
                    "kind",
                    "release_commit_sha",
                    "prior_release_contract_sha256",
                    "prior_release_source_commit_sha",
                },
                f"artifacts.{role}.origin",
            )
            validate_sha256(
                origin["prior_release_contract_sha256"],
                f"artifacts.{role}.origin.prior_release_contract_sha256",
            )
            validate_commit(
                origin["prior_release_source_commit_sha"],
                f"artifacts.{role}.origin.prior_release_source_commit_sha",
            )
            if origin["release_commit_sha"] != commit:
                raise ReleaseArtifactError(
                    f"artifacts.{role} reuse must identify the current release",
                )
            if prior_identity is None or (
                origin["prior_release_contract_sha256"]
                != prior_identity["contract_sha256"]
                or origin["prior_release_source_commit_sha"]
                != prior_identity["source_commit_sha"]
            ):
                raise ReleaseArtifactError(
                    f"artifacts.{role} reuse differs from the release prior identity",
                )
            if not historical:
                if prior_release is None:
                    raise ReleaseArtifactError(
                        f"artifacts.{role} reuse requires the complete validated prior release",
                    )
                if origin["prior_release_contract_sha256"] != prior_release["contract_sha256"]:
                    raise ReleaseArtifactError(
                        f"artifacts.{role} prior release checksum differs",
                    )
                if origin["prior_release_source_commit_sha"] != prior_release["source"]["commit_sha"]:
                    raise ReleaseArtifactError(
                        f"artifacts.{role} prior release source differs",
                    )
                prior_artifact = prior_release["artifacts"][role]
                if image["identity_sha256"] != prior_artifact["identity_sha256"]:
                    raise ReleaseArtifactError(
                        f"artifacts.{role} reuse identity differs from the prior release",
                    )
                for field in (
                    "name", "tag", "digest", "digest_reference",
                    "source_commit_sha", "attestations",
                ):
                    if image[field] != prior_artifact[field]:
                        raise ReleaseArtifactError(
                            f"artifacts.{role}.{field} differs from the prior release",
                        )
        else:
            raise ReleaseArtifactError(f"artifacts.{role}.origin.kind is unsupported")
    except ContractError as error:
        raise _release_error(error) from error
    return image


def _validate_artifact_plan(
    value: object, *, commit: str, prior_commit: str | None,
) -> dict[str, Any]:
    try:
        plan = exact_keys(
            value,
            {
                "schema", "from_commit_sha", "to_commit_sha", "mode", "reason",
                "changed_paths", "build_roles", "reuse_roles",
            },
            "artifact_plan",
        )
    except ContractError as error:
        raise _release_error(error) from error
    if plan["schema"] != ARTIFACT_BUILD_PLAN_SCHEMA:
        raise ReleaseArtifactError("artifact_plan.schema is unsupported")
    if plan["from_commit_sha"] != prior_commit or plan["to_commit_sha"] != commit:
        raise ReleaseArtifactError("artifact_plan source range differs from the release")
    if plan["mode"] not in {"build_all", "selective"}:
        raise ReleaseArtifactError("artifact_plan.mode is invalid")
    for key in ("changed_paths", "build_roles", "reuse_roles"):
        if (
            not isinstance(plan[key], list)
            or not all(isinstance(item, str) for item in plan[key])
            or plan[key] != sorted(set(plan[key]))
        ):
            raise ReleaseArtifactError(f"artifact_plan.{key} must be sorted and unique")
    build = set(plan["build_roles"])
    reuse = set(plan["reuse_roles"])
    if build | reuse != set(ARTIFACT_ROLES) or build & reuse:
        raise ReleaseArtifactError("artifact_plan must partition every artifact role")
    if plan["mode"] == "build_all" and build != set(ARTIFACT_ROLES):
        raise ReleaseArtifactError("build_all must include every artifact role")
    if reuse and prior_commit is None:
        raise ReleaseArtifactError("artifact reuse requires a prior release")
    if not isinstance(plan["reason"], str) or not plan["reason"]:
        raise ReleaseArtifactError("artifact_plan.reason is required")
    return plan


def _validate_upgrade_plan(
    value: object, *, commit: str, modules: set[str],
) -> dict[str, Any]:
    try:
        plan = exact_keys(
            value,
            {
                "schema",
                "from_commit_sha",
                "to_commit_sha",
                "mode",
                "reason",
                "changed_modules",
                "upgrade_modules",
                "foundation_paths",
            },
            "upgrade_plan",
        )
    except ContractError as error:
        raise _release_error(error) from error
    if plan["schema"] != UPGRADE_SCHEMA:
        raise ReleaseArtifactError("upgrade_plan.schema is unsupported")
    if plan["from_commit_sha"] is not None:
        try:
            validate_commit(plan["from_commit_sha"], "upgrade_plan.from_commit_sha")
        except ContractError as error:
            raise _release_error(error) from error
    if plan["to_commit_sha"] != commit:
        raise ReleaseArtifactError("upgrade_plan.to_commit_sha must match source")
    if plan["mode"] not in {"none", "dependency_closure", "full_fallback"}:
        raise ReleaseArtifactError("upgrade_plan.mode is invalid")
    for key in ("changed_modules", "upgrade_modules", "foundation_paths"):
        if not isinstance(plan[key], list) or plan[key] != sorted(set(plan[key])):
            raise ReleaseArtifactError(
                f"upgrade_plan.{key} must be a sorted unique list",
            )
    changed = set(plan["changed_modules"])
    upgrades = set(plan["upgrade_modules"])
    if not changed <= modules or not upgrades <= modules:
        raise ReleaseArtifactError(
            "upgrade_plan names a module outside the canonical perimeter",
        )
    if plan["mode"] == "full_fallback" and upgrades != modules:
        raise ReleaseArtifactError(
            "full_fallback must upgrade the entire canonical perimeter",
        )
    if plan["mode"] == "dependency_closure" and (
        not changed or not changed <= upgrades
    ):
        raise ReleaseArtifactError(
            "dependency_closure must include every changed module",
        )
    if plan["mode"] == "none" and (changed or upgrades):
        raise ReleaseArtifactError("none mode cannot contain module upgrades")
    if not isinstance(plan["reason"], str) or not plan["reason"]:
        raise ReleaseArtifactError("upgrade_plan.reason is required")
    return plan


def validate(
    payload: object,
    *,
    commit: str | None = None,
    prior_release: dict[str, Any] | None = None,
    historical: bool = False,
) -> dict[str, Any]:
    try:
        root = exact_keys(
            payload,
            {
                "schema",
                "source",
                "artifacts",
                "product",
                "component_sources",
                "build",
                "artifact_plan",
                "upgrade_plan",
                "prior_release",
                "contract_sha256",
            },
            "artifact",
        )
    except ContractError as error:
        raise _release_error(error) from error
    if root["schema"] != SCHEMA:
        raise ReleaseArtifactError(f"unsupported schema: {root['schema']!r}")
    try:
        verify_checksum(root, "artifact")
    except ContractError as error:
        raise _release_error(error) from error
    try:
        source = exact_keys(root["source"], {"repository", "commit_sha"}, "source")
        source_commit = validate_commit(source["commit_sha"], "source.commit_sha")
    except ContractError as error:
        raise _release_error(error) from error
    if not isinstance(source["repository"], str) or not REPOSITORY.fullmatch(
        source["repository"],
    ):
        raise ReleaseArtifactError("source.repository must be owner/name")
    if commit is not None and source_commit != commit:
        raise ReleaseArtifactError(
            "artifact commit does not match the requested commit",
        )

    prior_identity = root["prior_release"]
    prior_commit: str | None = None
    if prior_identity is not None:
        try:
            prior_identity = exact_keys(
                prior_identity,
                {"source_commit_sha", "contract_sha256"},
                "prior_release",
            )
            prior_commit = validate_commit(
                prior_identity["source_commit_sha"], "prior_release.source_commit_sha",
            )
            validate_sha256(
                prior_identity["contract_sha256"], "prior_release.contract_sha256",
            )
        except ContractError as error:
            raise _release_error(error) from error
    if prior_release is not None:
        prior_release = validate(prior_release, historical=True)
        expected_prior = {
            "source_commit_sha": prior_release["source"]["commit_sha"],
            "contract_sha256": prior_release["contract_sha256"],
        }
        if prior_identity != expected_prior:
            raise ReleaseArtifactError("prior_release identity differs from the supplied contract")

    artifact_plan = _validate_artifact_plan(
        root["artifact_plan"], commit=source_commit, prior_commit=prior_commit,
    )
    try:
        artifacts = exact_keys(root["artifacts"], set(ARTIFACT_ROLES), "artifacts")
    except ContractError as error:
        raise _release_error(error) from error
    for role in ARTIFACT_ROLES:
        image = _validate_image(
            artifacts[role],
            role=role,
            commit=source_commit,
            prior_release=prior_release,
            prior_identity=prior_identity,
            historical=historical,
        )
        expected_kind = (
            "built_for_release"
            if role in artifact_plan["build_roles"]
            else "reused_from_release"
        )
        if image["origin"]["kind"] != expected_kind:
            raise ReleaseArtifactError(
                f"artifacts.{role}.origin disagrees with artifact_plan",
            )

    try:
        product = exact_keys(
            root["product"], {"modules", "oca", "action_risk"}, "product",
        )
    except ContractError as error:
        raise _release_error(error) from error
    if not isinstance(product["modules"], list) or not product["modules"]:
        raise ReleaseArtifactError("product.modules must be a non-empty list")
    module_names: list[str] = []
    for index, item in enumerate(product["modules"]):
        try:
            module = exact_keys(item, {"name", "version"}, f"product.modules[{index}]")
        except ContractError as error:
            raise _release_error(error) from error
        if not all(
            isinstance(module[key], str) and module[key] for key in ("name", "version")
        ):
            raise ReleaseArtifactError(
                f"product.modules[{index}] values must be non-empty strings",
            )
        module_names.append(module["name"])
    if module_names != sorted(set(module_names)):
        raise ReleaseArtifactError("product.modules must be sorted and unique")
    if not historical and module_names != sorted(PRODUCT_MODULES):
        raise ReleaseArtifactError(
            "product.modules must be the sorted canonical product perimeter",
        )
    try:
        oca = exact_keys(
            product["oca"], {"bundle_sha256", "repositories"}, "product.oca",
        )
        validate_sha256(oca["bundle_sha256"], "product.oca.bundle_sha256")
    except ContractError as error:
        raise _release_error(error) from error
    if not isinstance(oca["repositories"], list) or not oca["repositories"]:
        raise ReleaseArtifactError("product.oca.repositories must be a non-empty list")
    for index, item in enumerate(oca["repositories"]):
        try:
            pin = exact_keys(
                item,
                {"name", "url", "branch", "commit"},
                f"product.oca.repositories[{index}]",
            )
            validate_commit(pin["commit"], f"product.oca.repositories[{index}].commit")
        except ContractError as error:
            raise _release_error(error) from error
    try:
        action_risk = exact_keys(
            product["action_risk"], {"policy_sha256"}, "product.action_risk",
        )
        validate_sha256(
            action_risk["policy_sha256"], "product.action_risk.policy_sha256",
        )
    except ContractError as error:
        raise _release_error(error) from error

    try:
        component_sources = exact_keys(
            root["component_sources"], {"document_renderer"}, "component_sources",
        )
        renderer = exact_keys(
            component_sources["document_renderer"],
            {"repository", "commit_sha"},
            "component_sources.document_renderer",
        )
        validate_commit(
            renderer["commit_sha"], "component_sources.document_renderer.commit_sha",
        )
    except ContractError as error:
        raise _release_error(error) from error
    if not isinstance(renderer["repository"], str) or not REPOSITORY.fullmatch(
        renderer["repository"],
    ):
        raise ReleaseArtifactError(
            "component_sources.document_renderer.repository must be owner/name",
        )

    try:
        build = exact_keys(
            root["build"],
            {"workflow_run_id", "workflow_run_attempt", "workflow_url"},
            "build",
        )
    except ContractError as error:
        raise _release_error(error) from error
    for key in ("workflow_run_id", "workflow_run_attempt"):
        if not isinstance(build[key], int) or build[key] < 1:
            raise ReleaseArtifactError(f"build.{key} must be a positive integer")
    expected_url = f"https://github.com/{source['repository']}/actions/runs/{build['workflow_run_id']}"
    if build["workflow_url"] != expected_url:
        raise ReleaseArtifactError(f"build.workflow_url must be {expected_url!r}")

    upgrade_plan = _validate_upgrade_plan(
        root["upgrade_plan"], commit=source_commit, modules=set(module_names),
    )
    if upgrade_plan["from_commit_sha"] != prior_commit:
        raise ReleaseArtifactError("upgrade_plan.from_commit_sha must match the prior release")
    return root


def _artifact(
    arguments: argparse.Namespace,
    role: str,
    artifact_plan: dict[str, Any],
    prior_release: dict[str, Any] | None,
) -> dict[str, Any]:
    name = ARTIFACT_NAMES[role]
    if role in artifact_plan["reuse_roles"]:
        if prior_release is None:
            raise ReleaseArtifactError(f"{role} reuse requires --prior-release")
        prior = prior_release["artifacts"][role]
        supplied_digest = getattr(arguments, f"{role}_digest")
        if supplied_digest is not None and supplied_digest != prior["digest"]:
            raise ReleaseArtifactError(f"{role} digest differs from the prior release")
        image = {
            key: prior[key]
            for key in (
                "name", "tag", "digest", "digest_reference", "source_commit_sha",
                "identity_sha256", "attestations",
            )
        }
        image["origin"] = {
            "kind": "reused_from_release",
            "release_commit_sha": arguments.commit,
            "prior_release_contract_sha256": prior_release["contract_sha256"],
            "prior_release_source_commit_sha": prior_release["source"]["commit_sha"],
        }
        return image
    digest = getattr(arguments, f"{role}_digest")
    if digest is None:
        raise ReleaseArtifactError(f"{role} build requires its digest")
    image = {
        "name": name,
        "tag": f"sha-{arguments.commit}",
        "digest": digest,
        "digest_reference": f"{name}@{digest}",
        "source_commit_sha": arguments.commit,
        "attestations": {
            key: {"status": "generated", "subject_digest": digest}
            for key in ("oci_sbom", "buildkit_provenance", "github_provenance")
        },
    }
    image["identity_sha256"] = _identity(image)
    image["origin"] = {
        "kind": "built_for_release", "release_commit_sha": arguments.commit,
    }
    return image


def create(arguments: argparse.Namespace) -> int:
    upgrade_plan = json.loads(Path(arguments.upgrade_plan).read_text(encoding="utf-8"))
    artifact_plan = json.loads(Path(arguments.artifact_plan).read_text(encoding="utf-8"))
    prior_release = None
    if arguments.prior_release:
        prior_release = json.loads(Path(arguments.prior_release).read_text(encoding="utf-8"))
        prior_release = validate(prior_release, historical=True)
    versions = product_module_versions()
    payload = {
        "schema": SCHEMA,
        "source": {"repository": arguments.repository, "commit_sha": arguments.commit},
        "artifacts": {
            role: _artifact(arguments, role, artifact_plan, prior_release)
            for role in ARTIFACT_ROLES
        },
        "product": {
            "modules": [
                {"name": name, "version": versions[name]} for name in sorted(versions)
            ],
            "oca": {
                "bundle_sha256": arguments.oca_bundle_sha256,
                "repositories": expected_oca_pins(),
            },
            "action_risk": {"policy_sha256": arguments.action_risk_policy_sha256},
        },
        "component_sources": {
            "document_renderer": {
                "repository": arguments.renderer_repository,
                "commit_sha": arguments.renderer_commit,
            },
        },
        "build": {
            "workflow_run_id": arguments.workflow_run_id,
            "workflow_run_attempt": arguments.workflow_run_attempt,
            "workflow_url": arguments.workflow_url,
        },
        "artifact_plan": artifact_plan,
        "upgrade_plan": upgrade_plan,
        "prior_release": (
            {
                "source_commit_sha": prior_release["source"]["commit_sha"],
                "contract_sha256": prior_release["contract_sha256"],
            }
            if prior_release
            else None
        ),
        "contract_sha256": "0" * 64,
    }
    payload = with_checksum(payload)
    validate(payload, commit=arguments.commit, prior_release=prior_release)
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output)
    print(output)
    return 0


def validate_file(arguments: argparse.Namespace) -> int:
    path = Path(arguments.path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError(f"cannot read {path}: {error}") from error
    prior_release = None
    if arguments.prior_release:
        prior_release = json.loads(Path(arguments.prior_release).read_text(encoding="utf-8"))
    validate(
        payload,
        commit=arguments.commit,
        prior_release=prior_release,
        historical=arguments.historical,
    )
    print(f"Valid {SCHEMA}: {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)
    create_command = subcommands.add_parser("create")
    create_command.add_argument("--repository", required=True)
    create_command.add_argument("--commit", required=True)
    for role in ARTIFACT_ROLES:
        create_command.add_argument(f"--{role.replace('_', '-')}-digest")
    create_command.add_argument("--oca-bundle-sha256", required=True)
    create_command.add_argument("--action-risk-policy-sha256", required=True)
    create_command.add_argument("--renderer-repository", required=True)
    create_command.add_argument("--renderer-commit", required=True)
    create_command.add_argument("--upgrade-plan", required=True)
    create_command.add_argument("--artifact-plan", required=True)
    create_command.add_argument("--prior-release")
    create_command.add_argument("--workflow-run-id", required=True, type=int)
    create_command.add_argument("--workflow-run-attempt", required=True, type=int)
    create_command.add_argument("--workflow-url", required=True)
    create_command.add_argument("--output", required=True)
    create_command.set_defaults(handler=create)
    validate_command = subcommands.add_parser("validate")
    validate_command.add_argument("path")
    validate_command.add_argument("--commit")
    validate_command.add_argument("--prior-release")
    validate_command.add_argument("--historical", action="store_true")
    validate_command.set_defaults(handler=validate_file)
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except (ReleaseArtifactError, OSError, json.JSONDecodeError) as error:
        print(f"distribution release: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
