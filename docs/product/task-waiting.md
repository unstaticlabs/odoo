# Say what a task is waiting for

A task can wait for something outside Odoo, such as a supplier quote or a
customer answer, without linking another Odoo task.

- **Wait.** On the task form, open the status menu and choose **Waiting**. An
  inline "Waiting for…" field appears and takes focus; type the reason, and
  optionally the contact the task is waiting on.
- **See it.** A waiting Kanban card shows one muted line under the title with
  the reason and the contact. The list can show the reason as an optional column.
- **Resume.** Choose **In Progress**, or clear the reason: the task resumes in
  one step. Choosing any other status also clears the reason.
- **History.** The reason and the contact are tracked in the task's chatter like
  any other field.
- **Another task.** To wait for an Odoo task, keep using **Blocked By**.

A task is Waiting while a task it depends on is open **or** a reason is set.
Clearing the reason does not resume a task whose dependencies are still open.
A reason never reopens a Done or Cancelled task, and a copy never inherits it.

The reason is an ordinary field of the task (`usl_waiting_reason`, with
`usl_waiting_partner_id`). Task access rules apply as for any field: people who
can edit a task can set it, portal users cannot read it, and Agents read and
write it through the generic tools; the task context tool returns it.
