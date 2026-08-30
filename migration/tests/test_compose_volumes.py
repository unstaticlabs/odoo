from __future__ import annotations

import unittest

from migration.compose_volumes import ComposeVolumeError, resolve


class ComposeVolumesTests(unittest.TestCase):
    def config(self):
        return {
            "services": {
                "odoo": {
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "odoo-data",
                            "target": "/var/lib/odoo",
                        }
                    ]
                }
            },
            "volumes": {
                "odoo-data": {"name": "explicit-production-odoo-data"},
            },
        }

    def test_resolves_explicit_volume_name(self):
        self.assertEqual(
            resolve(self.config(), "odoo", "/var/lib/odoo"),
            "explicit-production-odoo-data",
        )

    def test_rejects_missing_or_ambiguous_mount(self):
        with self.assertRaisesRegex(ComposeVolumeError, "found 0"):
            resolve(self.config(), "odoo", "/missing")
        config = self.config()
        config["services"]["odoo"]["volumes"].append(
            {"type": "volume", "source": "odoo-data", "target": "/var/lib/odoo"}
        )
        with self.assertRaisesRegex(ComposeVolumeError, "found 2"):
            resolve(config, "odoo", "/var/lib/odoo")

    def test_rejects_unresolved_volume_definition(self):
        config = self.config()
        config["volumes"].clear()
        with self.assertRaisesRegex(ComposeVolumeError, "definition is missing"):
            resolve(config, "odoo", "/var/lib/odoo")


if __name__ == "__main__":
    unittest.main()
