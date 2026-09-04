---
name: threat-modeling-repositories
description: Produce an evidence-grounded repository threat model when explicitly asked for trust boundaries, attacker abuse paths, or AppSec design risks.
license: Apache-2.0
metadata:
  source: https://github.com/unstaticlabs/agent-skills/tree/main/skills/threat-modeling-repositories
---

# Model credible abuse paths

- Define the requested scope and separate runtime, build, CI, tests, and examples.
  Trace entry points, components, data flows, stores, external services, assets,
  identities, and trust boundaries to repository evidence.
- Infer exposure, tenancy, deployment, and data sensitivity only when supported;
  mark consequential unknowns and assumptions.
- Describe each threat as a sequence: attacker goal and capabilities, prerequisite,
  exposed entry point, crossed boundary, affected asset, concrete impact, and
  existing controls with evidence.
- Rank likelihood, impact, confidence, and priority separately. Prefer a small set
  of credible abuse paths over exhaustive category coverage; do not label a design
  risk or missing context as a confirmed vulnerability.
- Tie mitigations to the component or boundary they protect. Prioritize controls
  that break several high-value paths and distinguish existing controls from
  proposed work.

Deliver the scoped system model, assumptions, assets, boundaries, attacker model,
prioritized abuse paths, evidence-linked controls, mitigations, and coverage gaps.
