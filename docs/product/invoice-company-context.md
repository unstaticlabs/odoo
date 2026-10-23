# Read a company-change invoice context

`account.move.get_company_change_context(target_company_id, offset=0, limit=50)`
returns native read-only facts for a human or delegated Agent considering an
ordinary company change. It does not change the move or propose account or tax
mappings. Native `write`, `web_save`, onchange and other public methods remain
available under the caller's existing authority.

The requested target must be in the caller's selected, authorized companies.
The result identifies the source, target and active company. Relations are
searched and read with the caller's native ACLs, record rules and field access;
there is no elevation for this context. A missing or inaccessible field appears
in `unavailable_fields`; an absent optional model or inaccessible section reports
`status: unavailable`. Such gaps are information, never a reassignment veto.

## Snapshot and paging

One call builds the entire optional result in one native Odoo transaction.
The getter carries native `@api.readonly` metadata. Odoo uses PostgreSQL
repeatable-read isolation. The `snapshot` identifies the
request, capture time, move ID and header write date. Line write dates and stable
IDs are included. Another page is another snapshot: do not combine pages into a
claim that no concurrent edit occurred. Reread after any ordinary native change.
This snapshot is not an authorization token or a stale-write guard.

Every top-level detail or choice section uses the same `offset` and `limit`,
ordered by ID. The `documents`, `line_analytic_accounts`, `current_accounts` and `current_taxes` sections are bounded
details of the current link/line page, always starting at zero. They expose
counts and truncation, with no standalone `next_offset`; use generic native
reads of the readable IDs in the current parent references or analytics keys
for omitted details. The default limit is 50, maximum 100; offset is 0 through 1000000.
`visible_count` counts only records the caller can search. `hidden_records:
unknown` makes no assertion about inaccessible records. `complete` means complete
within that readable section and page range; inspect `offset` and `next_offset`.
The lines section includes every native display type, including generated lines,
sections and notes. No 100-line sample is called the complete invoice.

Relation references are independently limited to 100 readable records. Their
count and truncation are explicit. The JSON snapshot is limited to 262144 UTF-8
bytes using compact JSON, text values to 1024 characters, and reference labels to
512 characters. If the response budget clips a section, its status becomes
`truncated` and `next_offset` points to the first omitted record. Long text has an
explicit field marker. The header exceeding the budget returns a validation
error. This budget applies to the native snapshot, excluding transport envelopes.

## What to inspect

The header retains native state, review state, previous-posting and sequence
facts, dates, terms, partners, currency amounts/rates, recurrence and credit-note
links. Source and target supplier properties are read separately. Current ledger
lines retain account/tax IDs, analytics, maturities, debit/credit, amount currency
and residuals. Current account and tax details retain native account types and included-tax
treatment even when the target company differs. The target rate is Odoo's company-to-document conversion at the
native invoice rate date; it is informational, not a simulated company change.

Actual partial/full reconciliations, counterpart lines, matched payments and
statement references are distinct. `payment_state` and matched-payment labels do
not prove that ledger lines were reconciled. No reconciliation is changed.

Purchase and expense origins, native attachment metadata and archived-document
links retain their original IDs and ownership. No attachment bytes, OCR body,
provider request, evidence copy or permission synchronization is performed.
Evidence absence is informational. An archived source original is not declared
to be an original owned by the requested target company.

Choices use the related model's native company compatibility domain, preserving
shared accounts and branch behavior. They are readable candidates, not inferred
mappings or permission to write. Accounts and taxes are never matched by code or
name. Extension metadata lists readable `check_company` fields and those outside
the concrete baseline; operators must review those native extensions as well.
Optional native onchange remains a separate scratch preview requiring native
write access. Read-only callers do not need that access to read this context.

The MCP shortcut opts in with `company_change: {target_company_id, offset, limit}`
on `accounting_get_invoice_context`. This mode returns `context.company_change`
and `invoice_ref` from one native call. Without it, the existing context and its
parallel legacy reads remain unchanged and make no single-snapshot claim.
