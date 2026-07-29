# Test the Documents application

This guide uses the isolated local QA environment. Use harmless synthetic
files only.

## Sign in

- Odoo: `http://127.0.0.1:18080`
- Paperless: `http://127.0.0.1:8010`
- Odoo administrator: `admin` / `admin`
- Odoo restricted user: `documents-restricted` / `admin`
- Paperless administrator: `archive-admin` / `admin`

Start in Odoo. Open **Documents** from the app launcher.

The QA stack is named `codex-paperless-docs`. If it is stopped, run:

```bash
docker compose -p codex-paperless-docs start
```

Stop it without deleting its data with:

```bash
docker compose -p codex-paperless-docs stop
```

Do not run `down --volumes` or `scripts/odoo-dev reset` during product testing.

## What should feel different

- The main screen is for finding and using documents, not maintaining the
  integration.
- Healthy synchronization, permission, checksum, and API details are hidden.
  A short warning appears only when something needs action.
- Cards show the title, date, type, correspondent, tags, and the most relevant
  Odoo record.
- **Download original** is the main action. Searchable PDF, new version, and
  Paperless are under **More**.
- Tags, correspondents, and document types come from Paperless and can be
  edited from Odoo.
- The old technical lists are under **Configuration** with clearer names:
  **Document register**, **Linked records**, and **User access**.

## Test 1 — Find a document

1. Search for `cobalt marigold evidence`. That phrase is inside a synthetic
   file, not only in its title.
2. Filter by **My Company** and **Invoice**.
3. Try a tag chip and then open **More filters**.
4. Open the result.

Expected:

- one authorized result set is shown;
- the preview is readable;
- the details start with useful classification and linked records;
- there is no healthy “synchronized” status competing for attention.

## Test 2 — Classify with Paperless metadata

1. Open any harmless synthetic document.
2. In **Classification**, click **Edit**.
3. Change its type or date and add a tag.
4. Click **Save**.
5. Open the same document in Paperless and confirm the change is there.

Expected:

- Odoo saves the change through Paperless and reads it back;
- the card and detail panel show the updated value;
- colored Paperless tags can be used as filters;
- no Odoo-only copy of the metadata is presented as authoritative.

Managers can maintain the shared catalogs under **Configuration > Tags**,
**Correspondents**, and **Document types**. Matching patterns and Paperless's
automatic matching option are available there without cluttering daily work.

## Test 3 — Search and saved views

1. Combine a company, type, tag, and date filter.
2. Enter a name under **Save this view** and save it.
3. Open a document and then one of its linked Odoo records.
4. Return to Documents.

Expected:

- the saved view appears under **My views**;
- search, filters, sorting, and card/list choice are preserved;
- removing a personal view does not change the shared company navigation.

## Test 4 — Upload from a vendor bill

1. Open Accounting and find the draft vendor bill with reference
   `USL-DOCS-QA-BILL`.
2. Click **Find / upload**.
3. Upload a harmless PDF or text file with a distinctive sentence.
4. Watch the visible operation move through pending and processing.
5. Open the archived document from the bill.

Expected:

- Odoo says “archived” only after Paperless confirms success;
- the document is linked to the bill;
- the original is stored in Paperless, not copied into a new Odoo attachment;
- **Download original** downloads the file that was received.

## Test 5 — Duplicate reuse

1. Upload the exact same file from the same bill again.
2. Read the notification and reopen the result.

Expected:

- the existing Paperless document is reused;
- no silent duplicate root document is created;
- no extra Odoo binary copy is created.

## Test 6 — Link one document twice

1. Open **Synthetic Documents Supplier**.
2. Click **Find / upload** and select the document from Test 4.
3. Click **Link to this record**.
4. Confirm the document shows both the bill and partner.
5. From the partner context, click **Remove link**.

Expected:

- one Paperless document can support both records;
- removing the partner link does not delete the archive or the bill link.

## Test 7 — File versions

1. Open a seeded `acceptance-…` document.
2. Expand **File versions**.
3. Confirm **Current** and **Received original** are easy to identify.
4. Under **More**, choose **Upload new version** and use a harmless file.
5. Expand **File versions** again and choose **Restore as current** on the
   earlier file.
6. Confirm the restoration.

Expected:

- the new upload becomes current and the earlier files remain available;
- restore creates another current version from the selected file;
- nothing is overwritten or deleted;
- every version can be previewed and downloaded;
- checksums remain under **Technical details**, not in the normal workflow.

## Test 8 — External Paperless ingestion

1. Sign in to Paperless as `archive-admin` / `admin`.
2. Upload a harmless file there.
3. Wait for Paperless processing and the Odoo synchronization job (normally
   within five minutes).
4. In Odoo, open **Needs review**.
5. Classify the item and link it to a synthetic project or task.

Expected:

- the item appears automatically rather than disappearing;
- Paperless remains authoritative for file and archive metadata;
- Odoo remains authoritative for company, confidentiality, and business links.

## Test 9 — Restricted access

1. Note the title of a document belonging to **My Company**.
2. Sign out and sign in as `documents-restricted` / `admin`.
3. Search for the title and try a previously copied preview or download URL.

Expected:

- the restricted user cannot see the title, tag, thumbnail, identifier, or
  other hint;
- direct preview and download attempts do not reveal the file.

## Test 10 — Paperless outage

This test stops only the isolated QA Paperless service:

```bash
docker compose -p codex-paperless-docs stop paperless-webserver
```

Refresh Documents, try a preview, and confirm the rest of Odoo still works.
Then restore Paperless:

```bash
docker compose -p codex-paperless-docs start paperless-webserver
```

Click **Retry**.

Expected:

- Odoo shows one useful archive-unavailable message;
- business records remain usable;
- recovery does not duplicate documents or links.

## What to report

For a problem, include the test number, URL, login, active company, document
title, exact message, and a screenshot when visual. Do not attach confidential
files. Also report anything that works but still feels confusing or unlike
normal Odoo.
