---
name: finding-bug-variants
description: Search a codebase for variants of a confirmed vulnerability or logic defect after its root cause is known; not for initial discovery or general review.
license: CC-BY-SA-4.0
metadata:
  source: https://github.com/unstaticlabs/agent-skills/tree/main/skills/finding-bug-variants
---

# Generalize a confirmed defect

- Express the root cause as an unsafe data path or violated invariant, including
  the state that makes it reachable. Start with a search that finds the known bug.
- Expand one grounded axis at a time: copied code, related identifiers, equivalent
  APIs, alternate sources or sinks, callers, data types, boolean forms, or partial
  fixes. Inspect new matches before widening again.
- Search the full relevant codebase. Treat generated, vendored, test, and
  unreachable code as explicit scope decisions, not silent exclusions.
- Use text search for reconnaissance, structural matching for syntax families,
  and data-flow analysis only when reachability through calls matters. Stop when
  added noise exceeds credible coverage.
- Triage each candidate using its callers, types, guards, validation,
  authorization, error paths, and realistic reachability. Separate severity from
  confidence and record why false positives are safe.
- Preserve the final useful search and its limits. Add a regression test or CI
  rule only when the pattern is precise and implementation is in scope.

Report confirmed variants, uncertain risks, false-positive controls, and remaining
coverage limits.
