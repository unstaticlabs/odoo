import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "paperless_seed_sanitize",
    ROOT / "scripts/paperless_seed_sanitize.py",
)
sanitizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sanitizer)


class PaperlessSeedSanitizeTest(unittest.TestCase):
    def test_passwords_are_sealed_and_integrations_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            export = Path(temporary)
            manifest = [
                {
                    "model": "auth.user",
                    "fields": {"password": "hash", "last_login": "today"},
                },
                {
                    "model": "authtoken.token",
                    "fields": {"key": "private"},
                },
            ]
            (export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = sanitizer.sanitize(export)
            stored = json.loads((export / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(result["sanitized_users"], 1)
            self.assertEqual(result["removed_credentials"], 1)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["fields"]["password"], "!")
            self.assertIsNone(stored[0]["fields"]["last_login"])

            stored.append({"model": "socialaccount.socialtoken", "fields": {"token": "secret"}})
            (export / "manifest.json").write_text(json.dumps(stored), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit migration decision"):
                sanitizer.sanitize(export)


if __name__ == "__main__":
    unittest.main()
