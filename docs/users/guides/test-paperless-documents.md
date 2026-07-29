# Try the Paperless-backed Documents application

This guide is for the isolated local QA environment. Everything in that
environment is synthetic. Do not use production invoices, employee files,
contracts, credentials, or other confidential material.

The test has two applications:

- Odoo at `http://127.0.0.1:18080`
- Paperless at `http://127.0.0.1:8010`

The local-only human logins follow the QA convention:

- Odoo administrator: `admin` / `admin`
- Odoo restricted user: `documents-restricted` / `admin`
- Paperless archive administrator: `archive-admin` / `admin`

Start in Odoo unless a step explicitly asks you to use Paperless.

## Start and stop this QA environment

The QA stack uses the isolated Compose project `codex-paperless-docs`. It does
not use the normal `usl-odoo-saas-19-2` project or any preserved accounting
database.

From the repository root, check its state with:

```bash
docker ps --filter label=com.docker.compose.project=codex-paperless-docs
```

If the containers have been stopped, start the existing QA environment with:

```bash
docker compose -p codex-paperless-docs start
```

Stop it without deleting its synthetic database or archive with:

```bash
docker compose -p codex-paperless-docs stop
```

Do not run `down --volumes`, `reset`, or a command against another Compose
project. Those actions are not part of product testing.

## What changed

The new **Documents** application gives Odoo users a native workspace over the
Paperless archive. Odoo decides company access, confidentiality, business
relationships, and review state. Paperless stores the original file, OCR text,
preview, versions, checksums, and archive metadata.

The change also adds document buttons to bills, invoices, journal entries,
expenses, partners, companies, projects, tasks, and employees. When the
accounting bridge is installed, the same buttons are available on tax
declarations and closing periods.

Files uploaded in the Documents application are sent to Paperless. They are not
stored again as Odoo attachments. Archiving a pre-existing Odoo attachment is a
deliberate exception: the Odoo copy is retained and the Paperless copy is
associated by checksum.

## Before you begin

1. Sign in to Odoo as the local administrator.
2. Open the app launcher and choose **Documents**.
3. Confirm that the sidebar contains **Needs attention**, **Recent documents**,
   **Recently ingested**, **Accounting evidence**, **Contracts & legal**,
   **Banking**, **Tax & reporting**, **HR restricted**, and **All accessible**.
4. Confirm that document cards and thumbnails load. Open one card and check
   that the preview, checksum, versions, source, permission state, and linked
   Odoo records are understandable.

If this first check fails, record the page, the exact message, and whether Odoo
itself still works. Do not reset the environment.

## Test 1: search the archive

1. In **Documents**, search for a phrase visible in one of the seeded document
   previews.
2. Try the company, document type, confidentiality, review state, and linked
   record filters.
3. Switch between card and compact-list views.
4. Sort by title and by recently ingested.
5. Open a result, then open one of its linked Odoo records.
6. Use the Odoo breadcrumb to return.

Expected result: OCR and metadata search produce one authorized result set.
Returning through the breadcrumb keeps the previous workspace, query, filters,
sort, page, and view mode.

## Test 2: upload from a vendor bill

1. Open **Accounting**, then the draft vendor bill whose reference is
   `USL-DOCS-QA-BILL`.
2. Click **Find / upload**.
3. Upload a harmless PDF or text file containing a distinctive sentence.
4. Watch the visible operation move from uploading to processing and finally
   archived.
5. Open the new document, preview it, and inspect its source filename,
   checksum, submitting user, company, and Odoo relationship.
6. Return to the vendor bill and confirm that its **Archive** count increased.

Expected result: the relationship is created only after Paperless confirms
archival. The UI must not say the file is archived while it is pending or
failed.

## Test 3: duplicate reuse

1. Upload exactly the same file again from the same bill.
2. Read the notification and recent ingestion activity.
3. Reopen the document and inspect its Paperless identity and link count.

Expected result: the operation says the duplicate was reused. There is still
one Paperless root document and no new Odoo attachment copy.

## Test 4: link one document to two records

1. From the seeded partner **Synthetic Documents Supplier**, click
   **Find / upload**.
2. Search for the document uploaded in Test 2.
3. Open it and click **Link to current Odoo record**.
4. Confirm that the detail panel shows both the vendor bill and partner.
5. Click **Remove this Odoo relationship** while in the partner context.
6. Return to the bill and open the document again.

Expected result: removing the partner relationship does not delete the
Paperless document and does not remove the vendor-bill relationship.

## Test 5: external Paperless ingestion

1. Sign in to Paperless with the individual QA archive account.
2. Upload a harmless file there rather than through Odoo.
3. Wait for Paperless processing and for the Odoo synchronization job. The
   normal synchronization interval is five minutes.
4. In Odoo, open **Documents > Needs attention**.
5. Open the new item. In **Documents > Archive records**, assign the legal
   company and appropriate confidentiality, then mark it reviewed.
6. Link it to the synthetic project or task from that record's
   **Find / upload** button.

Expected result: external ingestion appears automatically as a review item.
Paperless remains authoritative for its title, type, tags, OCR, and versions;
Odoo remains authoritative for company, confidentiality, review, and business
links.

## Test 6: versions and downloads

1. Open a seeded document that has more than one file version.
2. Select the current version and the **Received original** version.
3. Preview both and compare their checksums.
4. Try **Original** and **Archival derivative** downloads.
5. As an authorized manager, use **Add replacement as new version** with a
   harmless replacement file.

Expected result: the Paperless document identity and all Odoo links remain
stable. The received original remains available and unchanged while the new
file becomes the current version.

## Test 7: direct Paperless work

1. From an Odoo document, click **Open in Paperless**.
2. Confirm that Paperless opens the same document, not a generic home page.
3. Change a harmless Paperless-owned value such as the title.
4. Wait for synchronization and refresh Odoo.

Expected result: the new Paperless title appears in Odoo without creating a
second archive item. The button is present only when the Odoo user has a mapped
individual Paperless identity and synchronized document permission.

## Test 8: access control

1. Copy the title of a document belonging to **My Company**.
2. Sign out and use the local `documents-restricted` Odoo account.
3. Search for the copied title and browse **All accessible**.
4. If you recorded an Odoo preview URL, try opening it while signed in as the
   restricted user.

Expected result: the restricted user sees neither the title nor a metadata
hint, thumbnail, snippet, preview, or download. The denial must not reveal file
content or Paperless identity.

## Test 9: degraded archive behavior

Coordinate this test with the environment owner because it briefly stops only
the isolated QA Paperless web service.

1. Keep a vendor bill open in Odoo.
2. Stop the QA Paperless web service with
   `docker compose -p codex-paperless-docs stop paperless-webserver`.
3. Refresh the Documents workspace and try a preview.
4. Confirm that the bill and the rest of Odoo remain usable.
5. Restart Paperless with
   `docker compose -p codex-paperless-docs start paperless-webserver`, then
   click **Retry**.

Expected result: Odoo shows **Archive degraded** or an actionable unavailable
message. After recovery, synchronization resumes without creating duplicate
documents or relationships.

## What to report

For each problem, include:

- the test number and exact step;
- the Odoo or Paperless URL;
- the user role and active company;
- the document title and Paperless number if it was already visible;
- the exact message shown;
- whether retrying or refreshing changed the result;
- a screenshot when the problem is visual.

Never include the file itself when it contains confidential information. A
successful review should also note anything that technically works but feels
unnecessarily confusing, slow, or unlike the rest of Odoo.
