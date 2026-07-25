# Accounting migration and release gates

## Blocking principle

Accounting correctness is a release and migration gate. Visual similarity or plausible balances are insufficient.

## Required gates

### Source inventory

The source version, accounting modules, companies, fiscal settings, customizations, report definitions, external values, attachments and data perimeter are evidenced.

### Transfer integrity

The source package is repeatable, complete for its declared perimeter and traceable to target records. Exclusions and transformations are explicit.

### Ledger integrity

Entries are balanced; companies, accounts, dates, currencies, taxes, residuals and reconciliation relationships match the accepted source.

### Closed-year integrity

Closed periods, opening balances, retained earnings, locks, statutory reports and FEC remain consistent.

### Report parity

Mandatory reports pass line-level, filter, drill-down and export comparisons with no unexplained material difference.

### French compliance readiness

FEC passes the current official structural validator and reconciles to the ledger and accepted statements. Chronology, source-document traceability and applicable French report versions are professionally reviewed.

### Evidence and privacy

Required supporting documents remain accessible to authorized reviewers without exposing unrelated private information.

### Recovery

A representative imported environment can be backed up and restored with matching accounting control totals and attachments.

### Accountant acceptance

The accountant can independently inspect records, reports, evidence and FEC. Material objections are resolved or explicitly accepted.

## Automatic blockers

The following block release or migration unless formally resolved:

- silent alteration or loss of posted history;
- unexplained closed-year balance changes;
- missing accounting evidence;
- invalid or unreconciled FEC;
- loss of tax tags or external report values;
- incorrect company assignment;
- duplicate business consequences;
- loss of reconciliation relationships;
- weakened locks or permissions;
- unresolved legal or accounting uncertainty with material impact.

## Authority

The implementation team may recommend readiness. The accountant accepts the accounting workflow. Valentin makes the final production migration decision.
