import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pocket_id_dev.py"
SPEC = importlib.util.spec_from_file_location("usl_pocket_id_dev", SCRIPT_PATH)
POCKET_ID_DEV = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(POCKET_ID_DEV)


class TestPocketIDDevEnvironment(unittest.TestCase):
    def test_defaults_target_disposable_clone_of_canonical_odoo_dev(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(os.environ, {}, clear=True):
                POCKET_ID_DEV._write_new_env(path)
            values = POCKET_ID_DEV._read_env(path)
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(values["ODOO_INIT_DB"], "odoo_dev_pocketid_qa")
        self.assertEqual(values["ODOO_DB_FILTER"], "^odoo_dev_pocketid_qa$")
        self.assertEqual(values["POCKET_ID_SOURCE_DB"], "odoo_dev")
        self.assertEqual(values["POCKET_ID_QA_CLONE"], "1")
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

    def test_canonical_database_cannot_be_selected_as_qa_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with (
                patch.dict(
                    os.environ,
                    {"USL_POCKET_ID_DEV_ODOO_DB": "odoo_dev"},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    POCKET_ID_DEV.PocketIDError,
                    "protected",
                ),
            ):
                POCKET_ID_DEV._write_new_env(path)

    def test_source_database_cannot_be_redirected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with (
                patch.dict(
                    os.environ,
                    {"USL_POCKET_ID_DEV_SOURCE_DB": "another_database"},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    POCKET_ID_DEV.PocketIDError,
                    "canonical odoo_dev",
                ),
            ):
                POCKET_ID_DEV._write_new_env(path)


if __name__ == "__main__":
    unittest.main()
