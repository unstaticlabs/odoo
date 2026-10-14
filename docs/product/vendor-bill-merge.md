# Keep one vendor bill without losing its originals

In **Accounting → Vendors → Bills**, select two or more bills from one company
and choose **Merge Bills**. You need the ordinary Billing write access for every
selected bill. The same source, company and native Accounting checks apply to a
write-capable Agent.

Empty bills for the same identified supplier merge immediately: the first-created
bill keeps its own headers, including its currency and reference. Exact duplicates
also merge immediately. Equality includes state, supplier, currency, journal,
dates, references, payment terms, historical rate, taxes, accounts, analytics and
financial lines; an equal total alone is not enough. Generated posting numbers
and record identifiers do not make otherwise identical bills different.

For differences, use **Choose this bill** on the comparison row. Amount, currency,
state, reference and stable record number distinguish even bills whose native
display names are identical. Check the selected details, then confirm **Keep this
bill and merge** separately. Selection alone changes no bill. Its state is shown
explicitly, including when selecting a draft alongside a posted bill.
The chosen bill keeps its existing lines, amount and state, and is marked with
native **To Review**. Draft vendor bills show that result to Billing users in
their form, list and To Review filter. Closing the dialog changes no bills.

Other selected bills are cancelled through native Odoo cancellation. Their
records, original financial lines, files, file ownership and history remain.
Amounts and quantities are never added together. The chosen bill's **Original
Bills** button opens the originals you can read, including linked earlier
duplicates. A cancelled original provides **Open the bill kept**. Files stay on
their original bill rather than being copied or relocated.

Use ordinary **Reset to Draft** on an original to recover it independently. This
clears its active duplicate link and records recovery in its history; it does
not reset the chosen bill or its other duplicates. Native copy also starts
without an active duplicate link. Historical merge and recovery notes remain.

Eligible posted unpaid bills are supported using native cancellation rules. Bills
with payments/reconciliations, cancellation-approval requirements, locks, hashes,
protected settlements or certified bank dependencies retain their normal
protections. A payment hold alone does not make an unpaid bill ineligible. If
native reset would delete the immediate next recurring draft, merge refuses
without deleting it. A posted immediate next recurrence does not block a later
draft merely because both exist. Cancellation may remove native derived analytic
entries; original financial lines and their analytic distribution remain.

If source lines or evidence changed after the dialog opened, close it and review
a fresh selection. Native whole-transaction retries handle concurrent updates;
a savepoint does not refresh a REPEATABLE READ snapshot. A refusal or later
cancellation/audit failure rolls back the whole merge. Retrying a completed
selection reports its cancelled originals rather than cancelling more bills.

This workflow uses existing `usl_accounting` and native Odoo cancellation/review.
OCA's invoice merge creates a new invoice and adds quantities, so it cannot
implement this keep-one-duplicate contract. No Accounting role, native guard or
generic mutation authority is granted by the merge workflow.

For maintainers: saas~19.3 transient models use regular ACLs and record rules;
they do not implicitly restrict choices to their creator. The wizard therefore
has an explicit company/creator record rule, compatible with the Agent owner's
authority ceiling, and exact creator checks before selection or confirmation.
The accountable owner can inspect their Agent's choice, but cannot confirm that
Agent's dialog as a different actor. Open a new selection instead. Selection and
snapshot initialization remain private and cannot be forged by public context.
