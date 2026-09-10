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
from operations.gemini import GeminiError
from operations.release_manifest import _release_notes
from operations.release_notes import (
    ReleaseNotesError,
    build_notes,
    delivered_base,
    generate,
    is_ancestor,
    main,
    parse_title,
    range_commits,
    resolve_base,
    select_pull_requests,
    summarize,
    summary_prompt,
)


REPOSITORY = "unstaticlabs/odoo"
# Deliberately not a ``commit(n)``: the fake narrows a range whose base it
# knows, exactly as GitHub does, and a previous tip that is also a commit
# in the range would make these fixtures mean two things at once.
BEFORE = "b" * 40
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
        deployments: list[dict] | None = None,
        statuses: dict[int, list[str]] | None = None,
        compare_status: str = "ahead",
        deployments_error: bool = False,
    ):
        self.commits = commits
        self.compare_error = compare_error
        self.commits_error = commits_error
        self.deployments = deployments or []
        self.statuses = statuses or {}
        self.compare_status = compare_status
        self.deployments_error = deployments_error
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str]):
        self.calls.append(arguments)
        endpoint = arguments[0]
        if "/deployments/" in endpoint and "/statuses" in endpoint:
            identifier = int(endpoint.split("/deployments/")[1].split("/")[0])
            return [{"state": state} for state in self.statuses.get(identifier, [])]
        if endpoint.startswith(f"repos/{REPOSITORY}/deployments?"):
            if self.deployments_error:
                raise ReleaseNotesError("gh api deployments failed: HTTP 403")
            return self.deployments
        if endpoint.startswith(f"repos/{REPOSITORY}/compare/"):
            if self.compare_error:
                raise ReleaseNotesError("gh api compare failed: HTTP 404")
            # The history is ordered oldest first, so a base it contains
            # narrows the range exactly as GitHub would.
            shas = list(self.commits)
            base = endpoint.split("/compare/")[1].split("...")[0]
            if base in shas:
                shas = shas[shas.index(base) + 1:]
            return {
                "status": self.compare_status,
                "total_commits": len(shas),
                "commits": [{"sha": sha} for sha in shas],
            }
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


def deployment(identifier: int, sha: str, created: str) -> dict:
    return {
        "id": identifier,
        "sha": sha,
        "environment": "production-release",
        "created_at": created,
    }


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


class FakeGemini:
    """Answer one structured call from a canned payload, and record the prompt."""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    def structured(self, *, system_instruction, prompt, schema):
        self.calls.append(
            {"system_instruction": system_instruction, "prompt": prompt, "schema": schema},
        )
        if self.error is not None:
            raise self.error
        return self.payload


class SummaryTests(unittest.TestCase):
    def notes(self) -> dict:
        return build_notes(
            [
                node(3, "chore(ci): pin the release actions"),
                node(1, "fix(release): compare definitions with staging evidence"),
                node(2, "feat(b2c): allocate cost of goods on fulfilment events"),
            ],
            date=DATE,
        )

    def summarized(self, payload) -> tuple[dict, dict, FakeGemini]:
        original = self.notes()
        client = FakeGemini(payload)
        return original, summarize(original, client=client), client

    def test_the_prompt_carries_every_change_and_no_link_or_author(self) -> None:
        notes = self.notes()
        prompt = summary_prompt(notes)
        self.assertIn("#2 feat(b2c): allocate cost of goods on fulfilment events", prompt)
        self.assertIn("#1 fix(release): compare definitions with staging evidence", prompt)
        self.assertIn("#3 chore(ci): pin the release actions", prompt)
        self.assertIn("USL Distribution release 2026-09-05", prompt)
        self.assertIn("untrusted", prompt)
        # A link would invite the model to write one back; an author login has
        # no business in a third-party prompt.
        self.assertNotIn("https://", prompt)
        self.assertNotIn("elio-usl", prompt)

    def test_a_summary_rewrites_only_the_prose(self) -> None:
        original, notes, client = self.summarized({
            "overview": "Shop profit figures are now accurate. You need do nothing.",
            "changes": [
                {"number": 2, "text": "Your shop orders now show the real cost of each item."},
                {"number": 1, "text": "Release checks compare the right thing."},
                {"number": 3, "text": "Background maintenance you will not notice."},
            ],
        })
        self.assertEqual(notes["schema"], "usl-release-notes/v2")
        self.assertEqual(
            notes["summary"], "Shop profit figures are now accurate. You need do nothing.",
        )
        self.assertEqual(
            notes["changes"][0]["title"],
            "Your shop orders now show the real cost of each item.",
        )
        # Everything a link or the grouping depends on is untouched, so every
        # merged pull request stays linked exactly as it was.
        for before, after in zip(original["changes"], notes["changes"]):
            for field in ("type", "scope", "number", "url", "author"):
                self.assertEqual(before[field], after[field])
        self.assertEqual(notes["title"], original["title"])
        self.assertEqual(notes["action_required"], original["action_required"])
        self.assertEqual(len(client.calls), 1)
        _release_notes(notes)

    def test_action_required_is_never_rewritten(self) -> None:
        original = build_notes(
            [node(5, "feat(auth): rotate keys", labels={"nodes": [{"name": "action-required"}]})],
            date=DATE,
        )
        notes = summarize(original, client=FakeGemini({
            "overview": "Signing in is unchanged.",
            "changes": [{"number": 5, "text": "Nothing to worry about at all."}],
        }))
        self.assertEqual(notes["action_required"], "rotate keys")

    def test_an_unusable_rewrite_keeps_that_one_title(self) -> None:
        for text in (
            "",
            "   ",
            "See <b>the note</b>",
            "Read more at https://evil.example",
            "A [link](https://evil.example) for you",
            "x" * 301,
            None,
            42,
        ):
            with self.subTest(text=text):
                _original, notes, _client = self.summarized({
                    "overview": "A calm release.",
                    "changes": [
                        {"number": 2, "text": text},
                        {"number": 1, "text": "Release checks compare the right thing."},
                    ],
                })
                self.assertEqual(
                    notes["changes"][0]["title"],
                    "allocate cost of goods on fulfilment events",
                )
                # A neighbour is still rewritten: the fields degrade one by one.
                self.assertEqual(
                    notes["changes"][1]["title"], "Release checks compare the right thing.",
                )
                _release_notes(notes)

    def test_an_unusable_overview_keeps_the_mechanical_summary(self) -> None:
        for overview in ("", "   ", "Read <b>this</b>", "x" * 501, None, []):
            with self.subTest(overview=overview):
                original, notes, _client = self.summarized(
                    {"overview": overview, "changes": []},
                )
                self.assertEqual(notes["summary"], original["summary"])
                _release_notes(notes)

    def test_numbers_outside_the_changelog_are_ignored(self) -> None:
        _original, notes, _client = self.summarized({
            "overview": "A calm release.",
            "changes": [
                {"number": 999, "text": "A change that was never merged."},
                {"number": 2, "text": "Your shop orders now show the real cost."},
                {"number": True, "text": "A boolean is not a pull request."},
                "not an object",
                {"text": "No number at all."},
            ],
        })
        titles = [item["title"] for item in notes["changes"]]
        self.assertIn("Your shop orders now show the real cost.", titles)
        self.assertNotIn("A change that was never merged.", titles)
        self.assertNotIn("A boolean is not a pull request.", titles)
        self.assertEqual(len(notes["changes"]), 3)
        _release_notes(notes)

    def test_a_missing_changes_list_still_yields_valid_notes(self) -> None:
        original, notes, _client = self.summarized({"overview": "A calm release."})
        self.assertEqual(notes["summary"], "A calm release.")
        self.assertEqual(
            [item["title"] for item in notes["changes"]],
            [item["title"] for item in original["changes"]],
        )
        _release_notes(notes)


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

    def test_a_recorded_base_reaches_over_an_undeployed_release(self) -> None:
        api = FakeApi({commit(1): [node(9, "fix(home): keep the breadcrumb honest")]})
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "release-notes-base.json"
            record.write_text(json.dumps({
                "schema": "usl-release-notes-base/v1",
                "base_commit": "a" * 40,
                "reason": "the previous release never reached production",
            }), encoding="utf-8")
            code, out, err = self.run_main(
                [
                    "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                    "--date", "2026-09-05", "--unreleased-base", str(record),
                ],
                api,
            )
        self.assertEqual(code, 0, err)
        self.assertIn("reviewed record aaaaaaaaaaaa", err)
        self.assertIn("remove", err)
        compare = next(call[0] for call in api.calls if "/compare/" in call[0])
        self.assertIn("a" * 40, compare)
        self.assertNotIn(BEFORE, compare)
        self.assertEqual(json.loads(out)["changes"][0]["number"], 9)

    def test_an_unusable_recorded_base_stops_the_changelog(self) -> None:
        api = FakeApi({commit(1): [node(9, "fix(home): keep the breadcrumb honest")]})
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "release-notes-base.json"
            record.write_text(json.dumps({"schema": "wrong", "base_commit": "a" * 40}), encoding="utf-8")
            code, out, err = self.run_main(
                [
                    "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                    "--unreleased-base", str(record),
                ],
                api,
            )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("usl-release-notes-base/v1", err)
        self.assertEqual(api.calls, [])

    def test_no_record_keeps_the_pushed_range(self) -> None:
        api = FakeApi({commit(1): [node(9, "fix(home): keep the breadcrumb honest")]})
        with tempfile.TemporaryDirectory() as directory:
            code, _out, err = self.run_main(
                [
                    "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                    "--date", "2026-09-05",
                    "--unreleased-base", str(Path(directory) / "absent.json"),
                ],
                api,
            )
        self.assertEqual(code, 0, err)
        self.assertNotIn("reviewed record", err)
        self.assertIn("pushed range", err)
        compare = next(call[0] for call in api.calls if "/compare/" in call[0])
        self.assertIn(BEFORE, compare)

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

    def run_summarized(self, arguments: list[str], api, client) -> tuple:
        with mock.patch.object(release_notes, "GeminiClient", lambda *a, **k: client), \
                mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            return self.run_main([*arguments, "--summarize"], api)

    def test_summarize_rewrites_the_notes_for_users(self) -> None:
        api = FakeApi({commit(1): [node(9, "feat(home): add a distribution updates channel")]})
        client = FakeGemini({
            "overview": "You now have a channel for release news.",
            "changes": [{"number": 9, "text": "Release news arrives in its own channel."}],
        })
        code, out, err = self.run_summarized(
            ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA, "--date", "2026-09-05"],
            api,
            client,
        )
        self.assertEqual(code, 0, err)
        notes = json.loads(out)
        self.assertEqual(notes["schema"], "usl-release-notes/v2")
        self.assertEqual(notes["summary"], "You now have a channel for release news.")
        self.assertEqual(
            notes["changes"][0]["title"], "Release news arrives in its own channel.",
        )
        self.assertEqual(notes["changes"][0]["number"], 9)
        self.assertIn("summarized for users", err)
        _release_notes(notes)

    def test_the_provider_is_never_called_without_the_flag(self) -> None:
        api = FakeApi({commit(1): [node(9, "feat(home): welcome")]})
        client = FakeGemini({"overview": "unused", "changes": []})
        with mock.patch.object(release_notes, "GeminiClient", lambda *a, **k: client):
            code, out, _err = self.run_main(
                ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA], api,
            )
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(json.loads(out)["changes"][0]["title"], "welcome")

    def test_a_failed_summary_keeps_the_mechanical_changelog(self) -> None:
        for failure in (
            GeminiError("network", "Gemini could not be reached."),
            GeminiError("invalid_response", "Gemini returned invalid JSON."),
            ValueError("nonsense"),
            OSError("no route"),
        ):
            with self.subTest(failure=type(failure).__name__):
                api = FakeApi({commit(1): [node(9, "feat(home): welcome")]})
                code, out, err = self.run_summarized(
                    ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA],
                    api,
                    FakeGemini(error=failure),
                )
                self.assertEqual(code, 0)
                notes = json.loads(out)
                self.assertEqual(notes["schema"], "usl-release-notes/v2")
                self.assertEqual(notes["changes"][0]["title"], "welcome")
                self.assertIn("keeping the mechanical changelog", err)
                _release_notes(notes)

    def test_a_missing_key_keeps_the_mechanical_changelog(self) -> None:
        api = FakeApi({commit(1): [node(9, "feat(home): welcome")]})
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            code, out, err = self.run_main(
                ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA, "--summarize"],
                api,
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["changes"][0]["title"], "welcome")
        self.assertIn("keeping the mechanical changelog", err)

    def test_strict_does_not_make_a_failed_summary_fatal(self) -> None:
        """``--strict`` guards the changelog's provenance, not its wording."""
        api = FakeApi({commit(1): [node(9, "feat(home): welcome")]})
        code, out, err = self.run_summarized(
            ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA, "--strict"],
            api,
            FakeGemini(error=GeminiError("network", "Gemini could not be reached.")),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["schema"], "usl-release-notes/v2")

    def test_the_reviewed_fallback_is_never_sent_to_a_model(self) -> None:
        api = FakeApi({commit(1): []})
        client = FakeGemini({"overview": "unused", "changes": []})
        code, out, _err = self.run_summarized(
            ["--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA], api, client,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema"], "usl-release-notes/v1")
        self.assertEqual(client.calls, [])

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


# The production releases of 2026-09-08 and 2026-09-09, as they actually
# happened. #214 published and never deployed, so #203, #204, #205, #211 and
# #213 fell out of the range the next release announced.
DELIVERED = "6da8bf6dea4549fbaca624d51469a859022961ca"
UNDELIVERED = "8f55edd3440a0683b39f99a7a0ca82f7ff2300ad"
PROMOTED = "cab902594624840bbaa83b16ad3ca8188a43b34e"
PRODUCTION_REF = "refs/heads/19-usl"


def statuses_from(mapping: dict[int, list[str]]):
    return lambda identifier: [{"state": state} for state in mapping.get(identifier, [])]


class DeliveredBaseTests(unittest.TestCase):
    def test_the_newest_successful_deployment_is_the_base(self) -> None:
        api = FakeApi(
            {},
            deployments=[
                deployment(1, commit(1), "2026-09-08T17:00:00Z"),
                deployment(2, commit(2), "2026-09-09T04:00:00Z"),
            ],
            statuses={1: ["success"], 2: ["success"]},
        )
        self.assertEqual(delivered_base(REPOSITORY, api=api), commit(2))

    def test_a_failed_newest_deployment_reaches_further_back(self) -> None:
        api = FakeApi(
            {},
            deployments=[
                deployment(1, commit(1), "2026-09-08T17:00:00Z"),
                deployment(2, commit(2), "2026-09-09T04:00:00Z"),
            ],
            statuses={1: ["success"], 2: ["failure", "in_progress"]},
        )
        self.assertEqual(delivered_base(REPOSITORY, api=api), commit(1))

    def test_a_superseded_deployment_is_not_a_delivery(self) -> None:
        api = FakeApi(
            {},
            deployments=[deployment(1, commit(1), "2026-09-08T17:00:00Z")],
            statuses={1: ["inactive"]},
        )
        self.assertIsNone(delivered_base(REPOSITORY, api=api))

    def test_no_deployment_at_all_yields_no_base(self) -> None:
        self.assertIsNone(delivered_base(REPOSITORY, api=FakeApi({})))

    def test_an_unreadable_timestamp_becomes_a_release_notes_error(self) -> None:
        api = FakeApi(
            {},
            deployments=[deployment(1, commit(1), "the day before yesterday")],
            statuses={1: ["success"]},
        )
        with self.assertRaisesRegex(ReleaseNotesError, "unreadable"):
            delivered_base(REPOSITORY, api=api)


class IsAncestorTests(unittest.TestCase):
    def test_ahead_and_identical_place_the_base(self) -> None:
        for status in ("ahead", "identical"):
            api = FakeApi({}, compare_status=status)
            self.assertTrue(is_ancestor(REPOSITORY, commit(1), SHA, api))

    def test_diverged_and_behind_do_not(self) -> None:
        for status in ("diverged", "behind"):
            api = FakeApi({}, compare_status=status)
            self.assertFalse(is_ancestor(REPOSITORY, commit(1), SHA, api))


class ResolveBaseTests(unittest.TestCase):
    def ledger(self, **overrides) -> FakeApi:
        options = {
            "deployments": [deployment(1, DELIVERED, "2026-09-08T17:00:00Z")],
            "statuses": {1: ["success"]},
        }
        options.update(overrides)
        return FakeApi({}, **options)

    def resolve(self, api, **overrides):
        options = {
            "before": BEFORE,
            "sha": SHA,
            "ref": PRODUCTION_REF,
            "recorded": None,
        }
        options.update(overrides)
        return resolve_base(REPOSITORY, api=api, **options)

    def test_a_production_push_starts_from_the_last_delivered_release(self) -> None:
        base, reason = self.resolve(self.ledger())
        self.assertEqual(base, DELIVERED)
        self.assertIn("last delivered production release", reason)

    def test_a_staging_push_keeps_the_pushed_range(self) -> None:
        api = self.ledger()
        base, reason = self.resolve(api, ref="refs/heads/19-usl-staging")
        self.assertEqual(base, BEFORE)
        self.assertIn("only a production release is announced", reason)
        self.assertEqual(api.calls, [])

    def test_a_reviewed_record_outranks_the_ledger(self) -> None:
        api = self.ledger()
        base, reason = self.resolve(api, recorded="a" * 40)
        self.assertEqual(base, "a" * 40)
        self.assertIn("reviewed record", reason)
        self.assertEqual(api.calls, [])

    def test_a_base_that_is_not_an_ancestor_is_refused(self) -> None:
        base, reason = self.resolve(self.ledger(compare_status="diverged"))
        self.assertEqual(base, BEFORE)
        self.assertIn("is not an ancestor", reason)

    def test_an_unreadable_ledger_keeps_the_pushed_range(self) -> None:
        base, reason = self.resolve(self.ledger(deployments_error=True))
        self.assertEqual(base, BEFORE)
        self.assertIn("deployment ledger is unreadable", reason)

    def test_an_unplaceable_base_keeps_the_pushed_range(self) -> None:
        base, reason = self.resolve(self.ledger(compare_error=True))
        self.assertEqual(base, BEFORE)
        self.assertIn("cannot be placed", reason)

    def test_nothing_delivered_yet_keeps_the_pushed_range(self) -> None:
        base, reason = self.resolve(self.ledger(deployments=[], statuses={}))
        self.assertEqual(base, BEFORE)
        self.assertIn("no production release has reached users yet", reason)

    def test_a_ledger_that_agrees_with_the_push_changes_nothing(self) -> None:
        api = self.ledger()
        base, reason = self.resolve(api, before=DELIVERED)
        self.assertEqual(base, DELIVERED)
        self.assertIn("already starts at delivered", reason)

    def test_a_re_run_of_a_delivered_release_keeps_the_pushed_range(self) -> None:
        """Otherwise ``range_commits`` falls back to the last twenty commits.

        A release workflow can be re-run after it has already deployed, and a
        base equal to the pushed commit would announce a changelog of work that
        was announced long ago.
        """
        api = self.ledger(
            deployments=[deployment(1, SHA, "2026-09-09T10:31:00Z")],
            statuses={1: ["success"]},
        )
        base, reason = self.resolve(api)
        self.assertEqual(base, BEFORE)
        self.assertIn("is this release", reason)

    def test_an_invalid_identity_never_reaches_the_api(self) -> None:
        api = self.ledger()
        base, reason = self.resolve(api, sha="abc")
        self.assertEqual(base, BEFORE)
        self.assertIn("release identity is invalid", reason)
        self.assertEqual(api.calls, [])


class LedgerMainTests(MainTests):
    def incident(self) -> FakeApi:
        """The real 2026-09-09 history: one release that never deployed."""
        return FakeApi(
            {
                DELIVERED: [],
                "b22ff82261abbfbd1ea6b33948cc7964081f80d8": [
                    node(203, "docs(delivery): answer the delivery-protocol review"),
                ],
                "fb1982bb03045a048c32f8e6c1804c644474ff71": [
                    node(204, "fix(operations): name the Odoo service staging runs"),
                ],
                "a9b7c14e57a6f45e6dcc55cdced36e7759f9a33c": [
                    node(205, "fix(navigation): make Official Documents reachable again"),
                ],
                "7deb707f4cb26635312abade45fefb2b28082d71": [
                    node(211, "docs(agents): point every agent at its skills"),
                ],
                "a4a599fd01bf51b0873cf4efb1f812d9c36f9c46": [
                    node(213, "fix(delivery): move agent worktrees out of harm"),
                ],
                UNDELIVERED: [
                    node(
                        214, "chore(release): promote qualified staging",
                        baseRefName="19-usl", headRefName="19-usl-staging",
                    ),
                ],
                "423b602afc03f60a1a8aeb0d4857476cf6dac023": [
                    node(218, "fix(documents): keep a failed access push from failing a release"),
                ],
                PROMOTED: [
                    node(
                        220, "chore(release): promote qualified staging",
                        baseRefName="19-usl", headRefName="19-usl-staging",
                    ),
                ],
            },
            deployments=[
                deployment(1, DELIVERED, "2026-09-08T17:25:00Z"),
                deployment(2, UNDELIVERED, "2026-09-09T04:09:00Z"),
            ],
            statuses={1: ["success"], 2: ["failure", "in_progress"]},
        )

    def announced(self, api, **extra) -> tuple[int, dict, str]:
        code, out, err = self.run_main(
            [
                "--repository", REPOSITORY, "--before", UNDELIVERED,
                "--sha", PROMOTED, "--date", "2026-09-09",
                *[item for pair in extra.items() for item in pair],
            ],
            api,
        )
        return code, json.loads(out) if code == 0 and out.strip() else {}, err

    def test_the_changelog_reaches_over_the_release_that_never_deployed(self) -> None:
        """The regression this whole change exists for.

        Production released #214 and it rolled back. The next release pushed
        from that same tip, so the pull requests #214 carried were outside its
        range and were never announced to anyone.
        """
        api = self.incident()
        code, notes, err = self.announced(api, **{"--ref": "refs/heads/19-usl"})
        self.assertEqual(code, 0, err)
        self.assertEqual(
            sorted(change["number"] for change in notes["changes"]),
            [203, 204, 205, 211, 213, 218],
        )
        self.assertIn(f"last delivered production release {DELIVERED[:12]}", err)
        # The notes still have to satisfy the sealed manifest contract.
        _release_notes(notes)

    def test_without_the_ledger_the_same_history_loses_five_changes(self) -> None:
        """What the pushed range alone produces, which is the bug."""
        api = self.incident()
        code, notes, err = self.announced(api)
        self.assertEqual(code, 0, err)
        self.assertEqual([change["number"] for change in notes["changes"]], [218])

    def test_a_ledger_failure_never_fails_the_changelog(self) -> None:
        api = self.incident()
        api.deployments_error = True
        code, notes, err = self.announced(api, **{"--ref": "refs/heads/19-usl"})
        self.assertEqual(code, 0, err)
        self.assertEqual([change["number"] for change in notes["changes"]], [218])
        self.assertIn("deployment ledger is unreadable", err)

    def test_an_unreadable_fallback_still_produces_valid_notes(self) -> None:
        """A changelog must never be the thing that stops a release."""
        api = FakeApi({commit(1): []})
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "release-notes.json"
            broken.write_text("{ not json", encoding="utf-8")
            code, out, err = self.run_main(
                [
                    "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                    "--date", "2026-09-05", "--fallback", str(broken),
                ],
                api,
            )
        self.assertEqual(code, 0, err)
        notes = json.loads(out)
        self.assertEqual(notes["schema"], "usl-release-notes/v1")
        self.assertIn("minimal built-in notes", err)
        _release_notes(notes)

    def test_a_missing_fallback_file_is_not_fatal_either(self) -> None:
        api = FakeApi({commit(1): []})
        code, out, err = self.run_main(
            [
                "--repository", REPOSITORY, "--before", BEFORE, "--sha", SHA,
                "--date", "2026-09-05", "--fallback", "/nonexistent/notes.json",
            ],
            api,
        )
        self.assertEqual(code, 0, err)
        _release_notes(json.loads(out))


if __name__ == "__main__":
    unittest.main()
