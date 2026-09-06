from __future__ import annotations

import unittest

from migration import mcp_release as migration_release
from operations import mcp_release as operations_release


class McpReleaseCompatibilityTests(unittest.TestCase):
    """Keep one-shot migration callers bound to the canonical release contract."""

    def test_migration_imports_canonical_release_implementation(self):
        self.assertIs(migration_release.McpReleaseError, operations_release.McpReleaseError)
        self.assertIs(migration_release.load_release, operations_release.load_release)
        self.assertIs(migration_release.resolve_release, operations_release.resolve_release)


if __name__ == "__main__":
    unittest.main()
