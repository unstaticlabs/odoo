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
