from __future__ import annotations

import io
import subprocess
import unittest
from unittest.mock import patch

import pikepdf
from fastapi.testclient import TestClient

import app


class ReceiptFetcherSafetyTests(unittest.TestCase):
    def test_invalid_request_does_not_reflect_the_signed_url(self) -> None:
        signed_url = "https://example.com/receipt.pdf?token=" + "secret" * 2000
        response = TestClient(app.app).post(
            "/v1/receipts/fetch",
            json={"url": signed_url},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unsafe_url")
        self.assertNotIn("token", response.text)
        self.assertNotIn("secret", response.text)

    def test_only_https_without_credentials_or_custom_ports_is_accepted(self) -> None:
        self.assertEqual(app._validate_url("https://example.com/receipt"), "https://example.com/receipt")
        for value in (
            "http://example.com/receipt",
            "https://user:password@example.com/receipt",
            "https://example.com:8443/receipt",
            "https://example.com\\@127.0.0.1/receipt",
        ):
            with self.subTest(value=value), self.assertRaises(app.FetchFailure):
                app._validate_url(value)
        with self.assertRaisesRegex(app.FetchFailure, "egress_denied"):
            app._validate_url("https://blocked.example/receipt", {"blocked.example"})

    def test_sanitized_metadata_never_keeps_opaque_identifiers(self) -> None:
        chain = app._path_template(
            "https://RÉCUS.example/receipts/123456789/download/abcdef1234567890?token=secret",
        )
        self.assertEqual(chain, {"host": "xn--rcus-bpa.example", "path": "/receipts/{id}/download/{id}"})
        self.assertEqual(app._safe_filename("abcdef1234567890abcdef1234567890.pdf"), "receipt.pdf")
        self.assertEqual(app._safe_filename("../../Trip receipt.pdf"), "receipt.pdf")
        self.assertEqual(app._safe_filename("Valentin booking 123.pdf"), "receipt.pdf")

        personal = app._path_template(
            "https://receipts.example/users/Valentin-Viennot/customer-name.pdf",
        )
        self.assertEqual(
            personal,
            {
                "host": "receipts.example",
                "path": "/{segment}/{segment}/{id}.pdf",
            },
        )

    def test_nested_request_fields_are_bounded_and_strict(self) -> None:
        client = TestClient(app.app)
        oversized_token = "x" * 65
        response = client.post(
            "/v1/receipts/fetch",
            json={
                "url": "https://example.com/receipt.pdf",
                "candidate": {"label_tokens": [oversized_token]},
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unsafe_url")
        self.assertNotIn(oversized_token, response.text)

    def test_authentication_wall_uses_url_and_page_signals(self) -> None:
        self.assertTrue(app._authentication_required("https://auth.uber.com/session"))
        self.assertTrue(app._authentication_required("https://example.com/login"))
        self.assertTrue(
            app._authentication_required(
                "https://example.com/receipt",
                "please sign in to continue",
            )
        )
        self.assertTrue(
            app._authentication_required(
                "https://example.com/receipt",
                password_input=True,
            )
        )
        self.assertFalse(
            app._authentication_required("https://riders.uber.com/trips/receipt")
        )

    def test_valid_pdf_is_accepted_and_active_pdf_is_rejected(self) -> None:
        clean = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.save(clean)
        app._validate_pdf(clean.getvalue(), 20 * 1024 * 1024)

        active = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.Root.OpenAction = pikepdf.Dictionary(
                S=pikepdf.Name("/JavaScript"),
                JS=pikepdf.String("app.alert('x')"),
            )
            pdf.save(active)
        with self.assertRaisesRegex(app.FetchFailure, "pdf_active_content"):
            app._validate_pdf(active.getvalue(), 20 * 1024 * 1024)

        embedded = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.Root.Names = pikepdf.Dictionary(
                EmbeddedFiles=pikepdf.Dictionary(),
            )
            pdf.save(embedded)
        with self.assertRaisesRegex(app.FetchFailure, "pdf_active_content"):
            app._validate_pdf(embedded.getvalue(), 20 * 1024 * 1024)

        launch = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.Root.CustomAction = pikepdf.Dictionary(
                S=pikepdf.Name("/Launch"),
                F=pikepdf.String("unsafe-command"),
            )
            pdf.save(launch)
        with self.assertRaisesRegex(app.FetchFailure, "pdf_active_content"):
            app._validate_pdf(launch.getvalue(), 20 * 1024 * 1024)

    def test_encrypted_and_truncated_pdfs_are_rejected(self) -> None:
        encrypted = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.save(
                encrypted,
                encryption=pikepdf.Encryption(
                    owner="owner-secret",
                    user="user-secret",
                    R=6,
                ),
            )
        with self.assertRaisesRegex(app.FetchFailure, "pdf_encrypted"):
            app._validate_pdf(encrypted.getvalue(), 20 * 1024 * 1024)

        with self.assertRaisesRegex(app.FetchFailure, "invalid_pdf"):
            app._validate_pdf(b"%PDF-1.4\ntruncated", 20 * 1024 * 1024)

    def test_decompressed_size_and_parser_failures_are_bounded(self) -> None:
        compressed = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.Root.ReceiptFixture = pikepdf.Stream(pdf, b"x" * 16_384)
            pdf.save(compressed, compress_streams=True)
        self.assertLess(len(compressed.getvalue()), 4096)
        with self.assertRaisesRegex(app.FetchFailure, "pdf_too_large"):
            app._validate_pdf(compressed.getvalue(), 4096)

        clean = io.BytesIO()
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(72, 72))
            pdf.save(clean)
        completed = lambda returncode, stdout="": subprocess.CompletedProcess(
            ["fixture"], returncode, stdout=stdout, stderr=""
        )
        with patch.object(
            app.subprocess,
            "run",
            side_effect=[
                completed(2),
                completed(0),
                subprocess.TimeoutExpired(["python"], 8),
            ],
        ), self.assertRaisesRegex(app.FetchFailure, "invalid_pdf"):
            app._validate_pdf(clean.getvalue(), 20 * 1024 * 1024)

        with patch.object(
            app.subprocess,
            "run",
            side_effect=[completed(2), completed(0), completed(-11)],
        ), self.assertRaisesRegex(app.FetchFailure, "invalid_pdf"):
            app._validate_pdf(clean.getvalue(), 20 * 1024 * 1024)

        with patch.object(
            app.subprocess,
            "run",
            side_effect=[completed(2), completed(3)],
        ), self.assertRaisesRegex(app.FetchFailure, "invalid_pdf"):
            app._validate_pdf(clean.getvalue(), 20 * 1024 * 1024)

    def test_size_limit_is_checked_before_parsing(self) -> None:
        with self.assertRaisesRegex(app.FetchFailure, "pdf_too_large"):
            app._validate_pdf(b"%PDF-" + b"x" * 100, 10)


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeResponse:
    def __init__(self, url: str, status: int, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.request = _FakeRequest(url)
        self.status_code = status
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, method: str, url: str, **_kwargs):
        response = next(self.responses)
        if method != "GET" or response.request.url != url:
            raise AssertionError(f"unexpected request: {method} {url}")
        return response


class DirectFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_follows_relative_public_https_redirect_and_streams_pdf(self) -> None:
        pdf = b"%PDF-1.4\nfixture\n%%EOF\n"
        responses = [
            _FakeResponse(
                "https://links.example/start?token=secret",
                302,
                {"location": "/files/123456789/receipt.pdf?signature=secret"},
                [],
            ),
            _FakeResponse(
                "https://links.example/files/123456789/receipt.pdf?signature=secret",
                200,
                {"content-type": "application/pdf"},
                [pdf[:8], pdf[8:]],
            ),
        ]
        request = app.FetchRequest(url="https://links.example/start?token=secret")

        with patch.object(app.httpx, "AsyncClient", return_value=_FakeClient(responses)):
            result, chain, browser_url = await app._direct_fetch(request)

        self.assertEqual(result.content, pdf)
        self.assertEqual(result.mode, "http")
        self.assertIsNone(browser_url)
        self.assertEqual(chain[0], {"host": "links.example", "path": "/{segment}"})
        self.assertEqual(
            chain[1],
            {"host": "links.example", "path": "/files/{id}/receipt.pdf"},
        )
        self.assertNotIn("secret", str(chain))

    async def test_html_returns_browser_fallback_with_sanitized_chain(self) -> None:
        responses = [
            _FakeResponse(
                "https://receipts.example/page/abcdef1234567890?token=secret",
                200,
                {"content-type": "text/html; charset=utf-8"},
                [b"<!doctype html><button>Download PDF</button>"],
            ),
        ]
        request = app.FetchRequest(
            url="https://receipts.example/page/abcdef1234567890?token=secret",
        )

        with patch.object(app.httpx, "AsyncClient", return_value=_FakeClient(responses)):
            result, chain, browser_url = await app._direct_fetch(request)

        self.assertIsNone(result)
        self.assertEqual(
            browser_url,
            "https://receipts.example/page/abcdef1234567890?token=secret",
        )
        self.assertEqual(
            chain,
            [{"host": "receipts.example", "path": "/{segment}/{id}"}],
        )

    async def test_access_denial_gets_one_disposable_browser_fallback(self) -> None:
        responses = [
            _FakeResponse(
                "https://receipts.example/signed/receipt",
                403,
                {"content-type": "text/html"},
                [],
            ),
        ]
        request = app.FetchRequest(
            url="https://receipts.example/signed/receipt",
        )

        with patch.object(app.httpx, "AsyncClient", return_value=_FakeClient(responses)):
            result, chain, browser_url = await app._direct_fetch(request)

        self.assertIsNone(result)
        self.assertEqual(browser_url, request.url)
        self.assertEqual(
            chain,
            [{"host": "receipts.example", "path": "/{segment}/receipt"}],
        )

    async def test_chunked_oversize_response_is_rejected(self) -> None:
        responses = [
            _FakeResponse(
                "https://receipts.example/receipt.pdf",
                200,
                {"content-type": "application/pdf"},
                [b"%PDF-", b"x" * 1024],
            ),
        ]
        request = app.FetchRequest(
            url="https://receipts.example/receipt.pdf",
            limits=app.Limits(max_bytes=1024),
        )

        with (
            patch.object(app.httpx, "AsyncClient", return_value=_FakeClient(responses)),
            self.assertRaisesRegex(app.FetchFailure, "pdf_too_large"),
        ):
            await app._direct_fetch(request)

    async def test_blocked_redirect_host_is_rejected_before_the_next_request(self) -> None:
        responses = [
            _FakeResponse(
                "https://links.example/start",
                302,
                {"location": "https://blocked.example/receipt.pdf"},
                [],
            ),
        ]
        request = app.FetchRequest(
            url="https://links.example/start",
            blocked_hosts=["BLOCKED.example."],
        )

        with (
            patch.object(app.httpx, "AsyncClient", return_value=_FakeClient(responses)),
            self.assertRaisesRegex(app.FetchFailure, "egress_denied"),
        ):
            await app._direct_fetch(request)


if __name__ == "__main__":
    unittest.main()
