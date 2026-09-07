"""The release notification program must store its message before it reports it.

``odoo shell`` rolls the transaction back at exit. A program that prints a
message id without ``env.cr.commit()`` reports a message that never exists.
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import sys
import types
import unittest

from operations.stack import RELEASE_NOTIFICATION_PROGRAM


RELEASE_ID = "a" * 64
NOTES_V1 = {
    "schema": "usl-release-notes/v1",
    "title": "Safer releases",
    "summary": "The release is active.",
    "changes": ["Improved recovery <b>bold</b>."],
    "action_required": None,
}


class _Markup(str):
    """A minimal stand-in for ``markupsafe.Markup`` when it is not installed."""

    def __html__(self) -> str:
        return str(self)

    def __add__(self, other):
        return _Markup(str(self) + str(_escape(other)))

    def __mod__(self, arguments):
        if not isinstance(arguments, tuple):
            arguments = (arguments,)
        return _Markup(str(self) % tuple(str(_escape(item)) for item in arguments))

    def join(self, items):
        return _Markup(str(self).join(str(_escape(item)) for item in items))


def _escape(value):
    if hasattr(value, "__html__"):
        return _Markup(value.__html__())
    return _Markup(html.escape(str(value), quote=True))


class _Record:
    def __init__(self, identifier: int | None):
        self.id = identifier

    def __bool__(self) -> bool:
        return self.id is not None

    def sudo(self):
        return self


class _Channel(_Record):
    _name = "discuss.channel"

    def __init__(self, database: "_Database"):
        super().__init__(7)
        self.database = database

    def message_post(self, **values):
        self.database.log.append("message_post")
        identifier = self.database.next_id
        self.database.next_id += 1
        self.database.uncommitted[identifier] = dict(values, id=identifier)
        return _Record(identifier)


class _Messages:
    def __init__(self, database: "_Database"):
        self.database = database

    def sudo(self):
        return self

    def search(self, domain, limit=None):
        self.database.log.append("search")
        wanted = dict((name, value) for name, operator, value in domain)["message_id"]
        visible = dict(self.database.committed)
        if not self.database.fresh_transaction:
            visible.update(self.database.uncommitted)
        for identifier, values in visible.items():
            if values["message_id"] == wanted:
                return _Record(identifier)
        return _Record(None)


class _Cursor:
    def __init__(self, database: "_Database"):
        self.database = database

    def commit(self) -> None:
        self.database.log.append("commit")
        if not self.database.lose_writes:
            self.database.committed.update(self.database.uncommitted)
        self.database.uncommitted = {}
        self.database.fresh_transaction = True


class _Database:
    """Committed rows survive; uncommitted rows are visible only in-transaction."""

    def __init__(self, *, lose_writes: bool = False):
        self.committed: dict[int, dict] = {}
        self.uncommitted: dict[int, dict] = {}
        self.next_id = 74707
        self.log: list[str] = []
        self.fresh_transaction = False
        self.lose_writes = lose_writes


class _Environment:
    def __init__(self, database: _Database, notes: dict, evidence_url: str | None):
        self.database = database
        self.context = {
            "usl_release_notification_id": RELEASE_ID,
            "usl_release_notification_notes": json.dumps(notes, sort_keys=True),
            "usl_release_notification_evidence_url": evidence_url,
        }
        self.cr = _Cursor(database)
        self.channel = _Channel(database)

    def ref(self, xmlid: str):
        return {
            "usl_home.channel_distribution_updates": self.channel,
            "base.partner_root": _Record(2),
        }[xmlid]

    def __getitem__(self, model: str):
        assert model == "mail.message", model
        return _Messages(self.database)

    def invalidate_all(self) -> None:
        self.database.log.append("invalidate_all")


def run_program(database: _Database, notes: dict = NOTES_V1, evidence_url=None) -> dict:
    """Run the shell program with a fake environment; return the printed result."""
    database.fresh_transaction = False
    fake_modules = {}
    if "markupsafe" not in sys.modules:
        try:
            import markupsafe  # noqa: F401
        except ImportError:
            fake_modules["markupsafe"] = types.SimpleNamespace(Markup=_Markup, escape=_escape)
    fake_modules["odoo"] = types.SimpleNamespace(
        fields=types.SimpleNamespace(
            Datetime=types.SimpleNamespace(now=lambda: "2026-09-05 22:00:00"),
        ),
    )
    original = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            exec(
                compile(RELEASE_NOTIFICATION_PROGRAM, "notification", "exec"),
                {"env": _Environment(database, notes, evidence_url)},
            )
    finally:
        for name, module in original.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    database.log.append("print")
    prefix = "USL_RELEASE_NOTIFICATION_RESULT="
    lines = [line for line in output.getvalue().splitlines() if line.startswith(prefix)]
    return json.loads(lines[-1].removeprefix(prefix)) if lines else {}


class ReleaseNotificationProgramTests(unittest.TestCase):
    def test_program_commits_and_rereads_before_it_prints(self) -> None:
        program = RELEASE_NOTIFICATION_PROGRAM
        posted = program.index("message_post(")
        commit = program.index("env.cr.commit()")
        invalidate = program.index("env.invalidate_all()")
        reread = program.index("search(message_domain, limit=1)", commit)
        printed = program.index("print(")
        self.assertLess(posted, commit)
        self.assertLess(commit, invalidate)
        self.assertLess(invalidate, reread)
        self.assertLess(reread, printed)
        self.assertIn("was not stored after commit", program)
        compile(program, "notification", "exec")

    def test_posted_message_survives_the_shell_rollback(self) -> None:
        database = _Database()
        result = run_program(database)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(result["message_id"], 74707)
        self.assertEqual(result["release"], RELEASE_ID)
        self.assertIn(74707, database.committed)
        self.assertEqual(
            database.log,
            ["search", "message_post", "commit", "invalidate_all", "search", "print"],
        )
        body = str(database.committed[74707]["body"])
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", body)
        self.assertNotIn("<b>bold</b>", body)
        self.assertEqual(
            database.committed[74707]["message_id"],
            f"<usl-release-{RELEASE_ID}@unstaticlabs.com>",
        )

    def test_second_run_reports_already_posted_with_the_same_id(self) -> None:
        database = _Database()
        first = run_program(database)
        database.log.clear()
        second = run_program(database)
        self.assertEqual(second["status"], "already_posted")
        self.assertEqual(second["message_id"], first["message_id"])
        self.assertEqual(database.log, ["search", "print"])
        self.assertEqual(len(database.committed), 1)

    def test_lost_message_fails_instead_of_reporting_a_ghost_id(self) -> None:
        database = _Database(lose_writes=True)
        with self.assertRaisesRegex(RuntimeError, "74707 was not stored after commit"):
            run_program(database)
        self.assertEqual(database.committed, {})


def change(number: int, kind: str, scope, title: str) -> dict:
    return {
        "type": kind,
        "scope": scope,
        "title": title,
        "number": number,
        "url": f"https://github.com/unstaticlabs/odoo/pull/{number}",
        "author": "elio-usl",
    }


class ReleaseChangelogRenderingTests(unittest.TestCase):
    def notes(self, changes: list[dict], action_required=None) -> dict:
        return {
            "schema": "usl-release-notes/v2",
            "title": "USL Distribution release 2026-09-05",
            "summary": f"{len(changes)} changes since the previous release, in release.",
            "changes": changes,
            "action_required": action_required,
        }

    def test_short_changelog_is_one_flat_list_with_pull_request_links(self) -> None:
        database = _Database()
        result = run_program(
            database,
            self.notes([
                change(111, "fix", "release", "compare definitions <script>x</script>"),
                change(44, "other", None, "Contributors can understand a pull request"),
            ]),
            evidence_url="https://github.com/unstaticlabs/odoo/actions/runs/1",
        )
        body = str(database.committed[result["message_id"]]["body"])
        self.assertIn("<h3>USL Distribution release 2026-09-05</h3>", body)
        # The Conventional Commit prefix is gone: a title may already be a
        # plain-language sentence written for the people reading this channel.
        self.assertIn(
            '<li>compare definitions &lt;script&gt;x&lt;/script&gt; '
            '(<a href="https://github.com/unstaticlabs/odoo/pull/111">#111</a>)</li>',
            body,
        )
        self.assertIn(
            '<li>Contributors can understand a pull request '
            '(<a href="https://github.com/unstaticlabs/odoo/pull/44">#44</a>)</li>',
            body,
        )
        self.assertNotIn("fix(release):", body)
        self.assertNotIn("<li>other:", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("<strong>Fixes</strong>", body)
        self.assertNotIn("<table", body)
        self.assertEqual(body.count("<ul>"), 1)
        self.assertLess(body.index("</ul>"), body.index("Deployed 2026-09-05 22:00:00"))
        self.assertIn("release <code>aaaaaaaaaaaa</code>", body)
        self.assertIn('<a href="https://github.com/unstaticlabs/odoo/actions/runs/1">technical evidence</a>', body)

    def test_long_changelog_is_grouped_by_type_in_order(self) -> None:
        database = _Database()
        changes = [
            change(1, "chore", "ci", "tidy"),
            change(2, "fix", "release", "keep"),
            change(3, "fix", "access", "record"),
            change(4, "feat", "home", "welcome"),
            change(5, "docs", None, "explain"),
            change(6, "other", None, "Free text"),
        ]
        result = run_program(database, self.notes(changes, action_required="Rotate the keys"))
        body = str(database.committed[result["message_id"]]["body"])
        headings = [
            body.index(f"<p><strong>{label}</strong></p><ul>")
            for label in ("New features", "Fixes", "Documentation", "Maintenance", "Other changes")
        ]
        self.assertEqual(headings, sorted(headings))
        self.assertNotIn("<strong>Performance</strong>", body)
        self.assertEqual(body.count("<li>"), 6)
        self.assertLess(body.index("#2</a>"), body.index("#3</a>"))
        self.assertIn("<p><strong>Action required:</strong> Rotate the keys</p>", body)
        self.assertNotIn("<table", body)

    def test_summarized_notes_link_every_pull_request_exactly_once(self) -> None:
        """A summary rewrites titles; it never touches a number or a link."""
        database = _Database()
        changes = [
            change(200 + index, "chore", None, f"Background work you will not notice, {index}.")
            for index in range(12)
        ]
        changes[0] = change(
            111, "feat", "b2c",
            "Your shop orders now show the real cost of each item.",
        )
        result = run_program(
            database,
            self.notes(
                changes,
            ) | {"summary": "This release makes invoicing quicker. You need to do nothing."},
        )
        body = str(database.committed[result["message_id"]]["body"])
        self.assertIn(
            "<p>This release makes invoicing quicker. You need to do nothing.</p>", body,
        )
        self.assertIn(
            "<li>Your shop orders now show the real cost of each item. "
            '(<a href="https://github.com/unstaticlabs/odoo/pull/111">#111</a>)</li>',
            body,
        )
        prefix = 'href="https://github.com/unstaticlabs/odoo/pull/'
        self.assertEqual(body.count(prefix), len(changes))
        for item in changes:
            self.assertEqual(body.count(f">#{item['number']}</a>"), 1)
        self.assertNotIn("chore", body)
        self.assertNotIn("feat(b2c)", body)
        self.assertNotIn("<table", body)

    def test_v1_notes_still_render_as_plain_items(self) -> None:
        database = _Database()
        result = run_program(database, NOTES_V1)
        body = str(database.committed[result["message_id"]]["body"])
        self.assertIn("<ul><li>Improved recovery &lt;b&gt;bold&lt;/b&gt;.</li></ul>", body)


if __name__ == "__main__":
    unittest.main()
