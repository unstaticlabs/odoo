---
name: designing-property-based-tests
description: Design or diagnose property-based tests for parsers, normalization, roundtrips, validators, state invariants, and algebraic behavior; not ordinary example tests.
license: CC-BY-SA-4.0
metadata:
  source: https://github.com/unstaticlabs/agent-skills/tree/main/skills/designing-property-based-tests
---

# Test rules, not examples

- Use generated inputs only when the behavior has a meaningful property. Prefer,
  in order, error guarantees; shape or conservation invariants; idempotence or
  ordering; roundtrips, inverses, or an independent oracle.
- Ground the property in a contract, specification, type, or explicit decision.
  Never restate the implementation as its own oracle.
- Generate valid inputs directly. Model dependent fields, boundaries, empty and
  singleton values, duplicates, and documented malformed inputs while preserving
  useful shrinking.
- Keep known regressions as explicit examples. Reject assertions no broken
  implementation could falsify, shared-bug oracles, and vacuous preconditions.
- Use the repository's existing property-test library. Do not add a dependency or
  refactor production code merely to expose a property without authorization.
- Classify a minimized failure as a code bug, invalid property, generator bug,
  ambiguous contract, or test artifact; preserve the counterexample and evidence.

State the property, its contract basis, generated domain, important examples,
and why the assertion is non-vacuous.
