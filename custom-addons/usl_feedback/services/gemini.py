import json
from urllib.parse import urlsplit

import requests

INTERACTIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
API_REVISION = "2026-05-20"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
ERROR_TIMEOUT = "timeout"
ERROR_NETWORK = "network"
ERROR_INVALID_RESPONSE = "invalid_response"
ERROR_INVALID_INTERACTION = "invalid_interaction"
ERROR_CONNECTION_INCOMPLETE = "connection_incomplete"
ERROR_CONNECTION_INVALID = "connection_invalid"
ERROR_CONNECTION_UNVERIFIED = "connection_unverified"
CONNECTION_TEST_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "read_only_verified": {"type": "boolean"},
    },
    "required": ["project_name", "read_only_verified"],
    "additionalProperties": False,
}


class GeminiError(Exception):
    def __init__(self, code, message, *, retryable=False, status_code=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


def _safe_error(response):
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return f"HTTP {response.status_code}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        status = error.get("status")
        return str(status or f"HTTP {response.status_code}")
    return f"HTTP {response.status_code}"


class GeminiClient:
    def __init__(self, *, api_key, session=None):
        self.api_key = api_key
        self.session = session or requests.Session()

    @property
    def _headers(self):
        return {
            "Api-Revision": API_REVISION,
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

    def _request(self, method, url, **kwargs):
        try:
            response = self.session.request(
                method,
                url,
                headers={**self._headers, **kwargs.pop("headers", {})},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=False,
                **kwargs,
            )
        except requests.Timeout as error:
            raise GeminiError(
                ERROR_TIMEOUT, "Gemini did not answer in time.", retryable=True,
            ) from error
        except requests.RequestException as error:
            raise GeminiError(
                ERROR_NETWORK, "Gemini could not be reached.", retryable=True,
            ) from error
        if response.status_code >= 400:
            retryable = response.status_code in {408, 409, 429} or response.status_code >= 500
            raise GeminiError(
                f"http_{response.status_code}",
                _safe_error(response),
                retryable=retryable,
                status_code=response.status_code,
            )
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned invalid JSON.") from error

    def create_interaction(self, payload):
        return self._request("POST", INTERACTIONS_ENDPOINT, json=payload)

    def get_interaction(self, interaction_id):
        if not interaction_id or "/" in interaction_id:
            raise GeminiError(
                ERROR_INVALID_INTERACTION, "Gemini interaction identifier is invalid.",
            )
        return self._request("GET", f"{INTERACTIONS_ENDPOINT}/{interaction_id}")

    def test_model(self, model):
        return self._request("GET", f"{MODELS_ENDPOINT}/{model}")

    def test_mcp_interaction(self, *, model, mcp_url, mcp_headers):
        response = self.create_interaction(
            {
                "model": model,
                "background": False,
                "store": False,
                "system_instruction": (
                    "This is a read-only connection diagnostic. Use the odoo_projects MCP "
                    "server to find the Project named Odoo Product Feedback. Do not call or "
                    "attempt any write operation. Return only the requested JSON."
                ),
                "input": (
                    "Read the Odoo Projects catalogue and report the exact feedback Project "
                    "name. Set read_only_verified to true only after the MCP read succeeds."
                ),
                "tools": [
                    {
                        "type": "mcp_server",
                        "name": "odoo_projects",
                        "url": mcp_url,
                        "headers": mcp_headers,
                    },
                ],
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": CONNECTION_TEST_SCHEMA,
                },
            },
        )
        if response.get("status") != "completed":
            raise GeminiError(
                ERROR_CONNECTION_INCOMPLETE,
                "Gemini did not complete the MCP connection diagnostic.",
            )
        try:
            result = json.loads(self._response_text(response))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise GeminiError(
                ERROR_CONNECTION_INVALID, "Gemini returned an invalid connection diagnostic.",
            ) from error
        if result != {
            "project_name": "Odoo Product Feedback",
            "read_only_verified": True,
        }:
            raise GeminiError(
                ERROR_CONNECTION_UNVERIFIED,
                "Gemini could not verify read-only access to the feedback Project.",
            )
        return result

    @staticmethod
    def _response_text(response):
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        for output in response.get("outputs") or response.get("output") or []:
            if isinstance(output, dict):
                if isinstance(output.get("text"), str):
                    return output["text"]
                for content in output.get("content") or []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        return content["text"]
        raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini did not return text output.")

    @staticmethod
    def validate_mcp_url(url):
        parsed = urlsplit((url or "").strip())
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in ({"http", "https"} if local else {"https"}):
            message = "The MCP URL must use HTTPS outside local development."
            raise ValueError(message)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            message = "The MCP URL cannot contain credentials, a query, or a fragment."
            raise ValueError(message)
        if not parsed.netloc or parsed.path.rstrip("/") != "/mcp/projects":
            message = "The MCP URL must point exactly to /mcp/projects."
            raise ValueError(message)
        return parsed.geturl()
