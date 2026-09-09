"""No skill may send an agent's worktree into a temporary directory.

On 2026-09-09 a tmp cleaner emptied every `/private/tmp/odoo-*` worktree that had
not been written to for a few days. The directories were left in place, so `git
worktree list` still showed them and nothing looked wrong; the files were gone.
Six branches carried no commits at all, nothing had been pushed, and five clusters
of work reported between 45% and 90% complete ceased to exist. What survived was a
module whose `__init__.py` imported a file that was no longer there.

Committing and pushing is the real protection and the delivery skill already
requires it. This test covers the other half: an agent that follows a skill
literally must not be told to put a day's work somewhere the operating system is
entitled to delete.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (".agents/skills", ".claude/skills")

# A worktree path under any conventional temporary directory.
TEMPORARY_WORKTREE = re.compile(
    r"worktree\s+add[^\n]*?(?:/private/tmp/|/tmp/|\$TMPDIR|\bmktemp\b)"
)
# The same paths assigned to a variable a `worktree add` later expands.
TEMPORARY_ASSIGNMENT = re.compile(
    r"^\s*WORKTREE=(?:/private/tmp/|/tmp/|\$TMPDIR)", re.MULTILINE
)


class SkillWorktreeHomeTests(unittest.TestCase):
    def skills(self):
        for root in SKILL_ROOTS:
            yield from sorted((ROOT / root).glob("*/SKILL.md"))

    def test_no_skill_puts_a_worktree_in_a_temporary_directory(self) -> None:
        checked = 0
        for skill in self.skills():
            checked += 1
            body = skill.read_text(encoding="utf-8")
            path = skill.relative_to(ROOT).as_posix()
            for pattern in (TEMPORARY_WORKTREE, TEMPORARY_ASSIGNMENT):
                found = pattern.search(body)
                with self.subTest(skill=path):
                    self.assertIsNone(
                        found,
                        f"{path} puts a worktree in a temporary directory "
                        f"({found.group(0).strip() if found else ''}); use "
                        "~/Code/odoo-worktrees, which no cleaner touches",
                    )
        self.assertGreater(checked, 0, "no skills were checked")


if __name__ == "__main__":
    unittest.main()
