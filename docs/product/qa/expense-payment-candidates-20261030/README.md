# Employee payment candidate QA

Captured on **30 October 2026** against qualified expense product source
`1743fd7e6fa46f77af997c1bd0a1e972092e0eca`. The French browser session used an
ordinary, non-admin Expense Manager and Accounting Manager in a disposable clone
of the clean **165-module** delivered registry. Every company, person, amount,
bank reference and receipt is invented and visibly marked for QA.

These captures illustrate the final replacement for the rejected 17 October
posting dialog. Still images do not prove functional behavior.

| Capture | Viewport | Context |
| --- | --- | --- |
| `01-candidates-desktop.png` | 1440 × 900 | Native Submit opens the same candidate list used by expense detail. The exact candidate has the existing **Use** action; the near amount cannot be used. **Continue with employee payment** resumes Submit. |
| `02-batch-candidates-mobile.png` | 390 × 844 | A refreshed native Submit API review for two same-amount expenses. Expense identity, amount and **Use** remain visible together. Native horizontal scrolling exposes the remaining columns. |
| `03-native-posting-controls-desktop.png` | 1440 × 900 | Continuing an employee-paid Post returns directly to Odoo's journal and accounting-date controls. |
| `04-company-payment-matched-desktop.png` | 1440 × 900 | The existing **Use** confirmation has completed native company posting and bank reconciliation; the expense shows Paid and accepted matching history. |

The operator exercised Cancel, employee-paid Submit and Approve, Post through its
native journal/date dialog to a posted receipt, existing Use through its native
confirmation to a Paid expense, and no-match Submit. Browser assertions read the
resulting native states. Successful journeys recorded no browser exceptions,
console warnings/errors or failed HTTP responses. The native duplicate warning
remains intact; the fixture uses distinct receipt evidence for independent
expenses.

All **53 expense-matching tests** pass. With PR #304's unchanged native receipt
tests mounted read-only for qualification, the clean canonical registry passes
**63 affected tests**. Both same-amount mixed-payer regression cases preserve the
shown evidence across native company posting. Restoring the rejected Submit
implementation reproduces `None is not an instance of dict`; restoring the
unseen fingerprint refresh reproduces `account.move != hr.expense.post.wizard`.

Actual delegated JSON-2 requests exercised both reviewed and no-match native
employee posting. Independent checks verified exact original/copied receipt
bytes and SHA-256 values, separate attachment identities, native target links,
preserved originals, Agent attribution and the unchanged unmatched bank debit.
Repeated module updates preserve every pre-existing release-gate evidence row.

The expense-only action surface has **57,517 actions**. Runtime qualification
passes with **165 modules and 48,801 runtime actions**, using policy
`1ab68d729e8a15a4c05c6eb9ffe1b843c9edcc1fac8781cc5740572570f41970`.
Combined delivery with the bank-import work derives its own runtime policy.

The implementation owner personally inspected all four images for private data,
credentials, real contacts, financial details, document contents, browser address
bars and local paths. Only synthetic QA content appears. Each PNG contains only
IHDR, IDAT and IEND chunks; it has no EXIF, XMP, IPTC, text or comment metadata.
Capture sidecars remain outside the repository.
