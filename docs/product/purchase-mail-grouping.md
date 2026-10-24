# Purchase mail attachments

## Native UBL/CII compatibility

At the distribution's Odoo `saas~19.3` base
`b426963bb82ed8848767dc8845cff9c7a0fa0515`, native
`account.edi.common._retrieve_invoice_line_vals` checks whether optional
Enterprise deferred-period fields exist before parsing them, but returns both
keys with `None` even when those fields do not exist. A nonzero UBL invoice
line then fails native `account.move.line.create` with an invalid-field error.
The native attachment decoder catches the error, leaving a draft without the
imported lines.

USL Accounting extends that native method and removes only an absent field's
`None` value. Installed fields keep all their values. Unsupported nonempty
values still reach native validation and fail; the extension neither adds
fields nor discards financial information. The explicit dependency on native
`account_edi_ubl_cii` establishes the inherited model's loading order.

This is a distribution compatibility bridge, not a replacement importer. On
an upstream update, rerun the native UBL import and optional-field tests, and
remove the override when upstream stops emitting absent empty keys. The
field-present test models the optional registry shape at the delegate
boundary; it does not qualify an Enterprise installation.

## Grouping new purchase emails

New mail addressed to a native purchase-journal alias uses the existing Odoo
mail, draft-creation and attachment-import flow. Distinct invoice identities
become separate draft bills. A receipt referring uniquely to an invoice stays
with it, with the invoice document selected as main. A matching receipt never
sets a payment, reconciliation or paid status.

Grouping reads current file bytes. Native UBL/CII document-kind and supplier
helpers provide structured evidence. Supported English/French text PDFs need
an explicit invoice or credit-note heading, a single labeled invoice reference,
an identifiable supplier, a date, currency and labeled total. A payment document
needs its own explicit receipt heading and a coherent invoice reference,
payee, amount, currency and date. Explicit partial payments can match a uniquely
identified invoice. ISO dates and French day/month/year labels are supported;
ambiguous English slash dates remain unknown. Euro symbols are accepted.
Filenames, extensions, declared MIME types, sender identity and a word such as
“paid” do not establish an invoice/payment relationship.

The text parser is deliberately finite and supports labeled documents, not
arbitrary layouts or OCR. Scanned images, unsupported layouts, contradictory
identities, encrypted/corrupt PDFs and exceeded extraction limits remain
unknown. Extraction is limited to 10 MiB, 10 pages and 65,536 text characters
per file, with 64 files and 30 MiB total per message (including native extracted
representations). These are input/result bounds, not a hard CPU deadline for
the native PDF library. No OCR/indexing/provider service is invoked.

Native original/embedded families stay together. Matching standalone PDF/XML
representations can share one bill. All original bytes and copied mail history
remain available; imported XML may be stored in Odoo's native attachment-backed
`ubl_cii_xml_file` field. Unknown files stay on the original/first invoice draft,
or on its single draft when no invoice is proved. No extra holding bill is
created for uncertainty. Bills from the same email are linked. Byte equality
never suppresses a separate later email's business event.

## Reviewing attachments

Uncertain drafts appear in the existing **To Review** search alongside native
posted accounting reviews. The bill banner opens **Review attachments**. After
confirmation the same action remains under **Other Info → Mail attachments**, so
an operator can separate another invoice discovered later.

Each file has one **Use as** choice: **Supporting file**, **Main document**, or
**Another invoice**. Download the original directly from its row. Original
classification reasons remain available in the expandable evidence section.
An already saved, unchanged purchase draft can open review before its invoice
date is known. Pending edits and new records retain native save validation, as
do posting and unrelated actions.

**Confirm** records the chosen main document, operator and time. Selecting
**Another invoice** changes the primary action to **Create draft bill**. All files
with that choice are copied together to one new empty draft, in the source
company, purchase journal and document type. The original files remain on the
source bill, whose grouping choices are recorded as reviewed. The new draft
links back to its source, is marked for review, and opens for the operator to
complete its invoice details. Its attachment preview is evidence, not imported
invoice lines. Supplier, amounts, lines and payment state are not copied or
inferred. A posted source bill can supply copies without changing its posted
entries. Separating further invoices uses another selection and another draft.

Source-row locking and an exact selected-file-byte receipt make repeated
submission open the previously created bill rather than create a duplicate.
If that bill disappeared or is no longer readable, review reports the unresolved
history instead of silently replacing it. Changed source files require a fresh
review. Cancel changes neither the source bill nor its files. Native posting,
payment, reconciliation and the separate Accounting review keep their existing
rules; attachment review creates no additional mandatory approval step.

The journal upload helper reparents files and may auto-post. Its decoder can
commit the cursor. Manual separation therefore uses native draft creation and
attachment copying, with attachment import disabled on its internal provenance
note. The operation remains one transaction, and the source accounting values
and original mail history are preserved. A later upload/reply keeps ordinary
native existing-bill behavior and does not silently regroup history.

## Agent context and shared actions

People and delegated Agents use the same bill and attachment rights and the
same review workflow. `account.move.get_purchase_attachment_context(offset=0,
limit=50)` returns metadata only for vendor bills/credit notes with mail-grouping
context; other moves return `{}`. It does not read file bytes or compute the
review fingerprint. The current-rights document page has a maximum of 100 rows,
with `count`, `complete`, `truncated` and `next_offset`. Names, reasons and MIME
types are bounded to 512, 1024 and 128 characters; a corresponding `*_status`
reports `truncated` when needed. Related readable bill references are capped at
100 and expose their count and truncation flag. Full originals remain available
through normal native attachment reads.

The response advertises the relevant native action and transient line schema:
open `action_review_mail_grouping`, set each line's `use` to `supporting`, `main`
or `separate`, then call `action_keep_together` or `action_create_draft_bill` on
that review. Choose at most one main file. The review fingerprints current bytes
and rechecks permissions and file ownership before changing anything. Native
readonly actors receive evidence without write actions. Existing generic API
callers can still use the original review fields; the form presents only the
single per-document choice.

## Disposable qualification data

Seed attachments with native `raw` bytes and an explicit company matching the
bill. Odoo `saas~19.3` ignores the deprecated `datas` input, and a file's default
company follows the creating environment rather than its target bill. Checking
as an administrator can therefore hide an empty-file or company-boundary fixture
mistake. Verify nonempty bytes and the intended non-admin or Agent's document
context before exercising the workflow. Use separate databases for backend
suites and interactive review, and seed a new visual QA generation after backend
qualification rather than overwriting a scenario already exercised.

## Native import and transaction limits

Classification does not populate PDF invoice lines or guarantee that a native
decoder succeeds. Native import and its Chatter errors remain authoritative.
All group drafts, originals and grouping context are established before the
first native decoder runs. Native decoders explicitly commit outside module
tests, so an interrupted mail worker may leave some drafts imported and others
incomplete. There is no whole-message atomicity claim and no custom commit.

The existing native Message-ID/advisory-lock boundary prevents duplicate or
concurrent replay from creating another bill. Replay does not resume a stopped
decoder. Recover the retained incomplete draft using native editing or attaching
an original-file copy there; do not replay mail creation or move/delete its
source originals. Sales aliases, bank intake, disabled attachment import and
replies/manual attachments on existing bills keep their native behavior.
