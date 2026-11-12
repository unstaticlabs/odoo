"""Opt-in Docker proof using disposable images, networks and marker volumes.

USL_RUNTIME_DOCKER_QA=1 python3 -m unittest scripts.tests.test_staging_wake_docker -v
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from operations.runtime import CommandRunner, compose_command
from operations.stack import _start_runtime
from scripts.tests.test_release_manifest import manifest
from scripts.tests.test_generation_runtime_provenance import digested


@unittest.skipUnless(os.environ.get("USL_RUNTIME_DOCKER_QA") == "1", "disposable Docker QA is opt-in")
class StagingWakeDockerTests(unittest.TestCase):
    def test_admitted_a_stale_b_desired_c_and_matching_sleep(self):
        runner = CommandRunner()
        project = "usl-wake-qa-" + uuid.uuid4().hex[:10]
        owner = "com.unstaticlabs.wake-qa"
        images, volumes, networks = [], [], []
        identity = None
        with tempfile.TemporaryDirectory(prefix="usl-wake-qa-") as directory:
            root = Path(directory).resolve()
            try:
                base_image = os.environ.get("USL_RUNTIME_QA_BASE_IMAGE", "python:3.12-slim-bookworm")
                runner.run(["docker", "image", "inspect", base_image])
                for cohort in ("a", "b", "c", "mcp"):
                    image = project + ":" + cohort
                    dockerfile = f"FROM {base_image}\nLABEL {owner}={project}\nLABEL qa.cohort={cohort}\n"
                    runner.run([
                        "docker", "build", "--pull=false", "--network=none", "--tag", image, "-",
                    ], input_text=dockerfile)
                    images.append(image)
                for cohort in ("a", "b", "c"):
                    volume, network = f"{project}-data-{cohort}", f"{project}-net-{cohort}"
                    runner.run(["docker", "volume", "create", "--label", f"{owner}={project}", volume])
                    volumes.append(volume)
                    runner.run(["docker", "network", "create", "--internal", "--label", f"{owner}={project}", network])
                    networks.append(network)
                    runner.run([
                        "docker", "run", "--rm", "--network", "none", "--volume", volume + ":/data",
                        images[0], "python", "-c", f"from pathlib import Path;Path('/data/marker').write_text('synthetic-{cohort}')",
                    ])
                state_root = root / "runtime/staging"
                for cohort in ("a", "b"):
                    generation = state_root / "generations" / ("gadmitted-a" if cohort == "a" else "gfailed-b")
                    generation.mkdir(parents=True)
                    (generation / "usl-release.json").write_text(json.dumps(manifest()))
                    (generation / "compose.generation.json").write_text(json.dumps({
                        "services": {name: {"image": project + ":" + cohort} for name in ("odoo", "db", "mcp")},
                        "volumes": {"postgres-data": {"name": f"{project}-data-{cohort}", "external": True}},
                        "networks": {"default": {"name": f"{project}-net-{cohort}", "external": True}},
                    }))
                base = {
                    "services": {name: {
                        "image": images[2], "init": True, "command": ["python", "-c", "import time;time.sleep(3600)"],
                        "healthcheck": {"test": ["CMD", "python", "-c", "pass"], "interval": "1s", "timeout": "1s", "retries": 5},
                        **({"volumes": ["postgres-data:/data"]} if name == "db" else {}),
                    } for name in ("odoo", "db", "mcp")},
                    "volumes": {"postgres-data": {"name": volumes[2], "external": True}},
                    "networks": {"default": {"name": networks[2], "external": True}},
                }
                env = root / "qa.env"
                env.write_text("")
                bases = []
                commits = [letter * 40 for letter in ("1", "2", "3")]
                for index, cohort in enumerate(("a", "b", "c")):
                    archive = root / "gitops-runs" / (commits[index] + ".qa")
                    working = archive / "stacks/staging"
                    working.mkdir(parents=True)
                    (archive / ".usl-gitops-commit").write_text(commits[index])
                    definition = json.loads(json.dumps(base))
                    for service in definition["services"].values():
                        service["image"] = images[index]
                        service["environment"] = {"QA_BASE_COHORT": cohort}
                    definition["volumes"]["postgres-data"]["name"] = volumes[index]
                    definition["networks"]["default"]["name"] = networks[index]
                    base_path = working / "compose.json"
                    base_path.write_text(json.dumps(definition))
                    bases.append(base_path)
                desired = bases[2]
                desired_sha = hashlib.sha256(desired.read_bytes()).hexdigest()
                identity = {
                    "project": project, "working_directory": str(bases[1].parent), "environment_file": str(env),
                    "profiles": [], "anchor_service": "odoo", "compose_files": [str(bases[1])],
                }
                active = {
                    "generation": "gadmitted-a", "snapshot": "a" * 64, "volumes": {"odoo_postgres": volumes[0]},
                    "network": networks[0],
                    "release_manifest": str(state_root / "generations/gadmitted-a/usl-release.json"),
                }
                active_path = state_root / "active.json"
                active_path.write_text(json.dumps(active))
                active_sha = hashlib.sha256(active_path.read_bytes()).hexdigest()
                attempt = "attempt-disposable-admitted-a"
                release = manifest()
                operation = {
                    "target": "staging", "attempt": attempt, "source": "production",
                    "candidate_release": release["identity"], "snapshot": active["snapshot"],
                    "generation": "gadmitted-a", "gitops_commit": commits[0],
                    "upgrade_plan_sha256": "2" * 64, "prepare_receipt_sha256": "3" * 64,
                    "maintenance_receipt_sha256": "4" * 64,
                }
                bundle = hashlib.sha256(json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                claim = digested({
                    "schema": "usl-release-attempt/v2", **operation, "baseline_generation": None,
                    "operation_bundle_sha256": bundle, "claimed_at": "2026-11-12T00:00:00Z", "status": "claimed",
                })
                claim_path = state_root / "attempts" / attempt / "claim.json"
                claim_path.parent.mkdir(parents=True)
                claim_path.write_text(json.dumps(claim))
                receipt = digested({
                    "schema": "usl-release-admission/v1", "target": "staging", "attempt": attempt,
                    "release": release["identity"], "snapshot": active["snapshot"], "generation": "gadmitted-a",
                    "operation_bundle_sha256": bundle, "health_sha256": "5" * 64, "smoke_sha256": "6" * 64,
                    "control_validation_sha256": "7" * 64, "admitted_at": "2026-11-12T00:01:00Z", "status": "admitted",
                })
                (state_root / "generations/gadmitted-a/admission.json").write_text(json.dumps(receipt))
                stale = {**identity, "compose_files": [str(bases[1]), str(state_root / "generations/gfailed-b/compose.generation.json")]}
                runner.run(compose_command(stale, ["up", "--detach", "--wait", "--wait-timeout", "30", "--no-deps", "db", "odoo", "mcp"]))
                stale_ids = self.container_ids(runner, project)
                runner.run(compose_command(stale, ["stop", "--timeout", "2", "db", "odoo", "mcp"]))
                target = SimpleNamespace(name="staging", project=project, value={
                    "environment": "local", "state_directory": str(state_root),
                    "compose": {"resource_overlay": None, "anchor_service": "odoo", "profiles": [], "canonical": {
                        "working_directory": "stacks/staging", "compose_files": ["compose.json"], "environment_file": str(env),
                    }},
                    "services": {"odoo": "odoo", "odoo_db": "db", "mcp": "mcp"},
                })
                current = {"compose": stale, "active_state": active, "generation": "gadmitted-a"}
                authority = {"image": images[3], "sha256": "d" * 64, "commit": "d" * 40}
                # Local image tags stand in for registry digests in this offline
                # fixture. Separate authority tests exercise immutable ledgers.
                with (
                    mock.patch("operations.stack._runtime_start_authority", return_value=authority, create=True),
                    mock.patch("operations.stack._independent_mcp_image", return_value=images[3]),
                ):
                    restored = _start_runtime(target, runner, current, mcp_authority=False, wait_timeout=30)
                    self.assert_runtime(runner, project, images, volumes[0], networks[0])
                    restored_ids = self.container_ids(runner, project)
                    self.assertNotEqual(stale_ids, restored_ids)
                    runner.run(compose_command(restored, ["stop", "--timeout", "2", "db", "odoo", "mcp"]))
                    _start_runtime(target, runner, current, mcp_authority=False, wait_timeout=30)
                    self.assertEqual(self.container_ids(runner, project), restored_ids)
                    runner.run(["docker", "network", "disconnect", networks[0], restored_ids["odoo"]])
                    _start_runtime(target, runner, current, mcp_authority=False, wait_timeout=30)
                    self.assert_runtime(runner, project, images, volumes[0], networks[0])
                self.assertEqual(hashlib.sha256(active_path.read_bytes()).hexdigest(), active_sha)
                self.assertEqual(hashlib.sha256(desired.read_bytes()).hexdigest(), desired_sha)
                for cohort, volume in zip(("a", "b", "c"), volumes):
                    marker = runner.run([
                        "docker", "run", "--rm", "--network", "none", "--volume", volume + ":/data:ro",
                        images[0], "python", "-c", "from pathlib import Path;print(Path('/data/marker').read_text())",
                    ]).stdout.strip()
                    self.assertEqual(marker, "synthetic-" + cohort)
                print("Docker QA: admitted A base/settings/images restored via receipt and claim; stale B replaced; desired C unchanged; independent MCP retained; matching IDs retained; network drift repaired; A/B/C marker volumes unchanged")
            finally:
                if identity is not None:
                    runner.run(compose_command(identity, ["down", "--remove-orphans", "--timeout", "2"]), check=False)
                for kind, names in (("network", networks), ("volume", volumes), ("image", images)):
                    for name in reversed(names):
                        labels = runner.run(["docker", kind, "inspect", name, "--format", "{{json .Config.Labels}}" if kind == "image" else "{{json .Labels}}"], check=False)
                        if labels.returncode == 0 and (json.loads(labels.stdout) or {}).get(owner) == project:
                            runner.run(["docker", kind, "rm", name])
                remaining = runner.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"])
                self.assertEqual(remaining.stdout.strip(), "", "task-owned QA containers were not stopped and removed")

    def container_ids(self, runner, project):
        output = runner.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Label \"com.docker.compose.service\"}} {{.ID}}"])
        return dict(line.split() for line in output.stdout.splitlines())

    def assert_runtime(self, runner, project, images, volume, network):
        ids = self.container_ids(runner, project)
        for name, identifier in ids.items():
            value = json.loads(runner.run(["docker", "inspect", identifier]).stdout)[0]
            expected = images[3] if name == "mcp" else images[0]
            self.assertEqual(value["Config"]["Image"], expected)
            self.assertIn("QA_BASE_COHORT=a", value["Config"]["Env"])
            if name == "mcp":
                self.assertIn("MCP_BUILD_ID=" + "d" * 40, value["Config"]["Env"])
                self.assertEqual(value["Config"]["Labels"]["org.opencontainers.image.revision"], "d" * 40)
            self.assertEqual(set(value["NetworkSettings"]["Networks"]), {network})
            if name == "db":
                self.assertEqual(next(item["Name"] for item in value["Mounts"] if item["Destination"] == "/data"), volume)


if __name__ == "__main__":
    unittest.main()
