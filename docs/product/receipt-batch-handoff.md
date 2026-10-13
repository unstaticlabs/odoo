# Receipt recovery and batch submission

When a receipt website requires sign-in, **Open receipt website** opens it in a
new tab with one click. The expense stays open so you can attach the downloaded
PDF. Odoo does not receive the website password. The browser first submits an
authenticated, CSRF-protected internal request; the provider's signed link is
never returned by the expense RPC. A blocked popup explains how to try again.
The older internal GET page remains safe and does not record an opening.

A receipt waiting in the queue for 30 minutes offers **Retry** and explains
that it has not started. Waiting alone does not mean the provider failed.
Existing eligible work stays queued until it starts or the owner explicitly
retries it. Retry creates a new generation: an old queued job cannot start the
new request, and a late completion cannot attach a receipt or supersede it.
Paused, disabled or quarantined processing remains paused, disabled or
quarantined. No queue admission or production setting is changed.

In the batch worklist, select expenses and choose **Add to batch**. The dialog
adds expenses without submitting them. Review the batch, then choose **Submit
batch**. Its native submission records the submitting person and date and
keeps the existing approval, receipt and accounting checks. An expense already
in a batch points to that batch when submitted individually. Standalone
expenses keep their native Submit action.

The change covers feedback 2579 and 2580, and a demonstrated recovery defect
relevant to 2750. Read-only live metadata showed queued work with no recorded
attempts; it did not establish the cause of the live runner backlog. Queue
administration metadata remained inaccessible to the Agent. Enabling or
repairing a live worker is an operator action outside this code delivery.

## Qualification

Synthetic employee, manager, outsider and two-company fixtures exercise native
submission, unchanged state after denial, generation fencing, attachment
identity and the secure HTTP routes. Real headless Chrome checks use the
running isolated Odoo server and a synthetic intercepted provider; only the
external provider response is a fixture. Still images show layout and wording,
not proof of successful submission or download.
