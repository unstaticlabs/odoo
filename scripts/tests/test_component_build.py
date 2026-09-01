from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from operations.component_build import COMPONENTS, Component, component_digest, resolve


class ComponentBuildTests(unittest.TestCase):
    def create_repository(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(("git", "init", "-q"), cwd=root, check=True)
        (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (root / "source.txt").write_text("one\n", encoding="utf-8")
        (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=root, check=True)
        return root

    def test_unrelated_file_does_not_change_identity(self) -> None:
        root = self.create_repository()
        component = Component("test", "ghcr.io/usl/test", "Dockerfile", None, ("Dockerfile", "source.txt"))
        before = component_digest(component, root)
        (root / "ignored.txt").write_text("changed\n", encoding="utf-8")
        self.assertEqual(component_digest(component, root), before)

    def test_relevant_file_changes_identity(self) -> None:
        root = self.create_repository()
        component = Component("test", "ghcr.io/usl/test", "Dockerfile", None, ("Dockerfile", "source.txt"))
        before = component_digest(component, root)
        (root / "source.txt").write_text("two\n", encoding="utf-8")
        self.assertNotEqual(component_digest(component, root), before)

    def test_excluded_file_does_not_change_identity(self) -> None:
        root = self.create_repository()
        component = Component(
            "test",
            "ghcr.io/usl/test",
            "Dockerfile",
            None,
            ("Dockerfile", "*.txt"),
            ("ignored.txt",),
        )
        before = component_digest(component, root)
        (root / "ignored.txt").write_text("changed\n", encoding="utf-8")
        self.assertEqual(component_digest(component, root), before)

    def test_every_component_has_a_stable_content_tag(self) -> None:
        payload = resolve()
        self.assertEqual(set(payload["components"]), set(COMPONENTS))
        for value in payload["components"].values():
            self.assertRegex(value["tag"], r"^content-[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
