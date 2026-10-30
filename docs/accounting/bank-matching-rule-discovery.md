# Bank Matching Rule discovery

## Qualification

The final review, runtime, concurrency and upgrade evidence is recorded in the
[30 October QA context](../product/qa/proven-bank-rule-proposals-20261030/README.md).
The 15 October captures remain historical illustrations of the earlier form.
Release admission and recovery continue through the distribution's normal gates.

## Prepared workflow

**Accounting > Configuration > Bank Matching Rules > Find** prepares inert
native `account.reconcile.model` suggestions for an Accounting Manager in the
current company. It uses accessible posted, reconciled transactions from the
preceding two years, requires at least three distinct supporting transactions,
and offers at most twelve new suggestions per call.

Stable words and punctuation remain literal. Only demonstrated changing digit
slots become bounded native regular expressions. The form shows the pattern,
source transactions, native replay result and any uncertain competing rules.
Support counts describe examples, not a probability of correctness.

When some patterns are refused but other suggestions are available, a native
warning reports the counts and opens the suggestion list immediately. Links open
up to three preserved skipped-rule forms so their refusal can be inspected.
Patterns whose native accounting result differs require manual configuration.

Every included historical counterpart must match the maintained OCA prospective
calculation: account, partner, signed amount, company and foreign currency,
analytic distribution, taxes, reporting tags, repartition/group and reporting
base. A rule is not offered when the native rule lines cannot reproduce all its
examples. Multi-counterpart settlements and other unsupported effects remain
native manual-configuration work rather than being reduced to one guessed
account. The pinned OCA execution values omit `tax_base_amount`, so a surviving
nonzero reporting base is not asserted equivalent to native zero.

The native PostgreSQL matcher checks label, transaction details and narration,
company, journals, partners and signed amount. A conflict demonstrated by a
surviving transaction or a native-verified same-field witness refuses a generated
suggestion regardless of sequence or output account. Unknown regex relationships
and possible future cross-field combinations produce specific warnings and links
for review. Two separate label shapes can still match different text fields on
one future transaction; they remain reviewable suggestions. Finite examples and
separate label patterns never establish universal disjointness.

**Recheck evidence** validates current sources, settings and overlaps. An explicit
**Approve Rule** is still required before a suggestion can match transactions.
Changed settings invalidate the displayed proof. Rechecking an archived rule
leaves it archived. Native matching continues to exclude pending suggestions.

## Authority, lifecycle and preservation

Generated provenance, evidence references and proof digests are initialized
privately. Public fields, defaults and serialized context cannot mint a proof or
activate a generated suggestion. Copies remain suggestions and need rechecking
and approval. The complete relation to supporting transactions is checked before
reading source content: a hidden source cannot silently shrink the proof cohort.

Approval and generated-rule edits retain Accounting Manager and current-company
checks. Ordinary native manual rule authoring retains its operation-specific
permissions, including Billing create-only access. Manual edits receive no
historical-evidence ceremony. A proven conflict introduced against an active
generated rule is refused visibly. Existing protected deletion policy remains.
Older and manually prepared suggestions retain their existing Accounting
Manager approval workflow and review notes. They do not acquire a verified replay
claim merely by upgrading. Recheck evidence can establish the new proof when the
retained supporting transactions permit it; it is not a new prerequisite for
legacy approval.

Rejected new suggestions are archived with their reason rather than deleted;
repeated Find calls retain their history and evidence key.

Analysis, rechecking and approval do not create, write, delete, post, revalue or
reconcile financial records. Future application of an approved rule uses native
Bank Matching. No automatic provider activation or production repair is included.

## Concurrency decision

One private coordination row per company serializes rule and rule-line authoring.
It has no ACL, public API, counter or financial meaning. Fixed parameterized
UPSERTs update the tuple version after native originating-operation and company
checks. Agents use the same workflow under the existing delegated human rights;
this addon introduces no Agent-only policy. The barriers do not require
journal-write permission or change a journal.
Affected old/new companies and nested parent scopes are resolved before the
sorted barrier; only an identity-checked internal scope can reuse held companies.
Public create/copy results restore the caller environment so private proof and
held-company tokens cannot escape into a later operation on the returned records.

A stale REPEATABLE READ transaction receives native SQLSTATE 40001. The product
helper does not catch it, commit, open another cursor or retry locally. Odoo's
maintained outer retry restarts authority, defaults, source proof and overlap
checks. Financial evidence receives separate native row locks because financial
writers do not participate in rule-authoring coordination.

The public list action remains record-style, including an empty selected-ID
list. Final qualification must still exercise the actual protocol argument shape
and current normal-user desktop/mobile journeys.

## Qualification still required

- Literal fix removal, expected causal failure and exact restored pass.
- Final-source real RR and financial-writer race repeat, plus the full required
  nested-company, line-CRUD and competing-writer matrix.
- Full affected accounting tests, actual Agent protocol and French journeys.
- True predecessor and repeated upgrades with original financial, attachment,
  message and membership preservation; final standalone install.
- Exact 164-module runtime discovery and scoped policy reseal, normal assets,
  safe final screenshots, independent review and final-head hosted checks.

Committed financial race fixtures are restricted to an explicitly disposable
`odoo_proven_rule_races_*` database. The tests use the maintained registry-test
context and `release_test_lock`, while each worker owns a real independent
`_db.cursor` and Environment. This avoids the test server's startup registry lock
without weakening the product concurrency protocol. Keep these tests out of an
existing business or review database.
