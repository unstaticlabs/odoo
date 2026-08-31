# Conversational product feedback

## Product contract

Every authenticated internal user can open **Feedback** from the **Chats** tab
of Odoo's top-right messaging drawer. After capture, the drawer closes and a
native-style floating conversation opens in Odoo's ChatHub. Odoo first offers
a freshly captured image of the current screen, selected by default, then asks
the reporter to describe the issue or opportunity. The reporter may remove the
capture, add up to ten supporting files and opt in to sanitized page context.

The first message creates a partial `project.task` in **Inbox before any
provider call**. The floating conversation then uses native task chatter while
the Feedback Assistant asks up to three clarification questions and refines the
same card. It folds into the ChatHub like an ordinary chat, becomes fullscreen
on narrow screens and closes with the standard Escape/back behavior. The
reporter reviews the proposed brief and must explicitly choose
**Confirm and send to Triage**. Only that confirmation moves the card out of
Inbox. **Recent feedback** restores the reporter's saved conversations after a
reload or return to Odoo, while **New feedback** starts a separate Inbox card.

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

When selected, the capture is resized to at most 1920 pixels and encoded as
JPEG; every display-capture track is stopped immediately after the frame is
taken. Screenshots are limited to 5 MiB and other files to 10 MiB each. Draft
attachments are owned by a transient record and either move to the created task
or are deleted with the abandoned draft.

The company-wide assistant uses Google's Gemini Interactions API in background,
stored, stateful mode. Production enablement requires an administrator to
acknowledge the paid tier and Google's seven-day state retention, save a
server-side Gemini API key, select an approved Flash model, and save a second
API key for the dedicated read-only Odoo service identity. Saved secrets are
never returned to the browser.

Each turn contains the bounded task conversation, a bounded summary of open
feedback, the selected screenshot on the first turn, and a release-pinned
public source link of the form
`https://github.com/unstaticlabs/odoo/tree/<release-sha>`. Gemini may use URL
context and the configured HTTPS endpoint ending exactly in `/mcp/projects`.
Prompt and tool content are treated as untrusted. Structured output is
validated before a narrow privileged update changes the same task. Audit runs
retain identifiers, timestamps, model, hashes, token counts and safe error
codes—not prompts, keys, screenshots, provider reasoning or raw responses.

Failures leave the partial card in Inbox. Transient provider failures retry
with a bounded delay; permanent failures show a safe in-Odoo retry state. No
provider error may disclose credentials or response bodies. Task notifications
remain inside Odoo: feedback chatter suppresses outgoing email delivery.

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
