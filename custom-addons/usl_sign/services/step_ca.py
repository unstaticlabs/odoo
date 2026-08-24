import base64
import hashlib
import json
import os
import re
import secrets
import time

import requests
from jose import jwt


class StepCAError(RuntimeError):
    pass


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class StepCAClient:
    """Issue one-use short-lived certificates from a mounted JWK provisioner."""

    def __init__(self, *, base_url=None, provisioner=None, jwk_file=None, timeout=15):
        self.base_url = (base_url or os.getenv("USL_SIGN_STEP_CA_URL", "")).rstrip("/")
        self.provisioner = provisioner or os.getenv(
            "USL_SIGN_STEP_CA_PROVISIONER", "usl-sign",
        )
        self.jwk_file = jwk_file or os.getenv("USL_SIGN_STEP_CA_JWK_FILE", "")
        self.ca_bundle = os.getenv("USL_SIGN_STEP_CA_CA_BUNDLE", "")
        self.timeout = timeout

    def _check_connection_configuration(self):
        if not self.base_url:
            msg = "The local certificate authority is not configured."
            raise StepCAError(msg)
        if not self.base_url.startswith("https://") or not self.ca_bundle:
            msg = "The local certificate authority requires a trusted HTTPS connection."
            raise StepCAError(msg)

    def health(self):
        """Verify the configured CA over its trusted HTTPS connection."""
        self._check_connection_configuration()
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=self.timeout,
                verify=self.ca_bundle,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            msg = "The local certificate authority is unavailable."
            raise StepCAError(msg) from error
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            msg = "The local certificate authority returned an unhealthy response."
            raise StepCAError(msg)
        return payload

    def _private_jwk(self):
        if not self.jwk_file:
            msg = "The certificate provisioner is not configured."
            raise StepCAError(msg)
        try:
            with open(self.jwk_file, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as error:
            msg = "The certificate provisioner cannot be loaded."
            raise StepCAError(msg) from error

    def _token(self, csr_pem, subject, binding):
        key_data = self._private_jwk()
        algorithm = key_data.get("alg") or (
            "ES256" if key_data.get("kty") == "EC" else "RS256"
        )
        if key_data.get("kty") == "EC":
            thumbprint = {
                name: key_data[name] for name in ("crv", "kty", "x", "y")
            }
        elif key_data.get("kty") == "RSA":
            thumbprint = {name: key_data[name] for name in ("e", "kty", "n")}
        else:
            msg = "The provisioner key type is not supported."
            raise StepCAError(msg)
        kid = key_data.get("kid") or _b64url(
            hashlib.sha256(
                json.dumps(thumbprint, sort_keys=True, separators=(",", ":")).encode(),
            ).digest(),
        )
        now = int(time.time())
        required_binding = {
            "usl_signer",
            "usl_enrollment",
            "usl_request",
            "usl_role",
            "usl_document_sha256",
            "usl_policy_sha256",
            "usl_public_key_sha256",
        }
        if required_binding - set(binding):
            msg = "The certificate authorization binding is incomplete."
            raise StepCAError(msg)
        for digest_name in (
            "usl_document_sha256",
            "usl_policy_sha256",
            "usl_public_key_sha256",
        ):
            digest = str(binding[digest_name])
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                msg = "A certificate authorization digest is invalid."
                raise StepCAError(msg)
        token_id = secrets.token_urlsafe(24)
        claims = {
            "iss": self.provisioner,
            "sub": subject,
            "aud": f"{self.base_url}/1.0/sign",
            "iat": now,
            "nbf": now - 5,
            "exp": now + 60,
            "jti": token_id,
            "sha": _b64url(hashlib.sha256(csr_pem.encode()).digest()),
            "usl_subject": subject,
            "usl_csr_sha256": hashlib.sha256(csr_pem.encode()).hexdigest(),
            **{name: str(value) for name, value in binding.items()},
        }
        token = jwt.encode(claims, key_data, algorithm=algorithm, headers={"kid": kid})
        return token, {
            "authorization_id": token_id,
            "authorization_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "csr_sha256": claims["usl_csr_sha256"],
            "expires_at": now + 60,
        }

    def issue(self, csr_pem, *, subject, binding):
        self._check_connection_configuration()
        token, receipt = self._token(csr_pem, subject, binding)
        try:
            response = requests.post(
                f"{self.base_url}/1.0/sign",
                json={
                    "csr": csr_pem,
                    "ott": token,
                    "notBefore": "-30s",
                    "notAfter": "10m",
                },
                timeout=self.timeout,
                verify=self.ca_bundle,
            )
            if response.status_code >= 400:
                try:
                    detail = str(response.json().get("message") or "request rejected")
                except (ValueError, AttributeError):
                    detail = "request rejected"
                detail = re.sub(r"eyJ[A-Za-z0-9_.-]+", "[redacted-token]", detail)
                detail = re.sub(r"[\r\n\t]+", " ", detail).strip()[:240]
                raise StepCAError(
                    f"The local certificate authority rejected issuance "
                    f"(HTTP {response.status_code}: {detail}).",
                )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            msg = "The local certificate authority is unavailable."
            raise StepCAError(msg) from error
        certificate = payload.get("crt")
        chain = payload.get("ca") or payload.get("certChain") or []
        if not certificate:
            msg = "The local certificate authority returned no certificate."
            raise StepCAError(msg)
        if isinstance(chain, str):
            chain = [chain]
        receipt.update(
            {
                "certificate_sha256": hashlib.sha256(certificate.encode()).hexdigest(),
                "chain_sha256": [
                    hashlib.sha256(item.encode()).hexdigest() for item in chain
                ],
            },
        )
        return {"certificate": certificate, "chain": chain, "receipt": receipt}
