# Access and Role Reference

Audience: accountant, CEO, administrator.

## Accounting Read-Only User

Can inspect accounting records and reports.

Typical use:

- accountant review;
- CEO review;
- finance review without posting authority.

## USL Accountant Reviewer

Can inspect imported accounting evidence and create draft review decisions.

Can read:

- imported journal items;
- reports and exports;
- discrepancies;
- source report evidence;
- review-only workflow records;
- external report values;
- accounting attachments.
- Accounting Hygiene queues and their allowed-company drilldowns.

Cannot:

- modify imported posted moves;
- delete evidence;
- directly edit discrepancies;
- access unrelated private technical attachments;
- inspect companies not assigned to the user.
- refresh current closing controls or use New/Upload in supplier-evidence
  drilldowns.
- change the Chart of Accounts natural-balance policy used by Accounting
  Hygiene.

## Accounting Manager

Has stronger authority.

Can perform actions that a reviewer cannot, including:

- gated native reconciliation application where all required acceptance conditions are satisfied;
- ECB reference-rate configuration and immediate retrieval.
- Accounting Hygiene control refresh and normal supplier-document New/Upload
  actions.
- configure a documented account-specific debit, credit or two-sided Hygiene
  balance policy.

Use this role carefully. It can affect accounting presentation.

## Company Access

Company access matters.

A USL-only reviewer should see Unstatic Labs accounting records and should not see USL Media records. A multi-company accountant may need explicit access to both companies.

Do not grant multi-company access just to make a report easier to find.
