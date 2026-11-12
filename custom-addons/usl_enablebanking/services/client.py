"""Fixed AIS-only transport. Provider payloads and signing material are never logged."""
from datetime import datetime, timezone
from pathlib import Path
import os
from urllib.parse import quote, urlparse

import httpx
from jose import jwt

from .protocol import ProviderDataError

BASE_URL = "https://api.enablebanking.com"
EXPIRED_CODES = {"EXPIRED_SESSION", "CLOSED_SESSION", "SESSION_DOES_NOT_EXIST"}


class EnableBankingClient:
    def __init__(self, app_id, company_id):
        self.app_id = app_id
        self.key_file = os.environ.get(f"USL_ENABLEBANKING_KEY_FILE_{company_id}")

    def _token(self):
        if not self.app_id or not self.key_file:
            raise ProviderDataError("signing_not_configured")
        try:
            key = Path(self.key_file).read_bytes()
            now = int(datetime.now(timezone.utc).timestamp())
            return jwt.encode(
                {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 300},
                key, algorithm="RS256", headers={"kid": self.app_id, "typ": "JWT"},
            )
        except (OSError, ValueError, jwt.JWTError) as error:
            raise ProviderDataError("signing_not_configured") from error

    def request(self, method, path, *, params=None, data=None):
        # This object is private to the adapter; no RPC supplies a URL or path.
        try:
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                response = client.request(method, BASE_URL + path, params=params, json=data,
                                          headers={"Authorization": "Bearer " + self._token(), "Accept": "application/json"})
            if response.status_code != 200:
                try:
                    code = response.json().get("error")
                except (ValueError, AttributeError):
                    code = None
                if code == "ASPSP_PSU_ACTION_REQUIRED":
                    raise ProviderDataError("consent_renewal_required")
                if code in EXPIRED_CODES:
                    raise ProviderDataError("consent_expired")
                raise ProviderDataError("provider_unavailable")
            value = response.json()
            if not isinstance(value, dict):
                raise ProviderDataError("invalid_response")
            return value
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, ProviderDataError):
                raise
            raise ProviderDataError("provider_unavailable") from error

    def aspsps(self):
        value = self.request("GET", "/aspsps", params={"country": "FR", "psu_type": "personal", "service": "AIS"})
        banks = value.get("aspsps")
        if not isinstance(banks, list) or not all(isinstance(bank, dict) for bank in banks):
            raise ProviderDataError("invalid_response")
        return value

    def authorize(self, payload):
        result = self.request("POST", "/auth", data=payload)
        parsed = urlparse(result.get("url") or "")
        if parsed.scheme != "https" or parsed.hostname != "auth.enablebanking.com" or parsed.username or parsed.password:
            raise ProviderDataError("invalid_authorization_url")
        return result["url"]

    def session(self, code):
        return self.request("POST", "/sessions", data={"code": code})

    def transactions(self, uid, params):
        return self.request("GET", "/accounts/" + quote(uid, safe="") + "/transactions", params=params)

    def balances(self, uid):
        return self.request("GET", "/accounts/" + quote(uid, safe="") + "/balances")
