"""Isolated, URL-redacting linked-receipt retrieval service."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from playwright.async_api import Browser, Download, Page, Request, Response as BrowserResponse
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


MAX_HTML_BYTES = 2 * 1024 * 1024
OPAQUE_RE = re.compile(
    r"(?:\d{6,}|[0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f-]{27,}|[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)
DOWNLOAD_TERMS = {
    "download",
    "facture",
    "invoice",
    "justificatif",
    "pdf",
    "receipt",
    "recu",
    "reçu",
    "telecharger",
    "télécharger",
}
LOGIN_TERMS = {"authenticate", "login", "password", "sign in", "signin"}
AUTH_URL_TERMS = {"auth", "authenticate", "login", "signin"}
SAFE_PATH_SEGMENTS = DOWNLOAD_TERMS | {
    "api",
    "bill",
    "billing",
    "click",
    "documents",
    "factures",
    "file",
    "files",
    "invoices",
    "order",
    "orders",
    "r",
    "ride",
    "rides",
    "receipts",
    "trip",
    "trips",
    "v1",
    "v2",
    "v3",
}
SAFE_ERROR_MESSAGES = {
    "ambiguous_download": "Several possible receipt downloads were found.",
    "authentication_required": "The receipt page requires authentication.",
    "browser_crash": "The isolated browser stopped unexpectedly.",
    "browser_request_limit": "The receipt page made too many network requests.",
    "deadline": "The receipt download took too long.",
    "egress_denied": "The network safety policy denied the destination.",
    "expired_or_forbidden": "The signed receipt link is expired or forbidden.",
    "fetch_failed": "The receipt could not be downloaded.",
    "form_submission_required": "The receipt page requires a form submission.",
    "http_error": "The receipt provider returned an error.",
    "invalid_pdf": "The downloaded file is not a structurally valid PDF.",
    "no_pdf": "No unambiguous PDF receipt was found.",
    "pdf_active_content": "The PDF contains unsupported active content.",
    "pdf_encrypted": "The PDF is encrypted.",
    "pdf_too_large": "The PDF exceeds the 20 MB safety limit.",
    "unsafe_url": "The receipt link is not an allowed public HTTPS URL.",
}


class LearnedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["control"]
    tokens: str = Field(max_length=120)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label_tokens: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list,
        max_length=40,
    )
    learned_action: LearnedAction | None = None


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_redirects: int = Field(default=10, ge=0, le=10)
    max_browser_requests: int = Field(default=75, ge=1, le=75)
    deadline_seconds: int = Field(default=35, ge=5, le=35)


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=9, max_length=8192)
    blocked_hosts: list[Annotated[str, Field(max_length=253)]] = Field(
        default_factory=list,
        max_length=5000,
    )
    candidate: Candidate = Field(default_factory=Candidate)
    limits: Limits = Field(default_factory=Limits)


class FetchFailure(Exception):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code if code in SAFE_ERROR_MESSAGES else "fetch_failed"
        self.status = status


@dataclass
class FetchResult:
    content: bytes
    filename: str
    mode: str
    chain: list[dict[str, str]] = field(default_factory=list)
    learned_action: dict[str, str] | None = None


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
FETCH_SLOTS = asyncio.Semaphore(2)


@app.exception_handler(FetchFailure)
async def _fetch_failure(_request, error: FetchFailure):
    return JSONResponse(
        status_code=error.status,
        content={"code": error.code, "message": SAFE_ERROR_MESSAGES[error.code]},
    )


@app.exception_handler(RequestValidationError)
async def _invalid_request(_request, _error):
    # FastAPI's default validation response includes the rejected input.  A
    # signed URL must never be reflected into a response or downstream log.
    return JSONResponse(
        status_code=422,
        content={"code": "unsafe_url", "message": SAFE_ERROR_MESSAGES["unsafe_url"]},
    )


@app.exception_handler(Exception)
async def _unexpected_failure(_request, _error):
    return JSONResponse(
        status_code=500,
        content={"code": "fetch_failed", "message": SAFE_ERROR_MESSAGES["fetch_failed"]},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _normalized_blocked_hosts(values: list[str]) -> set[str]:
    normalized = set()
    for value in values:
        try:
            hostname = value.rstrip(".").encode("idna").decode("ascii").lower()
        except (AttributeError, UnicodeError):
            continue
        if hostname:
            normalized.add(hostname)
    return normalized


def _validate_url(url: str, blocked_hosts: set[str] | None = None) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise FetchFailure("unsafe_url") from None
    if host in (blocked_hosts or set()):
        raise FetchFailure("egress_denied")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or "\\" in url
        or any(ord(character) < 32 for character in url)
    ):
        raise FetchFailure("unsafe_url")
    return url


def _path_template(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    parts = []
    for raw in parsed.path.split("/"):
        part = unquote(raw)
        normalized = part.casefold()
        if not normalized:
            parts.append("")
        elif OPAQUE_RE.search(part):
            parts.append("{id}")
        elif normalized in SAFE_PATH_SEGMENTS:
            parts.append(normalized)
        elif normalized.endswith(".pdf"):
            stem = normalized[:-4]
            parts.append(f"{stem if stem in SAFE_PATH_SEGMENTS else '{id}'}.pdf")
        else:
            parts.append("{segment}")
    return {"host": host, "path": "/".join(parts)[:512] or "/"}


def _safe_filename(value: str | None) -> str:
    # A filename is provider-controlled and often embeds passenger or booking
    # data.  It is not needed for matching, validation, or accounting.
    return "receipt.pdf"


def _filename_from_headers(headers) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.IGNORECASE)
    return _safe_filename(match.group(1) if match else None)


def _is_pdf_semantics(headers, content: bytes) -> bool:
    mimetype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    disposition = headers.get("content-disposition", "").lower()
    return content.startswith(b"%PDF-") and (
        mimetype in {"application/pdf", "application/octet-stream"}
        or ".pdf" in disposition
    )


async def _read_limited(response, maximum: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > maximum:
        raise FetchFailure("pdf_too_large")
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise FetchFailure("pdf_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _proxy_url() -> str:
    return os.environ.get("USL_RECEIPT_EGRESS_PROXY", "http://usl-receipt-egress:4750")


def _authentication_required(
    page_url: str,
    body_text: str = "",
    *,
    password_input: bool = False,
) -> bool:
    """Classify an authentication wall without retaining its full URL."""
    parsed = urlsplit(page_url)
    hostname_tokens = set((parsed.hostname or "").casefold().split("."))
    path_tokens = set(re.findall(r"[^\W_]+", unquote(parsed.path).casefold()))
    return bool(
        password_input
        or hostname_tokens & AUTH_URL_TERMS
        or path_tokens & AUTH_URL_TERMS
        or any(term in body_text for term in LOGIN_TERMS)
    )


async def _direct_fetch(
    request: FetchRequest,
) -> tuple[FetchResult | None, list[dict[str, str]], str | None]:
    timeout = httpx.Timeout(10.0, connect=5.0, read=10.0, write=5.0, pool=5.0)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=0)
    blocked_hosts = _normalized_blocked_hosts(request.blocked_hosts)
    current = _validate_url(request.url, blocked_hosts)
    chain = []
    try:
        async with httpx.AsyncClient(
            proxy=_proxy_url(),
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/pdf,text/html;q=0.8",
                "User-Agent": "Mozilla/5.0 USL-Receipt-Fetcher/1",
            },
        ) as client:
            for hop in range(request.limits.max_redirects + 1):
                async with client.stream("GET", current, headers={"Referer": ""}) as response:
                    chain.append(_path_template(str(response.request.url)))
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if hop >= request.limits.max_redirects:
                            raise FetchFailure("http_error")
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailure("http_error")
                        current = _validate_url(urljoin(current, location), blocked_hosts)
                        continue
                    if response.status_code in {401, 403}:
                        # Some providers deny non-browser clients before a
                        # JavaScript challenge. Give the disposable browser
                        # one chance; it still cannot authenticate or submit.
                        return None, chain, current
                    if response.status_code == 410:
                        raise FetchFailure("expired_or_forbidden")
                    if response.status_code in {408, 425, 429} or response.status_code >= 500:
                        raise FetchFailure("http_error", 503)
                    if response.status_code >= 400:
                        raise FetchFailure("http_error")
                    mimetype = response.headers.get("content-type", "").lower()
                    maximum = request.limits.max_bytes if "pdf" in mimetype or "octet-stream" in mimetype else MAX_HTML_BYTES
                    content = await _read_limited(response, maximum)
                    if _is_pdf_semantics(response.headers, content):
                        return (
                            FetchResult(
                                content,
                                _filename_from_headers(response.headers),
                                "http",
                                chain,
                            ),
                            chain,
                            None,
                        )
                    if "html" in mimetype or content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        return None, chain, current
                    raise FetchFailure("invalid_pdf")
    except FetchFailure:
        raise
    except (httpx.ProxyError, httpx.ConnectError) as error:
        code = "egress_denied" if isinstance(error, httpx.ProxyError) else "http_error"
        raise FetchFailure(code, 503 if code == "http_error" else 422) from None
    except httpx.TimeoutException:
        raise FetchFailure("deadline", 503) from None
    raise FetchFailure("http_error")


async def _browser_fetch(
    request: FetchRequest,
    initial_chain: list[dict[str, str]],
    start_url: str,
) -> FetchResult:
    pdf_queue: asyncio.Queue[FetchResult] = asyncio.Queue()
    failure_queue: asyncio.Queue[FetchFailure] = asyncio.Queue()
    request_count = 0
    popup_count = 0
    policy_denied = False
    chain: list[dict[str, str]] = initial_chain.copy()
    blocked_hosts = _normalized_blocked_hosts(request.blocked_hosts)

    async def capture_response(response: BrowserResponse) -> None:
        try:
            if response.request.resource_type == "document":
                item = _path_template(response.url)
                if not chain or chain[-1] != item:
                    chain.append(item)
            headers = await response.all_headers()
            content_type = headers.get("content-type", "").split(";", 1)[0].lower()
            disposition = headers.get("content-disposition", "").lower()
            if (
                content_type not in {"application/pdf", "application/octet-stream"}
                and ".pdf" not in disposition
                and not urlsplit(response.url).path.lower().endswith(".pdf")
            ):
                return
            declared = headers.get("content-length", "")
            if declared.isdigit() and int(declared) > request.limits.max_bytes:
                failure_queue.put_nowait(FetchFailure("pdf_too_large"))
                return
            content = await response.body()
            if len(content) > request.limits.max_bytes:
                failure_queue.put_nowait(FetchFailure("pdf_too_large"))
            elif _is_pdf_semantics(headers, content):
                response_item = _path_template(response.url)
                response_chain = chain.copy()
                if not response_chain or response_chain[-1] != response_item:
                    response_chain.append(response_item)
                await pdf_queue.put(
                    FetchResult(
                        content,
                        _filename_from_headers(headers),
                        "browser",
                        response_chain,
                    )
                )
            else:
                failure_queue.put_nowait(FetchFailure("invalid_pdf"))
        except Exception:
            return

    async def capture_download(download: Download) -> None:
        try:
            path = await download.path()
            if path:
                download_path = Path(path)
                if download_path.stat().st_size > request.limits.max_bytes:
                    failure_queue.put_nowait(FetchFailure("pdf_too_large"))
                    return
                content = download_path.read_bytes()
                if content.startswith(b"%PDF-"):
                    download_chain = chain.copy()
                    download_url = download.url
                    if (
                        urlsplit(download_url).scheme.casefold() == "https"
                        and urlsplit(download_url).hostname
                    ):
                        download_item = _path_template(download_url)
                        if not download_chain or download_chain[-1] != download_item:
                            download_chain.append(download_item)
                    await pdf_queue.put(
                        FetchResult(
                            content,
                            _safe_filename(download.suggested_filename),
                            "browser",
                            download_chain,
                        )
                    )
                else:
                    failure_queue.put_nowait(FetchFailure("invalid_pdf"))
        except Exception:
            return

    async def route_handler(route, browser_request: Request) -> None:
        nonlocal policy_denied, request_count
        request_count += 1
        if request_count > request.limits.max_browser_requests:
            await route.abort("blockedbyclient")
            return
        try:
            _validate_url(browser_request.url, blocked_hosts)
        except FetchFailure as error:
            if error.code == "egress_denied":
                policy_denied = True
            await route.abort("blockedbyclient")
            return
        if browser_request.method not in {"GET", "HEAD"}:
            await route.abort("blockedbyclient")
            return
        if browser_request.resource_type in {"font", "image", "media", "websocket"}:
            await route.abort("blockedbyclient")
            return
        # The context starts empty and is destroyed after this request. Allow
        # provider cookies created inside that context; many signed-link pages
        # require them for the subsequent JavaScript download.
        headers = {
            key: value
            for key, value in browser_request.headers.items()
            if key.lower() != "referer"
        }
        await route.continue_(headers=headers)

    async def block_websocket(websocket_route) -> None:
        await websocket_route.close(code=1008, reason="WebSockets are disabled")

    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(
                chromium_sandbox=True,
                proxy={"server": _proxy_url()},
                args=[
                    "--disable-background-networking",
                    "--disable-breakpad",
                    "--disable-component-update",
                    "--disable-features=WebRtcAllowInputVolumeAdjustment,WebUSB,WebBluetooth,MediaDevices,NotificationTriggers,PushMessaging,InterestFeedContentSuggestions,Translate",
                    "--disable-quic",
                    "--disable-sync",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--no-first-run",
                ],
            )
            context = await browser.new_context(
                accept_downloads=True,
                bypass_csp=False,
                ignore_https_errors=False,
                java_script_enabled=True,
                permissions=[],
                service_workers="block",
            )
            await context.clear_cookies()
            await context.route("**/*", route_handler)
            await context.route_web_socket("**/*", block_websocket)
            context.on("response", capture_response)
            page = await context.new_page()
            page.on("download", capture_download)

            async def capture_popup(popup: Page) -> None:
                nonlocal popup_count
                popup_count += 1
                if popup_count > 1:
                    await popup.close()
                    return
                popup.on("download", capture_download)

            # Register after creating the main page. The context emits the
            # same event for new_page(), which would otherwise consume the
            # single permitted popup before a provider opens it.
            context.on("page", capture_popup)
            navigation = await page.goto(
                start_url,
                wait_until="domcontentloaded",
                timeout=12_000,
                referer="",
            )
            try:
                return await asyncio.wait_for(pdf_queue.get(), timeout=1.0)
            except TimeoutError:
                pass
            if not failure_queue.empty():
                raise failure_queue.get_nowait()
            if policy_denied:
                raise FetchFailure("egress_denied")
            if navigation and navigation.status in {401, 403, 410}:
                raise FetchFailure("expired_or_forbidden")
            body_text = (await page.locator("body").inner_text(timeout=2_000)).casefold()[:20_000]
            controls = page.locator("a, button, [role=button]")
            ranked = []
            form_control_found = False
            learned_tokens = set(
                (request.candidate.learned_action.tokens if request.candidate.learned_action else "").split()
            )
            candidate_tokens = set(request.candidate.label_tokens)
            for index in range(min(await controls.count(), 100)):
                control = controls.nth(index)
                try:
                    text = " ".join((await control.inner_text(timeout=300)).casefold().split())[:160]
                    if text and any(term in text for term in DOWNLOAD_TERMS):
                        submits_form = await control.evaluate(
                            "el => {"
                            " const form = el.closest('form');"
                            " if (!form || el.tagName !== 'BUTTON') return false;"
                            " const type = (el.getAttribute('type') || 'submit').toLowerCase();"
                            " return type === 'submit';"
                            "}"
                        )
                        if submits_form:
                            form_control_found = True
                            continue
                        text_tokens = set(text.split())
                        rank = sum(term in text for term in DOWNLOAD_TERMS)
                        rank += 3 * len(text_tokens & learned_tokens)
                        rank += len(text_tokens & candidate_tokens)
                        ranked.append((rank, text, index))
                except Exception:
                    continue
            if request_count > request.limits.max_browser_requests:
                raise FetchFailure("browser_request_limit")
            ranked.sort(reverse=True)
            if not ranked:
                password_input = await page.locator('input[type="password"]').count()
                if _authentication_required(
                    page.url,
                    body_text,
                    password_input=bool(password_input),
                ):
                    raise FetchFailure("authentication_required")
                raise FetchFailure("form_submission_required" if form_control_found else "no_pdf")
            if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][1] != ranked[1][1]:
                raise FetchFailure("ambiguous_download")
            for rank, text, index in ranked[:3]:
                await controls.nth(index).click(timeout=3_000, no_wait_after=True)
                try:
                    result = await asyncio.wait_for(pdf_queue.get(), timeout=5.0)
                    result.learned_action = {"role": "control", "tokens": " ".join(sorted(set(text.split()) & DOWNLOAD_TERMS))[:120]}
                    return result
                except TimeoutError:
                    if not failure_queue.empty():
                        raise failure_queue.get_nowait()
                    if policy_denied:
                        raise FetchFailure("egress_denied")
                    if request_count > request.limits.max_browser_requests:
                        raise FetchFailure("browser_request_limit")
                    continue
            if form_control_found:
                raise FetchFailure("form_submission_required")
            if _authentication_required(page.url):
                raise FetchFailure("authentication_required")
            raise FetchFailure("no_pdf")
    except FetchFailure:
        raise
    except PlaywrightTimeoutError:
        if policy_denied:
            raise FetchFailure("egress_denied") from None
        raise FetchFailure("deadline", 503) from None
    except Exception:
        if policy_denied:
            raise FetchFailure("egress_denied") from None
        raise FetchFailure("browser_crash", 503) from None


def _validate_pdf(content: bytes, maximum: int) -> None:
    if len(content) > maximum:
        raise FetchFailure("pdf_too_large")
    if not content.startswith(b"%PDF-"):
        raise FetchFailure("invalid_pdf")
    with tempfile.TemporaryDirectory(prefix="receipt-") as directory:
        path = Path(directory) / "document.pdf"
        path.write_bytes(content)
        try:
            encrypted = subprocess.run(
                ["qpdf", "--is-encrypted", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            if encrypted.returncode == 0:
                raise FetchFailure("pdf_encrypted")
            if encrypted.returncode != 2:
                raise FetchFailure("invalid_pdf")
            qpdf = subprocess.run(
                ["qpdf", "--check", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
                check=False,
            )
            # qpdf uses exit status 3 for warnings.  The receipt boundary is
            # intentionally stricter: only a completely clean structural
            # check is accepted before the independent pikepdf inspection.
            if qpdf.returncode != 0:
                raise FetchFailure("invalid_pdf")
            parsed = subprocess.run(
                ["python", "/app/validate_pdf.py", str(path), str(maximum)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=8,
                check=False,
                text=True,
            )
        except subprocess.TimeoutExpired:
            raise FetchFailure("invalid_pdf") from None
        if parsed.returncode:
            try:
                code = json.loads(parsed.stdout).get("code")
            except (json.JSONDecodeError, AttributeError):
                code = "invalid_pdf"
            mapped = {
                "active_content": "pdf_active_content",
                "decompressed_size": "pdf_too_large",
                "encrypted_pdf": "pdf_encrypted",
            }.get(code, "invalid_pdf")
            raise FetchFailure(mapped)


@app.post("/v1/receipts/fetch")
async def fetch_receipt(request: FetchRequest):
    _validate_url(request.url, _normalized_blocked_hosts(request.blocked_hosts))
    try:
        async with asyncio.timeout(request.limits.deadline_seconds):
            async with FETCH_SLOTS:
                result, direct_chain, browser_url = await _direct_fetch(request)
                if result is None:
                    result = await _browser_fetch(
                        request,
                        direct_chain,
                        browser_url or request.url,
                    )
                # Keep qpdf/pikepdf off the event loop so the request deadline
                # can still return a typed timeout for a hostile slow parser.
                # The parser processes have their own shorter hard timeouts
                # and resource ceilings if the worker thread is cancelled.
                await asyncio.to_thread(
                    _validate_pdf,
                    result.content,
                    request.limits.max_bytes,
                )
    except TimeoutError:
        raise FetchFailure("deadline", 503) from None
    digest = hashlib.sha256(result.content).hexdigest()
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-USL-Fetch-Mode": result.mode,
        "X-USL-Filename": result.filename,
        "X-USL-Redirect-Hosts": json.dumps(result.chain, separators=(",", ":")),
        "X-USL-SHA256": digest,
    }
    if result.learned_action:
        raw = json.dumps(result.learned_action, sort_keys=True, separators=(",", ":")).encode()
        headers["X-USL-Learned-Action"] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return Response(result.content, media_type="application/pdf", headers=headers)
