# Scan historical expense emails

Enabling linked receipt downloads does not replay existing emails. For a draft
or approved expense without receipt evidence, open the form's cog menu and choose
**Scan existing emails for receipts**. For a batch, select expenses in the list
and use the action with the same name.

The action requires write access and either expense ownership or the Accounting
Manager role. It respects company access and the environment activation gates.
Employees cannot scan approved expenses when native Odoo rules deny them write
access; an Accounting Manager with expense write access must perform that scan.
It skips non-eligible expenses (for example, paid or refused), existing retrieval
requests (including dismissed or failed requests), and expenses with attached
receipt evidence. Use the
existing retry or link-selection controls to manage a previous request.

Emails are examined newest first. The first email with eligible candidates
creates one retrieval request per expense. Recognized links may be queued;
otherwise choose the receipt link on the expense. No candidate means no request
is created. Repeating the action does not duplicate an existing request.

The scan does not change amounts, company ownership, approval or payment states.
It does not teach a provider pattern or bypass authentication. Downloading and
attaching a receipt remains subject to the existing receipt security controls.
