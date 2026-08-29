import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "migration_sanitize_export",
    ROOT / "migration/sanitize_export.py",
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
                {
                    "model": "paperless_personal_ai.personalaiprofile",
                    "fields": {"api_key_ciphertext": "encrypted-private"},
                },
            ]
            (export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = sanitizer.sanitize(export)
            stored = json.loads((export / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(result["sanitized_users"], 1)
            self.assertEqual(result["removed_credentials"], 2)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["fields"]["password"], "!")
            self.assertIsNone(stored[0]["fields"]["last_login"])

            stored.append({"model": "socialaccount.socialtoken", "fields": {"token": "secret"}})
            (export / "manifest.json").write_text(json.dumps(stored), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit migration decision"):
                sanitizer.sanitize(export)

            result = sanitizer.sanitize(export, remove_integrations=True)
            stored = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["removed_integrations"], 1)
            self.assertNotIn("socialaccount.socialtoken", {item["model"] for item in stored})

    def test_database_sanitizer_is_clone_guarded_and_removes_identity_material(self):
        script = (ROOT / "scripts/paperless_release_sanitize.py").read_text(
            encoding="utf-8",
        )

        self.assertIn('confirmation != "paperless-release-clone"', script)
        self.assertIn('database_name.startswith("paperless_release_")', script)
        self.assertIn('"paperless_personal_ai.PersonalAIProfile"', script)
        self.assertIn('"socialaccount.SocialAccount"', script)
        self.assertIn('"authtoken.Token"', script)
        self.assertIn("release-disabled-", script)
        self.assertIn("set_unusable_password", script)

    def test_portable_candidate_removes_identity_and_environment_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            export = Path(temporary)
            manifest = [
                {
                    "model": "socialaccount.socialaccount",
                    "fields": {"uid": "pocket-subject"},
                },
                {
                    "model": "documents.workflowactionwebhook",
                    "fields": {"url": "https://old.example/hook"},
                },
                {
                    "model": "paperless.applicationconfiguration",
                    "fields": {
                        "llm_api_key": "secret",
                        "public_url": "https://old.example",
                        "app_title": "USL Documents",
                    },
                },
            ]
            (export / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            result = sanitizer.sanitize(export, portable_candidate=True)
            stored = json.loads(
                (export / "manifest.json").read_text(encoding="utf-8"),
            )

            self.assertEqual(stored, [])
            self.assertEqual(
                result["removed_environment_models"],
                {
                    "documents.workflowactionwebhook": 1,
                    "paperless.applicationconfiguration": 1,
                    "socialaccount.socialaccount": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
