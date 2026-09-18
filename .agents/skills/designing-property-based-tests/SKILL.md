---
name: designing-property-based-tests
description: "Designs, reviews, and diagnoses property-based tests around invariants and generated input domains. Use when code has roundtrips, normalization, parsers, validators, algebraic behavior, state invariants, or an existing property-test failure. Do not use for coverage-guided fuzzing, mutation testing, benchmarks, or ordinary example-based tests."
license: CC-BY-SA-4.0
metadata:
  usl-owner: unstatic-labs
  usl-version: "0.1.0"
  usl-status: experimental
  usl-risk: low
  usl-source: "https://github.com/trailofbits/skills/tree/6feac677af72e52ef4d279412276b5a6f21366f0/plugins/property-based-testing/skills/property-based-testing"
---

# Designing Property-Based Tests

Use generated inputs when the behavior has a rule stronger than a list of examples. Choosing example tests is a valid outcome when no meaningful property exists.

## Find the strongest supported property

Prefer properties in roughly increasing strength:

- documented error behavior or no unexpected failure;
- type, range, shape, conservation, or state invariant;
- idempotence, ordering, monotonicity, identity, commutativity, or associativity;
- roundtrip, inverse, or comparison with an independent oracle.

Ground the property in a specification, public contract, types, documentation, existing tests, or an explicit maintainer decision. Do not restate the implementation as its own oracle.

## Design the domain

- Generate valid inputs directly. Encode constraints in generators rather than discarding most cases after generation.
- Include dependent fields, boundary values, empty and singleton structures, duplicate values, malformed inputs for documented error paths, and domain-specific edge cases.
- Preserve shrinking: model inputs so a failure can reduce to an understandable counterexample.
- Keep a few known regressions as explicit examples when they must run every time.

Reject tautological assertions, properties no broken implementation could falsify, reimplementations that share the same bug, and vacuous tests whose preconditions make useful inputs unreachable.

## Fit the repository

Use the project's existing property-testing library and conventions. Adding a new dependency or refactoring production code to expose a property requires the user's authorization; name the property and benefit before proposing either. Keep I/O wrappers on example tests when a pure core is the real property-bearing unit.

## Diagnose failures

Classify the shrunk counterexample against the strongest available contract:

- code bug: an in-domain input violates a supported guarantee;
- invalid property: the assertion contradicts the contract;
- generator bug: the input violates a documented precondition;
- ambiguous specification: the edge behavior requires a maintainer decision;
- test artifact: the behavior disappears under justified realistic constraints.

Do not suppress uncertain findings; label the classification and evidence.

## Output contract

Provide the proposed or reviewed property, its contract basis, generator domain and constraints, important explicit examples, why the assertion is non-vacuous, dependency or refactor decisions, and failure classification when applicable.
