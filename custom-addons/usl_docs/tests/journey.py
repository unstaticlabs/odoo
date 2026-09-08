"""Run a tour as a documented journey and record what the reader would see.

A journey is an ordinary tour plus a ``usl_docs.journeys`` registry entry
(see ``static/tests/journey_runner.js``). ``JourneyCase.run_journey`` starts
it through the runner, listens for the runner's console lines, captures the
screen over the DevTools protocol at every documented step, and writes one
JSON record per journey and viewport. ``scripts/docs_generate.py`` turns
those records into the committed page and screenshots; the qualification run
turns them into the evidence the viewer shows.

Two constraints shape the code. ``ChromeBrowser._handle_console`` runs on
the thread that receives every DevTools message, including the response to
the screenshot request, so the capture must happen on a thread of its own or
the browser deadlocks. And the tour's synthetic screenshot step is awaiting a
promise the Python side resolves, so a capture that never completes must
fail loudly rather than hang the tour until its timeout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import odoo
from odoo.tests import HttpCase
from odoo.tests.common import ChromeBrowser, get_db_name

RECORD_SCHEMA = "usl-docs-journey/v1"
ENABLE_ENV_VAR = "USL_DOCS_JOURNEYS"
OUTPUT_ENV_VAR = "USL_DOCS_JOURNEY_OUTPUT"
JOURNEY_TAG = "usl_docs_journey"
VIEWPORTS = {
    "desktop": {"size": "1366x768", "touch": False},
    "mobile": {"size": "390x844", "touch": True},
}
_JOURNEY_RE = re.compile(r"^usl_docs:journey:(\{.*\})$", re.DOTALL)
_SCREENSHOT_RE = re.compile(r"^usl_docs:screenshot:([A-Za-z0-9_.]+):([A-Za-z0-9_-]+)$")


def journeys_enabled():
    return os.environ.get(ENABLE_ENV_VAR) == "1"


def output_directory():
    configured = os.environ.get(OUTPUT_ENV_VAR)
    if configured:
        return Path(configured)
    return Path(odoo.tools.config["screenshots"]) / get_db_name() / "usl_docs"


def _git_commit():
    for name in ("USL_RELEASE_COMMIT", "GITHUB_SHA"):
        value = (os.environ.get(name) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    return None


def _chromium_version(browser):
    try:
        info = browser._websocket_request("Browser.getVersion", timeout=5)
    except Exception:  # noqa: BLE001 - the version is informative only
        return ""
    return str(info.get("product", "")) if isinstance(info, dict) else ""


class _Capture:
    """One journey run on one viewport: the console listener and the capture thread."""

    def __init__(self, case, viewport, directory):
        self.case = case
        self.viewport = viewport
        self.directory = directory
        self.queue = queue.Queue()
        self.journey = None
        self.screenshots = {}
        self.failure = None
        self.chromium = ""
        self.thread = threading.Thread(target=self._drain, name="usl-docs-capture", daemon=True)

    # -- console side, runs on the DevTools receiver thread ------------

    def on_message(self, browser, message):
        """Return True when ``message`` was ours and must not reach Odoo's handler."""
        match = _JOURNEY_RE.match(message)
        if match:
            self.journey = json.loads(match.group(1))
            return True
        match = _SCREENSHOT_RE.match(message)
        if match:
            self.queue.put((browser, match.group(1), match.group(2)))
            return True
        return False

    # -- capture side, runs on its own thread ---------------------------

    def start(self):
        self.thread.start()

    def stop(self):
        self.queue.put(None)
        self.thread.join(timeout=60)

    def _drain(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            browser, tour, step_id = item
            try:
                self._capture(browser, tour, step_id)
            except Exception as error:  # noqa: BLE001 - reported to the test
                self.failure = self.failure or error
                # Release the tour so it fails on the missing screenshot
                # rather than hanging until its own timeout.
                self._resolve(browser, step_id)

    def _capture(self, browser, tour, step_id):
        if not self.chromium:
            self.chromium = _chromium_version(browser)
        # Long-polling and websocket threads never finish; only give the
        # short-lived request threads a moment to settle.
        self.case._wait_remaining_requests(timeout=2)
        future = browser._websocket_send(
            "Page.captureScreenshot",
            params={"format": "png", "captureBeyondViewport": False},
            with_future=True,
        )
        if future is None:
            raise RuntimeError("the browser is gone before the screenshot")
        data = future.result(timeout=30).get("data")
        if not data:
            raise RuntimeError(f"no image data for step {step_id}")
        png = base64.b64decode(data)
        index = len(self.screenshots) + 1
        name = f"{index:02d}-{step_id}.png"
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / name).write_bytes(png)
        self.screenshots[step_id] = {
            "file": name,
            "sha256": hashlib.sha256(png).hexdigest(),
            "bytes": len(png),
        }
        self._resolve(browser, step_id)

    @staticmethod
    def _resolve(browser, step_id):
        browser._websocket_send(
            "Runtime.evaluate",
            params={
                "expression": (
                    "window.__uslDocsResolve && window.__uslDocsResolve[%s] && window.__uslDocsResolve[%s]()"
                    % (json.dumps(step_id), json.dumps(step_id))
                ),
            },
        )


class JourneyCase(HttpCase):
    """Base class for tests that document a journey.

    Tag the subclass ``usl_docs_journey`` (with ``post_install``). The test
    runs only when ``USL_DOCS_JOURNEYS=1``; the ordinary module suite then
    skips it, and the qualification run and ``make docs-journeys`` enable it.
    """

    def setUp(self):
        super().setUp()
        if not journeys_enabled():
            self.skipTest(f"journeys run only with {ENABLE_ENV_VAR}=1")

    def run_journey(self, tour_name, url_path, login, viewports=("desktop",), timeout=180):
        """Run ``tour_name`` on each viewport and write its records. Returns them."""
        records = []
        for viewport in viewports:
            records.append(self._run_on_viewport(tour_name, url_path, login, viewport, timeout))
        return records

    def _run_on_viewport(self, tour_name, url_path, login, viewport, timeout):
        settings = VIEWPORTS[viewport]
        directory = output_directory() / tour_name / viewport
        capture = _Capture(self, viewport, directory)
        original_handle = ChromeBrowser._handle_console

        def handle_console(browser, type, args=None, stackTrace=None, **kw):  # noqa: A002 - Odoo's signature
            if type == "log" and args:
                # Never send a DevTools request from here: this runs on the
                # thread that would have to receive the answer.
                first = str(browser._from_remoteobject(args[0]))
                if capture.on_message(browser, first):
                    return None
            return original_handle(browser, type, args=args, stackTrace=stackTrace, **kw)

        self._pin_appearance(login)
        started = datetime.now(timezone.utc)
        self.browser_size = settings["size"]
        self.touch_enabled = settings["touch"]
        capture.start()
        try:
            with patch.object(ChromeBrowser, "_handle_console", handle_console):
                self.start_tour(
                    url_path,
                    tour_name,
                    login=login,
                    code=f"odoo.uslDocsStartTour({tour_name!r}, {{stepDelay: 0, debug: false}})",
                    timeout=timeout,
                )
        finally:
            capture.stop()
        finished = datetime.now(timezone.utc)
        if capture.failure:
            raise AssertionError(f"journey {tour_name} could not be captured: {capture.failure}")
        self.assertTrue(capture.journey, f"journey {tour_name} never announced itself; is it registered in usl_docs.journeys?")
        journey = capture.journey
        expected = [step["id"] for step in journey["steps"] if step["screenshot"]]
        self.assertEqual(
            list(capture.screenshots),
            expected,
            f"journey {tour_name} documented {expected} but captured {list(capture.screenshots)}",
        )
        record = {
            "schema": RECORD_SCHEMA,
            "journey": journey["id"],
            "tour": tour_name,
            "type": journey["type"],
            "title": journey["title"],
            "description": journey["description"],
            "persona": journey["persona"],
            "lang": journey["lang"],
            "viewport": viewport,
            "viewport_size": settings["size"],
            "source_test": f"{type(self).__module__}.{type(self).__name__}.{self._testMethodName}",
            "login": login,
            "started": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "finished": finished.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status": "success",
            "git_commit": _git_commit(),
            "chromium": capture.chromium,
            "module_versions": self._module_versions(),
            "steps": [
                {
                    "index": index,
                    "id": step["id"],
                    "text": step["text"],
                    "assertion": step["assertion"],
                    "screenshot": capture.screenshots.get(step["id"]),
                }
                for index, step in enumerate(journey["steps"], start=1)
            ],
        }
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "journey.json").write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return record

    #: The theme colour every journey company wears, so the top bar does not
    #: change with the fixture company's database id.
    THEME_COLOR = "#714B67"

    def _pin_appearance(self, login):
        """Remove the run-to-run variation the fixtures would otherwise carry.

        The automatic company theme colour derives from the company's id, and
        the avatar from the user's creation; both differ on every fresh
        database and would make identical screens compare as changed.
        """
        user = self.env["res.users"].sudo().search([("login", "=", login)], limit=1)
        if not user:
            return
        companies = user.company_ids | user.company_id
        if "usl_ui_theme_color" in companies._fields:
            companies.filtered(lambda company: not company.usl_ui_theme_color).write({
                "usl_ui_theme_color": self.THEME_COLOR,
            })
        self.env.flush_all()

    def _module_versions(self):
        modules = self.env["ir.module.module"].sudo().search([
            ("state", "=", "installed"),
            ("name", "=like", "usl_%"),
        ])
        return {module.name: module.installed_version for module in modules}
