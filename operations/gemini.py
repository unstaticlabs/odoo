"""A minimal Gemini client for release tooling, using only the standard library.

This mirrors ``GeminiClient.generate_structured_feedback`` of
``custom-addons/usl_feedback/services/gemini.py``: one stateless structured
``generateContent`` call, no tools, no stored state, header authentication, and
the same refusal to relay provider prose.

The duplication is deliberate. The feedback client cannot be imported here:

* It starts with ``import requests``. The ``release`` job of
  ``.github/workflows/product-image.yml`` has no ``setup-python`` and no
  ``pip install``, so release tooling must run on the standard library alone.
* Moving the shared code into the addon would change a file under
  ``custom-addons/usl_feedback``, which changes that module's ``source_sha256``
  in the sealed action-risk surface. Refreshing that seal needs a live Odoo
  runtime. ``operations/`` is outside the sealed surface.

The two clients therefore share a contract, not an implementation.
``scripts/tests/test_gemini_operations.py`` reads the addon file as text and
fails when the model constants drift apart.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
API_REVISION = "2026-05-20"
# The cheapest approved model: the feedback assistant's own degraded path.
MODEL = "gemini-3.5-flash-lite"
TIMEOUT = 30
MAXIMUM_OUTPUT_TOKENS = 4096
ERROR_NETWORK = "network"
ERROR_TIMEOUT = "timeout"
ERROR_INVALID_RESPONSE = "invalid_response"
# The provider's own error codes, which carry no prompt text.
SAFE_CODES = frozenset({
    "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED", "UNAUTHENTICATED",
    "PERMISSION_DENIED", "NOT_FOUND", "INTERNAL", "UNAVAILABLE",
})


class GeminiError(RuntimeError):
    """Gemini could not be reached, or did not answer usefully."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the API key is never replayed at another host."""

    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def _safe_error(status: int, body: bytes) -> str:
    """Describe a failure without relaying prose that can echo prompts or keys."""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return f"HTTP {status}"
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("status") if isinstance(error, dict) else None
    if isinstance(code, str) and code in SAFE_CODES:
        return f"HTTP {status} ({code})"
    return f"HTTP {status}"


class GeminiClient:
    """One structured, stateless completion. No tools and no stored state."""

    def __init__(self, api_key: str, *, model: str = MODEL, opener=None):
        if not api_key:
            raise GeminiError("configuration", "No Gemini API key is configured.")
        self.api_key = api_key
        self.model = model
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Api-Revision": API_REVISION,
                "Content-Type": "application/json",
                # A header, never a query parameter: a URL survives into logs
                # and tracebacks, where GitHub's secret masking does not reach.
                "x-goog-api-key": self.api_key,
            },
        )
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise GeminiError(
                f"http_{error.code}", _safe_error(error.code, error.read() or b""),
            ) from error
        except TimeoutError as error:
            raise GeminiError(ERROR_TIMEOUT, "Gemini did not answer in time.") from error
        except (urllib.error.URLError, OSError) as error:
            raise GeminiError(ERROR_NETWORK, "Gemini could not be reached.") from error
        try:
            result = json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned invalid JSON.")
        return result

    def structured(self, *, system_instruction: str, prompt: str, schema: dict) -> dict:
        """Return the parsed JSON object Gemini produced for this prompt."""
        response = self._post(
            f"{MODELS_ENDPOINT}/{self.model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": MAXIMUM_OUTPUT_TOKENS,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                },
            },
        )
        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as error:
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned no answer.") from error
        text = "".join(
            str(part.get("text") or "") for part in parts if isinstance(part, dict)
        ).strip()
        # Accept a single JSON fence, never extract JSON from arbitrary prose.
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4].strip()
        try:
            result = json.loads(text)
        except (ValueError, json.JSONDecodeError) as error:
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise GeminiError(ERROR_INVALID_RESPONSE, "Gemini returned invalid JSON.")
        return result
