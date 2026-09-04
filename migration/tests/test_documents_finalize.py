from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DocumentsFinalizeTests(unittest.TestCase):
    def test_one_shot_finalizer_mounts_recorded_source_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "frozen source"
            (source / "filestore").mkdir(parents=True)
            calls = directory / "docker-calls"
            binary = directory / "docker"
            binary.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> {str(calls)!r}\n"
                "case \"$*\" in\n"
                "  *' ps -q --status running '*) printf 'owned-container\\n' ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{directory}:{os.environ['PATH']}",
                "USL_MIGRATION_RUNTIME_STATE": str(directory / "runtime.json"),
                "COMPOSE_PROJECT_NAME": "test-runtime",
                "ODOO_DEV_DB": "odoo_dev",
                "USL_ONLINE_DUMP_DIR": str(source),
                "USL_EINVOICE_LIVE_ENABLED": "0",
                "USL_EREPORTING_LIVE_ENABLED": "0",
            }

            result = subprocess.run(
                ("bash", str(ROOT / "migration/internal/documents-finalize")),
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            invocations = calls.read_text(encoding="utf-8")
            self.assertIn(
                f"-v {source}:/mnt/accounting-source:ro",
                invocations,
            )


if __name__ == "__main__":
    unittest.main()
