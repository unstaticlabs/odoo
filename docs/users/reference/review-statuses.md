# Review Status and Decision Reference

Audience: accountant, CEO, finance operator.

## Discrepancy Severities

| Severity | Meaning |
| --- | --- |
| P0 | Blocks milestone closure or production migration until resolved or formally accepted. |
| P1 | Important accounting or product risk that needs a decision. |
| P2 | Lower-priority issue or follow-up. |

## Common Discrepancy Classifications

| Classification | Meaning |
| --- | --- |
| legal_or_accounting_uncertainty | Accountant, legal or stakeholder judgment is required. |
| period_or_scope_difference | The difference is caused by company, date, posted/draft or migration scope. |
| presentation_difference | Accounting meaning matches but presentation differs. |
| external_value_difference | A value depends on a manual or externally supplied report value. |
| missing_capability | The target cannot yet express the behavior. |
| transfer_defect | Imported target failed to preserve source accounting meaning. |
| report_definition_defect | Ledger is correct but report classification or calculation is wrong. |

## Review Decision Gates

| Gate | Used For |
| --- | --- |
| Report Parity | Accepting or rejecting report evidence. |
| FEC Validation | Accepting or rejecting FEC evidence. |
| Tax External Value | Reviewing manual or external tax values. |
| Discrepancy Acceptance | Accepting or rejecting a discrepancy treatment. |
| Scope Exclusion | Accepting deliberate non-parity or out-of-scope items. |
| Milestone Closure | Final milestone closure decision. |

## Review Decision Conclusions

| Conclusion | Meaning |
| --- | --- |
| Pending | Not reviewed yet. |
| Accepted | Reviewed and accepted. |
| Accepted With Difference | Difference is known, documented and accepted. |
| Requires Change | More implementation or correction is required. |
| Rejected | Proposed treatment is not accepted. |
| Not Applicable | Item does not apply to the reviewed company or period. |

## Source Report Parity Levels

| Level | Meaning |
| --- | --- |
| Level 0 - Unmapped | No target treatment is assigned. |
| Level 1 - Available | A target report exists. |
| Level 2 - Ledger Controls | Ledger-level controls exist. |
| Level 3 - Semantic Partial | Some semantic report mapping exists. |
| Level 4 - Evidence Partial | Technical availability, export and sampled drill-down evidence exist. |
| Level 4 - Accepted | Authorized reviewer recorded acceptance. |

Current technical evidence can reach Level 4 evidence partial automatically. Level 4 accepted requires a recorded review decision.

