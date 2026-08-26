# Collaboration restoration

The final Collaboration stage runs only after every operational importer and
the full Documents archive have succeeded. It reads the locked Odoo Online
database through a read-only connection and routes every message, tracking
value, follower, activity, reaction, recipient and attachment relationship to
either a native business successor or sealed private archive evidence.

The locked source contains 50,005 messages, 36,946 tracking values, 5,862
followers and 895 activities. The production gate requires 49,451 messages on
native or rebuilt records and 554 technical, deleted-record or otherwise
unsupported messages in structured HTML/JSON archives. A changed count or
unknown model blocks the run.

The 554 archived messages are 350 generated notifications and 204 tracking
events, all using the internal Note subtype. They contain no email, comment,
reply, subject, recipient, attachment, reaction or parent relationship. Their
291 archived threads comprise 371 server-action events, 94 scheduled-action
events, 60 orphaned partner/Peppol notifications, seven automation events, six
bank-link events, six depreciation-rule events, three IAP-account events, four
CRM team/member events, two deleted-product creation notices and one Quality
team creation notice. This is configuration audit history, not customer or
operational narrative, so it intentionally remains private.

Run the stage only inside canonical reconstruction:

```bash
scripts/collaboration-restore all
scripts/collaboration-restore finalize
scripts/collaboration-restore product-validate
```

Meaningful aliases retain their local part and target record but are rebound to
the target-owned mail domain. Set `COLLABORATION_TARGET_MAIL_DOMAIN` for a
deployed environment. Isolated development reconstruction defaults to the
non-routable `unstatic-labs.test`; the source `unstatic-labs.odoo.com` domain is
never copied. Existing configured target company domains take precedence.

The importer never requeues `mail.mail`, recreates delivery notifications, or
copies presence, push-device, RTC or credential state. It assigns the 179 open
agent-created Project To-Dos whose source assignee is blank to Roger, their
source creator, and records that reconciliation in each activity note. Live
followers are limited to active mapped internal identities. Completed Project
and Expense activities remain inactive history; the completed Sign activity is
rendered as a dated internal note on its canonical signed-evidence document.

Finalization uninstalls the temporary module and drops its binding and run
tables. Source IDs and restoration metadata remain only in mode-`0600`,
checksum-sealed private artifacts; native business chatter remains in Odoo.

The Collaboration stage completes all 64 attachment relationships assigned to
its scope. The source-wide attachment gate nevertheless remains blocked on ten
separate payloads: nine standard spreadsheet-dashboard definitions and one
private strategy PDF used as an AI-agent source. The dashboard definitions are
not personal preferences or historical figures. Three have installed native
target counterparts and are recomputed; six require unsupported Enterprise
dashboard modules and remain private evidence. The PDF is genuine business
content and must become a restricted `usl.document`; its AI indexing and agent
configuration must not be copied.
