# How To Review Discrepancies and Record Decisions

Audience: accountant, Valentin, finance operator preparing the review.

Use this guide when a report, tax value, reconciliation boundary or scope decision needs human approval.

## Open Discrepancies

Go to:

```text
Accounting > Review > Advanced Audit > Discrepancies
```

Filter for:

- open;
- investigating;
- P0;
- P1.

Open the discrepancy.

## Read the Discrepancy

Review:

- severity;
- classification;
- company;
- period;
- source value;
- target value;
- difference;
- accounting impact;
- legal or tax impact;
- likely cause;
- recommendation;
- status.

The discrepancy should explain what is affected and what decision is needed.

## Record a Review Decision

From the discrepancy, click `Record Review Decision`.

Fill in:

- conclusion;
- required authority;
- reviewer name;
- decision summary;
- evidence summary;
- remaining risk;
- next action.

Do not use `Accepted` unless the reviewer has actually reviewed and accepted the evidence.

## Decision Conclusions

Use:

- `Accepted` when the evidence is acceptable with no remaining material difference.
- `Accepted With Difference` when the difference is known, documented and acceptable.
- `Requires Change` when the system needs correction.
- `Rejected` when the proposed evidence or treatment is not acceptable.
- `Not Applicable` when the item is outside scope.

## Record the Decision

When the form is complete:

1. Click `Record`.
2. Odoo marks the decision as recorded.
3. The linked discrepancy, source report or external value is updated according to the decision.

Recorded decisions are intentionally immutable. If the conclusion changes later, supersede the old decision and create a new one.

## What Not To Do

- Do not edit imported posted journal entries to make a discrepancy disappear.
- Do not record accountant acceptance if you are only preparing the file.
- Do not use a review decision to hide an unexplained accounting difference.
- Do not accept a tax value without checking its source.
