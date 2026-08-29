from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "migration/documents_archive/ollama_model_archive.py"
MANIFEST_RELATIVE = Path(
    "manifests/registry.ollama.ai/library/usl-bge-m3/documents-20260824-rc1",
)


class OllamaModelArchiveTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, str]:
        models = root / "models"
        manifest = models / MANIFEST_RELATIVE
        manifest.parent.mkdir(parents=True)
        (models / "blobs").mkdir()
        config_payload = b"config"
        layer_payload = b"layer"
        config = hashlib.sha256(config_payload).hexdigest()
        layer = hashlib.sha256(layer_payload).hexdigest()
        (models / "blobs" / f"sha256-{config}").write_bytes(config_payload)
        (models / "blobs" / f"sha256-{layer}").write_bytes(layer_payload)
        manifest.write_text(
            json.dumps(
                {
                    "config": {"digest": f"sha256:{config}"},
                    "layers": [{"digest": f"sha256:{layer}"}],
                },
            ),
            encoding="utf-8",
        )
        return models, hashlib.sha256(manifest.read_bytes()).hexdigest()

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(SCRIPT), *arguments],
            capture_output=True,
            check=False,
            text=True,
        )

    def test_archive_contains_only_alias_and_referenced_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models, digest = self.fixture(root)
            unrelated = models / "blobs" / f"sha256-{'c' * 64}"
            unrelated.write_bytes(b"do not transfer")
            output = root / "portable.tgz"
            created = self.run_script(
                "create",
                "--models-root",
                str(models),
                "--output",
                str(output),
                "--expected-manifest-sha256",
                digest,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            with tarfile.open(output, "r:gz") as archive:
                names = sorted(archive.getnames())
            self.assertEqual(len(names), 3)
            self.assertNotIn(f"models/blobs/{unrelated.name}", names)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            inspected = self.run_script("inspect", "--archive", str(output))
            self.assertEqual(inspected.returncode, 0, inspected.stderr)

    def test_archive_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models, digest = self.fixture(root)
            outputs = [root / "one.tgz", root / "two.tgz"]
            for output in outputs:
                result = self.run_script(
                    "create",
                    "--models-root",
                    str(models),
                    "--output",
                    str(output),
                    "--expected-manifest-sha256",
                    digest,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())

    def test_wrong_manifest_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models, _digest = self.fixture(root)
            result = self.run_script(
                "create",
                "--models-root",
                str(models),
                "--output",
                str(root / "portable.tgz"),
                "--expected-manifest-sha256",
                "0" * 64,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_missing_referenced_blob_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models, digest = self.fixture(root)
            next((models / "blobs").iterdir()).unlink()
            result = self.run_script(
                "create",
                "--models-root",
                str(models),
                "--output",
                str(root / "portable.tgz"),
                "--expected-manifest-sha256",
                digest,
            )
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
