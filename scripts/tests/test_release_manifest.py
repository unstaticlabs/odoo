from __future__ import annotations

import copy
import hashlib
import json
import unittest

from operations.release_manifest import ReleaseManifestError, validate


COMMIT = "a" * 40


def component(name: str) -> dict[str, object]:
    input_sha = ({"distribution": "1", "backup-tool": "2", "paperless": "3", "sign-dss": "4"}[name]) * 64
    image = f"ghcr.io/unstaticlabs/{name}"
    digest = "sha256:" + "a" * 64
    value = {
        "input_sha256": input_sha,
        "image": image,
        "tag": f"content-{input_sha}",
        "digest": digest,
        "digest_reference": f"{image}@{digest}",
        "attestations": {
            "sbom": {"predicate_type": "https://spdx.dev/Document", "subject_digest": digest},
            "provenance": {"predicate_type": "https://slsa.dev/provenance/v1", "subject_digest": digest},
        },
    }
    return value


def inventory() -> dict[str, object]:
    modules = {
        "usl_test": {
            "version": "19.3.1.0.0",
            "dependencies": ["base"],
            "source_sha256": "1" * 64,
            "stored_model_sha256": "2" * 64,
        },
    }
    digest = hashlib.sha256(json.dumps(modules, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema": "usl-module-inventory/v1", "modules": modules, "sha256": digest}


def manifest() -> dict[str, object]:
    foundation = {
        "odoo_series": "19.3",
        "odoo_core_commit": "9" * 40,
        "odoo_core_sha256": "8" * 64,
        "oca_sha256": "8" * 64,
        "python_constraints_sha256": "7" * 64,
        "security_policy_sha256": "6" * 64,
    }
    foundation["digest"] = hashlib.sha256(
        json.dumps(foundation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    mcp_contract = {
        "schema": "usl-odoo-mcp-support/v1",
        "odoo_series": "19.3",
        "supported_mcp_major": 1,
        "required_modules": ["usl_access_control"],
        "public_methods": ["usl.agent.current_identity"],
        "actions": ["usl.agent.current_identity"],
        "agent_identity": {
            "method": "usl.agent.current_identity",
            "principal_kind": "agent",
            "schema_version": 3,
            "fields": [
                "access_mode",
                "agent",
                "authority_reduced",
                "companies",
                "credential",
                "effective_applications",
                "owner",
                "principal_kind",
                "schema_version",
            ],
        },
    }
    mcp_contract["sha256"] = hashlib.sha256(
        json.dumps(mcp_contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    value = {
        "schema": "usl-release/v3",
        "source": {"repository": "unstaticlabs/odoo", "ref": "refs/heads/19-usl-staging", "commit": COMMIT},
        "components": {name: component(name) for name in ("distribution", "backup-tool", "paperless", "sign-dss")},
        "modules": inventory(),
        "foundation": foundation,
        "mcp": {
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "ref": "b" * 40,
            "commit": "b" * 40,
            "image": "ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "b" * 64,
            "compatibility_sha256": "c" * 64,
            "release_schema": "usl-odoo-mcp-oci-release/v2",
            "release_manifest_sha256": "5" * 64,
        },
        "mcp_contract": mcp_contract,
        "renderer": {
            "repository": "https://github.com/unstaticlabs/unstatic_latex_templates",
            "commit": "d" * 40,
            "image": "ghcr.io/unstaticlabs/usl-document-renderer@sha256:" + "d" * 64,
        },
        "ollama": {
            "model": "bge-m3:latest",
            "manifest_sha256": "f" * 64,
            "dimension": 1024,
        },
        "qualification": {"evidence": {"unit-tests": "4" * 64}},
        "build": {
            "workflow_run_id": 123,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/123",
        },
    }
    value["identity"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


class ReleaseManifestTests(unittest.TestCase):
    def test_accepts_complete_content_addressed_release(self) -> None:
        self.assertEqual(validate(manifest(), commit=COMMIT)["schema"], "usl-release/v3")

    def test_rejects_component_tag_not_bound_to_inputs(self) -> None:
        value = copy.deepcopy(manifest())
        value["components"]["distribution"]["tag"] = "content-" + "9" * 64
        with self.assertRaisesRegex(ReleaseManifestError, "tag"):
            validate(value)

    def test_rejects_mutable_external_image(self) -> None:
        value = copy.deepcopy(manifest())
        value["mcp"]["image"] = "ghcr.io/unstaticlabs/odoo-mcp:latest"
        with self.assertRaisesRegex(ReleaseManifestError, "MCP identity"):
            validate(value)

    def test_rejects_changed_mcp_support_contract(self) -> None:
        value = copy.deepcopy(manifest())
        value["mcp_contract"]["actions"].append("usl.document.mcp_get")
        with self.assertRaisesRegex(ReleaseManifestError, "digest differs"):
            validate(value)

    def test_rejects_unsorted_mcp_support_surface(self) -> None:
        value = copy.deepcopy(manifest())
        value["mcp_contract"]["actions"] = ["z.method", "a.method"]
        body = {key: item for key, item in value["mcp_contract"].items() if key != "sha256"}
        value["mcp_contract"]["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ReleaseManifestError, "sorted and unique"):
            validate(value)

    def test_rejects_incomplete_agent_identity_support(self) -> None:
        value = copy.deepcopy(manifest())
        value["mcp_contract"]["agent_identity"]["fields"].remove("authority_reduced")
        body = {
            key: item
            for key, item in value["mcp_contract"].items()
            if key != "sha256"
        }
        value["mcp_contract"]["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ReleaseManifestError, "omits required fields"):
            validate(value)

    def test_rejects_wrong_embedding_dimension(self) -> None:
        value = copy.deepcopy(manifest())
        value["ollama"]["dimension"] = 768
        with self.assertRaisesRegex(ReleaseManifestError, "1024"):
            validate(value)

    def test_accepts_legacy_v2_only_for_historical_verification(self) -> None:
        value = manifest()
        for component_value in value["components"].values():
            component_value.pop("attestations")
        value = {
            "schema": "usl-release/v2",
            "source": {"repository": value["source"]["repository"], "commit": value["source"]["commit"]},
            "components": value["components"],
            "mcp": {key: value["mcp"][key] for key in ("repository", "ref", "commit", "image", "compatibility_sha256")},
            "renderer": value["renderer"],
            "ollama": {"image": "ollama/ollama@sha256:" + "e" * 64, **value["ollama"]},
            "build": value["build"],
        }
        self.assertEqual(validate(value)["schema"], "usl-release/v2")

    def test_rejects_release_from_non_release_branch(self) -> None:
        value = manifest()
        value["source"]["ref"] = "refs/heads/feature"
        with self.assertRaisesRegex(ReleaseManifestError, "authorized ref"):
            validate(value)


if __name__ == "__main__":
    unittest.main()
