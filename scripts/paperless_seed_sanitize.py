#!/usr/bin/env python3
"""Remove environment authentication material from a Paperless QA export."""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
from pathlib import Path

FORBIDDEN_MODELS = {
    "allauth.mfa.authenticator",
    "documents.workflowactionemail",
    "documents.workflowactionwebhook",
    "paperless_mail.mailaccount",
    "paperless_mail.mailrule",
    "socialaccount.socialaccount",
    "socialaccount.socialapp",
    "socialaccount.socialtoken",
}
REMOVED_CREDENTIAL_MODELS = {
    "authtoken.token",
    "paperless_personal_ai.personalaiprofile",
    "sessions.session",
}
PORTABLE_CONFIGURATION_MODELS = {
    "paperless.applicationconfiguration",
}


def sanitize(
    export_dir: Path,
    *,
    remove_integrations: bool = False,
    portable_candidate: bool = False,
) -> dict:
    manifest_path = export_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        message = "Paperless manifest must be a serialized record list"
        raise TypeError(message)
    unexpected = {}
    removed_environment = {}
    integration_removal_enabled = remove_integrations or portable_candidate
    for record in manifest:
        model = record.get("model", "")
        if model in FORBIDDEN_MODELS and not integration_removal_enabled:
            unexpected[model] = unexpected.get(model, 0) + 1
        if model == "paperless.applicationconfiguration" and (
            record.get("fields", {}).get("llm_api_key")
        ) and not integration_removal_enabled:
            unexpected["paperless.applicationconfiguration.llm_api_key"] = 1
    if unexpected:
        details = ", ".join(f"{name}={count}" for name, count in sorted(unexpected.items()))
        raise ValueError(
            "Paperless export contains environment integrations that require "
            f"an explicit migration decision: {details}",
        )
    removed_credentials = sum(
        record.get("model") in REMOVED_CREDENTIAL_MODELS for record in manifest
    )
    removed_integrations = sum(
        integration_removal_enabled
        and record.get("model") in FORBIDDEN_MODELS
        for record in manifest
    )
    removed_models = set(REMOVED_CREDENTIAL_MODELS)
    if integration_removal_enabled:
        removed_models.update(FORBIDDEN_MODELS)
    if portable_candidate:
        removed_models.update(PORTABLE_CONFIGURATION_MODELS)
    for record in manifest:
        model = record.get("model", "")
        if model in removed_models:
            removed_environment[model] = removed_environment.get(model, 0) + 1
    manifest = [
        record
        for record in manifest
        if record.get("model") not in removed_models
    ]
    sanitized_users = 0
    sanitized_configuration_fields = 0
    for record in manifest:
        if record.get("model") == "auth.user":
            fields = record.get("fields") or {}
            fields["password"] = "!"
            fields["last_login"] = None
            sanitized_users += 1
        if remove_integrations and record.get("model") == "paperless.applicationconfiguration":
            fields = record.get("fields") or {}
            for name in ("llm_api_key", "llm_model", "llm_endpoint", "llm_backend"):
                if name in fields:
                    fields[name] = None
                    sanitized_configuration_fields += 1
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return {
        "removed_credentials": removed_credentials,
        "removed_integrations": removed_integrations,
        "removed_environment_models": dict(sorted(removed_environment.items())),
        "sanitized_configuration_fields": sanitized_configuration_fields,
        "sanitized_users": sanitized_users,
        "status": "passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--release-remove-integrations", action="store_true")
    mode.add_argument("--portable-candidate", action="store_true")
    args = parser.parse_args()
    try:
        result = sanitize(
            args.export_dir.resolve(),
            remove_integrations=args.release_remove_integrations,
            portable_candidate=args.portable_candidate,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Paperless seed sanitation failed: {error}") from error
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
