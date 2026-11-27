# Find a task from its conversation

Search in Home or choose **Content** in the native Tasks search bar. A readable
task comment, shared note, or user correspondence subject/body can find its task
after the scheduled refresh. Home identifies the message source and shows a
short plain-text excerpt. Opening that result follows Odoo's existing message
link into the task conversation. Ordinary task results keep their native form
navigation.

The first conversation slice covers `mail.message` records on tasks. It excludes
native user notifications, tracking records, and automatic task-event
notifications. Genuine comments remain eligible, including comments carrying a
task-specific subtype. Shared native notes and comments remain eligible when
an API author uses the default notification type. This avoids creation/status
noise while retaining conversations people expect to find.

Current native task and message permissions, source-field permissions, record
rules, and selected companies apply before matching or returning an excerpt.
Opening a source checks its exact task, message and field again. A stale or
withdrawn source produces refresh guidance. Text deliberately quoted in a
readable comment remains that comment's text: mentioning a private project does
not grant access to that project's record.

An Agent can use generic MCP search on `project.task` with a `usl_search_text`
domain and request `usl_search_sources`. Pass the same companies and language,
and set the public `search_source_query` context key to the query for an
explicit source explanation. Without that key the evidence field is empty.
The returned message ID, matched field, excerpt and native URL use current
source authority. Reserved `usl_*` client context remains filtered by MCP. This
slice does not expand access to the abstract Home service.

## Refresh and recovery

Successful native message creation and edits enqueue derived maintenance in the
same transaction. The fixed scheduled action processes at most 500 messages per
minute. Ready text supports word matching in any order and substring matching.
While a changed message is pending, its old fragments are excluded and current
native contains matching remains available. A backlog or failed run can extend
the delay before reordered-word matches appear. Searching never runs a refresh.

The queue and fragments reference the original message with cascading deletion.
Publication and dequeue commit together; native request retry handles a write
that conflicts with publication. Refresh changes only derived storage, retaining
the original HTML, subject, attachments and history. A failed batch remains
queued. Normal upgrades queue missing projections; reinstall queues the source
set again. An authorized operator can use the private
`mail.message._usl_queue_task_messages(all_records=True)` helper in an Odoo shell
for an explicit rebuild. Direct SQL edits require explicit requeueing.

This is lexical task-conversation search. Activities keep their existing live
search behavior; transcripts, attachment contents, semantic
matching and linked-record traversal remain separate work. See
[task search](task-search.md) and [project/contact search](project-contact-search.md).
