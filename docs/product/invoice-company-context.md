# Read a company-change invoice context

## Move a draft vendor bill to another company

Open the draft vendor bill or credit note and select **Change company**. The
dialog offers every company you can access, whether or not it is active in the
company selector, and preselects it when there is only one other. When something prevents the move, the dialog says so at once,
names each record with a link and gives the next step. Otherwise choose the
destination and select **Review accounts**.

Odoo then proposes the destination the way a new bill there would get it:

- the first active purchase journal with no currency or the bill's currency;
- the fiscal position Odoo finds for the supplier in the destination company;
- the current payment terms when they are shared, otherwise the supplier's terms there;
- for each line, the shared account, otherwise the product's expense account,
  otherwise the account with the same code in the destination company. When
  none matches, the journal's default account is only a guess: the dialog and
  the preview say so, and an agent must pass `account_id` to confirm it;
- for each taxed line, the product's supplier taxes of the destination company
  mapped by the fiscal position, otherwise the destination purchase tax with the
  same rate, type and price-included setting.

The dialog also shows the destination total, its taxes and the due dates Odoo
will compute. A line whose tax has no equivalent stays empty and needs
destination taxes, or an explicit choice to use no tax. Select **Transfer draft**
to apply the reviewed result in one step. Odoo then opens the moved bill, switching to its
company when it is not the active one.

The bill keeps its ID, supplier, reference, document currency, quantities and
unit prices. Odoo recalculates the tax and installment lines in the destination
company. The original files, archive links and source records keep their
identities. The bill's chatter names the original and destination companies.

### What stops a move

A draft cannot change company while it is linked to a payment, a bank
transaction, another reconciled entry, a purchase order, an expense, a recurring
bill, a cash rounding method of its current company, a supplier or product
restricted to its current company, or, for a credit note, the bill it reverses.
A bill that was posted
before also stays where its sequence number was issued. Each blocker is shown
when the dialog opens, with a link to the record and what to do: for example,
open the payment, unreconcile it from the bill, then reopen **Change company**.
Odoo does not move or rewrite the payment or the reconciliation for you.

### The same move for an agent

`account.move.change_bill_company(target_company_id, mapping=None, confirm=False,
preview_id=None)` is the one method behind the dialog. Both the dialog and
the MCP tool `accounting_change_bill_company` call it, so a person and a delegated
agent get the same proposal and the same refusals under the same rights.

1. Call it without `confirm`. Nothing is written. The reply holds `status`
   (`blocked`, `needs_input` or `ready`), the `blockers`, the `unresolved`
   items, the proposed `journal`, `fiscal_position`, `payment_term` and
   `payable_account`, every line from and to with its account and taxes, the
   destination `amount_total`, tax groups and `due_dates`, and a `preview_id`.
2. Call it again with `confirm=true` and that `preview_id`. Odoo applies the
   proposal in one transaction and returns the bill before and after, with its
   link. Pass `mapping` in both calls only for what the preview left unresolved
   or what you want to change: `journal_id`, `fiscal_position_id` (`false` for
   none), `payment_term_id`, and `lines` with `line_id`, `account_id`, `tax_ids`
   (an empty list for no tax) and `analytic_distribution`.

The preview id covers the bill as the preview saw it, the destination, the caller
and every resolved choice. A later edit, or a confirmation with a different
`mapping`, calls for a new preview. After a lost reply, confirm again with the same
preview id: a bill already in the destination reports `already_done` and moves nothing.

## Inspect a company change without opening the transfer

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

When a draft bill changes supplier, Odoo recalculates its installments using
the new supplier's payable account and the bill's fiscal position. The
distribution carries a one-line correction in
Odoo's installment account lookup. It excludes every line being recalculated
when looking for an existing account to reuse. On an upstream sync, run
`test_change_supplier_recomputes_payable_on_all_installments` to check whether
the upstream implementation covers this case.

The MCP shortcut opts in with `company_change: {target_company_id, offset, limit}`
on `accounting_get_invoice_context`. This mode returns `context.company_change`
and `invoice_ref` from one native call. Without it, the existing context and its
parallel legacy reads remain unchanged and make no single-snapshot claim.
