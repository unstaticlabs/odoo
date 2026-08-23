import os
import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_compat.cli import build_parser, configure_source_mount


class SourceMountContractCase(unittest.TestCase):
    def test_cli_source_dir_becomes_compose_mount(self):
        with patch.dict(os.environ, {}, clear=False):
            package = configure_source_mount("/tmp/usl-source-contract")
            expected = Path("/tmp/usl-source-contract").resolve()
            self.assertEqual(package.path, expected)
            self.assertEqual(
                os.environ["USL_ONLINE_DUMP_DIR"],
                str(expected),
            )

    def test_environment_supplies_default_source_dir(self):
        with patch.dict(
            os.environ,
            {"USL_ONLINE_DUMP_DIR": "/tmp/usl-source-from-environment"},
        ):
            args = build_parser().parse_args(["source-validate"])
            self.assertEqual(args.source_dir, "/tmp/usl-source-from-environment")


if __name__ == "__main__":
    unittest.main()
