"""The release-notes generator lists merged pull requests without GitHub."""

from __future__ import annotations

import datetime
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from operations import release_notes
from operations.release_manifest import _release_notes
from operations.release_notes import (
    ReleaseNotesError,
    build_notes,
    generate,
    main,
    parse_title,
    range_commits,
    select_pull_requests,
)


REPOSITORY = "unstaticlabs/odoo"
BEFORE = "0" * 39 + "1"
SHA = "f" * 40
DATE = datetime.date(2026, 9, 5)


def node(number: int, title: str, **overrides) -> dict:
    value = {
        "number": number,
        "title": title,
        "url": f"https://github.com/unstaticlabs/odoo/pull/{number}",
        "merged": True,
        "mergedAt": f"2026-09-05T{number % 24:02d}:00:00Z",
        "baseRefName": "19-usl-staging",
        "headRefName": f"codex/change-{number}",
        "author": {"login": "elio-usl"},
        "labels": {"nodes": []},
        "baseRepository": {"nameWithOwner": REPOSITORY},
    }
    value.update(overrides)
    return value


class FakeApi:
    """Answer ``gh api`` calls from canned data and record them."""

    def __init__(
        self,
        commits: dict[str, list[dict]],
        *,
        compare_error: bool = False,
        commits_error: bool = False,
    ):
        self.commits = commits
        self.compare_error = compare_error
        self.commits_error = commits_error
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str]):
        self.calls.append(arguments)
        endpoint = arguments[0]
        if endpoint.startswith(f"repos/{REPOSITORY}/compare/"):
            if self.compare_error:
                raise ReleaseNotesError("gh api compare failed: HTTP 404")
            shas = list(self.commits)
            return {"total_commits": len(shas), "commits": [{"sha": sha} for sha in shas]}
        if endpoint.startswith(f"repos/{REPOSITORY}/commits?"):
            if self.commits_error:
                raise ReleaseNotesError("gh api commits failed: connection refused")
            return [{"sha": sha} for sha in reversed(self.commits)]
        if endpoint == "graphql":
            query = arguments[arguments.index("-f") + 1].removeprefix("query=")
            repository = {}
            index = 0
            for sha, nodes in self.commits.items():
                if f'object(oid: "{sha}")' in query:
                    repository[f"c{index}"] = {
                        "oid": sha,
                        "associatedPullRequests": {"nodes": nodes},
                    }
                    index += 1
            return {"data": {"repository": repository}}
        raise AssertionError(f"unexpected gh api call: {arguments}")


def commit(index: int) -> str:
    return f"{index:040x}"


class ParseTitleTests(unittest.TestCase):
    def test_conventional_titles_split_into_type_scope_and_subject(self) -> None:
        self.assertEqual(parse_title("fix(release): keep it"), ("fix", "release", "keep it"))
        self.assertEqual(parse_title("feat!: breaking"), ("feat", None, "breaking"))
        self.assertEqual(parse_title("docs(ops/cd):  spaced  "), ("docs", "ops/cd", "spaced"))

    def test_free_text_and_unknown_types_are_other(self) -> None:
        title = "Contributors can understand a pull request before reading it."
        self.assertEqual(parse_title(title), ("other", None, title))
        self.assertEqual(parse_title("hotfix(x): y"), ("other", None, "hotfix(x): y"))


class RangeCommitsTests(unittest.TestCase):
    def test_zero_or_missing_before_uses_the_last_commits(self) -> None:
        api = FakeApi({commit(1): [], commit(2): []})
        for before in (None, "", "0" * 40, "not-a-commit"):
            api.calls.clear()
            self.assertEqual(range_commits(REPOSITORY, before, SHA, api), [commit(1), commit(2)])
            self.assertEqual(len(api.calls), 1)
            self.assertIn("per_page=20", api.calls[0][0])

    def test_unreachable_base_falls_back_to_the_last_commits(self) -> None:
        api = FakeApi({commit(1): [], commit(2): []}, compare_error=True)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(range_commits(REPOSITORY, BEFORE, SHA, api), [commit(1), commit(2)])
        self.assertIn("using the last commits", err.getvalue())
        self.assertEqual(len(api.calls), 2)
        self.assertIn("per_page=20", api.calls[1][0])

    def test_compare_range_is_used_when_before_is_valid(self) -> None:
        api = FakeApi({commit(1): [], commit(2): []})
        self.assertEqual(range_commits(REPOSITORY, BEFORE, SHA, api), [commit(1), commit(2)])
        self.assertIn(f"compare/{BEFORE}...{SHA}", api.calls[0][0])


class SelectionTests(unittest.TestCase):
    def test_promotion_is_excluded_but_carried_pull_requests_stay(self) -> None:
        promotion = node(
            112, "chore(release): promote qualified staging",
            baseRefName="19-usl", headRefName="19-usl-staging",
        )
        urgent = node(113, "fix(access): record audit", baseRefName="19-usl", headRefName="urgent/x")
        carried = node(111, "fix(release): compare definitions")
        unmerged = node(114, "feat(x): draft", merged=False)
        fork = node(115, "fix(y): from a fork", baseRepository={"nameWithOwner": "someone/odoo"})
        selected = select_pull_requests(
            REPOSITORY, [promotion, carried, urgent, unmerged, fork],
        )
        self.assertEqual([item["number"] for item in selected], [111, 113])


class BuildNotesTests(unittest.TestCase):
    def test_changes_are_ordered_by_type_and_summarised_by_scope(self) -> None:
        notes = build_notes(
            [
                node(3, "chore(ci): tidy"),
                node(1, "fix(release): keep"),
                node(2, "feat(access): add", author=None),
                node(4, "Plain title without a type"),
            ],
            date=DATE,
        )
        self.assertEqual(notes["schema"], "usl-release-notes/v2")
        self.assertEqual(notes["title"], "USL Distribution release 2026-09-05")
        self.assertEqual(
            notes["summary"],
            "4 changes since the previous release, in access, ci and release.",
        )
        self.assertEqual(
            [(item["type"], item["number"]) for item in notes["changes"]],
            [("feat", 2), ("fix", 1), ("chore", 3), ("other", 4)],
        )
        self.assertEqual(notes["changes"][0]["author"], None)
        self.assertEqual(notes["changes"][1]["author"], "elio-usl")
        self.assertEqual(notes["changes"][3]["title"], "Plain title without a type")
        self.assertIsNone(notes["action_required"])
        _release_notes(notes)

    def test_action_required_label_lifts_the_title(self) -> None:
        notes = build_notes(
            [node(5, "feat(auth): rotate keys", labels={"nodes": [{"name": "action-required"}]})],
            date=DATE,
        )
        self.assertEqual(notes["summary"], "1 change since the previous release, in auth.")
        self.assertEqual(notes["action_required"], "rotate keys")

    def test_empty_range_returns_nothing(self) -> None:
        self.assertIsNone(build_notes([], date=DATE))


class GenerateTests(unittest.TestCase):
    def test_pull_requests_are_deduplicated_across_their_commits(self) -> None:
        carried = node(111, "fix(release): compare definitions")
        promotion = node(
            112, "chore(release): promote qualified staging",
            baseRefName="19-usl", headRefName="19-usl-staging",
        )
        api = FakeApi({
            commit(1): [carried],
            commit(2): [carried],
            commit(3): [promotion],
        })
        notes = generate(REPOSITORY, BEFORE, SHA, date=DATE, api=api)
        self.assertEqual([item["number"] for item in notes["changes"]], [111])
        self.assertEqual(len([call for call in api.calls if call[0] == "graphql"]), 1)

    def test_invalid_inputs_are_rejected_before_any_call(self) -> None:
        api = FakeApi({})
        with self.assertRaisesRegex(ReleaseNotesError, "repository"):
            generate("bad repo", BEFORE, SHA, date=DATE, api=api)
        with self.assertRaisesRegex(ReleaseNotesError, "release commit"):
            generate(REPOSITORY, BEFORE, "abc", date=DATE, api=api)
        self.assertEqual(api.calls, [])


class MainTests(unittest.TestCase):
    def run_main(self, arguments: list[str], api) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(release_notes, "gh_api", api), redirect_stdout(out), redirect_stderr(err):
            code = main(arguments)
        return code, out.getvalue(), err.getvalue()

    def test_fallback_is_used_when_nothing_was_merged(self) -> None:
        api = FakeApi({commit(1): []})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "notes.json"
            code, out, err = self.run_main(
                [
                    "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                    "--date", "2026-09-05", "--output", str(output),
                ],
                api,
            )
            self.assertEqual(code, 0, err)
            written = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(written["schema"], "usl-release-notes/v1")
        self.assertIn("fallback", err)
        self.assertEqual(out.strip(), str(output))

    def test_github_failure_uses_the_fallback_unless_strict(self) -> None:
        api = FakeApi({commit(1): []}, compare_error=True, commits_error=True)
        arguments = ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA]
        code, out, err = self.run_main(arguments, api)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema"], "usl-release-notes/v1")
        self.assertIn("using the fallback notes", err)
        code, out, err = self.run_main([*arguments, "--strict"], api)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("commits failed", err)

    def test_generated_notes_are_printed_when_no_output_is_given(self) -> None:
        api = FakeApi({commit(1): [node(9, "feat(home): welcome")]})
        code, out, _err = self.run_main(
            ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA, "--date", "2026-09-05"],
            api,
        )
        self.assertEqual(code, 0)
        notes = json.loads(out)
        self.assertEqual(notes["schema"], "usl-release-notes/v2")
        self.assertEqual(notes["changes"][0]["number"], 9)


if __name__ == "__main__":
    unittest.main()
