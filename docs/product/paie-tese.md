# Paie TESE

Status: shipped

End-user workflow: [Paie TESE](../users/guides/paie-tese.md)

## Product decision

USL records payroll calculated by an outside provider such as TESE in Odoo
without pretending that Odoo calculated the French payslip. The provider PDF
is the payroll evidence. The Odoo record connects that evidence to the
employee record, the employee version applicable to the period, an immutable
accounting snapshot, the posted payroll entry and the settlement of salary,
social and withholding liabilities.

The application is deliberately an external-payroll accounting workflow:

- Employees and employee versions are the HR source of identity, contract
  dates, wage and working hours.
- A versioned TESE profile supplies provider figures and the eleven approved
  French accounting components for a bounded validity period.
- Preparing a monthly payroll record copies the profile and HR references
  into an immutable snapshot. Later profile or employee changes do not rewrite
  history.
- Posting requires the provider PDF and creates or links one balanced journal
  entry. The PDF and payroll link remain visible from the entry and from the
  authorized Documents archive.
- **Settled** is derived from actual payment residuals. A recognized URSSAF
  carry-over remains on `431000` without reopening the payroll. The status is
  never trusted from a checkbox or a migrated Studio value.
- Automatic settlement is allowed only for one unique and safe bank
  candidate. Salary must be exact. A unique URSSAF candidate may differ by at
  most EUR 5; the real bank amount is used and the exact remainder stays open
  on `431000` as a carry-over. The payroll is settled once both real payments
  are matched; later clearing belongs to normal accounting hygiene, not the
  payroll record. Ambiguous, partial, larger or foreign-currency bank
  transactions remain in the OCA Bank Matching workspace for an accountant.

## Alternatives considered

### Odoo Payroll

Odoo 19 Payroll provides contracts, work entries, salary structures, rules,
payslips and accounting entries. It is the appropriate option when Odoo is the
payroll calculation engine. The Community fork used by USL does not ship that
Enterprise application, and TESE remains the legal calculation and document
provider. Recreating French payroll calculation rules here would duplicate
the provider and create an unsafe second payroll authority.

### OCA Payroll

The maintained OCA Payroll repository currently publishes its payroll and
payroll-accounting modules on 18.0. They provide a broad internal payroll
engine, not a 19.2 external-provider evidence and social-debt workflow.
Forward-porting that engine would be materially larger than the source
functionality and would still require a separate TESE integration.

### Journal entries and attachments only

Entering one monthly journal entry and attaching the PDF is operationally
simple, but loses the period/employee uniqueness rule, the historical
provider profile, HR-version comparison, explicit salary and TESE residuals,
and controlled settlement. It also cannot diagnose missing documents or
configuration before posting.

The focused `usl_tese_payroll` add-on is selected. It reuses native Employees,
employee versions, Accounting, chatter and attachments plus the installed OCA
Bank Matching interface. It does not alter Odoo core and does not calculate a
legal payslip.

## Accounting contract

The supported French component set is fixed:

| Account | Meaning | Side | Settlement role |
| --- | --- | --- | --- |
| `641100` | Gross remuneration | Debit | Cost |
| `645100` | Employer social contributions | Debit | Cost |
| `645200` | Employer mutual-insurance contribution | Debit | Cost |
| `645300` | Employer pension contribution | Debit | Cost |
| `633300` | Employer vocational-training contribution | Debit | Cost |
| `633500` | Employer apprenticeship-tax contribution | Debit | Cost |
| `421000` | Net salary payable | Credit | Salary |
| `431000` | Social-security liability | Credit | TESE |
| `437020` | Mutual-insurance liability | Credit | TESE |
| `437030` | Pension liability | Credit | TESE |
| `442100` | Withholding income-tax liability | Credit | TESE |

Loading French defaults resolves these exact accounts in the active company.
It never creates an account, changes an account code or guesses between
duplicates. Every configured liability account must be reconcilable before a
settlement bridge can be created.

Preparation enforces:

1. one payroll record per employee, company and canonical payroll month;
2. one provider reference per company;
3. one active profile for the employee and period;
4. an HR employee version applicable to the period;
5. exactly the eleven component codes with their fixed role and side;
6. gross less employee contributions equals net before tax;
7. net before tax less withholding equals net paid;
8. gross plus employer contributions equals total debit;
9. debit equals credit in company currency;
10. the `421000` amount equals net paid.

`pay_period` stores the canonical month as its first day and is rendered with
the native month picker. New records propose the oldest missing completed
month, then the month after the latest payroll without going beyond the
current month. The posting date is the payroll period end. Salary is normally
due on the following day. The initial TESE collection suggestion is the
fifteenth of the second month after the payroll month. Explicit user dates
remain unchanged.

## Workflow and immutability

The state sequence is:

`Draft → Prepared → Ready to post → To reconcile → Settled`

Cancellation is available only before posted accounting history exists.
Preparation freezes the component snapshot when the draft entry is created.
The provider PDF can still be attached while the entry is draft; posting then
freezes both figures and evidence. Posted payroll and settlement entries
cannot be reset, cancelled or deleted through this application. Corrections
use an explicit accounting reversal and a new payroll record.

Candidate scoring is advisory. It considers amount, date, partner, employee
or provider label and reference. The automatic action rechecks the current
line, currency, residual, reconcilability, date safety and uniqueness
immediately before settlement. A social payment spanning several liability
accounts uses a balanced settlement bridge in the payroll journal; the bank
suspense line and each declared liability are reconciled in their native
accounts. An URSSAF rounding remainder of at most EUR 5 stays visibly open on
`431000`; it is not auto-posted to an income or expense account. It is shown
as a neutral carry-over while the payroll remains settled. Treating it as an
unpaid payroll would confuse payment completion with ledger hygiene; clearing
it from the payroll would duplicate native accounting reconciliation. Both
alternatives are therefore rejected.

Before posting, **Update settings** can revise provider figures, native
employment terms, or both in one atomic flow. It closes and archives the
previous TESE profile, preserves all eleven components in a dated copy,
creates a native employee version through `employee.create_version` (or a
first contract when none exists), and reapplies both versions to the payroll.
A linked draft entry is regenerated; posted accounting remains immutable.
The operation requires both HR and Accounting Administrator roles.

## Security and privacy

Payroll is visible only to users who have both HR Administrator and Accounting
read access. Workflow actions additionally require Accountant access.
Profiles and company defaults require both HR Administrator and Accounting
Administrator.

This intersection is enforced by global record rules and server-side checks;
menu visibility or a user-supplied context flag cannot bypass it. Employee
private data continues to use the native HR access boundary. Read-only
accountants can inspect payroll only when they also hold the HR role and
cannot trigger a mutation.

## Diagnostics

Diagnostics retain issue history instead of deleting a warning when it
disappears. Each issue has a stable key, severity, category, affected object,
message, suggested correction, last-seen time and resolution time.

**Configuration → Diagnostics** runs the checks and opens the refreshed issue
list in one action. The separate legacy run menu is inactive. Removing the
default Active filter also reveals resolved history. **Configuration →
Settings** opens a dedicated TESE form
for the current company; it does not reuse the generic Company form or expose
unrelated settings such as electronic invoicing.

**Configuration → Comptes de paie** reuses Odoo's native Chart of Accounts
views with the fixed eleven TESE component codes as its action domain. It
does not duplicate account records or introduce a payroll-specific account
model. Accounting Administrators can use the focused list while changes
remain changes to the native ledger accounts.

Blocking checks include missing or non-PDF evidence, invalid profile
components, missing journal or collector, unbalanced snapshots, missing
posted moves and broken move links. Reconciliation observations distinguish
an open liability from an unsafe or ambiguous bank candidate.

## Migration boundary

The delivered module contains no dump parser, source identifier, restoration
run or migration menu. The temporary `usl_tese_restore` module lives under
`migration/tese_restore/`, is mounted only in the `tese-migration` Compose
profile, and is uninstalled after exact parity and idempotency checks. Native
employees, versions, chatter, attachments, profiles, payroll records and
accounting links remain after finalization.

`usl_tese_payroll` depends on the delivered Documents foundation so a payroll
can open or upload its supporting archive evidence. Paperless owns the archive
original and OCR; the native payroll/move attachment remains the operational
posting evidence and Odoo remains authoritative for access and accounting.
Because a posted TESE PDF may technically belong to its accounting entry, a
repeatable reconciler reuses that attachment's existing Paperless root and adds
the payroll record and employee-library relationships. It runs immediately
when the payslip attachment changes and when its archive operation completes,
then twice daily as a recovery safety net. It applies the more specific
**Payroll record**, **HR**, and **Payroll** metadata without uploading a
duplicate. The payroll form reports processing until that relationship is
actually present; it no longer treats a shared accounting attachment as proof
that Documents linking finished.
