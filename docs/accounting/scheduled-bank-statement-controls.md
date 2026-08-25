# Scheduled bank-statement accounting controls

## Sources of truth

- Native `account.bank.statement.line` records are the operational bank
  transactions.
- One managed native `account.bank.statement` is the monthly checkpoint for a
  configured journal and calendar period.
- The machine-readable OFX supplies movements and initial balance values.
- The unchanged official PDF supplies external evidence; a person confirms the
  bank-reported opening and closing balances against it.
- Mail, file and download metadata is integration provenance, not a ledger.

No process creates a balancing movement. The currency-aware controls are:

```text
bank opening balance + posted statement movements = bank closing balance
previous immediately preceding certified closing = current opening
```

The first automated month has no preceding checkpoint. An Accounting Manager
must explicitly accept its opening balance as the cut-over baseline. A gap in
monthly certified periods is a broken continuity result, even when the two
balances happen to be equal.

## Identity and convergence

Stable identity is company/journal, provider, normalized source account and
OFX FITID. A partial PostgreSQL unique index enforces that identity, while a
separate OCA import identity recognizes lines previously imported with the
standard wizard. Migrated `transaction_details.extra.id` is considered only as
an exact historical identity. Date, amount, partner and label are never a
deduplication key.

Message-ID, immutable attachment SHA-256, managed period uniqueness, partial
evidence uniqueness, advisory transaction locks and `FOR UPDATE SKIP LOCKED`
make duplicate delivery, overlapping exports, concurrent workers and retry
converge. Every source file that repeats a movement remains linked to the one
native line.

OFX rows without a usable FITID are exposed as candidates. Parser-only markers
allow the maintained OFX parser to return their accounting fields without
altering the retained file. No line is created until a manager maps the
candidate to an exact same-date/same-amount line or approves a deterministic
file-SHA/account/period/ordinal fallback. The decision is retained and reused
on retry.

## Certification and mutation boundary

Certification snapshots user/time, period, balances, movement total/count,
accepted evidence checksum and version, and a digest of reviewed transaction
identities. Repeated certification is idempotent. Certification does not move
company lock dates and does not require invoice/payment matching.

While certified, statement membership, period, reference, reported balances,
accepted evidence, transaction date/amount/bank identity and the liquidity
journal item are protected. Adding, changing or removing reconciliation
counterparts and categorizing a partner remain normal accounting work. An
Accounting Manager can reopen with a mandatory reason; the prior snapshot and
evidence remain immutable.

## Existing data and OCA boundary

The operational module does not rewrite historical statements or infer matches
from accounting descriptions. The migration-only adoption command compares a
private OFX with exact migrated provider IDs, dates and amounts, then fills the
provider and OCA identities without changing ledger facts or reconciliation.
Preview, apply and repeated preview are required before activation.

The normal OCA bank-import pin remains authoritative for base/file/CAMT/QIF
modules. OFX is exposed from the separately audited OCA 19.0 commit documented
in the architecture register, with only SaaS 19.3 binary and partner-bank field
adaptations. Standard manual imports and bank reconciliation remain available.
