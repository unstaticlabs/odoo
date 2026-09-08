"""The observation wrapper must not be able to reach a mutating verb."""

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "usl-stack-observe"


def load_wrapper():
    loader = importlib.machinery.SourceFileLoader("usl_stack_observe", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestUslStackObserve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_wrapper()

    def argv_for(self, target, observation):
        """The argv the wrapper hands to operations.stack.main, without running it."""
        captured = {}

        def fake_main():
            captured["argv"] = list(sys.argv)
            return 0

        original_main = self.module.main
        original_argv = list(sys.argv)
        self.module.main = fake_main
        try:
            code = self.module.run([target, observation])
        finally:
            self.module.main = original_main
            sys.argv = original_argv
        return code, captured.get("argv")

    def test_every_observation_asks_for_json_on_the_named_target(self):
        for observation in sorted(self.module.OBSERVATIONS):
            with self.subTest(observation=observation):
                code, argv = self.argv_for("staging", observation)
                self.assertEqual(code, 0)
                self.assertEqual(argv[:3], ["usl-stack", "--target", "staging"])
                self.assertEqual(argv[-1], "--json")

    def test_mapped_actions_are_status_only(self):
        for observation, verb in self.module.OBSERVATIONS.items():
            with self.subTest(observation=observation):
                if len(verb) > 1:
                    self.assertEqual(verb[1], "status")

    def test_mutating_verbs_are_unreachable(self):
        # Real operations.stack subcommands and actions. None may be reachable
        # through this wrapper, whichever argument carries them.
        for smuggled in (
            "restore",
            "backup",
            "cleanup",
            "run",
            "start",
            "stop",
            "adopt",
            "adopt-gateway",
            "abort",
            "recovery-proof",
        ):
            with self.subTest(smuggled=smuggled):
                self.assertNotIn(smuggled, self.module.OBSERVATIONS)
                self.assertEqual(self.module.run(["staging", smuggled]), 2)

    def test_unknown_target_is_refused(self):
        self.assertEqual(self.module.run(["prod", "health"]), 2)
        self.assertEqual(self.module.run(["../../etc", "health"]), 2)

    def test_argument_count_is_exact(self):
        self.assertEqual(self.module.run([]), 2)
        self.assertEqual(self.module.run(["staging"]), 2)
        self.assertEqual(self.module.run(["staging", "health", "--confirm"]), 2)


if __name__ == "__main__":
    unittest.main()
