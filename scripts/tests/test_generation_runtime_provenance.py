"""Historical admission and independent MCP provenance are separate contracts."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from operations.runtime import MCP_RETAINED_LEDGER_SCAN, ReadOnlyRunner, RuntimeError, load_target, read_only_command_problem
from operations.stack import (
    _generation_base_compose_identity, _independent_mcp_override,
    _observed_mcp_authority, _record_generation_compose_identity,
    _runtime_admission_evidence,
)
from scripts.tests.test_observation_read_only import Host, MCP_IMAGE, RUN_ROOT, authority_sha256, deployed_production, ledger
from scripts.tests.test_release_manifest import manifest

ROOT = Path(__file__).resolve().parents[2]


def digested(value):
    return {**value, "sha256": hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


class Files:
    def __init__(self):
        self.files = {}
        self.links = {}
        self.roots = []
        self.commands = []

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        output, code = "", 0
        if command[0] == "cat":
            output, code = self.files.get(command[1], ""), 0 if command[1] in self.files else 1
        elif command[0] == "readlink":
            output = self.links.get(command[-1], command[-1])
        elif command[0] == "test":
            if command[1] == "-f":
                code = 0 if command[2] in self.files else 1
        elif command[0] == "find":
            pattern = command[command.index("-name") + 1].removesuffix("*") if "-name" in command else ""
            output = "\n".join(root for root in self.roots if Path(root).name.startswith(pattern))
        elif command[0] == "python3":
            self.files[command[3]] = input_text
        else:
            raise AssertionError(command)
        if check and code:
            raise RuntimeError("fixture path unavailable")
        return subprocess.CompletedProcess(command, code, output, "")


class GenerationBaseProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.target = load_target("staging", ROOT / "operations/targets-host")
        self.host = Files()
        self.release = manifest()
        self.generation = "gadmitted-a"
        self.state = self.target.value["state_directory"]
        self.generation_root = f"{self.state}/generations/{self.generation}"
        self.active = {"generation": self.generation, "snapshot": "a" * 64}
        self.contract = self.target.value["compose"]["canonical"]
        self.commit = "1" * 40
        self.root_a = f"/var/lib/usl-odoo/gitops-runs/{self.commit}.original"
        self.base = self.add_archive(self.root_a)
        self.captured = {**self.base, "working_directory": "/retained/stale-b", "compose_files": [
            "/retained/stale-b/compose.yaml", f"{self.state}/generations/gfailed-b/compose.generation.json",
        ]}
        attempt = "attempt-qa-admission-a"
        operation = {
            "target": "staging", "attempt": attempt, "source": "production",
            "candidate_release": self.release["identity"], "snapshot": self.active["snapshot"],
            "generation": self.generation, "gitops_commit": self.commit,
            "upgrade_plan_sha256": "2" * 64, "prepare_receipt_sha256": "3" * 64,
            "maintenance_receipt_sha256": "4" * 64,
        }
        bundle = hashlib.sha256(json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.claim = digested({
            "schema": "usl-release-attempt/v2", **operation, "baseline_generation": None,
            "operation_bundle_sha256": bundle, "claimed_at": "2026-11-12T00:00:00Z", "status": "claimed",
        })
        self.receipt = digested({
            "schema": "usl-release-admission/v1", "target": "staging", "attempt": attempt,
            "release": self.release["identity"], "snapshot": self.active["snapshot"],
            "generation": self.generation, "operation_bundle_sha256": bundle,
            "health_sha256": "5" * 64, "smoke_sha256": "6" * 64, "control_validation_sha256": "7" * 64,
            "admitted_at": "2026-11-12T00:01:00Z", "status": "admitted",
        })
        self.claim_path = f"{self.state}/attempts/{attempt}/claim.json"
        self.receipt_path = f"{self.generation_root}/admission.json"
        self.host.files[self.claim_path] = json.dumps(self.claim)
        self.host.files[self.receipt_path] = json.dumps(self.receipt)

    def add_archive(self, root, content="admitted-a"):
        self.host.roots.append(root)
        working = root + "/" + self.contract["working_directory"]
        files = [working + "/" + name for name in self.contract["compose_files"]]
        self.host.files[root + "/.usl-gitops-commit"] = self.commit
        for path in files:
            self.host.files[path] = content
        self.host.files[self.contract["environment_file"]] = ""
        return {
            "project": self.target.project, "working_directory": working,
            "environment_file": self.contract["environment_file"], "compose_files": files,
            "profiles": self.target.value["compose"]["profiles"], "anchor_service": "odoo-staging",
        }

    def resolve(self):
        return _generation_base_compose_identity(self.target, self.host, self.active, self.captured, self.release)

    def test_historical_admission_selects_a_base_instead_of_stale_b(self):
        self.assertEqual(self.resolve(), self.base)
        self.assertTrue(all(command[0] in {"cat", "test", "readlink", "find"} for command in self.host.commands))

    def test_identical_duplicate_runs_are_selected_deterministically(self):
        duplicate = self.add_archive(self.root_a.replace(".original", ".aaaa"))
        self.assertEqual(self.resolve(), duplicate)

    def test_different_definitions_at_one_commit_are_reported_as_ambiguous(self):
        self.add_archive(self.root_a.replace(".original", ".second"), content="different-definition")
        with self.assertRaisesRegex(RuntimeError, "ambiguous.*gadmitted-a"):
            self.resolve()

    def test_missing_retained_context_never_falls_back_to_b(self):
        self.host.roots = []
        with self.assertRaisesRegex(RuntimeError, "admitted GitOps context is missing.*gadmitted-a"):
            self.resolve()

    def test_claim_must_match_admission_bundle_generation_and_snapshot(self):
        for field, value in (("generation", "gother"), ("snapshot", "f" * 64), ("operation_bundle_sha256", "f" * 64)):
            with self.subTest(field=field):
                receipt = {key: item for key, item in self.receipt.items() if key != "sha256"}
                receipt[field] = value
                self.host.files[self.receipt_path] = json.dumps(digested(receipt))
                with self.assertRaisesRegex(RuntimeError, "operation bundle differs"):
                    self.resolve()

    def test_tampered_receipt_and_claim_are_rejected(self):
        self.host.files[self.receipt_path] = json.dumps({**self.receipt, "status": "quarantined"})
        with self.assertRaises(RuntimeError):
            self.resolve()
        self.host.files[self.receipt_path] = json.dumps(self.receipt)
        self.host.files[self.claim_path] = json.dumps({**self.claim, "gitops_commit": "f" * 40})
        with self.assertRaises(RuntimeError):
            self.resolve()

    def test_archive_marker_and_direct_canonical_paths_are_required(self):
        self.host.files[self.root_a + "/.usl-gitops-commit"] = "f" * 40
        with self.assertRaisesRegex(RuntimeError, "marker differs"):
            self.resolve()
        self.host.files[self.root_a + "/.usl-gitops-commit"] = self.commit
        self.host.links[self.base["compose_files"][0]] = "/other/compose.yaml"
        with self.assertRaisesRegex(RuntimeError, "not direct"):
            self.resolve()

    def test_future_generation_records_existing_compose_provenance_fields(self):
        _record_generation_compose_identity(self.target, self.host, self.generation, self.release, self.base)
        path = self.generation_root + "/compose.identity.json"
        value = json.loads(self.host.files[path])
        self.assertEqual(value["compose"], self.base)
        del self.host.files[self.receipt_path]
        self.host.roots = []
        self.assertEqual(self.resolve(), self.base)
        value["compose"]["environment_file"] = "/wrong.env"
        body = {key: item for key, item in value.items() if key != "sha256"}
        self.host.files[path] = json.dumps(digested(body))
        with self.assertRaisesRegex(RuntimeError, "provenance is invalid"):
            self.resolve()

    def test_older_unreceipted_generation_can_use_its_own_context(self):
        del self.host.files[self.receipt_path]
        with self.assertRaisesRegex(RuntimeError, "provenance is unavailable"):
            self.resolve()
        self.captured = {**self.base, "compose_files": [*self.base["compose_files"], self.generation_root + "/compose.generation.json"]}
        self.assertEqual(self.resolve(), self.base)

    def test_legacy_v2_keeps_its_captured_base(self):
        self.release = {"schema": "usl-release/v2"}
        result = self.resolve()
        self.assertEqual(result["working_directory"], self.captured["working_directory"])
        self.assertEqual(self.host.commands, [])


class AuthorityHost(Host):
    def run(self, command, **kwargs):
        if command[:2] == ["docker", "inspect"] and command[-1] == "{{.Config.Image}}":
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, self.image, "")
        if command[:2] == ["docker", "compose"] and command[-3:] == ["config", "--format", "json"]:
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({"services": {
                self.target.value["services"]["mcp"]: {"image": self.image},
            }}), "")
        if command[0] == "readlink":
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, command[-1], "")
        if command[:3] == ["python3", "-c", MCP_RETAINED_LEDGER_SCAN]:
            self.commands.append(command)
            roots = sorted({path.removesuffix("/.usl-gitops-commit") for path in self.files if path.endswith("/.usl-gitops-commit")})
            candidates = [{
                "root": root, "gitops_commit": self.files[root + "/.usl-gitops-commit"].strip(),
                "ledger": json.loads(self.files[root + "/komodo/releases/usl-odoo-production-mcp.json"]),
            } for root in roots]
            return subprocess.CompletedProcess(command, 0, json.dumps(candidates), "")
        return super().run(command, **kwargs)


class IndependentAuthorityProvenanceTests(unittest.TestCase):
    def split(self, *, sidecar=True, independent=False):
        target, original, identity, _old = deployed_production()
        host = AuthorityHost(target, original.files, copy.deepcopy(identity))
        commit = "7" * 40
        root = f"/var/lib/usl-odoo/gitops-runs/{commit}.newmcp"
        selected, release = ledger("production")
        selected["image"] = "ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "9" * 64
        host.image = selected["image"]
        release["image"]["digest_reference"] = selected["image"]
        host.files[root + "/.usl-gitops-commit"] = commit
        host.files[root + "/komodo/releases/usl-odoo-production-mcp.json"] = json.dumps(selected)
        host.files[root + "/komodo/releases/usl-odoo-production-mcp-manifest.json"] = json.dumps(release)
        authority = {**selected, "gitops_commit": commit, "sha256": authority_sha256(selected, commit)}
        if independent:
            path = "/var/lib/usl-odoo/runtime/mcp/production/rollbacks/aaaaaaaaaaaa-example/candidate.yaml"
            host.files[path] = _independent_mcp_override(authority)
        else:
            path = target.value["state_directory"] + "/authorities/mcp-" + authority["sha256"] + ".json"
            host.files[path] = json.dumps({"services": {"odoo-mcp": {"image": selected["image"]}}})
        host.identity["compose_files"][-1] = path
        if sidecar:
            host.files[path + ".source.json"] = json.dumps(digested({
                "schema": "usl-mcp-runtime-source/v1", "target": "production",
                "gitops_root": root, "gitops_commit": commit, "image": selected["image"],
            }))
        return target, host, identity, path, authority

    def observe(self, target, host, identity):
        value = _observed_mcp_authority(target, ReadOnlyRunner(host), identity)
        self.assertTrue(all(read_only_command_problem(command) is None for command in host.commands))
        return value

    def test_split_odoo_a_mcp_m_uses_recorded_source(self):
        target, host, _odoo, _path, expected = self.split()
        authority, evidence = self.observe(target, host, host.identity)
        self.assertEqual(authority["sha256"], expected["sha256"])
        self.assertEqual(evidence["source"], "recorded-gitops-run")
        self.assertIn(RUN_ROOT, host.identity["working_directory"])

    def test_full_observation_follows_independent_mcp_labels_after_odoo_restore(self):
        target, host, odoo, _path, expected = self.split(independent=True)
        evidence = _runtime_admission_evidence(target, ReadOnlyRunner(host), odoo)
        self.assertEqual(evidence["mcp_runtime"]["image"], expected["image"])
        self.assertEqual(evidence["mcp_runtime"]["observation"]["source"], "recorded-gitops-run")
        self.assertTrue(all(read_only_command_problem(command) is None for command in host.commands))

    def test_new_override_build_metadata_matches_selected_mcp_commit(self):
        target, host, _odoo, path, expected = self.split()
        settings = {
            "image": expected["image"], "environment": {"MCP_BUILD_ID": expected["commit"]},
            "labels": {"org.opencontainers.image.revision": expected["commit"]},
        }
        host.files[path] = json.dumps({"services": {"odoo-mcp": settings}})
        self.assertEqual(self.observe(target, host, host.identity)[0]["image"], expected["image"])
        settings["environment"]["MCP_BUILD_ID"] = "f" * 40
        host.files[path] = json.dumps({"services": {"odoo-mcp": settings}})
        with self.assertRaisesRegex(RuntimeError, "override is unreadable or differs"):
            self.observe(target, host, host.identity)

    def test_old_split_authority_is_recovered_from_its_exact_digest(self):
        target, host, _odoo, _path, expected = self.split(sidecar=False)
        authority, evidence = self.observe(target, host, host.identity)
        self.assertEqual(authority["sha256"], expected["sha256"])
        self.assertEqual(evidence["source"], "retained-gitops-run")

    def test_existing_independent_candidate_and_rollback_templates_remain_valid(self):
        for sidecar in (False, True):
            for name in ("candidate", "rollback"):
                with self.subTest(sidecar=sidecar, name=name):
                    target, host, _odoo, path, expected = self.split(sidecar=sidecar, independent=True)
                    other = path.replace("candidate.yaml", name + ".yaml")
                    host.files[other] = host.files.pop(path)
                    if sidecar:
                        host.files[other + ".source.json"] = host.files.pop(path + ".source.json")
                    host.identity["compose_files"][-1] = other
                    authority, _evidence = self.observe(target, host, host.identity)
                    self.assertEqual(authority["image"], expected["image"])

    def test_invalid_source_never_falls_back_to_another_ledger(self):
        target, host, _odoo, path, _expected = self.split()
        source = json.loads(host.files[path + ".source.json"])
        for field, value in (("target", "staging"), ("gitops_commit", "f" * 40), ("image", MCP_IMAGE)):
            with self.subTest(field=field):
                invalid = {key: item for key, item in source.items() if key != "sha256"}
                invalid[field] = value
                host.files[path + ".source.json"] = json.dumps(digested(invalid))
                with self.assertRaises(RuntimeError):
                    self.observe(target, host, host.identity)

    def test_sidecar_digest_and_override_content_are_both_required(self):
        target, host, _odoo, path, _expected = self.split()
        host.files[path + ".source.json"] = host.files[path + ".source.json"].replace('"target": "production"', '"target": "staging"')
        with self.assertRaises(RuntimeError):
            self.observe(target, host, host.identity)
        target, host, _odoo, path, _expected = self.split(independent=True)
        host.files[path] += "  unrelated-service: {}\n"
        with self.assertRaisesRegex(RuntimeError, "override differs"):
            self.observe(target, host, host.identity)

    def test_local_has_no_independent_authority_or_reads(self):
        target = load_target("local", ROOT / "operations/targets")
        host = Files()
        authority, evidence = _observed_mcp_authority(target, ReadOnlyRunner(host), {})
        self.assertIsNone(authority)
        self.assertEqual(evidence, {"source": "local"})
        self.assertEqual(host.commands, [])

    def test_read_only_ledger_probe_has_no_extra_arguments_or_program_changes(self):
        allowed = ["python3", "-c", MCP_RETAINED_LEDGER_SCAN, "production"]
        self.assertIsNone(read_only_command_problem(allowed))
        for command in (
            allowed + ["/etc"], allowed[:-1] + ["../../etc"],
            ["python3", "-c", MCP_RETAINED_LEDGER_SCAN + "\nopen('/x','w')", "production"],
            ["find", "/var/lib/usl-odoo/gitops-runs", "-delete"],
        ):
            self.assertIsNotNone(read_only_command_problem(command))



if __name__ == "__main__":
    unittest.main()
