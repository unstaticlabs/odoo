"""Exercise the image entrypoint with a disposable file and synthetic credentials."""
import os
from pathlib import Path
import subprocess
import unittest


@unittest.skipUnless(os.environ.get("USL_ODOO_CONFIG_QA_IMAGE"), "supply an Odoo image to exercise its entrypoint")
class RuntimeConfigFileTests(unittest.TestCase):
    def test_new_and_existing_config_remain_private(self):
        entrypoint = Path(__file__).resolve().parents[2] / "docker/entrypoint.sh"
        shell = """
            umask 022
            sh /fixture-entrypoint true
            test "$(stat -c %a "$ODOO_CONFIG")" = 600
            grep -qF "fixture-first-password" "$ODOO_CONFIG"
            chmod 644 "$ODOO_CONFIG"
            echo old-fixture-data >> "$ODOO_CONFIG"
            ODOO_ADMIN_PASSWORD=short sh /fixture-entrypoint true
            test "$(stat -c %a "$ODOO_CONFIG")" = 600
            grep -qF "admin_passwd = short" "$ODOO_CONFIG"
            if grep -qE 'fixture-first-password|old-fixture-data' "$ODOO_CONFIG"; then exit 1; fi
        """
        subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "--mount", f"type=bind,src={entrypoint},dst=/fixture-entrypoint,readonly",
            "-e", "ODOO_CONFIG=/tmp/fixture-odoo.conf", "-e", "ODOO_ADMIN_PASSWORD=fixture-first-password",
            os.environ["USL_ODOO_CONFIG_QA_IMAGE"], "-ec", shell,
        ], check=True, capture_output=True)
