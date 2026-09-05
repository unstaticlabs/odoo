from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from operations.component_build import (
    COMPONENTS,
    Component,
    component_digest,
    component_files,
    resolve,
)


class ComponentBuildTests(unittest.TestCase):
    def test_operations_boundary_scripts_are_packaged_and_hashed(self):
        root = Path(__file__).resolve().parents[2]
        files = set(component_files(COMPONENTS["backup-tool"]))
        dockerfile = (root / "docker/backup.Dockerfile").read_text()
        ignores = (root / "docker/backup.Dockerfile.dockerignore").read_text().splitlines()
        for path in ['scripts/odoo/production_quarantine.py', 'scripts/odoo/production_activate.py', 'scripts/odoo/production_side_effect_boundary.py', 'scripts/sign-services-smoke.py']:
            with self.subTest(path=path):
                self.assertIn(path, files)
                self.assertIn("!" + path, ignores)
                self.assertIn("COPY " + path + " /opt/usl/" + path, dockerfile)

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

    def test_every_component_owns_its_dockerignore_contract(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for component in COMPONENTS.values():
            dockerfile = Path(component.dockerfile)
            dockerignore_path = dockerfile.with_name(
                f"{dockerfile.name}.dockerignore",
            )
            self.assertTrue((root / dockerignore_path).is_file())
            self.assertIn(str(dockerignore_path), component_files(component))
            self.assertNotIn(".dockerignore", component_files(component))

    def test_backup_tool_sources_enter_only_its_build_context(self) -> None:
        root = Path(__file__).resolve().parents[2]
        dockerignore = (root / "docker/backup.Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        inclusions = {
            line[1:]
            for line in dockerignore.splitlines()
            if line.startswith("!")
        }
        self.assertIn("compose.resources.production.json", inclusions)
        self.assertIn("compose.resources.staging.json", inclusions)
        self.assertIn("operations/**", inclusions)
        self.assertIn("deploy/production.cron-policy.json", inclusions)
        self.assertIn("scripts/cohort-runtime", inclusions)
        self.assertNotIn("custom-addons/**", inclusions)

    def test_backup_tool_packages_both_resource_overlays(self) -> None:
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "docker/backup.Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY compose.resources.production.json compose.resources.staging.json /opt/usl/",
            dockerfile,
        )
        backup_files = set(component_files(COMPONENTS["backup-tool"]))
        for relative in (
            "compose.resources.production.json",
            "compose.resources.staging.json",
        ):
            self.assertIn(relative, backup_files)
            for name, component in COMPONENTS.items():
                if name != "backup-tool":
                    self.assertNotIn(relative, component_files(component))

    def test_target_runtime_assets_are_packaged_by_backup_tool(self) -> None:
        root = Path(__file__).resolve().parents[2]
        runtime_assets = set()
        for directory in (root / "operations/targets", root / "operations/targets-host"):
            for target_path in directory.glob("*.json"):
                target = json.loads(target_path.read_text(encoding="utf-8"))
                overlay = target["compose"].get("resource_overlay")
                if overlay:
                    runtime_assets.add(overlay)
                cron_policy = target["cron_policy"]["path"]
                if cron_policy and cron_policy.startswith("/opt/usl/"):
                    runtime_assets.add(cron_policy.removeprefix("/opt/usl/"))

        self.assertEqual(
            runtime_assets,
            {
                "compose.resources.production.json",
                "compose.resources.staging.json",
                "deploy/production.cron-policy.json",
            },
        )
        backup_files = set(component_files(COMPONENTS["backup-tool"]))
        dockerignore = (
            root / "docker/backup.Dockerfile.dockerignore"
        ).read_text(encoding="utf-8")
        inclusions = {
            line[1:]
            for line in dockerignore.splitlines()
            if line.startswith("!")
        }
        dockerfile = (root / "docker/backup.Dockerfile").read_text(encoding="utf-8")
        for relative in runtime_assets:
            self.assertTrue((root / relative).is_file())
            self.assertIn(relative, backup_files)
            self.assertIn(relative, inclusions)
            self.assertIn(relative, dockerfile)

    def test_resource_overlay_changes_only_the_backup_tool_identity(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(("git", "init", "-q"), cwd=root, check=True)
        inputs = {
            component.dockerfile for component in COMPONENTS.values()
        } | {
            "compose.resources.production.json",
            "compose.resources.staging.json",
        }
        for relative in inputs:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {relative}\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=root, check=True)
        before = resolve(root)["components"]
        (root / "compose.resources.staging.json").write_text(
            '{"services":{"odoo-staging":{}}}\n', encoding="utf-8",
        )
        after = resolve(root)["components"]
        changed = {
            name
            for name in COMPONENTS
            if before[name]["input_sha256"] != after[name]["input_sha256"]
        }
        self.assertEqual(changed, {"backup-tool"})

    def test_cron_policy_changes_only_the_backup_tool_identity(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(("git", "init", "-q"), cwd=root, check=True)
        inputs = {
            component.dockerfile for component in COMPONENTS.values()
        } | {"deploy/production.cron-policy.json"}
        for relative in inputs:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {relative}\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=root, check=True)
        before = resolve(root)["components"]
        (root / "deploy/production.cron-policy.json").write_text(
            '{"schema":"changed"}\n', encoding="utf-8",
        )
        after = resolve(root)["components"]
        changed = {
            name
            for name in COMPONENTS
            if before[name]["input_sha256"] != after[name]["input_sha256"]
        }
        self.assertEqual(changed, {"backup-tool"})

    def test_backup_tool_installs_the_runtime_health_probe_client(self) -> None:
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "docker/backup.Dockerfile").read_text(encoding="utf-8")
        install = re.search(r"apt-get install -y --no-install-recommends ([^\n]+)", dockerfile)
        self.assertIsNotNone(install)
        self.assertIn("curl", install.group(1).split())


if __name__ == "__main__":
    unittest.main()
