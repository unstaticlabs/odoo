#!/usr/bin/env python3
"""Provision the isolated Pocket ID preproduction tenant."""

# This is an operator CLI: concise literal errors and stdout/stderr output are
# deliberate parts of its interface.
# ruff: noqa: EM101, T201

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".pocket-id.env"
BASE_REQUIRED_ENV_KEYS = {
    "COMPOSE_PROJECT_NAME",
    "ODOO_INIT_DB",
    "ODOO_PUBLIC_BASE_URL",
    "POCKET_ID_APP_URL",
    "POCKET_ID_CLIENT_ID",
    "POCKET_ID_CLIENT_SECRET",
    "POCKET_ID_ENCRYPTION_KEY",
    "POCKET_ID_GROUP_NAME",
    "POCKET_ID_PROSPER_EMAIL",
    "POCKET_ID_PROSPER_ID",
    "POCKET_ID_ROGER_ID",
    "POCKET_ID_STATIC_API_KEY",
    "POCKET_ID_VALENTIN_ID",
    "USL_POCKET_ID_BREAK_GLASS_PASSWORD",
}
PAPERLESS_REQUIRED_ENV_KEYS = {
    "PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL",
    "PAPERLESS_DB_PASSWORD",
    "PAPERLESS_PUBLIC_URL",
    "PAPERLESS_PUBLIC_BASE_URL",
    "PAPERLESS_SECRET_KEY",
    "POCKET_ID_PAPERLESS_CLIENT_ID",
    "POCKET_ID_PAPERLESS_CLIENT_SECRET",
}
REQUIRED_ENV_KEYS = BASE_REQUIRED_ENV_KEYS | PAPERLESS_REQUIRED_ENV_KEYS
USER_DEFINITIONS = {
    "valentin": {
        "id_key": "POCKET_ID_VALENTIN_ID",
        "email": "valentin@unstaticlabs.com",
        "first_name": "Valentin",
        "last_name": "",
        "display_name": "Valentin",
    },
    "roger": {
        "id_key": "POCKET_ID_ROGER_ID",
        "email": "roger@unstaticlabs.com",
        "first_name": "Roger",
        "last_name": "",
        "display_name": "Roger",
    },
    "prosper": {
        "id_key": "POCKET_ID_PROSPER_ID",
        "email_key": "POCKET_ID_PROSPER_EMAIL",
        "first_name": "Prosper",
        "last_name": "",
        "display_name": "Prosper",
    },
}
UNLINKED_TEST_USERNAME = "unlinked-test"
SAFE_DATABASE_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SAFE_LOCALHOST_PATTERN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*localhost$",
)


class PocketIDError(RuntimeError):
    """A safe Pocket ID provisioning error."""


def _read_env(
    path: Path,
    *,
    required_keys: set[str] = REQUIRED_ENV_KEYS,
) -> dict[str, str]:
    if not path.exists():
        raise PocketIDError(
            f"{path.name} is missing; run scripts/pocket-id-dev bootstrap first.",
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PocketIDError(f"{path.name} must not be readable by group or others.")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or any(character.isspace() for character in key):
            raise PocketIDError(f"Invalid {path.name} line {line_number}.")
        values[key] = value
    missing = sorted(required_keys - values.keys())
    if missing:
        raise PocketIDError(
            f"{path.name} is incomplete; missing: {', '.join(missing)}.",
        )
    return values


def _write_new_env(path: Path) -> None:
    if path.exists():
        values = _read_env(path, required_keys=BASE_REQUIRED_ENV_KEYS)
        additions = {}
        paperless_public_url = values.get("PAPERLESS_PUBLIC_BASE_URL") or os.getenv(
            "USL_POCKET_ID_DEV_PAPERLESS_URL",
            "http://paperless.localhost:8010",
        ).strip()
        if not values.get("PAPERLESS_PUBLIC_BASE_URL"):
            additions["PAPERLESS_PUBLIC_BASE_URL"] = paperless_public_url
        if not values.get("PAPERLESS_PUBLIC_URL"):
            additions["PAPERLESS_PUBLIC_URL"] = paperless_public_url
        if not values.get("PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL"):
            additions["PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL"] = (
                "https" if paperless_public_url.startswith("https://") else "http"
            )
        if not values.get("PAPERLESS_DB_PASSWORD"):
            additions["PAPERLESS_DB_PASSWORD"] = secrets.token_urlsafe(36)
        if not values.get("PAPERLESS_SECRET_KEY"):
            additions["PAPERLESS_SECRET_KEY"] = secrets.token_urlsafe(64)
        if not values.get("PAPERLESS_ALLOWED_HOSTS"):
            additions["PAPERLESS_ALLOWED_HOSTS"] = (
                "localhost,127.0.0.1,paperless-webserver,paperless.localhost"
            )
        if not values.get("PAPERLESS_CORS_ALLOWED_HOSTS"):
            additions["PAPERLESS_CORS_ALLOWED_HOSTS"] = paperless_public_url
        if not values.get("PAPERLESS_CSRF_TRUSTED_ORIGINS"):
            additions["PAPERLESS_CSRF_TRUSTED_ORIGINS"] = paperless_public_url
        if not values.get("PAPERLESS_HTTP_PORT"):
            additions["PAPERLESS_HTTP_PORT"] = "8010"
        if not values.get("POCKET_ID_PAPERLESS_CLIENT_ID"):
            additions["POCKET_ID_PAPERLESS_CLIENT_ID"] = (
                "usl-paperless-preproduction"
            )
        if not values.get("POCKET_ID_PAPERLESS_CLIENT_SECRET"):
            additions["POCKET_ID_PAPERLESS_CLIENT_SECRET"] = (
                secrets.token_urlsafe(36)
            )
        if additions:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n# Paperless Pocket ID client; generated during schema upgrade.\n",
                )
                for key, value in additions.items():
                    stream.write(f"{key}={value}\n")
            _read_env(path)
            print(
                f"Upgraded {path.name} with the separate Paperless OIDC client.",
            )
        else:
            _read_env(path)
            print(f"{path.name} already exists and passed validation.")
        return
    project_name = os.getenv(
        "USL_POCKET_ID_DEV_COMPOSE_PROJECT",
        "usl-odoo-saas-19-3",
    ).strip()
    database = os.getenv(
        "USL_POCKET_ID_DEV_ODOO_DB",
        "odoo_dev",
    ).strip()
    odoo_port = os.getenv("USL_POCKET_ID_DEV_ODOO_PORT", "8069").strip()
    gevent_port = os.getenv("USL_POCKET_ID_DEV_GEVENT_PORT", "8072").strip()
    pocket_port = os.getenv("USL_POCKET_ID_DEV_POCKET_PORT", "1411").strip()
    paperless_port = os.getenv(
        "USL_POCKET_ID_DEV_PAPERLESS_PORT",
        "8010",
    ).strip()
    odoo_hostname = os.getenv(
        "USL_POCKET_ID_DEV_ODOO_HOSTNAME",
        "odoo.localhost",
    ).strip()
    pocket_hostname = os.getenv(
        "USL_POCKET_ID_DEV_POCKET_HOSTNAME",
        "pocket-id.localhost",
    ).strip()
    prosper_odoo_email = os.getenv(
        "USL_POCKET_ID_DEV_PROSPER_ODOO_EMAIL",
        "",
    ).strip()
    if not SAFE_PROJECT_PATTERN.fullmatch(project_name):
        raise PocketIDError("The local Compose project name is unsafe.")
    if not SAFE_DATABASE_PATTERN.fullmatch(database):
        raise PocketIDError("The local Odoo database name is unsafe.")
    if database != "odoo_dev":
        raise PocketIDError(
            "Local Pocket ID must target the canonical odoo_dev database.",
        )
    ports = (odoo_port, gevent_port, pocket_port, paperless_port)
    if any(not port.isdigit() or not 1 <= int(port) <= 65535 for port in ports):
        raise PocketIDError("Local service ports must be between 1 and 65535.")
    if len(set(ports)) != len(ports):
        raise PocketIDError("Local service ports must be distinct.")
    for hostname in (odoo_hostname, pocket_hostname):
        if not SAFE_LOCALHOST_PATTERN.fullmatch(hostname):
            raise PocketIDError("Local service hostnames must use .localhost.")
    paperless_public_url = os.getenv(
        "USL_POCKET_ID_DEV_PAPERLESS_URL",
        "http://paperless.localhost:8010",
    ).strip()
    values = {
        "COMPOSE_PROJECT_NAME": project_name,
        "ODOO_DB_FILTER": f"^{database}$",
        "ODOO_GEVENT_PORT": gevent_port,
        "ODOO_HOSTNAME": odoo_hostname,
        "ODOO_HTTP_PORT": odoo_port,
        "ODOO_INIT_DB": database,
        "ODOO_PUBLIC_BASE_URL": f"http://{odoo_hostname}:{odoo_port}",
        "PAPERLESS_PUBLIC_BASE_URL": paperless_public_url,
        "PAPERLESS_PUBLIC_URL": paperless_public_url,
        "PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL": "http",
        "PAPERLESS_ALLOWED_HOSTS": (
            "localhost,127.0.0.1,paperless-webserver,paperless.localhost"
        ),
        "PAPERLESS_CORS_ALLOWED_HOSTS": paperless_public_url,
        "PAPERLESS_CSRF_TRUSTED_ORIGINS": paperless_public_url,
        "PAPERLESS_DB_PASSWORD": secrets.token_urlsafe(36),
        "PAPERLESS_DISABLE_REGULAR_LOGIN": "true",
        "PAPERLESS_HTTP_PORT": paperless_port,
        "PAPERLESS_REDIRECT_LOGIN_TO_SSO": "true",
        "PAPERLESS_SECRET_KEY": secrets.token_urlsafe(64),
        "POCKET_ID_APP_URL": f"http://{pocket_hostname}:{pocket_port}",
        "POCKET_ID_CLIENT_ID": "usl-odoo-preproduction",
        "POCKET_ID_CLIENT_SECRET": secrets.token_urlsafe(36),
        "POCKET_ID_ENCRYPTION_KEY": secrets.token_urlsafe(36),
        "POCKET_ID_GROUP_NAME": "odoo-preproduction",
        "POCKET_ID_HOSTNAME": pocket_hostname,
        "POCKET_ID_HTTP_PORT": pocket_port,
        "POCKET_ID_IMAGE": (
            "ghcr.io/pocket-id/pocket-id:v2.14.0@"
            "sha256:01540977dcf4c7b41b1159f34d68e4632f2658d62790e460ca65a42722b13c4a"
        ),
        "POCKET_ID_PROSPER_EMAIL": "prosper@preproduction.invalid",
        "POCKET_ID_PROSPER_ODOO_EMAIL": prosper_odoo_email,
        "POCKET_ID_PROSPER_ID": str(uuid.uuid4()),
        "POCKET_ID_PAPERLESS_CLIENT_ID": "usl-paperless-preproduction",
        "POCKET_ID_PAPERLESS_CLIENT_SECRET": secrets.token_urlsafe(36),
        "POCKET_ID_ROGER_ID": str(uuid.uuid4()),
        "POCKET_ID_STATIC_API_KEY": secrets.token_urlsafe(36),
        "POCKET_ID_VALENTIN_ID": str(uuid.uuid4()),
        "USL_EINVOICE_LIVE_ENABLED": "0",
        "USL_EREPORTING_LIVE_ENABLED": "0",
        "USL_POCKET_ID_BREAK_GLASS_PASSWORD": secrets.token_urlsafe(36),
        "USL_POCKET_ID_LOGIN_POLICY": "sso_only",
        "USL_POCKET_ID_BREAK_GLASS_ENABLED": "0",
        "USL_POCKET_ID_BREAK_GLASS_EXPIRES_AT": "",
    }
    if os.getenv("USL_POCKET_ID_DEV_STRONG_DATABASE_SECRETS") == "1":
        database_password = secrets.token_urlsafe(36)
        values.update({
            "POSTGRES_PASSWORD": database_password,
            "ODOO_DB_PASSWORD": database_password,
            "ODOO_ADMIN_PASSWORD": secrets.token_urlsafe(36),
        })
    content = (
        "# Generated local Pocket ID target configuration. Do not commit.\n"
        + "\n".join(f"{key}={value}" for key, value in values.items())
        + "\n"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    print(f"Created {path.name} with mode 0600.")


class PocketIDAPI:
    def __init__(self, values: dict[str, str]) -> None:
        self.base_url = values["POCKET_ID_APP_URL"].rstrip("/")
        self.api_key = values["POCKET_ID_STATIC_API_KEY"]

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        body = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response_body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise PocketIDError(
                f"Pocket ID API {method} {path} returned HTTP {error.code}: {detail}",
            ) from error
        except urllib.error.URLError as error:
            raise PocketIDError(
                f"Pocket ID API {method} {path} is unavailable.",
            ) from error
        if not response_body:
            return None
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise PocketIDError(
                f"Pocket ID API {method} {path} returned invalid JSON.",
            ) from error

    def wait_until_ready(self) -> None:
        for _attempt in range(40):
            try:
                self.request("GET", "/api/users?pagination%5Blimit%5D=100")
                return
            except PocketIDError:
                time.sleep(0.5)
        raise PocketIDError("Pocket ID did not become API-ready within 20 seconds.")


def _paginated_data(payload: object, label: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PocketIDError(f"Pocket ID returned an invalid {label} list.")
    return payload["data"]


def _user_payload(
    values: dict[str, str],
    username: str,
    group_id: str,
) -> dict[str, object]:
    definition = USER_DEFINITIONS[username]
    email = (
        values[definition["email_key"]]
        if "email_key" in definition
        else definition["email"]
    )
    return {
        "id": values[definition["id_key"]],
        "username": username,
        "email": email,
        "emailVerified": True,
        "firstName": definition["first_name"],
        "lastName": definition["last_name"],
        "displayName": definition["display_name"],
        "isAdmin": False,
        "disabled": False,
        "userGroupIds": [group_id],
        "locale": "en",
    }


def _extra_user_payloads(
    values: dict[str, str],
    group_id: str,
) -> list[dict[str, object]]:
    raw_users = values.get("POCKET_ID_EXTRA_USERS_JSON", "").strip()
    if not raw_users:
        return []
    try:
        specifications = json.loads(raw_users)
    except json.JSONDecodeError as error:
        raise PocketIDError("POCKET_ID_EXTRA_USERS_JSON is invalid JSON.") from error
    if not isinstance(specifications, list):
        raise PocketIDError("POCKET_ID_EXTRA_USERS_JSON must be a list.")
    payloads = []
    seen_usernames = set(USER_DEFINITIONS)
    seen_ids = {
        values[definition["id_key"]]
        for definition in USER_DEFINITIONS.values()
    }
    for specification in specifications:
        if not isinstance(specification, dict):
            raise PocketIDError("Each extra Pocket ID user must be an object.")
        username = specification.get("username")
        user_id = specification.get("id")
        email = specification.get("email")
        display_name = specification.get("display_name")
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (username, user_id, email, display_name)
            )
            or set(specification)
            != {"username", "id", "email", "display_name"}
        ):
            raise PocketIDError("An extra Pocket ID user is incomplete.")
        if username in seen_usernames or user_id in seen_ids:
            raise PocketIDError("An extra Pocket ID user duplicates an identity.")
        seen_usernames.add(username)
        seen_ids.add(user_id)
        payloads.append(
            {
                "id": user_id,
                "username": username,
                "email": email,
                "emailVerified": True,
                "firstName": display_name,
                "lastName": "",
                "displayName": display_name,
                "isAdmin": False,
                "disabled": False,
                "userGroupIds": [group_id],
                "locale": "en",
            },
        )
    return payloads


def _odoo_client_payload(values: dict[str, str]) -> dict[str, object]:
    return {
        "id": values["POCKET_ID_CLIENT_ID"],
        "name": "USL Odoo Preproduction",
        "description": "Local Odoo Community Pocket ID integration validation",
        "callbackURLs": [
            f"{values['ODOO_PUBLIC_BASE_URL'].rstrip('/')}/auth_oauth/signin",
        ],
        "logoutCallbackURLs": [
            f"{values['ODOO_PUBLIC_BASE_URL'].rstrip('/')}/web/login",
        ],
        "isPublic": False,
        "pkceEnabled": True,
        "pkceSupported": True,
        "requiresReauthentication": False,
        "requiresPushedAuthorizationRequests": False,
        "skipConsent": True,
        "credentials": {"federatedIdentities": []},
        "launchURL": values["ODOO_PUBLIC_BASE_URL"],
        "isGroupRestricted": True,
    }


def _paperless_client_payload(values: dict[str, str]) -> dict[str, object]:
    paperless_url = values["PAPERLESS_PUBLIC_BASE_URL"].rstrip("/")
    return {
        "id": values["POCKET_ID_PAPERLESS_CLIENT_ID"],
        "name": "USL Paperless Preproduction",
        "description": (
            "Paperless-ngx individual login through the shared USL identity provider"
        ),
        "callbackURLs": [
            f"{paperless_url}/accounts/oidc/pocket-id/login/callback/",
        ],
        "logoutCallbackURLs": [],
        "isPublic": False,
        "pkceEnabled": True,
        "pkceSupported": True,
        "requiresReauthentication": False,
        "requiresPushedAuthorizationRequests": False,
        "skipConsent": True,
        "credentials": {"federatedIdentities": []},
        "launchURL": paperless_url,
        "isGroupRestricted": True,
    }


def _ensure_group(api: PocketIDAPI, values: dict[str, str]) -> dict[str, object]:
    groups = _paginated_data(
        api.request("GET", "/api/user-groups?pagination%5Blimit%5D=100"),
        "group",
    )
    matches = [
        group for group in groups if group.get("name") == values["POCKET_ID_GROUP_NAME"]
    ]
    if len(matches) > 1:
        raise PocketIDError("The configured Pocket ID group name is ambiguous.")
    payload = {
        "friendlyName": "Odoo Preproduction",
        "name": values["POCKET_ID_GROUP_NAME"],
    }
    if matches:
        group = api.request("PUT", f"/api/user-groups/{matches[0]['id']}", payload)
    else:
        group = api.request("POST", "/api/user-groups", payload)
    if not isinstance(group, dict) or not isinstance(group.get("id"), str):
        raise PocketIDError("Pocket ID did not return the configured group.")
    return group


def _ensure_users(
    api: PocketIDAPI,
    values: dict[str, str],
    group_id: str,
) -> list[dict[str, object]]:
    users = _paginated_data(
        api.request("GET", "/api/users?pagination%5Blimit%5D=100"),
        "user",
    )
    result = []
    payloads = [
        _user_payload(values, username, group_id)
        for username in USER_DEFINITIONS
    ] + _extra_user_payloads(values, group_id)
    for payload in payloads:
        username = str(payload["username"])
        matches = [user for user in users if user.get("username") == username]
        if len(matches) > 1:
            raise PocketIDError(f"Pocket ID username {username!r} is ambiguous.")
        if matches and matches[0].get("id") != payload["id"]:
            raise PocketIDError(
                f"Pocket ID username {username!r} has an unexpected immutable ID.",
            )
        if matches:
            user = api.request("PUT", f"/api/users/{payload['id']}", payload)
        else:
            user = api.request("POST", "/api/users", payload)
        if not isinstance(user, dict) or user.get("id") != payload["id"]:
            raise PocketIDError(f"Pocket ID did not return user {username!r}.")
        result.append(user)
    return result


def _ensure_client(
    api: PocketIDAPI,
    payload: dict[str, object],
    secret: str,
    group_id: str,
) -> dict[str, object]:
    clients = _paginated_data(
        api.request("GET", "/api/oidc/clients?pagination%5Blimit%5D=100"),
        "OIDC client",
    )
    client_id = str(payload["id"])
    matches = [client for client in clients if client.get("id") == client_id]
    if matches:
        client = api.request("PUT", f"/api/oidc/clients/{client_id}", payload)
    else:
        client = api.request("POST", "/api/oidc/clients", payload)
    _ensure_client_secret(api, client_id, secret)
    api.request(
        "PUT",
        f"/api/oidc/clients/{client_id}/allowed-user-groups",
        {"userGroupIds": [group_id]},
    )
    if not isinstance(client, dict) or client.get("id") != client_id:
        raise PocketIDError("Pocket ID did not return the configured OIDC client.")
    return client


def _ensure_client_secret(api: PocketIDAPI, client_id: str, secret: str) -> None:
    """Keep the environment-owned secret present without rotating it on deploy."""
    secrets = api.request("GET", f"/api/oidc/clients/{client_id}/secrets")
    if not isinstance(secrets, list) or any(
        not isinstance(item, dict) for item in secrets
    ):
        raise PocketIDError("Pocket ID returned an invalid OIDC client secret list.")
    expected_prefix = secret[:4] if len(secret) > 4 else ""
    if any(
        item.get("prefix") == expected_prefix and item.get("isActive") is True
        for item in secrets
    ):
        return
    api.request(
        "POST",
        f"/api/oidc/clients/{client_id}/secrets",
        {"secret": secret},
    )


def provision(values: dict[str, str]) -> None:
    api = PocketIDAPI(values)
    api.wait_until_ready()
    group = _ensure_group(api, values)
    users = _ensure_users(api, values, str(group["id"]))
    api.request(
        "PUT",
        f"/api/user-groups/{group['id']}/users",
        {"userIds": [str(user["id"]) for user in users]},
    )
    clients = [
        _ensure_client(
            api,
            _odoo_client_payload(values),
            values["POCKET_ID_CLIENT_SECRET"],
            str(group["id"]),
        ),
        _ensure_client(
            api,
            _paperless_client_payload(values),
            values["POCKET_ID_PAPERLESS_CLIENT_SECRET"],
            str(group["id"]),
        ),
    ]
    print(
        json.dumps(
            {
                "client_ids": [client["id"] for client in clients],
                "group": group["name"],
                "issuer": values["POCKET_ID_APP_URL"],
                "users": [
                    {
                        "username": user["username"],
                        "disabled": user.get("disabled", False),
                    }
                    for user in users
                ],
            },
            indent=2,
            sort_keys=True,
        ),
    )


def odoo_policy(values: dict[str, str]) -> None:
    prosper_odoo_email = values.get(
        "POCKET_ID_PROSPER_ODOO_EMAIL",
        values["POCKET_ID_PROSPER_EMAIL"],
    )
    policy = [
        {
            "login": "admin",
            "profile": "break_glass",
            "companies": "all",
        },
        {
            "login": "valentin",
            "name": "Valentin",
            "email": USER_DEFINITIONS["valentin"]["email"],
            "profile": "administrator",
            "companies": "all",
            "subject": values["POCKET_ID_VALENTIN_ID"],
            "create_if_missing": True,
        },
        {
            "login": "roger@unstaticlabs.com",
            "name": "Roger",
            "email": USER_DEFINITIONS["roger"]["email"],
            "profile": "technical_operator",
            "companies": "all",
            "subject": values["POCKET_ID_ROGER_ID"],
            "create_if_missing": True,
        },
        {
            "login": "roger@xaic.cat",
            "profile": "historical",
            "optional_if_missing": True,
        },
        {
            "login": "prosper",
            "name": "Prosper",
            "profile": "accountant_reviewer",
            "companies": ["Unstatic Labs", "USL MEDIA"],
            "subject": values["POCKET_ID_PROSPER_ID"],
            "create_if_missing": bool(prosper_odoo_email),
        },
    ]
    if prosper_odoo_email:
        policy[-1]["email"] = prosper_odoo_email
    print(json.dumps(policy, separators=(",", ":")))


def paperless_policy(values: dict[str, str]) -> None:
    """Describe the governed people that need an individual Paperless login."""
    policy = []
    for username, definition in USER_DEFINITIONS.items():
        email = (
            values[definition["email_key"]]
            if "email_key" in definition
            else definition["email"]
        )
        policy.append(
            {
                "subject": values[definition["id_key"]],
                "username": username,
                "email": email,
                "display_name": definition["display_name"],
            },
        )
    print(json.dumps(policy, separators=(",", ":")))


def _find_user(
    api: PocketIDAPI,
    values: dict[str, str],
    username: str,
) -> dict[str, object]:
    if username in USER_DEFINITIONS:
        user_id = values[USER_DEFINITIONS[username]["id_key"]]
        user = api.request("GET", f"/api/users/{user_id}")
    else:
        users = _paginated_data(
            api.request("GET", "/api/users?pagination%5Blimit%5D=100"),
            "user",
        )
        matches = [user for user in users if user.get("username") == username]
        if not matches:
            raise PocketIDError(
                f"Pocket ID user {username!r} is not provisioned.",
            )
        if len(matches) > 1:
            raise PocketIDError(
                f"Pocket ID username {username!r} is ambiguous.",
            )
        user = matches[0]
    if not isinstance(user, dict) or user.get("username") != username:
        raise PocketIDError(f"Pocket ID user {username!r} is not provisioned.")
    return user


def ensure_unlinked_test_user(values: dict[str, str]) -> None:
    api = PocketIDAPI(values)
    group = _ensure_group(api, values)
    users = _paginated_data(
        api.request("GET", "/api/users?pagination%5Blimit%5D=100"),
        "user",
    )
    matches = [
        user for user in users if user.get("username") == UNLINKED_TEST_USERNAME
    ]
    if len(matches) > 1:
        raise PocketIDError("The unlinked Pocket ID lifecycle username is ambiguous.")
    payload = {
        "id": str(matches[0]["id"]) if matches else str(uuid.uuid4()),
        "username": UNLINKED_TEST_USERNAME,
        "email": "unlinked-test@preproduction.invalid",
        "emailVerified": True,
        "firstName": "Unlinked",
        "lastName": "Lifecycle Test",
        "displayName": "Unlinked Lifecycle Test",
        "isAdmin": False,
        "disabled": False,
        "userGroupIds": [str(group["id"])],
        "locale": "en",
    }
    if matches:
        user = api.request("PUT", f"/api/users/{payload['id']}", payload)
    else:
        user = api.request("POST", "/api/users", payload)
    if not isinstance(user, dict) or user.get("id") != payload["id"]:
        raise PocketIDError("Pocket ID did not return the unlinked lifecycle user.")
    full_group = api.request("GET", f"/api/user-groups/{group['id']}")
    if not isinstance(full_group, dict) or not isinstance(
        full_group.get("users"),
        list,
    ):
        raise PocketIDError("Pocket ID returned an invalid group membership list.")
    user_ids = {
        str(candidate["id"])
        for candidate in full_group["users"]
        if isinstance(candidate, dict) and candidate.get("id")
    }
    user_ids.add(str(user["id"]))
    api.request(
        "PUT",
        f"/api/user-groups/{group['id']}/users",
        {"userIds": sorted(user_ids)},
    )
    print("Provisioned Pocket ID-only lifecycle user unlinked-test.")


def one_time_link(values: dict[str, str], username: str, ttl: str) -> None:
    api = PocketIDAPI(values)
    user = _find_user(api, values, username)
    result = api.request(
        "POST",
        f"/api/users/{user['id']}/one-time-access-token",
        {"ttl": ttl},
    )
    if not isinstance(result, dict) or not isinstance(result.get("token"), str):
        raise PocketIDError("Pocket ID did not return a one-time access token.")
    print(f"{values['POCKET_ID_APP_URL'].rstrip('/')}/lc/{result['token']}")


def set_disabled(values: dict[str, str], username: str, disabled: bool) -> None:
    api = PocketIDAPI(values)
    user = _find_user(api, values, username)
    group = _ensure_group(api, values)
    payload = _user_payload(values, username, str(group["id"]))
    payload["disabled"] = disabled
    api.request("PUT", f"/api/users/{user['id']}", payload)
    print(f"Pocket ID user {username} disabled={str(disabled).lower()}.")


def set_profile(
    values: dict[str, str],
    username: str,
    email: str,
    display_name: str,
) -> None:
    api = PocketIDAPI(values)
    user = _find_user(api, values, username)
    group = _ensure_group(api, values)
    payload = _user_payload(values, username, str(group["id"]))
    payload.update(
        {
            "disabled": bool(user.get("disabled", False)),
            "email": email,
            "displayName": display_name,
        },
    )
    api.request("PUT", f"/api/users/{user['id']}", payload)
    print(f"Pocket ID user {username} profile updated.")


def set_group(values: dict[str, str], username: str, present: bool) -> None:
    api = PocketIDAPI(values)
    user = _find_user(api, values, username)
    group = _ensure_group(api, values)
    current_users = group.get("users")
    if not isinstance(current_users, list):
        full_group = api.request("GET", f"/api/user-groups/{group['id']}")
        if not isinstance(full_group, dict) or not isinstance(
            full_group.get("users"),
            list,
        ):
            raise PocketIDError("Pocket ID returned an invalid group membership list.")
        current_users = full_group["users"]
    user_ids = {
        str(candidate["id"])
        for candidate in current_users
        if isinstance(candidate, dict) and candidate.get("id")
    }
    if present:
        user_ids.add(str(user["id"]))
    else:
        user_ids.discard(str(user["id"]))
    api.request(
        "PUT",
        f"/api/user-groups/{group['id']}/users",
        {"userIds": sorted(user_ids)},
    )
    print(f"Pocket ID user {username} group_present={str(present).lower()}.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap")
    subparsers.add_parser("provision")
    subparsers.add_parser("ensure-unlinked")
    subparsers.add_parser("odoo-policy")
    subparsers.add_parser("paperless-policy")
    link_parser = subparsers.add_parser("one-time-link")
    link_parser.add_argument("username")
    link_parser.add_argument("--ttl", default="1h")
    disabled_parser = subparsers.add_parser("set-disabled")
    disabled_parser.add_argument("username", choices=tuple(USER_DEFINITIONS))
    disabled_parser.add_argument("state", choices=("true", "false"))
    profile_parser = subparsers.add_parser("set-profile")
    profile_parser.add_argument("username", choices=tuple(USER_DEFINITIONS))
    profile_parser.add_argument("email")
    profile_parser.add_argument("display_name")
    group_parser = subparsers.add_parser("set-group")
    group_parser.add_argument("username", choices=tuple(USER_DEFINITIONS))
    group_parser.add_argument("state", choices=("present", "absent"))
    arguments = parser.parse_args()
    try:
        if arguments.command == "bootstrap":
            _write_new_env(arguments.env_file)
            return 0
        values = _read_env(arguments.env_file)
        if arguments.command == "provision":
            provision(values)
        elif arguments.command == "ensure-unlinked":
            ensure_unlinked_test_user(values)
        elif arguments.command == "odoo-policy":
            odoo_policy(values)
        elif arguments.command == "paperless-policy":
            paperless_policy(values)
        elif arguments.command == "one-time-link":
            one_time_link(values, arguments.username, arguments.ttl)
        elif arguments.command == "set-disabled":
            set_disabled(values, arguments.username, arguments.state == "true")
        elif arguments.command == "set-profile":
            set_profile(
                values,
                arguments.username,
                arguments.email,
                arguments.display_name,
            )
        elif arguments.command == "set-group":
            set_group(values, arguments.username, arguments.state == "present")
    except PocketIDError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
