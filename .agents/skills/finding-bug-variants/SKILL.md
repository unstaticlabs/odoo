---
name: finding-bug-variants
description: "Searches a codebase for variants of a known vulnerability, logic defect, or unsafe pattern. Use when one concrete bug and its root cause are already known and the user asks where else it occurs or wants a generalized search rule. Do not use for initial vulnerability discovery or general code review."
license: CC-BY-SA-4.0
metadata:
  usl-owner: unstatic-labs
  usl-version: "0.1.0"
  usl-status: experimental
  usl-risk: medium
  usl-source: "https://github.com/trailofbits/skills/tree/6feac677af72e52ef4d279412276b5a6f21366f0/plugins/variant-analysis/skills/variant-analysis"
---

# Finding Bug Variants

Turn one confirmed defect into a calibrated search for other manifestations of the same cause.

## Establish the root cause

Confirm the original location and explain why it is wrong, not merely what the code does. Express either:

- how controlled data reaches a dangerous operation without a required protection; or
- which invariant the logic violates and under what state.

Identify independent expansion axes grounded in the repository: copied code, related identifiers, equivalent APIs, alternate sources or sinks, framework idioms, callers, data-type edges, boolean forms, and incomplete fixes.

## Calibrate and generalize

1. Create an exact search that finds the known instance. A miss means the model of the bug is wrong.
2. Generalize one element at a time and inspect every newly introduced match before widening again.
3. Search the full relevant codebase, not only the original module, while keeping generated, vendored, test, or unreachable code visible as explicit scope decisions.
4. Use the lightest adequate tool: textual search for reconnaissance, structural matching for syntax families, and data-flow analysis only when reachability through calls matters. Do not install tools or dependencies without authorization.

Stop widening when a change adds little credible coverage relative to triage cost. Revert the noisy abstraction and try another axis; do not rely on a universal false-positive threshold.

## Triage candidates

Read the surrounding function, callers, types, guards, validation, sanitization, and authorization. Test whether the dangerous state is reachable, controllable, and unprotected. Examine null, empty, boundary, anonymous, and error-path behavior where relevant.

Record confirmed variants, lower-confidence or currently unreachable risks, and false positives with the control that makes them safe. Keep severity separate from confidence.

## Prevent recurrence

Preserve the final useful search and its coverage limits. When the pattern is precise enough, propose a regression test or CI rule; adding enforcement remains part of the user's requested implementation scope.

## Output contract

Provide the original root cause, expansion axes, search versions and scope, confirmed variants with evidence and severity/confidence, grouped false positives, coverage limits, failed searches, and the most useful regression guard.
