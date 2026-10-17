# Company expense inboxes

In **Expenses → Configuration → Settings**, switch to the company you want and
set its optional **Company Expense Inbox** prefix. The address uses that
company's native email alias domain. Leave the prefix empty to keep using the
global expense address. Installing or upgrading the module configures no
company addresses automatically. Purchase and sales aliases retain their native
settings.

When a receipt arrives at a company inbox, Odoo uses the sender's existing
employee profile in that company. This also works for a verified personal sender
address. Verification still identifies the same person; the destination chooses
among that person's existing employee profiles. It never creates an employee or
grants access to another company.

Native expense import still parses the subject, chooses the product, account,
taxes and currency, retains the receipt, and sends its usual confirmation. It
creates a draft expense. Posting, approval and payment stay in their native
workflows. If the sender's default employee company differs from the inbox,
an internal note explains which company profile was used.

The global address keeps native sender-based company selection. It remains
available alongside every company inbox, including when the same email names
both addresses. Each native route retains its own company selection.

If no matching employee exists in the addressed company, or the sender has
several matching employee records there, native mail recovery explains the
problem. Ask HR to check existing company and sender-address links, or resend
to the global address. Unknown and unverified senders retain native acceptance
and verification rules. The inbox stays usable for other senders.

Renaming uses the same native alias record. Clearing the prefix disables that
address without changing the global address. Native alias uniqueness prevents
colliding addresses. Settings configuration requires the usual company and
email-alias permissions, shared by people and delegated Agents.

Email delivery to Odoo still requires the deployment's existing mail gateway and
domain setup. This feature does not configure an email provider or activate a
live inbox.
