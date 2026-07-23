# Route Bills and Expenses by Email

Audience: Accounting Managers, finance operators and deployment administrators.

Odoo Community can create a draft supplier bill or employee expense from an
incoming email and preserve the email and its attachment in the record chatter.
The alias alone is not an inbox: a deployment administrator must also connect a
real inbound domain and mail server.

## Configure the inbound mail service

An Accounting Manager and the administrator responsible for email must agree on
a domain controlled by Unstatic Labs. Do not reuse an `odoo.com` SaaS alias
domain on the self-hosted deployment.

In Odoo, open:

```text
Settings > General Settings > Emails
```

Then:

1. enable custom email servers;
2. create and test the incoming mail server;
3. select the controlled `Alias Domain`;
4. configure the email provider and DNS so mail for that domain reaches the
   incoming mailbox;
5. configure the matching outgoing server so Odoo can send notifications and
   bounces.

Keep the provider, mailbox owner, DNS change and tested date in the deployment
runbook. Alias names must not be published until the inbound server test
succeeds.

## Configure the supplier-bill alias

Go to:

```text
Accounting > Configuration > Journals
```

Open the purchase journal, select `Advanced Settings`, and set the email alias
under `Emails`. The reconstructed source alias name is `purchases`; the domain
must be the new self-hosted domain selected above.

Send one supplier bill per email. Odoo accepts any attachment, interprets
supported PDF/XML invoice content when possible, and otherwise keeps the
attachment for manual entry.

## Configure the employee-expense alias

Go to:

```text
Settings > Expenses > Incoming Emails
```

Enable incoming expense emails and set the alias. The source evidence includes
the alias `notes-de-frais-employes`; choose whether to retain that address or
use the shorter native `expense` address before publishing it.

The sender must match an employee work email or the email of the employee's
linked Odoo user. Put the expense-category internal reference first in the
subject when known, followed by a description and amount. For example:

```text
MEAL Client lunch EUR 42.50
```

Attach the receipt. Odoo creates the expense in draft and preserves the
incoming message and receipt.

## Perform the production smoke test

Use dedicated, non-production evidence and unique subjects:

1. email a small supplier-bill attachment to the purchase alias;
2. confirm one draft supplier bill appears in
   `Accounting > Vendors > Bills`;
3. confirm the sender, incoming message and attachment are visible;
4. email a receipt from a real employee address to the expense alias;
5. confirm one draft expense appears in `Accounting > Vendors > Expenses`;
6. confirm the employee, amount/category when supplied, message and receipt;
7. resend neither email: repeated messages with the same message identifier are
   intentionally rejected as duplicates.

Delete or cancel the two smoke-test records according to the accounting
manager's test-data policy. Record the successful route, time and reviewer in
the deployment runbook.

## Troubleshooting boundary

If no record appears, check the provider mailbox, DNS delivery, incoming-server
log and alias domain before changing Odoo accounting data. If Odoo creates a
record without parsed invoice fields, keep the attachment and complete the
draft manually; OCR is an optional paid service and is not part of the
Community replacement proof.
