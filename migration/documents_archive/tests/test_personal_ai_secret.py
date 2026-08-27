import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.documents_sso_env import ensure_personal_ai_master_keys


class TestPersonalAIMasterKeys(unittest.TestCase):
    def test_creates_and_revalidates_private_key_ring(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "personal-ai.json"

            ensure_personal_ai_master_keys(path)
            original = path.read_bytes()
            ensure_personal_ai_master_keys(path)

            payload = json.loads(original)
            self.assertEqual(payload["format"], "usl-paperless-personal-ai-keys-v1")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_rejects_a_secret_readable_by_other_users(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "personal-ai.json"
            ensure_personal_ai_master_keys(path)
            path.chmod(0o644)

            with self.assertRaisesRegex(RuntimeError, "must have mode 0600"):
                ensure_personal_ai_master_keys(path)


if __name__ == "__main__":
    unittest.main()
