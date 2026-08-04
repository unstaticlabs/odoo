import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pocket_id_dev.py"
ROOT = SCRIPT_PATH.parents[1]
SPEC = importlib.util.spec_from_file_location("usl_pocket_id_dev", SCRIPT_PATH)
POCKET_ID_DEV = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(POCKET_ID_DEV)


class TestPocketIDDevEnvironment(unittest.TestCase):
    def test_defaults_target_canonical_odoo_dev(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(os.environ, {}, clear=True):
                POCKET_ID_DEV._write_new_env(path)
            values = POCKET_ID_DEV._read_env(path)
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(values["ODOO_INIT_DB"], "odoo_dev")
        self.assertEqual(values["ODOO_DB_FILTER"], "^odoo_dev$")
        self.assertEqual(values["POCKET_ID_PROSPER_ODOO_EMAIL"], "")
        self.assertEqual(values["ODOO_HTTP_PORT"], "8069")
        self.assertEqual(values["ODOO_GEVENT_PORT"], "8072")
        self.assertEqual(mode, 0o600)

    def test_policy_reuses_source_aligned_logins_without_synthetic_odoo_email(self):
        values = {
            "POCKET_ID_PROSPER_EMAIL": "prosper@preproduction.invalid",
            "POCKET_ID_PROSPER_ODOO_EMAIL": "",
            "POCKET_ID_PROSPER_ID": "prosper-subject",
            "POCKET_ID_ROGER_ID": "roger-subject",
            "POCKET_ID_VALENTIN_ID": "valentin-subject",
        }
        with patch("builtins.print") as print_mock:
            POCKET_ID_DEV.odoo_policy(values)
        policy = json.loads(print_mock.call_args.args[0])
        users_by_profile = {entry["profile"]: entry for entry in policy}

        self.assertEqual(
            users_by_profile["collaborator"]["login"],
            "roger@unstaticlabs.com",
        )
        self.assertEqual(
            users_by_profile["accountant_reviewer"]["login"],
            "prosper",
        )
        self.assertNotIn("email", users_by_profile["accountant_reviewer"])
        self.assertFalse(
            users_by_profile["accountant_reviewer"]["create_if_missing"],
        )

    def test_noncanonical_database_cannot_be_selected_as_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with (
                patch.dict(
                    os.environ,
                    {"USL_POCKET_ID_DEV_ODOO_DB": "odoo_dev_sso_qa"},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    POCKET_ID_DEV.PocketIDError,
                    "canonical odoo_dev",
                ),
            ):
                POCKET_ID_DEV._write_new_env(path)

    def test_canonical_reconstruction_applies_target_policy_last(self):
        script = (ROOT / "scripts" / "target-reconstruct").read_text(
            encoding="utf-8",
        )
        ordered_steps = [
            "scripts/accounting-compat dev-reset",
            "scripts/accounting-compat dev-import",
            "scripts/accounting-compat dev-validate",
            "scripts/project-restore all",
            "scripts/tese-restore all",
            "scripts/target-finalize",
        ]
        positions = [script.index(step) for step in ordered_steps]

        self.assertEqual(positions, sorted(positions))
        self.assertGreaterEqual(script.count("stop_product"), 5)
        self.assertIn("USL_EINVOICE_LIVE_ENABLED=0", script)
        self.assertIn("USL_EREPORTING_LIVE_ENABLED=0", script)

    def test_local_pocket_helper_has_no_database_clone_lifecycle(self):
        script = (ROOT / "scripts" / "pocket-id-dev").read_text(
            encoding="utf-8",
        )

        self.assertNotIn("cleanup-qa-clone", script)
        self.assertNotIn("createdb", script)
        self.assertNotIn("dropdb", script)
        self.assertIn("canonical odoo_dev", script)

    def test_login_link_resolves_any_exact_pocket_username(self):
        api = Mock()
        api.request.return_value = {
            "data": [
                {"id": "other-subject", "username": "other.user"},
                {"id": "requested-subject", "username": "finance.operator"},
            ],
        }

        user = POCKET_ID_DEV._find_user(api, {}, "finance.operator")

        self.assertEqual(user["id"], "requested-subject")
        api.request.assert_called_once_with(
            "GET",
            "/api/users?pagination%5Blimit%5D=100",
        )

    def test_login_link_rejects_missing_or_ambiguous_username(self):
        api = Mock()
        api.request.return_value = {
            "data": [
                {"id": "first", "username": "duplicate"},
                {"id": "second", "username": "duplicate"},
            ],
        }
        with self.assertRaisesRegex(
            POCKET_ID_DEV.PocketIDError,
            "ambiguous",
        ):
            POCKET_ID_DEV._find_user(api, {}, "duplicate")

        api.request.return_value = {"data": []}
        with self.assertRaisesRegex(
            POCKET_ID_DEV.PocketIDError,
            "not provisioned",
        ):
            POCKET_ID_DEV._find_user(api, {}, "missing")

    def test_make_login_link_requires_an_explicit_username(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("login-link:", makefile)
        self.assertIn('if [ "$(origin USER)" != "command line" ]', makefile)
        self.assertIn(
            'scripts/pocket-id-dev one-time-link "$(USER)"',
            makefile,
        )


if __name__ == "__main__":
    unittest.main()
