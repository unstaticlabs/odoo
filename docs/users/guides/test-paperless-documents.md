# Try the Documents application

Use this guide to review the Paperless-backed Documents experience in a
prepared QA runtime. Ask the operator for the Odoo, Paperless, and Pocket ID
URLs plus a fresh login link.

## Sign in

Open the Pocket ID link once, complete sign-in as your own named user, and let
it return you to Odoo. Do not share or reuse the link.

Open **Documents** from the Odoo app launcher. If you also open Paperless, use
the provided Pocket ID route; do not create a separate local account.

## Review a document

1. Open a workspace or smart view you are allowed to use.
2. Search for a known document by title, text, company, or linked record.
3. Open the preview and confirm that the original is readable.
4. Review document type, correspondent, tags, date, company, and business
   links.
5. Open the linked Odoo record and return to the same document.
6. Where versions exist, verify that the intended version and original remain
   available.

Search may use OCR text and semantic matches. The result must still respect
your Odoo company and record access.

## Add and organize a test document

Use only approved disposable test material in QA:

1. Upload or attach the file from its normal Odoo business record.
2. Confirm it is immediately usable in Odoo while archive processing runs.
3. Wait for its preview, OCR, and archive metadata.
4. Add an allowed title, document type, correspondent, tags, or business link.
5. Refresh and confirm that the metadata remains stable.

Do not upload production secrets, identity credentials, or unrelated personal
documents.

## Access checks

Switch only through the normal Odoo company selector. Confirm that:

- a single-company view shows that company's records;
- multi-company mode shows the intended combined records with clear company
  labels;
- a user without access cannot discover the document through search, preview,
  direct URL, version history, or Paperless;
- removing a business relationship removes the corresponding access after
  synchronization.

Report any case where Odoo and Paperless disagree about access.

## Operations and failure states

For changed document operations, try the relevant move, archive, restore,
trash, version, link, or metadata action. Confirm that:

- the UI explains what will happen;
- the operation updates without losing your current context unnecessarily;
- failures are visible and retryable;
- the original and audit history are not silently discarded;
- another company cannot operate on the record.

## What to report

Include:

- your named user and active company selection;
- the Odoo record and document title, without credentials or private content;
- the action, expected result, and observed result;
- whether the problem affects Odoo, Paperless, or both;
- a screenshot only when it does not expose confidential data.

The operator validates archive counts, queues, OCR, Tantivy, vectors, model
identity, restart, and coordinated recovery separately. You do not need to run
terminal commands or exhaustively inspect unchanged documents.
