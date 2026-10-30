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

## Vendor bills

A supplier email containing a document link but no PDF now offers recovery on
its vendor bill. Confirm the suggested link or choose another one. The existing
retrieval worker keeps the original PDF, its checksum and a note on that exact
bill; it does not post, pay, reconcile or change the bill's amounts.

For an older bill without a file or retrieval request, use **Find document links
in emails** on the bill form. Existing files and requests are skipped. Native
bill readers can see the retrieval context; native bill writers can manage it,
including delegated Agents with the same access. Select the bill's company;
it may be one of several selected companies rather than the current company.

When a website requires sign-in, open its website and upload the downloaded PDF
on the bill. The native upload widget retains every file and selects the main
attachment only if one is still missing. Retry, choose another link or ignore
the email when needed. A queue paused by the environment remains visibly pending.

A changed bill, changed source email, manually attached file, cancelled bill or
lost requester authority prevents a pending worker from publishing stale evidence.
The original email and any existing files remain available for recovery. Existing
expense retrieval and supplier-email invoice splitting keep their native behavior.

A saved imported bill can retrieve or upload its source document before its bill
date is known. These document controls keep native save validation for new bills
and unsaved edits; ordinary financial actions keep their normal required fields.
Native upload metadata retains Odoo's current-company default. Linked-record
access remains authoritative: an ordinary user with only the bill's company can
read its original, while a user without access to that bill cannot read the file.
