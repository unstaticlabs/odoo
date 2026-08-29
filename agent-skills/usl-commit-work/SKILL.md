---
name: usl-commit-work
description: Create or validate commits in the USL Odoo repository. Use whenever staging changes, committing work, repairing commit attribution, or proposing commit boundaries.
---

# USL commit work

Keep commits scoped and reviewable. Inspect `git status`, the relevant diff, and
the staged diff before each commit. Split unrelated changes; never stage user or
foreign changes merely to obtain a clean tree.

Create agent-authored commits only through `scripts/agent/commit`. The helper
reads the worktree-local agent and driving-human identities, creates a real
multiline message file, adds the repository attribution exactly once, rejects
literal `\\n`, and validates the staged diff before calling Git. Do not manually
repeat attribution text in prompts or `git commit -m` arguments.

Example:

```bash
scripts/agent/commit \
  --type fix \
  --scope accounting \
  --summary "preserve invoice currency rates" \
  --body "Restore the posted source rate so analysis remains consistent with EUR ledger entries." \
  --validation "focused Accounting migration tests passed"
```

Use `--dry-run` to inspect the generated message without committing. After a
batch, run `scripts/agent/verify commits --strict --base <base>`.
