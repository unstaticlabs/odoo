# Authenticated product feedback

## Product contract

Every authenticated internal user can send product feedback from the native
Odoo user menu without leaving the workflow they are performing. The focused
form captures a summary, description, category, native task priority and up to
ten optional attachments. A successful submission remains visible in **My
feedback**, where the reporter can follow status changes and discuss the item
in native chatter.

The canonical record is a `project.task` in the XML-owned **Odoo Product
Feedback** Project. The workflow uses native tags **Bug**, **Improvement**,
**Question** and **UX**, and native stages **New**, **Triaged**, **Planned**,
**In Progress**, **Ready to Verify**, **Done** and **Declined**. This keeps the
Project available to separately governed downstream orchestration without an
Odoo-to-GitHub API bridge or credentials.

## Context and privacy boundary

Page context is opt-in. The browser sends only typed candidates for the current
action, model, record identifier and viewport. It never sends the URL, query
string, fragment, tokens, local storage, form values or arbitrary browser
state. Before privileged creation, the server verifies the action, model and
record against the reporter's own read access. An unreadable or removed source
record is omitted and the feedback is still accepted with an explicit
confirmation.

The active company is recorded on the task and remains subject to Odoo's native
multi-company rule. The exact running release SHA is resolved from the sealed
database release identity or `USL_RELEASE_COMMIT`; submission fails clearly if
no exact 40-character identity is available or if the two trusted sources
disagree. Company and release identity are required routing and audit metadata,
not arbitrary page state.

## Access model

The feedback Project itself is hidden from ordinary employees, including
Project users and Project administrators who are not Feedback Maintainers. A
global `project.task` rule intersects all native Project rules:

- a reporter can read only feedback whose `usl_feedback_reporter_id` is their
  own user, and native task access still requires their task followership;
- another employee cannot retrieve the task through search, `read_group`, a
  guessed identifier, a generic Project view, chatter, activities or
  attachments;
- a reporter cannot edit, move or delete the submitted task; followership,
  chatter replies and chatter attachments remain available through native mail
  access on the readable task;
- members of the explicit **Feedback Maintainer** capability can read and
  operate every feedback task available in their active companies;
- an approved automation/service user may receive the same capability. When
  the Distribution Access Control module is installed and it is also marked
  **AI Agent**, that module's existing mutation audit records its writes.

Submission uses one narrow elevated region after every user-controlled value,
attachment and source record has been validated. The elevated environment
keeps the reporter UID, so `create_uid`, followership and the submission note
remain attributable to the human. No general Project ACL or Project role is
granted to reporters or maintainers.

## Architecture decision

The selected path uses Odoo's native `user_menuitems` registry and a transient
form dialog. The same entry appears in the desktop user systray and mobile
burger menu, the dialog preserves the current action stack, and pending uploads
have an owned transient record before task creation.

Two alternatives were rejected:

1. Opening the native task form directly would require exposing the governed
   Project before submission, would allow unsafe default/context injection and
   would leave no clean ownership boundary for pre-create attachments.
2. A custom feedback model or OCA helpdesk-style ticket would duplicate task
   stages, tags, chatter and downstream Project interoperability. No maintained
   OCA add-on in the pinned runtime provides this private global capture
   boundary without introducing another canonical ticket model.

## Installation, upgrade and recovery

Install `usl_feedback` after its declared `project` and `web` dependencies. It
does not pull the broader Distribution Access Control dependency graph merely
to define its narrow maintainer capability. A normal module update is:

```text
-u usl_feedback
```

The module adds fields and indexes to `project.project` and `project.task`, one
transient submission table and relation, XML-owned security records, the
Project, stages, tags, actions and views. There is no one-shot migration and no
external side effect. Clean installation, an update, and an identical repeated
update must all pass. The XML-owned workflow data is `noupdate` so an update
does not move live feedback or overwrite deliberate operational stage changes.

Before production upgrade, take a consistent database and filestore backup.
Verify the module is installed, the Project has exactly the governed stages,
reporter isolation still denies cross-user and cross-company reads, and a
synthetic submission carries the deployed SHA. On failure, stop the upgraded
workers and restore the matched database and filestore backup with the prior
image. Removing only the code is not a rollback after its stored fields and
tasks exist. No operation in the feature is intentionally irreversible and no
source dump or migration project participates in recovery.
