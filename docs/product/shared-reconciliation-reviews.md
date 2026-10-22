# Shared Remaining-balance reviews

General Reconciliation offers an optional review mark beside account groups.
It records the accounting team member, time, resolved filters and native
Remaining balance for that group. Colleagues with the same visible query,
selected companies and currency context share the mark. Marking a group does
not change journal entries, matching, payments or the native entry review state.

Open the mark to inspect its scope and balance. **Mark reviewed** records the
current balance. Green means the current Remaining equals that reviewed balance
at native monetary precision. Changes to line membership, Debit or Credit do
not invalidate a review when Remaining stays equal. Full server aggregates cover
folded groups and lines beyond the current page. Each status request batches up to 200
visible groups, with one native aggregate query per distinct grouping path
(at most three per batch in the normal list), rather than one query per group. Supported nested groups are
Account, Partner and calendar Month, with an account in the path.

A warning shows the reviewed balance, current balance and difference.
**Acknowledge change** records who acknowledged that observed amount without
replacing the reviewed balance or turning the mark green. Another amount change
warns again. **Review current balance** explicitly records a new baseline;
returning to the existing baseline also restores green. A concurrent colleague
update or a changed Remaining amount requires a fresh view before saving.

Changing the account, group path, resolved filters, selected companies or active
currency context selects a separate review. Sorting and pagination do not.
Mixed currencies follow the native header's conversion and display currency;
the details show the rate date. A new day alone does not discard a shared mark,
while a rate change that changes displayed Remaining warns. Unlike currencies
are shown separately rather than subtracted.

Normal accounting permissions and native readable journal-item scope govern
this workflow for people and delegated Agents alike. Accounting readers can
inspect marks; accounting writers can record them. The server computes amounts
and attribution. Stored filter literals have technical field visibility;
ordinary status reads return only the exact caller-supplied scope. Failure to
read status offers retry rather than displaying a stale green mark. Deleting an
otherwise native-deletable empty account or partner also removes its obsolete
annotations; native journal-item deletion protections remain in place.
