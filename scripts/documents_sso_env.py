#!/usr/bin/env python3
"""Create the ignored Pocket ID secrets for the isolated Documents QA stack."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import uuid
from pathlib import Path


REQUIRED_KEYS = {
    "COMPOSE_PROJECT_NAME",
    "ODOO_DB_FILTER",
    "ODOO_INIT_DB",
    "ODOO_PUBLIC_BASE_URL",
    "PAPERLESS_DISABLE_REGULAR_LOGIN",
    "PAPERLESS_OIDC_ENABLED",
    "PAPERLESS_PUBLIC_BASE_URL",
    "PAPERLESS_REDIRECT_LOGIN_TO_SSO",
    "POCKET_ID_APP_URL",
    "POCKET_ID_CLIENT_ID",
    "POCKET_ID_CLIENT_SECRET",
    "POCKET_ID_ENCRYPTION_KEY",
    "POCKET_ID_EXTRA_USERS_JSON",
    "POCKET_ID_GROUP_NAME",
    "POCKET_ID_HOSTNAME",
    "POCKET_ID_HTTP_PORT",
    "POCKET_ID_PAPERLESS_CLIENT_ID",
    "POCKET_ID_PAPERLESS_CLIENT_SECRET",
    "POCKET_ID_PROSPER_EMAIL",
    "POCKET_ID_PROSPER_ID",
    "POCKET_ID_ROGER_ID",
    "POCKET_ID_STATIC_API_KEY",
    "POCKET_ID_VALENTIN_ID",
    "USL_EINVOICE_LIVE_ENABLED",
    "USL_EREPORTING_LIVE_ENABLED",
    "USL_POCKET_ID_BREAK_GLASS_PASSWORD",
}


def read_env(path: Path) -> dict[str, str]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(f"{path} must have mode 0600")
    values = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or any(character.isspace() for character in key):
            raise RuntimeError(f"invalid environment line {line_number}")
        values[key] = value
    missing = sorted(REQUIRED_KEYS - values.keys())
    if missing:
        raise RuntimeError(f"missing QA SSO keys: {', '.join(missing)}")
    return values


def ensure_env(path: Path) -> None:
    if path.exists():
        values = read_env(path)
        extra_users = json.loads(values["POCKET_ID_EXTRA_USERS_JSON"])
        if not any(
            item.get("username") == "documents-sso-user"
            for item in extra_users
        ):
            extra_users.append(
                {
                    "username": "documents-sso-user",
                    "id": str(uuid.uuid4()),
                    "email": "documents-sso-user@documents-qa.invalid",
                    "display_name": "Documents Pocket QA User",
                },
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text(
                "\n".join(
                    (
                        "POCKET_ID_EXTRA_USERS_JSON="
                        + json.dumps(extra_users, separators=(",", ":"))
                    )
                    if line.startswith("POCKET_ID_EXTRA_USERS_JSON=")
                    else line
                    for line in lines
                )
                + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            read_env(path)
        print(f"{path.name} already exists and passed validation.")
        return
    extra_users = [
        {
            "username": username,
            "id": str(uuid.uuid4()),
            "email": f"{username}@documents-qa.invalid",
            "display_name": display_name,
        }
        for username, display_name in (
            ("documents-user", "Documents General User"),
            ("documents-accountant", "Documents Accountant"),
            ("documents-hr", "Documents HR Reviewer"),
            ("documents-restricted", "Documents Restricted User"),
            ("documents-sso-user", "Documents Pocket QA User"),
        )
    ]
    values = {
        "COMPOSE_PROJECT_NAME": "codex-paperless-docs",
        "ODOO_DB_FILTER": "^odoo_usl_documents_test$",
        "ODOO_INIT_DB": "odoo_usl_documents_test",
        "ODOO_PUBLIC_BASE_URL": "http://odoo-documents.localhost:18080",
        "PAPERLESS_DISABLE_REGULAR_LOGIN": "false",
        "PAPERLESS_OIDC_ENABLED": "1",
        "PAPERLESS_PUBLIC_BASE_URL": "http://127.0.0.1:8010",
        "PAPERLESS_REDIRECT_LOGIN_TO_SSO": "false",
        "POCKET_ID_APP_URL": "http://pocket-id-documents.localhost:18110",
        "POCKET_ID_CLIENT_ID": "usl-odoo-documents-qa",
        "POCKET_ID_CLIENT_SECRET": secrets.token_urlsafe(36),
        "POCKET_ID_ENCRYPTION_KEY": secrets.token_urlsafe(36),
        "POCKET_ID_EXTRA_USERS_JSON": json.dumps(
            extra_users,
            separators=(",", ":"),
        ),
        "POCKET_ID_GROUP_NAME": "documents-qa",
        "POCKET_ID_HOSTNAME": "pocket-id-documents.localhost",
        "POCKET_ID_HTTP_PORT": "18110",
        "POCKET_ID_PAPERLESS_CLIENT_ID": "usl-paperless-documents-qa",
        "POCKET_ID_PAPERLESS_CLIENT_SECRET": secrets.token_urlsafe(36),
        "POCKET_ID_PROSPER_EMAIL": "prosper@documents-qa.invalid",
        "POCKET_ID_PROSPER_ID": str(uuid.uuid4()),
        "POCKET_ID_ROGER_ID": str(uuid.uuid4()),
        "POCKET_ID_STATIC_API_KEY": secrets.token_urlsafe(36),
        "POCKET_ID_VALENTIN_ID": str(uuid.uuid4()),
        "USL_EINVOICE_LIVE_ENABLED": "0",
        "USL_EREPORTING_LIVE_ENABLED": "0",
        "USL_POCKET_ID_BREAK_GLASS_PASSWORD": secrets.token_urlsafe(36),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("# Generated Documents QA SSO secrets. Do not commit.\n")
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    read_env(path)
    print(f"Created {path.name} with mode 0600.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    try:
        ensure_env(arguments.path)
    except RuntimeError as error:
        print(f"error: {error}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
