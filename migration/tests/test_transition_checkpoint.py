from __future__ import annotations

import unittest

from migration.runtime import RuntimeError
from migration.transition_checkpoint import mcp_oauth_volume


class TransitionCheckpointTests(unittest.TestCase):
    def test_requires_owned_mcp_oauth_volume(self) -> None:
        resources = {"volumes": [{"name": "runtime-odoo-mcp-oauth-data"}]}
        services = {
            "odoo-mcp": {
                "mounts": [
                    {
                        "type": "volume",
                        "source": "runtime-odoo-mcp-oauth-data",
                        "destination": "/data",
                    }
                ]
            }
        }

        self.assertEqual(
            mcp_oauth_volume(resources, services),
            "runtime-odoo-mcp-oauth-data",
        )
        resources["volumes"] = [{"name": "foreign-volume"}]
        with self.assertRaisesRegex(RuntimeError, "not an owned runtime volume"):
            mcp_oauth_volume(resources, services)


if __name__ == "__main__":
    unittest.main()
