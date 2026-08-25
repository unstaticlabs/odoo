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


def sanitize(export_dir: Path) -> dict:
    manifest_path = export_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        message = "Paperless manifest must be a serialized record list"
        raise TypeError(message)
    unexpected = {}
    for record in manifest:
        model = record.get("model", "")
        if model in FORBIDDEN_MODELS:
            unexpected[model] = unexpected.get(model, 0) + 1
        if model == "paperless.applicationconfiguration" and (
            record.get("fields", {}).get("llm_api_key")
        ):
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
    manifest = [
        record
        for record in manifest
        if record.get("model") not in REMOVED_CREDENTIAL_MODELS
    ]
    sanitized_users = 0
    for record in manifest:
        if record.get("model") == "auth.user":
            fields = record.get("fields") or {}
            fields["password"] = "!"
            fields["last_login"] = None
            sanitized_users += 1
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return {
        "removed_credentials": removed_credentials,
        "sanitized_users": sanitized_users,
        "status": "passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    args = parser.parse_args()
    try:
        result = sanitize(args.export_dir.resolve())
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Paperless seed sanitation failed: {error}") from error
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
