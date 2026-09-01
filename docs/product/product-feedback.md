# Conversational product feedback

## Product contract

Every authenticated internal user can open **Feedback** beside **New Message**
from every tab of Odoo's top-right messaging drawer. The drawer closes and a
native-style floating conversation opens immediately in Odoo's ChatHub. Odoo
prepares a local preview of the visible Odoo tab, selected by default, while
asking what the reporter wants to improve. The reporter may clear **Include
this page preview**, add up to ten supporting files and opt in to **Share page
details**.

**Send feedback** creates a partial `project.task` in **Inbox before any
provider call**. The floating conversation then uses native task chatter while
the Feedback Assistant asks one focused question per turn and updates the same
card. It folds into the ChatHub like an ordinary chat, becomes fullscreen on
narrow screens and closes with the standard Escape/back behavior. The reporter
reviews the result, chooses **Add details** when needed, then selects **Send to
product team**. Only that action moves the card to Triage. **Your feedback**
restores saved conversations after a reload or return to Odoo, while **New
feedback** starts a separate Inbox card.

The canonical **Odoo Product Feedback** Project remains ordinary Odoo Project
data. Its native stages are **Inbox**, **Triage**, **Shaping**, **Build**,
**Review**, **Release** and **Icebox**; its native tags are **Bug**,
**Improvement**, **Question** and **UX**. This matches the team's established
product-delivery board and lets separately governed AI Pipelines use standard
Project models without an Odoo-to-GitHub write bridge.

## Collaboration and access model

Product feedback is a shared internal board: internal employees can read and
discuss all cards, chatter and attachments, while the original reporter is
recorded on every card. Ordinary employees cannot edit canonical card fields,
move stages, schedule or mutate activities, delete cards, change followers for
another person, or create a feedback task through generic Project RPCs.
The governed Project, workflow stages and category tags carry explicit markers
and model-level guards, so generic Project-manager RPCs and imports cannot
rewrite them without the Feedback Maintainer capability.

Members of the explicit **Feedback Maintainer** capability can operate the
whole board. The separate **Feedback Agent (read-only service)** capability is
for one dedicated non-human external identity used by the remote Projects MCP.
It can read only the governed feedback Project, cards, stages, tags,
attachments and activities. It has no create, write, delete or chatter rights,
cannot inspect unrelated Projects, and cannot simultaneously be an Internal
User or Feedback Maintainer. Gemini receives that identity's Odoo API key only
as the remote MCP authorization header.

Feedback tasks are deliberately company-neutral so the shared product board is
consistent across company switching. Typed `usl_feedback_company_id` records
the source company after verifying it belongs to the reporter. No source
company business record becomes readable through the task, and negative tests
cover users with disjoint company access.

## Context, evidence and provider boundary

Page context is off by default. The browser constructs only typed candidates
for the action, model, record identifier and viewport. The server retains the
model and identifier only when the reporter can still read that record. The
company and exact 40-character release SHA are resolved server-side. URLs,
queries, fragments, tokens, local storage, form contents and arbitrary browser
state are never collected.

The page preview reproduces only the current Odoo tab. It includes the navbar,
active view and ordinary Odoo dialogs, while excluding the messages drawer,
floating chats, Feedback controls, toast alerts, password fields and elements
marked private. External media and inaccessible frames render as empty areas.
Odoo sends only the resulting JPEG—not HTML or browser state—and keeps it on
the device until the reporter selects **Send feedback**. The image is resized
to at most 1920 pixels and limited to 5 MiB; other files are limited to 10 MiB
each. Draft attachments are owned by a transient record and either move to the
created task or are deleted with the abandoned draft.

The company-wide assistant uses Google's Gemini Interactions API in background,
stored, stateful mode for the conversation. Because Gemini's background agent
currently rejects inline images, the selected JPEG first goes through a bounded,
non-stored Gemini 3.5 Flash-Lite visual analysis. Only that analysis enters the
stateful conversation. If visual analysis is unavailable, the saved card still
continues through the text conversation. Production enablement requires an
administrator to acknowledge the paid tier and Google's seven-day state
retention, save a server-side Gemini API key and select an approved Flash
model. Saved secrets are never returned to the browser.

Projects MCP is optional enrichment. Without it, Gemini still receives the
conversation, selected page preview, sanitized page details, release-pinned
source link and Odoo's bounded summary of current feedback. When an
administrator also configures the exact Projects MCP URL and the dedicated
read-only service identity's API key, Gemini may inspect relevant existing
cards for better duplicate detection. Missing or partial MCP settings never
block feedback submission or clarification.

Each turn contains the bounded task conversation, a bounded summary of open
feedback, the selected page preview on the first turn, and a release-pinned
public source link of the form
`https://github.com/unstaticlabs/odoo/tree/<release-sha>`. Gemini may use URL
context and, when configured, the HTTPS endpoint ending exactly in
`/mcp/projects`.
Prompt and tool content are treated as untrusted. Structured output is
validated before a narrow privileged update changes the same task. Audit runs
retain identifiers, timestamps, model, hashes, token counts and safe error
codes—not prompts, keys, screenshots, provider reasoning or raw responses.

Failures leave the partial card in Inbox. Transient provider failures retry
with a bounded delay. If the background interaction still fails, one bounded,
non-stored Gemini 3.5 Flash-Lite request completes the turn from the full
sanitized chatter without URL or MCP tools. If that request also fails, Odoo
states that the feedback is saved and offers **Try again**. No provider error
may disclose credentials or response bodies. Task notifications remain inside
Odoo: feedback chatter suppresses outgoing email delivery.

## Architecture decision

The selected path patches Odoo's native `MessagingMenu` only to add the
launcher, then extends `ChatHub` with a module-owned floating window that embeds
native `Chatter` against the canonical task. It follows Odoo's normal chat
launch, fold, close, focus, desktop positioning and mobile fullscreen patterns,
preserves the current action behind the conversation, and avoids a second
ticket/conversation model.

Stock `mail.ChatWindow` was also evaluated. It is hard-wired to a
`discuss.channel`, so using it directly would create a duplicate conversation
beside the task chatter. A standalone Project form or user-menu wizard exposes
implementation fields too early and cannot sustain clarification without
leaving the reporting context. The OCA/helpdesk alternative introduces a
second canonical record and stage system. The ChatHub extension preserves the
native interaction while keeping `project.task` and its chatter canonical.

For visual evidence, `usl_feedback` owns a licensed copy of the DOM-to-image
renderer already shipped by Odoo Point of Sale. Rendering `.o_web_client`
locally avoids browser screen-sharing permission and cannot capture another
tab, application, display or browser chrome. Depending on Point of Sale only
for this utility would broaden the product graph; importing it without that
dependency would make asset loading invalid. A server-side browser would also
lose the reporter's live session state. The isolated local renderer therefore
fits the product boundary and degrades to manual file attachment when a page
cannot be reproduced.

## Installation, upgrade and recovery

Install `usl_feedback` after its declared `base_setup`, `project`, `mail` and
`web` dependencies. A normal update is:

```text
-u usl_feedback
```

Version `saas~19.3.2.0.0` adds typed task metadata, the transient draft and
assistant-run tables, a minute cron, settings, security records and frontend
assets. Its idempotent post-migration keeps existing XML identifiers while
renaming the former stages to the new workflow, makes the feedback Project and
tasks company-neutral, backfills source company metadata, removes obsolete
private-boundary rules and removes the old standalone submission action. It
does not move existing cards between workflow positions.

The private page-preview refinement changes frontend assets only. It adds no
model, field or data migration. Upgrade with the same `-u usl_feedback`
command, verify the preview journey, then repeat the upgrade to prove identical
behavior. Recovery is to restore the previous module code and upgrade
`usl_feedback`; existing tasks, chatter and attachments remain valid.

Version `saas~19.3.2.0.3` routes selected previews through the bounded Gemini
vision pass before the stateful text interaction and replaces the generic task
creation log with a direct feedback-record link. It adds no field or data
migration. Recovery is to restore the previous module code and upgrade
`usl_feedback`; saved cards and attachments remain valid.

Version `saas~19.3.2.0.4` adds the non-stored structured completion fallback
for exhausted background-agent failures. It adds no field or data migration.
Recovery is to restore the previous module code and upgrade `usl_feedback`;
saved cards, conversations and provider audit runs remain valid.

Before production upgrade, take a consistent database and filestore backup.
Afterward verify the seven governed stages, company-neutral cards with retained
source company, inactive legacy rules, secret-status indicators, read-only
service denial, one synthetic conversation and a repeated identical update.
Keep both regulatory live flags at `0` during qualification.

For provider trouble, disable the assistant or remove either key; saved Inbox
cards remain available and no schema rollback is required. For a failed module
upgrade, stop the new workers and restore the matched database and filestore
backup with the prior image. Removing code alone is not a rollback once stored
fields, tasks or attachments exist. No migration source dump or production
provider activation participates in recovery.
