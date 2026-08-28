## Product goal

Link the Odoo Project task and summarize the observable outcome and acceptance
criteria. The Odoo task remains the product/work authority; this PR is the
engineering and review authority.

## Engineering handoff

Feature Developers: replace the placeholder below with the exact output of
`scripts/agent/handoff render PATH`. Do not edit only the prose while leaving a
stale JSON contract.

<!-- usl-feature-handoff:start -->
```json
{
  "schema": "usl-feature-handoff/v1",
  "readiness": {
    "status": "NOT READY TO MERGE",
    "rationale": "Replace this placeholder with a validated handoff.",
    "blockers": ["Missing canonical feature handoff."]
  }
}
```
<!-- usl-feature-handoff:end -->

## Lead Developer review

- [ ] Reviewed the actual diff against the latest `origin/19-usl`.
- [ ] Independently qualified applicable migration, accounting, security and UI risks.
- [ ] Confirmed final integration checks on the exact candidate.
- [ ] Confirmed production deployment remains owned by CI.
