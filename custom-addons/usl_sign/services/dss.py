import base64
import os

import requests


class DSSServiceError(RuntimeError):
    pass


class DSSRejectedError(DSSServiceError):
    """The authoritative service reached a deterministic negative result."""


class DSSUnavailableError(DSSServiceError):
    """The authoritative service could not provide a result."""


class DSSClient:
    """Fail-closed client for the internal DSS authority."""

    def __init__(self, *, base_url=None, timeout=None, session=None):
        self.base_url = (base_url or os.getenv("USL_SIGN_DSS_URL", "")).rstrip("/")
        self.timeout = timeout or float(os.getenv("USL_SIGN_DSS_TIMEOUT", "30"))
        self.session = session or requests.Session()
        self.client_cert = os.getenv("USL_SIGN_DSS_CLIENT_CERT", "")
        self.client_key = os.getenv("USL_SIGN_DSS_CLIENT_KEY", "")
        self.ca_bundle = os.getenv("USL_SIGN_DSS_CA_BUNDLE", "")
        self.allow_plaintext = os.getenv("USL_SIGN_DSS_ALLOW_PLAINTEXT") == "1"

    def _call(self, operation, payload):
        if not self.base_url:
            msg = "The internal DSS service is not configured."
            raise DSSUnavailableError(msg)
        if not self.base_url.startswith("https://") and not self.allow_plaintext:
            msg = "The internal DSS service must use mutual TLS."
            raise DSSUnavailableError(msg)
        if self.base_url.startswith("https://") and not (
            self.client_cert and self.client_key and self.ca_bundle
        ):
            msg = "The DSS mutual-TLS credentials are incomplete."
            raise DSSUnavailableError(msg)
        try:
            response = self.session.post(
                f"{self.base_url}/v1/{operation}",
                json=payload,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
                cert=(self.client_cert, self.client_key)
                if self.base_url.startswith("https://")
                else None,
                verify=self.ca_bundle if self.base_url.startswith("https://") else True,
            )
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            msg = "The internal signature service is unavailable."
            raise DSSUnavailableError(
                msg,
            ) from error
        if response.status_code >= 500:
            raise DSSUnavailableError(
                result.get("error") or "The internal signature service is unavailable.",
            )
        if response.status_code >= 400 or not result.get("ok"):
            raise DSSRejectedError(
                result.get("error") or "DSS rejected the operation.",
            )
        return result

    @staticmethod
    def _document(data):
        return base64.b64encode(data).decode()

    def health(self):
        return self._call("health", {})

    def seal(self, data, *, request_reference, timestamp=False):
        return self._call(
            "pades/seal",
            {
                "document": self._document(data),
                "requestReference": request_reference,
                "timestamp": bool(timestamp),
            },
        )

    def data_to_sign(
        self,
        data,
        certificate_pem,
        *,
        certificate_chain=None,
        request_reference,
        timestamp=False,
        appearance=None,
    ):
        return self._call(
            "pades/data-to-sign",
            {
                "document": self._document(data),
                "certificate": certificate_pem,
                "certificateChain": list(certificate_chain or []),
                "requestReference": request_reference,
                "timestamp": bool(timestamp),
                "appearance": appearance,
            },
        )

    def embed_signature(
        self,
        data,
        certificate_pem,
        signature,
        *,
        request_reference,
        signing_context,
    ):
        return self._call(
            "pades/embed",
            {
                "document": self._document(data),
                "certificate": certificate_pem,
                "signature": base64.b64encode(signature).decode(),
                "requestReference": request_reference,
                "signingContext": signing_context,
            },
        )

    def prepare_signing_fields(self, data, fields):
        return self._signing_fields_call("pdf/prepare-signing-fields", data, fields)

    def fill_signing_fields(self, data, fields):
        return self._signing_fields_call("pdf/fill-signing-fields", data, fields)

    def _signing_fields_call(self, endpoint, data, fields):
        result = self._call(
            endpoint,
            {
                "document": self._document(data),
                "fields": [
                    {
                        key: (
                            self._document(value)
                            if key == "document"
                            else value
                        )
                        for key, value in field.items()
                    }
                    for field in fields
                ],
            },
        )
        try:
            document = base64.b64decode(result["document"], validate=True)
        except (KeyError, TypeError, ValueError) as error:
            msg = "The signature service returned an invalid PDF candidate."
            raise DSSServiceError(msg) from error
        if not document.startswith(b"%PDF-"):
            msg = "The signature service returned an invalid PDF candidate."
            raise DSSServiceError(msg)
        return document

    def validate(self, data, *, expected_level=None, expected_signers=None):
        return self._call(
            "pades/validate",
            {
                "document": self._document(data),
                "expectedLevel": expected_level,
                "expectedSigners": list(expected_signers or []),
            },
        )

    def revision_matches(self, frozen_data, signed_data):
        return self._call(
            "pades/revision-match",
            {
                "frozenDocument": self._document(frozen_data),
                "signedDocument": self._document(signed_data),
            },
        )

    def sign_manifest(self, manifest):
        return self._call(
            "manifest/sign",
            {"manifest": self._document(manifest)},
        )

    def build_dossier(self, *, title, summary, artifacts, cover=None):
        payload = {
            "title": title,
            "summary": list(summary),
            "artifacts": [
                {
                    "name": artifact["name"],
                    "content": self._document(artifact["content"]),
                    "mimeType": artifact.get("mimetype")
                    or "application/octet-stream",
                    "relationship": artifact.get("relationship") or "Supplement",
                    "description": artifact.get("description")
                    or "Document signing evidence artifact",
                }
                for artifact in artifacts
            ],
        }
        if cover:
            payload["coverDocument"] = self._document(cover)
        return self._call("dossier/build", payload)

    def validate_pdfa(self, data):
        return self._call("pdfa/validate", {"document": self._document(data)})

    def cross_validate(self, data):
        return self._call("pades/cross-validate", {"document": self._document(data)})
