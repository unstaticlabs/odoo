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

Cannot:

- modify imported posted moves;
- delete evidence;
- directly edit discrepancies;
- access unrelated private technical attachments;
- inspect companies not assigned to the user.

## Accounting Manager

Has stronger authority.

Can perform actions that a reviewer cannot, including gated native reconciliation application where all required acceptance conditions are satisfied.

Use this role carefully. It can affect accounting presentation.

## Company Access

Company access matters.

A USL-only reviewer should see Unstatic Labs accounting records and should not see USL Media records. A multi-company accountant may need explicit access to both companies.

Do not grant multi-company access just to make a report easier to find.

