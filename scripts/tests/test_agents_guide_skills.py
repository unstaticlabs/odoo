"""Every skill the repository ships has to be reachable from `AGENTS.md`.

Skills are the only place several procedures are written down, and most agents
find them exactly one way: a path named in `AGENTS.md`. Claude Code also loads
`.claude/skills/` on its own, which hid the problem — five delivery skills sat in
the repository for a week while every Codex session worked without them, because
nothing pointed anywhere. The guide's one skill reference at the time named
`writing-clearly-and-concisely` with no path at all.

A skill nobody can find is a skill nobody follows. These tests keep the index in
`AGENTS.md` honest in both directions: it may not name a file that is gone, and a
new skill may not land without being named.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "AGENTS.md"
SKILL_ROOTS = (".agents/skills", ".claude/skills")


def skills_section() -> str:
    body = GUIDE.read_text(encoding="utf-8")
    match = re.search(r"^## Skills$(.*?)^## ", body, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError("AGENTS.md has no '## Skills' section")
    return match.group(1)


class AgentsGuideSkillIndexTests(unittest.TestCase):
    def test_every_path_the_skills_section_names_exists(self) -> None:
        # Only backticked tokens that look like repository files; the section also
        # quotes things like `sudo` and `/usl-merge-train`, which are not paths.
        referenced = re.findall(r"`([\w.\-/]+\.(?:md|py|json|xml))`", skills_section())
        self.assertTrue(referenced, "the Skills section names no files")
        for path in referenced:
            with self.subTest(path=path):
                self.assertTrue(
                    (ROOT / path).exists(),
                    f"AGENTS.md points at {path}, which does not exist",
                )

    def test_every_skill_in_the_repository_is_named(self) -> None:
        body = GUIDE.read_text(encoding="utf-8")
        for root in SKILL_ROOTS:
            for skill in sorted((ROOT / root).glob("*/SKILL.md")):
                path = skill.relative_to(ROOT).as_posix()
                with self.subTest(skill=path):
                    self.assertIn(
                        path,
                        body,
                        f"{path} exists but AGENTS.md never names it, so an agent "
                        "that does not scan this directory will never read it",
                    )


if __name__ == "__main__":
    unittest.main()
