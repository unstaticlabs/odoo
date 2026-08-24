import ipaddress
import logging
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from jose import jwt
from jose.exceptions import JWSError, JWTError

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..exceptions import PocketIDAccessDenied, PocketIDReason

_logger = logging.getLogger(__name__)

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_DEFAULT_SCOPES = "openid profile email groups"
_SUPPORTED_SIGNING_ALGORITHMS = ("RS256",)
_SUPPORTED_TOKEN_AUTH_METHODS = {
    "client_secret_basic",
    "client_secret_post",
}
_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 20
_ENVIRONMENT_MANAGED_FIELDS = {
    "auth_endpoint",
    "body",
    "client_id",
    "client_secret",
    "enabled",
    "flow",
    "jwks_uri",
    "name",
    "scope",
    "token_endpoint",
    "token_map",
    "usl_end_session_endpoint",
    "usl_allow_unique_email_link",
    "usl_oidc_issuer",
    "usl_pocketid",
    "usl_public_base_url",
    "usl_required_group",
    "usl_token_auth_method",
    "validation_endpoint",
}


@dataclass(frozen=True)
class PocketIDClientConfiguration:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_id: str
    client_secret: str
    required_group: str
    redirect_uri: str
    token_auth_method: str
    scopes: str
    fresh_passkey_supported: bool
    discovery_snapshot: dict[str, object]


def _env_enabled(name):
    return os.getenv(name, "").strip().lower() in _TRUTHY_VALUES


def _origin(url):
    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname, port


class AuthOauthProvider(models.Model):
    _inherit = "auth.oauth.provider"

    usl_pocketid = fields.Boolean(
        string="USL Pocket ID provider",
        copy=False,
        help="This provider is governed by the USL Pocket ID environment.",
    )
    usl_oidc_issuer = fields.Char(
        string="OIDC Issuer",
        copy=False,
        help="Exact issuer expected in Pocket ID ID tokens.",
    )
    usl_public_base_url = fields.Char(
        string="Odoo Public Base URL",
        copy=False,
        help="Canonical public Odoo URL used to construct the registered callback.",
    )
    usl_required_group = fields.Char(
        string="Required Pocket ID Group",
        copy=False,
        help="Pocket ID group required to authenticate. It never grants Odoo groups.",
    )
    usl_end_session_endpoint = fields.Char(
        string="OIDC Logout Endpoint",
        copy=False,
        help="Validated Pocket ID endpoint used for provider-aware logout.",
    )
    usl_allow_unique_email_link = fields.Boolean(
        string="Allow pre-approved unique email linking",
        copy=False,
        help=(
            "Allow a verified email to create an identity link only when exactly "
            "one active internal Odoo user was explicitly pre-approved."
        ),
    )
    usl_token_auth_method = fields.Selection(
        [
            ("client_secret_basic", "Client secret HTTP Basic"),
            ("client_secret_post", "Client secret POST body"),
        ],
        string="Token endpoint authentication",
        copy=False,
        default="client_secret_basic",
    )

    def write(self, values):
        if (
            _ENVIRONMENT_MANAGED_FIELDS.intersection(values)
            and any(self.mapped("usl_pocketid"))
        ):
            raise ValidationError(
                _(
                    "The USL Pocket ID provider is environment-managed. "
                    "Use the documented configuration helper.",
                ),
            )
        return super().write(values)

    def _usl_pocketid_environment_write(self, values):
        self.ensure_one()
        expected_provider = self.env.ref("usl_pocketid.provider_pocketid")
        if self != expected_provider or not self.usl_pocketid:
            raise ValidationError(_("This is not the governed Pocket ID provider."))
        return super().write(values)

    @api.ondelete(at_uninstall=False)
    def _prevent_pocketid_provider_deletion(self):
        if any(self.mapped("usl_pocketid")):
            raise ValidationError(
                _(
                    "The USL Pocket ID provider must be disabled through "
                    "environment configuration, not deleted.",
                ),
            )

    @api.constrains("usl_pocketid", "client_secret")
    def _check_pocketid_secret_not_stored(self):
        for provider in self:
            if provider.usl_pocketid and provider.client_secret:
                raise ValidationError(
                    _(
                        "Pocket ID client secrets must come from "
                        "USL_POCKET_ID_CLIENT_SECRET and cannot be stored in Odoo.",
                    ),
                )

    @api.constrains("usl_pocketid")
    def _check_single_pocketid_provider(self):
        if any(self.mapped("usl_pocketid")) and self.sudo().search_count(
            [("usl_pocketid", "=", True)],
        ) > 1:
            raise ValidationError(_("Only one USL Pocket ID provider is allowed."))

    @api.model
    def _usl_validate_url(
        self,
        url,
        *,
        label,
        allow_path=True,
        allow_query=True,
    ):
        parsed = urlsplit(url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (not allow_path and parsed.path not in ("", "/"))
            or (not allow_query and parsed.query)
        ):
            raise ValidationError(_("%(label)s is not a safe absolute URL.", label=label))
        if parsed.scheme == "https":
            return url.rstrip("/")
        if parsed.scheme != "http":
            raise ValidationError(_("%(label)s must use HTTPS.", label=label))
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            hostname = parsed.hostname.rstrip(".")
            is_loopback = hostname == "localhost" or hostname.endswith(".localhost")
        if not is_loopback:
            raise ValidationError(
                _("%(label)s may use HTTP only for a loopback test service.", label=label),
            )
        return url.rstrip("/")

    @api.model
    def _usl_discover_pocketid(self, issuer):
        issuer = self._usl_validate_url(
            issuer,
            label=_("Pocket ID issuer"),
            allow_query=False,
        )
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        try:
            response = requests.get(discovery_url, timeout=10)
            response.raise_for_status()
            discovery = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ValidationError(
                _("Pocket ID discovery could not be loaded safely."),
            ) from error
        discovered_issuer = discovery.get("issuer")
        if not discovered_issuer or discovered_issuer.rstrip("/") != issuer:
            raise ValidationError(_("Pocket ID discovery returned a different issuer."))
        endpoints = {}
        for key, label in (
            ("authorization_endpoint", _("Pocket ID authorization endpoint")),
            ("token_endpoint", _("Pocket ID token endpoint")),
            ("jwks_uri", _("Pocket ID JWKS endpoint")),
        ):
            value = discovery.get(key)
            if not value:
                raise ValidationError(_("%(label)s is missing from discovery.", label=label))
            endpoints[key] = self._usl_validate_url(value, label=label)
            if _origin(endpoints[key]) != _origin(issuer):
                raise ValidationError(
                    _("%(label)s must use the Pocket ID issuer origin.", label=label),
                )
        end_session_endpoint = discovery.get("end_session_endpoint")
        if end_session_endpoint:
            end_session_endpoint = self._usl_validate_url(
                end_session_endpoint,
                label=_("Pocket ID logout endpoint"),
            )
            if _origin(end_session_endpoint) != _origin(issuer):
                raise ValidationError(
                    _("Pocket ID logout endpoint must use the issuer origin."),
                )
        if "code" not in discovery.get("response_types_supported", []):
            raise ValidationError(
                _("Pocket ID discovery does not advertise the authorization-code flow."),
            )
        algorithms = discovery.get(
            "id_token_signing_alg_values_supported",
            [],
        )
        if "RS256" not in algorithms:
            raise ValidationError(
                _("Pocket ID discovery does not advertise RS256 ID-token signing."),
            )
        return {
            **endpoints,
            "issuer": discovered_issuer,
            "end_session_endpoint": end_session_endpoint or False,
            "prompt_values_supported": discovery.get("prompt_values_supported", []),
            "fresh_passkey_reauthentication_supported": bool(
                discovery.get("fresh_passkey_reauthentication_supported"),
            ),
            "token_endpoint_auth_methods_supported": discovery.get(
                "token_endpoint_auth_methods_supported",
                ["client_secret_basic"],
            ),
        }

    @api.model
    def _usl_pocketid_sign_configuration(self):
        """Return the environment-only Pocket ID client used for Sign authorization."""
        issuer = os.getenv("USL_POCKET_ID_ISSUER", "").strip()
        public_base_url = os.getenv("USL_POCKET_ID_ODOO_BASE_URL", "").strip()
        client_id = os.getenv("USL_POCKET_ID_SIGN_CLIENT_ID", "").strip()
        client_secret = os.getenv("USL_POCKET_ID_SIGN_CLIENT_SECRET", "")
        required_group = os.getenv("USL_POCKET_ID_SIGN_REQUIRED_GROUP", "").strip()
        missing = [
            name
            for name, value in (
                ("USL_POCKET_ID_ISSUER", issuer),
                ("USL_POCKET_ID_ODOO_BASE_URL", public_base_url),
                ("USL_POCKET_ID_SIGN_CLIENT_ID", client_id),
                ("USL_POCKET_ID_SIGN_CLIENT_SECRET", client_secret),
                ("USL_POCKET_ID_SIGN_REQUIRED_GROUP", required_group),
            )
            if not value
        ]
        if missing:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        public_base_url = self._usl_validate_url(
            public_base_url,
            label=_("Odoo public base URL"),
            allow_path=False,
            allow_query=False,
        )
        discovery = self._usl_discover_pocketid(issuer)
        supported_methods = set(discovery["token_endpoint_auth_methods_supported"])
        requested_method = os.getenv("USL_POCKET_ID_TOKEN_AUTH_METHOD", "").strip()
        token_auth_method = requested_method or (
            "client_secret_basic"
            if "client_secret_basic" in supported_methods
            else "client_secret_post"
        )
        if token_auth_method not in _SUPPORTED_TOKEN_AUTH_METHODS or token_auth_method not in supported_methods:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        strict_required = _env_enabled("USL_POCKET_ID_SIGN_FRESH_REQUIRED")
        strict_supported = discovery["fresh_passkey_reauthentication_supported"]
        # Strong Sign has no permissive mode. The explicit environment switch
        # makes deployment intent auditable, while discovery proves that the
        # selected Pocket ID build can actually enforce a fresh passkey.
        if not strict_required or not strict_supported:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        if "login" not in discovery["prompt_values_supported"]:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        return PocketIDClientConfiguration(
            issuer=discovery["issuer"],
            authorization_endpoint=discovery["authorization_endpoint"],
            token_endpoint=discovery["token_endpoint"],
            jwks_uri=discovery["jwks_uri"],
            client_id=client_id,
            client_secret=client_secret,
            required_group=required_group,
            redirect_uri=f"{public_base_url}/sign/pocketid/callback",
            token_auth_method=token_auth_method,
            scopes="openid profile email groups",
            fresh_passkey_supported=strict_supported,
            discovery_snapshot={
                "issuer": discovery["issuer"],
                "authorization_endpoint": discovery["authorization_endpoint"],
                "token_endpoint": discovery["token_endpoint"],
                "jwks_uri": discovery["jwks_uri"],
                "prompt_values_supported": discovery["prompt_values_supported"],
                "token_endpoint_auth_methods_supported": discovery[
                    "token_endpoint_auth_methods_supported"
                ],
                "fresh_passkey_reauthentication_supported": strict_supported,
            },
        )

    @api.model
    def _usl_pocketid_exchange_code_for_client(
        self,
        configuration,
        *,
        code,
        code_verifier,
    ):
        if not code or len(code) > 4096:
            raise PocketIDAccessDenied(PocketIDReason.PROVIDER_DENIED)
        data = {
            "client_id": configuration.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": configuration.redirect_uri,
        }
        auth = None
        if configuration.token_auth_method == "client_secret_post":
            data["client_secret"] = configuration.client_secret
        else:
            auth = (configuration.client_id, configuration.client_secret)
        try:
            response = requests.post(
                configuration.token_endpoint,
                data=data,
                auth=auth,
                timeout=10,
            )
            response.raise_for_status()
            token_response = response.json()
        except (requests.RequestException, ValueError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            reason = (
                PocketIDReason.PROVIDER_DENIED
                if status is not None and 400 <= status < 500
                else PocketIDReason.PROVIDER_UNAVAILABLE
            )
            raise PocketIDAccessDenied(reason) from error
        access_token = token_response.get("access_token")
        id_token = token_response.get("id_token")
        if (
            not isinstance(access_token, str)
            or not isinstance(id_token, str)
            or str(token_response.get("token_type", "Bearer")).lower() != "bearer"
            or token_response.get("refresh_token")
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        return access_token, id_token

    @api.model
    def _usl_pocketid_validate_id_token_for_client(
        self,
        configuration,
        *,
        id_token,
        access_token,
        nonce,
    ):
        provider = self.env.ref("usl_pocketid.provider_pocketid").sudo()
        try:
            header = jwt.get_unverified_header(id_token)
        except JWTError as error:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID) from error
        if header.get("alg") not in _SUPPORTED_SIGNING_ALGORITHMS:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        # The Sign and login clients share the same governed issuer and JWKS.
        if provider.jwks_uri != configuration.jwks_uri:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        keys = provider._usl_get_signing_keys(header.get("kid"))
        claims = None
        last_error = None
        for key in keys:
            try:
                claims = jwt.decode(
                    id_token,
                    key,
                    algorithms=list(_SUPPORTED_SIGNING_ALGORITHMS),
                    audience=configuration.client_id,
                    issuer=configuration.issuer,
                    access_token=access_token,
                    options={
                        "require_aud": True,
                        "require_exp": True,
                        "require_iat": True,
                        "require_iss": True,
                        "require_sub": True,
                        "leeway": 60,
                    },
                )
                break
            except (JWTError, JWSError) as error:
                last_error = error
        if claims is None:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID) from last_error
        claim_nonce = claims.get("nonce")
        subject = claims.get("sub")
        if (
            not isinstance(claim_nonce, str)
            or not secrets.compare_digest(claim_nonce, nonce)
            or not isinstance(subject, str)
            or not subject
            or len(subject) > 255
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if (
            isinstance(audience, list)
            and len(audience) > 1
            and authorized_party != configuration.client_id
        ) or (authorized_party and authorized_party != configuration.client_id):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        groups = claims.get("groups", [])
        if isinstance(groups, str):
            groups = [groups]
        if not isinstance(groups, list) or configuration.required_group not in groups:
            raise PocketIDAccessDenied(PocketIDReason.GROUP_REQUIRED)
        return claims, keys

    @api.model
    def _usl_disable_default_odoo_oauth(self):
        default_odoo_provider = self.env.ref(
            "auth_oauth.provider_openerp",
            raise_if_not_found=False,
        )
        if not default_odoo_provider or not default_odoo_provider.enabled:
            return
        default_odoo_provider.sudo().write({"enabled": False})
        self.env["usl.oidc.audit.event"]._record(
            event_type="configuration",
            reason_code="default_odoo_oauth_disabled",
            provider_id=default_odoo_provider.id,
        )

    @api.model
    def _usl_pocketid_apply_environment(self):
        provider = self.env.ref("usl_pocketid.provider_pocketid").sudo()
        self._usl_disable_default_odoo_oauth()
        if not _env_enabled("USL_POCKET_ID_ENABLED"):
            provider._usl_pocketid_environment_write(
                {"enabled": False, "client_secret": False},
            )
            self.env["usl.oidc.audit.event"]._record(
                event_type="configuration",
                reason_code="environment_disabled",
                provider_id=provider.id,
            )
            return False

        issuer = os.getenv("USL_POCKET_ID_ISSUER", "").strip()
        client_id = os.getenv("USL_POCKET_ID_CLIENT_ID", "").strip()
        client_secret = os.getenv("USL_POCKET_ID_CLIENT_SECRET", "")
        public_base_url = os.getenv("USL_POCKET_ID_ODOO_BASE_URL", "").strip()
        required_group = os.getenv("USL_POCKET_ID_REQUIRED_GROUP", "").strip()
        scopes = os.getenv("USL_POCKET_ID_SCOPES", _DEFAULT_SCOPES).strip()
        missing = [
            name
            for name, value in (
                ("USL_POCKET_ID_ISSUER", issuer),
                ("USL_POCKET_ID_CLIENT_ID", client_id),
                ("USL_POCKET_ID_CLIENT_SECRET", client_secret),
                ("USL_POCKET_ID_ODOO_BASE_URL", public_base_url),
                ("USL_POCKET_ID_REQUIRED_GROUP", required_group),
            )
            if not value
        ]
        if missing:
            raise ValidationError(
                _("Pocket ID is enabled but required variables are missing: %s")
                % ", ".join(missing),
            )
        scope_set = set(scopes.split())
        if not {"openid", "email", "groups"}.issubset(scope_set):
            raise ValidationError(
                _("Pocket ID scopes must include openid, email and groups."),
            )
        public_base_url = self._usl_validate_url(
            public_base_url,
            label=_("Odoo public base URL"),
            allow_path=False,
            allow_query=False,
        )
        discovery = self._usl_discover_pocketid(issuer)
        requested_auth_method = os.getenv(
            "USL_POCKET_ID_TOKEN_AUTH_METHOD",
            "",
        ).strip()
        supported_auth_methods = set(
            discovery["token_endpoint_auth_methods_supported"],
        )
        if requested_auth_method:
            if requested_auth_method not in _SUPPORTED_TOKEN_AUTH_METHODS:
                raise ValidationError(
                    _("Unsupported Pocket ID token authentication method."),
                )
            token_auth_method = requested_auth_method
        elif "client_secret_basic" in supported_auth_methods:
            token_auth_method = "client_secret_basic"
        else:
            token_auth_method = "client_secret_post"
        if token_auth_method not in supported_auth_methods:
            raise ValidationError(
                _("Pocket ID does not advertise the selected client authentication."),
            )

        provider._usl_pocketid_environment_write(
            {
                "name": "Pocket ID",
                "flow": "id_token_code",
                "client_id": client_id,
                "client_secret": False,
                "auth_endpoint": discovery["authorization_endpoint"],
                "token_endpoint": discovery["token_endpoint"],
                "jwks_uri": discovery["jwks_uri"],
                "validation_endpoint": False,
                "scope": scopes,
                "body": _("Log in with Pocket ID"),
                "enabled": True,
                "usl_pocketid": True,
                "usl_oidc_issuer": discovery["issuer"],
                "usl_public_base_url": public_base_url,
                "usl_required_group": required_group,
                "usl_end_session_endpoint": discovery["end_session_endpoint"],
                "usl_allow_unique_email_link": _env_enabled(
                    "USL_POCKET_ID_ALLOW_UNIQUE_EMAIL_LINK",
                ),
                "usl_token_auth_method": token_auth_method,
            },
        )
        self.env["usl.oidc.audit.event"]._record(
            event_type="configuration",
            reason_code="environment_enabled",
            provider_id=provider.id,
        )
        return True

    def _usl_pocketid_redirect_uri(self):
        self.ensure_one()
        if not self.usl_public_base_url:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        return f"{self.usl_public_base_url.rstrip('/')}/auth_oauth/signin"

    def _usl_pocketid_client_secret(self):
        self.ensure_one()
        secret = os.getenv("USL_POCKET_ID_CLIENT_SECRET", "")
        if not secret:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        return secret

    def _usl_exchange_code(self, *, code, code_verifier, redirect_uri):
        self.ensure_one()
        if not self.usl_pocketid or not self.enabled:
            raise PocketIDAccessDenied(PocketIDReason.CONFIGURATION)
        if not code or len(code) > 4096:
            raise PocketIDAccessDenied(PocketIDReason.PROVIDER_DENIED)
        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
        auth = None
        client_secret = self._usl_pocketid_client_secret()
        if self.usl_token_auth_method == "client_secret_post":
            data["client_secret"] = client_secret
        else:
            auth = (self.client_id, client_secret)
        try:
            response = requests.post(
                self.token_endpoint,
                data=data,
                auth=auth,
                timeout=10,
            )
            response.raise_for_status()
            token_response = response.json()
        except (requests.RequestException, ValueError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            _logger.warning(
                "Pocket ID token exchange failed%s.",
                f" with HTTP {status}" if status else "",
            )
            reason = (
                PocketIDReason.PROVIDER_DENIED
                if status is not None and 400 <= status < 500
                else PocketIDReason.PROVIDER_UNAVAILABLE
            )
            raise PocketIDAccessDenied(reason) from error
        access_token = token_response.get("access_token")
        id_token = token_response.get("id_token")
        token_type = token_response.get("token_type", "Bearer")
        if (
            not isinstance(access_token, str)
            or not isinstance(id_token, str)
            or str(token_type).lower() != "bearer"
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        return access_token, id_token

    def _usl_get_signing_keys(self, kid):
        self.ensure_one()
        try:
            response = requests.get(self.jwks_uri, timeout=10)
            response.raise_for_status()
        except requests.RequestException as error:
            _logger.warning("Pocket ID JWKS could not be loaded safely.")
            raise PocketIDAccessDenied(
                PocketIDReason.PROVIDER_UNAVAILABLE,
            ) from error
        content_length = response.headers.get("Content-Length")
        try:
            declared_size = int(content_length) if content_length else 0
        except (TypeError, ValueError) as error:
            _logger.warning("Pocket ID JWKS returned an invalid content length.")
            raise PocketIDAccessDenied(
                PocketIDReason.PROVIDER_UNAVAILABLE,
            ) from error
        if declared_size > _MAX_JWKS_BYTES or len(response.content) > _MAX_JWKS_BYTES:
            _logger.warning("Pocket ID JWKS exceeded the configured size limit.")
            raise PocketIDAccessDenied(PocketIDReason.PROVIDER_UNAVAILABLE)
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            _logger.warning("Pocket ID JWKS could not be loaded safely.")
            raise PocketIDAccessDenied(
                PocketIDReason.PROVIDER_UNAVAILABLE,
            ) from error
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if (
            not isinstance(keys, list)
            or not keys
            or len(keys) > _MAX_JWKS_KEYS
            or any(not isinstance(key, dict) for key in keys)
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        if kid is None and len(keys) != 1:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        matching_keys = [
            key
            for key in keys
            if (
                (kid is None or key.get("kid") == kid)
                and key.get("kty") == "RSA"
                and key.get("use", "sig") == "sig"
                and key.get("alg", "RS256") == "RS256"
            )
        ]
        if not matching_keys:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        return matching_keys

    def _usl_validate_id_token(self, *, id_token, access_token, nonce):
        self.ensure_one()
        try:
            header = jwt.get_unverified_header(id_token)
        except JWTError as error:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID) from error
        algorithm = header.get("alg")
        if algorithm not in _SUPPORTED_SIGNING_ALGORITHMS:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        keys = self._usl_get_signing_keys(header.get("kid"))
        last_error = None
        claims = None
        for key in keys:
            try:
                claims = jwt.decode(
                    id_token,
                    key,
                    algorithms=list(_SUPPORTED_SIGNING_ALGORITHMS),
                    audience=self.client_id,
                    issuer=self.usl_oidc_issuer,
                    access_token=access_token,
                    options={
                        "require_aud": True,
                        "require_exp": True,
                        "require_iat": True,
                        "require_iss": True,
                        "require_sub": True,
                        "leeway": 60,
                    },
                )
                break
            except (JWTError, JWSError) as error:
                last_error = error
        if claims is None:
            _logger.warning(
                "Pocket ID ID-token validation failed: %s.",
                type(last_error).__name__ if last_error else "no_matching_key",
            )
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID) from last_error
        claim_nonce = claims.get("nonce")
        if (
            not isinstance(claim_nonce, str)
            or not secrets.compare_digest(claim_nonce, nonce)
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if (
            isinstance(audience, list)
            and len(audience) > 1
            and authorized_party != self.client_id
        ):
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        if authorized_party and authorized_party != self.client_id:
            raise PocketIDAccessDenied(PocketIDReason.TOKEN_INVALID)
        groups = claims.get("groups", [])
        if isinstance(groups, str):
            groups = [groups]
        if (
            not isinstance(groups, list)
            or self.usl_required_group not in groups
        ):
            raise PocketIDAccessDenied(PocketIDReason.GROUP_REQUIRED)
        return claims
