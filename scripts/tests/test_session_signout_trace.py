"""Guards on the temporary sign-out instrumentation in Odoo's session store.

Production signs people out after several hours of inactivity, with no release
involved and nothing in the logs, and the whole session file disappears rather
than rotating. `odoo/http/session.py` carries `USL-TRACE` logging so the next
occurrence names its own cause. See
`docs/operations/session-signout-investigation-20260910.md`.

These tests do not import Odoo — the qualification job that runs them has no
Odoo runtime. They read the source, which is enough for the two things that
would actually hurt: instrumentation that leaks a bearer token into the logs,
and instrumentation nobody can find again to remove.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "odoo" / "http" / "session.py"
MARKER = "USL-TRACE"


def source() -> str:
    return SESSION.read_text(encoding="utf-8")


def trace_log_calls() -> list[ast.Call]:
    """Every ``_logger`` call whose message carries the marker."""
    calls = []
    for node in ast.walk(ast.parse(source())):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "_logger":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str) and MARKER in node.args[0].value:
                calls.append(node)
    return calls


class SessionSignoutTraceTests(unittest.TestCase):
    def test_the_trace_covers_every_way_a_session_can_disappear(self) -> None:
        # Each of these is a distinct answer to "who removed it", and the point
        # of the exercise is that the log distinguishes them.
        messages = " ".join(call.args[0].value for call in trace_log_calls())
        for expected in (
            "sign-out",            # logout(), with the route it happened on
            "expired",             # check(): the session outlived deletion_time
            "token mismatch",      # check(): a res.users column moved
            "file deleted",        # a hard rotation removed one file
            "rotated",             # a rotation, which is not a disappearance
            "vacuum",              # the scheduled reaper
            "identifiers cleared",  # the only path that removes a whole identifier
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, messages)

    def test_no_request_path_is_logged_without_the_bearer_guard(self) -> None:
        # ``/agent-documents/<grant>`` carries a bearer token in its path. The
        # gateway sets access_log off AND error_log /dev/null on that route to
        # keep it out of logs; instrumentation that prints a raw path would
        # hand it back. Every trace call must route a path through the helper.
        for call in trace_log_calls():
            rendered = ast.unparse(call)
            with self.subTest(message=call.args[0].value[:40]):
                self.assertNotIn("httprequest.path", rendered)
                self.assertNotIn("full_path", rendered)
                self.assertNotIn("httprequest.url", rendered)

    def test_the_route_helper_names_what_it_will_print(self) -> None:
        text = source()
        allowed = re.search(
            r"^USL_TRACE_LOGGABLE_ROUTES = \((.*)\)$", text, re.MULTILINE,
        )
        self.assertIsNotNone(allowed, "the allowlist must stay a readable literal")
        self.assertNotIn("/agent-documents", allowed.group(1))
        # Anything outside the allowlist is bucketed rather than printed, so a
        # route added later cannot start leaking one by default.
        helper = text.split("def usl_trace_route", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("USL_TRACE_LOGGABLE_ROUTES", helper)
        self.assertIn("'(other)'", helper)

    def test_no_session_id_is_logged_beyond_the_prefix_odoo_already_logs(self) -> None:
        # Odoo itself logs ``session.sid[:8]``; ``res.device.log`` stores 42
        # characters. Nothing here may print more than the 8 upstream already
        # considers safe.
        for call in trace_log_calls():
            rendered = ast.unparse(call)
            for match in re.finditer(r"\.sid\[:(\d+)\]", rendered):
                with self.subTest(rendered=rendered[:60]):
                    self.assertLessEqual(int(match.group(1)), 8)
            self.assertNotIn("session_token", rendered)
            self.assertNotIn(".sid,", rendered)
            self.assertNotIn(".sid)", rendered)

    def test_the_trace_is_findable_and_removable(self) -> None:
        # It is temporary. Whoever removes it should be able to find all of it
        # from the marker alone, and find out why it exists at all.
        text = source()
        self.assertIn(
            "docs/operations/session-signout-investigation-20260910.md", text,
        )
        runbook = ROOT / "docs/operations/session-signout-investigation-20260910.md"
        self.assertTrue(runbook.is_file(), "the trace must point at a runbook that exists")
        # Every added block carries the marker in a comment or the message, so
        # `grep -n USL-TRACE` is a complete removal list.
        marked = [line for line in text.splitlines() if MARKER in line]
        self.assertGreaterEqual(len(marked), len(trace_log_calls()))


if __name__ == "__main__":
    unittest.main()
