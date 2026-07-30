import importlib.util
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
        self.assertEqual(values["ODOO_HTTP_PORT"], "8069")
        self.assertEqual(values["ODOO_GEVENT_PORT"], "8072")
        self.assertEqual(mode, 0o600)

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
