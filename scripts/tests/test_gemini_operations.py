"""The release tooling's Gemini client must never leak a key or provider prose.

The client is a deliberate mirror of the feedback assistant's client rather
than an import of it, so these tests pin the contract the two share.
"""

from __future__ import annotations

import io
import json
import re
import unittest
import urllib.error
from pathlib import Path

from operations.gemini import (
    API_REVISION,
    MAXIMUM_OUTPUT_TOKENS,
    MODEL,
    GeminiClient,
    GeminiError,
    _NoRedirect,
)


ROOT = Path(__file__).resolve().parents[2]
FEEDBACK_CLIENT = ROOT / "custom-addons/usl_feedback/services/gemini.py"
API_KEY = "test-key-2f8c41d0"
SCHEMA = {"type": "object", "properties": {"overview": {"type": "string"}}}


def answer(payload: object) -> bytes:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
    }).encode()


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_exception) -> bool:
        return False


class FakeOpener:
    """Record the request and reply with a canned body or an error."""

    def __init__(self, body: bytes = b"{}", error: Exception | None = None):
        self.body = body
        self.error = error
        self.requests: list = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.body)


def client(opener: FakeOpener) -> GeminiClient:
    return GeminiClient(API_KEY, opener=opener)


def http_error(status: int, body: object) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com/", status, "error", {},
        io.BytesIO(json.dumps(body).encode()),
    )


class GeminiRequestTests(unittest.TestCase):
    def test_request_is_a_stateless_structured_completion(self) -> None:
        opener = FakeOpener(answer({"overview": "Everything is calmer."}))
        result = client(opener).structured(
            system_instruction="Write plainly.", prompt="#1 fix: keep", schema=SCHEMA,
        )
        self.assertEqual(result, {"overview": "Everything is calmer."})
        request, timeout = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"/{MODEL}:generateContent",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 30)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["x-goog-api-key"], API_KEY)
        self.assertEqual(headers["api-revision"], API_REVISION)
        payload = json.loads(request.data)
        self.assertEqual(
            payload["generationConfig"],
            {
                "maxOutputTokens": MAXIMUM_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseJsonSchema": SCHEMA,
            },
        )
        # No tools and no stored state: an injected instruction has nothing to
        # reach for, and no prompt is retained by the provider.
        self.assertNotIn("tools", payload)
        self.assertNotIn("store", payload)
        self.assertNotIn("previous_interaction_id", payload)

    def test_the_key_never_travels_in_the_url(self) -> None:
        opener = FakeOpener(answer({"overview": "ok"}))
        client(opener).structured(system_instruction="a", prompt="b", schema=SCHEMA)
        request, _timeout = opener.requests[0]
        self.assertNotIn(API_KEY, request.full_url)
        self.assertNotIn("key=", request.full_url)

    def test_a_model_override_selects_that_model(self) -> None:
        opener = FakeOpener(answer({"overview": "ok"}))
        GeminiClient(API_KEY, model="gemini-other", opener=opener).structured(
            system_instruction="a", prompt="b", schema=SCHEMA,
        )
        self.assertIn("/gemini-other:generateContent", opener.requests[0][0].full_url)

    def test_a_missing_key_is_refused_before_any_request(self) -> None:
        opener = FakeOpener()
        with self.assertRaises(GeminiError):
            GeminiClient("", opener=opener)
        self.assertEqual(opener.requests, [])

    def test_redirects_are_refused_so_the_key_is_never_replayed(self) -> None:
        # Returning ``None`` makes urllib raise instead of following, which is
        # the standard library equivalent of ``allow_redirects=False``.
        self.assertIsNone(
            _NoRedirect().redirect_request(
                None, None, 302, "Found", {}, "https://elsewhere.example/",
            ),
        )


class GeminiErrorTests(unittest.TestCase):
    def failure(self, error: Exception) -> GeminiError:
        with self.assertRaises(GeminiError) as raised:
            client(FakeOpener(error=error)).structured(
                system_instruction="a", prompt="b", schema=SCHEMA,
            )
        return raised.exception

    def test_provider_prose_is_never_relayed(self) -> None:
        error = self.failure(http_error(400, {"error": {
            "status": "INVALID_ARGUMENT",
            "message": f"Your prompt said 'secret plan' and your key is {API_KEY}",
        }}))
        self.assertEqual(str(error), "HTTP 400 (INVALID_ARGUMENT)")
        self.assertNotIn(API_KEY, str(error))
        self.assertNotIn("secret plan", str(error))

    def test_an_unknown_status_is_reduced_to_the_code(self) -> None:
        error = self.failure(http_error(503, {"error": {"status": "SOMETHING_NEW"}}))
        self.assertEqual(str(error), "HTTP 503")
        self.assertEqual(error.code, "http_503")

    def test_an_unparsable_body_is_reduced_to_the_code(self) -> None:
        raw = urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com/", 500, "error", {},
            io.BytesIO(b"<html>gateway</html>"),
        )
        self.assertEqual(str(self.failure(raw)), "HTTP 500")

    def test_network_and_timeout_failures_are_named(self) -> None:
        self.assertEqual(
            self.failure(urllib.error.URLError("no route")).code, "network",
        )
        self.assertEqual(self.failure(TimeoutError()).code, "timeout")

    def test_an_unusable_answer_is_refused(self) -> None:
        for body in (
            b"not json",
            b"[]",
            json.dumps({"candidates": []}).encode(),
            json.dumps({"candidates": [{"content": {"parts": []}}]}).encode(),
            answer("a string, not an object"),
        ):
            with self.subTest(body=body):
                with self.assertRaises(GeminiError):
                    client(FakeOpener(body)).structured(
                        system_instruction="a", prompt="b", schema=SCHEMA,
                    )

    def test_a_single_json_fence_is_accepted_but_prose_is_not(self) -> None:
        fenced = json.dumps({"candidates": [{"content": {"parts": [
            {"text": '```json\n{"overview": "ok"}\n```'},
        ]}}]}).encode()
        self.assertEqual(
            client(FakeOpener(fenced)).structured(
                system_instruction="a", prompt="b", schema=SCHEMA,
            ),
            {"overview": "ok"},
        )
        prose = json.dumps({"candidates": [{"content": {"parts": [
            {"text": 'Sure! Here it is: {"overview": "ok"}'},
        ]}}]}).encode()
        with self.assertRaises(GeminiError):
            client(FakeOpener(prose)).structured(
                system_instruction="a", prompt="b", schema=SCHEMA,
            )


class GeminiContractTests(unittest.TestCase):
    def test_the_model_matches_the_feedback_assistant_cheap_path(self) -> None:
        """Read the addon as text: importing it would need ``requests``."""
        source = FEEDBACK_CLIENT.read_text(encoding="utf-8")
        fallback = re.search(r'^FALLBACK_MODEL = "([^"]+)"', source, re.MULTILINE)
        revision = re.search(r'^API_REVISION = "([^"]+)"', source, re.MULTILINE)
        self.assertIsNotNone(fallback)
        self.assertIsNotNone(revision)
        self.assertEqual(MODEL, fallback.group(1))
        self.assertEqual(API_REVISION, revision.group(1))


if __name__ == "__main__":
    unittest.main()
