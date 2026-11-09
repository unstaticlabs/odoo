from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from operations.mattermost_odoo_release import (
    MattermostOdooReleaseError,
    load_pin,
    load_pin_at,
    parse_pin,
    review,
    verify_reachable,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = "odoo/usl_mattermost"


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(directory), *arguments),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def pin(commit: str, *, path: str = MODULE_PATH) -> dict[str, str]:
    return {
        "schema": "usl-mattermost-odoo-release-v1",
        "repository": "https://github.com/unstaticlabs/mattermost-odoo.git",
        "commit": commit,
        "module": "usl_mattermost",
        "path": path,
    }


class Upstream:
    """A local stand-in for unstaticlabs/mattermost-odoo, served over file://."""

    def __init__(self, root: Path) -> None:
        self.path = root / "upstream"
        self.path.mkdir()
        git(self.path, "init", "--quiet", "--initial-branch=main")
        git(self.path, "config", "user.name", "Pin Test")
        git(self.path, "config", "user.email", "pin-test@example.com")
        git(self.path, "config", "uploadpack.allowFilter", "true")
        git(self.path, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.url = self.path.resolve().as_uri()

    def commit(self, relative: str, text: str, message: str) -> str:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git(self.path, "add", ".")
        git(self.path, "commit", "--quiet", "-m", message)
        return git(self.path, "rev-parse", "HEAD")


class PinFormatTest(unittest.TestCase):
    def test_the_tracked_pin_is_valid(self) -> None:
        self.assertRegex(load_pin(ROOT)["commit"], r"^[0-9a-f]{40}$")

    def test_refuses_a_short_commit(self) -> None:
        with self.assertRaisesRegex(MattermostOdooReleaseError, "full SHA-1"):
            parse_pin(json.dumps(pin("abc1234")))

    def test_refuses_a_repository_outside_unstaticlabs(self) -> None:
        value = pin("a" * 40) | {"repository": "https://github.com/example/mattermost-odoo.git"}
        with self.assertRaisesRegex(MattermostOdooReleaseError, "public unstaticlabs URL"):
            parse_pin(json.dumps(value))

    def test_refuses_a_path_that_leaves_the_repository(self) -> None:
        with self.assertRaisesRegex(MattermostOdooReleaseError, "module path"):
            parse_pin(json.dumps(pin("a" * 40, path="odoo/../secrets")))

    def test_refuses_an_extra_field(self) -> None:
        with self.assertRaisesRegex(MattermostOdooReleaseError, "unexpected fields"):
            parse_pin(json.dumps(pin("a" * 40) | {"ref": "main"}))


class ReachabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.upstream = Upstream(self.root)
        self.first = self.upstream.commit(f"{MODULE_PATH}/__manifest__.py", "{}\n", "first")
        self.second = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 1\n", "second")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def workdir(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.root))

    def test_accepts_the_tip_and_an_older_commit_of_main(self) -> None:
        self.assertEqual(
            verify_reachable(self.upstream.url, self.second, self.workdir()),
            self.second,
        )
        self.assertEqual(
            verify_reachable(self.upstream.url, self.first, self.workdir()),
            self.second,
        )

    def test_refuses_a_commit_that_only_another_branch_holds(self) -> None:
        # A fork-only commit looks the same: the host serves it by SHA,
        # but main of the pinned repository does not contain it.
        git(self.upstream.path, "switch", "--quiet", "-c", "fork")
        fork = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 'evil'\n", "fork")
        git(self.upstream.path, "switch", "--quiet", "main")

        with self.assertRaisesRegex(MattermostOdooReleaseError, "not in the history of main"):
            verify_reachable(self.upstream.url, fork, self.workdir())

    def test_the_ancestor_check_refuses_a_fork_commit_that_is_present(self) -> None:
        # An older Git ignores GIT_NO_LAZY_FETCH and fetches the commit by SHA.
        git(self.upstream.path, "switch", "--quiet", "-c", "fork")
        fork = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 'evil'\n", "fork")
        git(self.upstream.path, "switch", "--quiet", "main")

        def lazy_fetch(directory: Path, commit: str) -> bool:
            git(directory, "fetch", "--quiet", "origin", commit)
            return True

        with patch("operations.mattermost_odoo_release._has_commit", side_effect=lazy_fetch):
            with self.assertRaisesRegex(MattermostOdooReleaseError, "not an ancestor of main"):
                verify_reachable(self.upstream.url, fork, self.workdir())

    def test_refuses_a_commit_that_main_dropped(self) -> None:
        git(self.upstream.path, "reset", "--quiet", "--hard", self.first)

        with self.assertRaisesRegex(MattermostOdooReleaseError, "not in the history of main"):
            verify_reachable(self.upstream.url, self.second, self.workdir())

    def test_refuses_an_unknown_commit(self) -> None:
        with self.assertRaisesRegex(MattermostOdooReleaseError, "not in the history of main"):
            verify_reachable(self.upstream.url, "c" * 40, self.workdir())


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.upstream = Upstream(self.root)
        self.old = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 1\n", "old")
        self.upstream.commit("plugin/server.go", "package main\n", "plugin only")
        self.new = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 2\n", "new")
        self.directory = Path(tempfile.mkdtemp(dir=self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_shows_the_stat_and_the_module_diff_only(self) -> None:
        verify_reachable(self.upstream.url, self.new, self.directory)

        text = review(pin(self.old), pin(self.new), self.directory, remote=self.upstream.url)

        self.assertIn(f"compare/{self.old}...{self.new}", text)
        self.assertIn("Commits that the new pin adds: 2. Commits that it drops: 0.", text)
        self.assertIn(f"{MODULE_PATH}/models.py | 2 +-", text)
        self.assertIn("-A = 1\n+A = 2", text)
        self.assertNotIn("plugin/server.go", text)

    def test_prints_nothing_when_the_pin_is_unchanged(self) -> None:
        verify_reachable(self.upstream.url, self.new, self.directory)

        self.assertEqual(review(pin(self.new), pin(self.new), self.directory), "")

    def test_shows_the_whole_module_for_a_new_pin(self) -> None:
        verify_reachable(self.upstream.url, self.new, self.directory)

        text = review(None, pin(self.new), self.directory)

        self.assertIn("adds the pin", text)
        self.assertIn("+A = 2", text)

    def test_fetches_an_old_pin_that_main_does_not_hold(self) -> None:
        git(self.upstream.path, "switch", "--quiet", "-c", "fork", self.old)
        fork = self.upstream.commit(f"{MODULE_PATH}/models.py", "A = 'fork'\n", "fork")
        git(self.upstream.path, "switch", "--quiet", "main")
        verify_reachable(self.upstream.url, self.new, self.directory)

        text = review(pin(fork), pin(self.new), self.directory, remote=self.upstream.url)

        self.assertIn("Commits that it drops: 1.", text)
        self.assertIn("-A = 'fork'\n+A = 2", text)

    def test_truncates_a_large_diff(self) -> None:
        verify_reachable(self.upstream.url, self.new, self.directory)

        text = review(pin(self.old), pin(self.new), self.directory, max_diff_bytes=10)

        self.assertIn("shows the first 10 bytes", text)

    def test_a_backtick_fence_in_the_diff_does_not_end_the_block(self) -> None:
        newer = self.upstream.commit(f"{MODULE_PATH}/README.md", "````\n", "fence")
        verify_reachable(self.upstream.url, newer, self.directory)

        text = review(pin(self.new), pin(newer), self.directory)

        self.assertIn("`````diff", text)


class PinAtRevisionTest(unittest.TestCase):
    def test_reads_the_pin_at_a_revision_and_none_before_it_existed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git(root, "init", "--quiet")
            git(root, "config", "user.name", "Pin Test")
            git(root, "config", "user.email", "pin-test@example.com")
            (root / "README.md").write_text("x\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "--quiet", "-m", "before")
            before = git(root, "rev-parse", "HEAD")
            target = root / "deploy/mattermost-odoo/release.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(pin("a" * 40)), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "--quiet", "-m", "pin")

            self.assertIsNone(load_pin_at(root, before))
            self.assertEqual(load_pin_at(root, "HEAD")["commit"], "a" * 40)
            with self.assertRaisesRegex(MattermostOdooReleaseError, "not available"):
                load_pin_at(root, "d" * 40)


class WorkflowTest(unittest.TestCase):
    def test_the_release_verifies_the_pin_before_any_job_uses_it(self) -> None:
        workflow = (ROOT / ".github/workflows/product-image.yml").read_text(encoding="utf-8")
        resolve = workflow[workflow.index("  resolve:\n"):workflow.index("  external:\n")]
        verify = resolve.index("run: scripts/mattermost-odoo verify")
        self.assertLess(verify, resolve.index("mattermost_odoo_commit=$("))

    def test_qualification_verifies_and_reviews_before_the_sync(self) -> None:
        workflow = (ROOT / ".github/workflows/qualification.yml").read_text(encoding="utf-8")
        job = workflow[workflow.index("  compatibility:\n"):workflow.index("  database:\n")]
        self.assertIn("scripts/mattermost-odoo verify", job)
        self.assertIn('scripts/mattermost-odoo review --base "$BASE_SHA"', job)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', job)
        self.assertLess(
            job.index("scripts/mattermost-odoo review"),
            job.index("run: scripts/sync-oca-addons"),
        )


if __name__ == "__main__":
    unittest.main()
