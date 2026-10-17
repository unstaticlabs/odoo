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

## Reviewing uncertainty

Uncertain drafts appear in the existing **To Review** search alongside native
posted accounting reviews. The bill banner opens the current attachment list,
original classification reasons and optional native main-document selection.
**Keep these files together** records the operator and time. It does not
complete the separate accounting review, create financial lines, post, pay or
reconcile. Cancel leaves the bill untouched. An attachment change while the
dialog is open requires a fresh review. A later manual upload/reply keeps the
ordinary native existing-bill behavior and does not silently regroup history.

When another invoice is identified later, create a separate draft with copies
of its readable files and keep the source originals. The native journal import
helper reparents attachments and may auto-post, so it is not used as a recovery
shortcut here. Ordinary native main selection, editing, posting and other
generic tools retain their existing access rules. The same workflow and
native bill/attachment rights apply to people and delegated Agents; a
read-only actor cannot confirm a grouping.

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
