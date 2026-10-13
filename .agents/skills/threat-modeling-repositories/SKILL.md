---
name: threat-modeling-repositories
description: "Produces an evidence-grounded threat model for a repository or selected path. Use when the user explicitly asks for threat modeling, attacker abuse paths, trust boundaries, or AppSec design risks. Do not use for a general architecture summary, ordinary code review, or searching for variants of a known bug."
license: Apache-2.0
metadata:
  usl-owner: unstatic-labs
  usl-version: "0.1.0"
  usl-status: experimental
  usl-risk: low
  usl-source: "https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/security-threat-model"
---

# Threat Modeling Repositories

Model realistic abuse paths from repository evidence instead of applying a generic security checklist.

## Build the system model

- Define the requested paths and exclusions. Separate runtime components from CI, build tooling, tests, and examples.
- Trace entry points, components, data flows, data stores, external services, and trust boundaries to concrete files or configuration.
- Identify assets whose confidentiality, integrity, or availability matters and the controls already visible in the repository.
- Infer deployment, exposure, identity, tenancy, and data sensitivity only when evidence supports them. Mark consequential unknowns.

Ask targeted questions only when missing context would materially change scope or priority and cannot be inferred. If the user cannot answer, proceed with explicit assumptions and conditional rankings.

## Derive and prioritize threats

Describe each threat as an abuse path with:

- attacker goal and realistic capabilities, including important non-capabilities;
- prerequisite and exposed entry point;
- crossed trust boundary and sequence of actions;
- affected asset and concrete impact;
- existing controls with evidence;
- qualitative likelihood, impact, confidence, and priority.

Prefer a small set of distinct, credible paths over exhaustive category coverage. Do not claim a vulnerability where only a design risk or missing context is established.

## Mitigate

Tie recommendations to the relevant component, boundary, or entry point. Distinguish existing controls from proposed changes and prioritize mitigations that break multiple high-value abuse paths. Mark recommendations conditional when the risk depends on an unresolved assumption.

Use [the report format](references/report-format.md) for a formal artifact. Follow an existing documentation convention or user-supplied location; do not create a new report file by default.

## Output contract

Deliver the scoped system model, assumptions, assets, boundaries, attacker model, prioritized abuse paths, evidence-linked controls and mitigations, coverage limits, and questions that remain material.
