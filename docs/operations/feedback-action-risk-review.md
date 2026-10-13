# Product Feedback action-risk review

Feedback submission, polling, clarification, confirmation and withdrawal are
operational workflows. Polling can invoke Gemini; it is not a read-only status
request. Owner and state checks, run locking, stale-result rejection and native
task/chatter permissions remain mandatory. Neutralized databases refuse
provider processing from both polling and cron before reading credentials.

Uploaded attachments and the screenshot pointer must belong to the reporter's
current draft and the validated attachment set before copying, replacing or
deleting them. Linking another reporter's attachment or one from another draft
does not authorize elevated access. Run-history deletion remains protected.

The final native task description contains a server-owned deployment snapshot.
Provider text cannot supply or replace it; later clarification and batched
maintainer edits preserve each task's original snapshot. The release field and
description use the same validated SHA. Conflicting runtime sources remain
Unknown rather than claiming a false commit.

Existing project, task, stage, tag, activity and settings overrides retain their
prior policy consequences and central guards. New native inherited helpers are
classified only after exact implementation comparison, recorded per action.
UI/JS transports list their exact reviewed RPC delegates; navigation that
creates drafts or starts provider work is not classified as read-only.

Qualification covers the backend feedback suite, forged attachment relations,
neutralization, deployment-snapshot integrity and desktop/mobile journeys.
The HOOT runner must execute nonempty desktop and mobile suites; zero selected
tests is a qualification failure, not evidence that the frontend works.

## Provider phases and capture repair — 2026-09-06

PR #120 separates public source lookup, read-only Projects MCP lookup and
tool-free drafting. The reviewed delta adds five sinks, removes the old
`_build_payload` parameter-read sink and updates 26 existing action digests,
including the runtime `write` and connection-test entry points.

- `_phase_payload` reads connection parameters with elevation only for the
  validated MCP endpoint. Public source lookup receives only the validated
  release URL, not business chatter, images or credentials. The MCP phase
  selects four read tools; the existing read-only service identity remains
  the server-side authorization boundary.
- `_accept_phase` checks run state, task stage, cancellation and interaction
  identity before advancing. It bounds temporary evidence, redacts configured
  keys and leaves task changes to the existing reporter-context completion.
  The private submit/poll callers retain run locking and neutralization checks.
- The new `write` override clears temporary evidence at terminal states and
  delegates to native ORM access checks. It does not grant write access to
  reporters, maintainers, technical administrators or the MCP identity.
- The Settings diagnostic retains native settings authority, makes only the
  configured diagnostic requests, refuses neutralized databases and stores
  safe status text. Its new status-write sink is operational, not read-only.
- HTTP 429 polling retains the current interaction. Existing bounded retry,
  fallback, stale-result handling and withdrawal semantics remain in force.

The three new private sinks are `system_internal`; the run-write and Settings
status-write sinks are `operational`. Existing classifications, deletion
guards and Agent permissions are unchanged. Runtime identities for the two
changed public methods were captured from a disposable Feedback registry;
unrelated full-product registry facts were retained, not replaced by that
smaller fixture.

All 53 Feedback backend tests passed on clean installation and again after
module upgrade. The 508 repository tests passed with two environment-dependent
skips. These checks cover the exact tests named by the
`feedback-provider-phases-20260906` evidence family. This repair changes policy
records, not UI behavior, so existing screenshots remain illustrative evidence
of the original frontend revision. Full-product database and live multi-phase
provider qualification remain release gates; production was not changed.

## Quick capture and bounded navigation — 2026-10-13

The runtime discovery contains the same 164 modules and action keys. Only the
`usl_feedback` module identity moves. Twelve normalized action digests change;
the remaining changed entries carry shifted source line locations only.

The operational `feedback_start` entry and its two existing draft-creation and
parameter-read sinks now retain at most five untrusted action/model candidates
on the transient draft. The native browser sends technical identifiers only;
the transient draft can receive untrusted candidates containing extra keys.
The private `_validated_page_context` assignment and three metadata-read sinks
include the result of `_validated_navigation_context`: readable actions/models
are checked without elevation, malformed candidates and extra keys are omitted
before saving report/provider context. Opting out saves neither current-page
state nor navigation on the report; it does not prevent transient receipt.
Existing `sudo()` calls resolve validated model metadata; they do not grant
business-record access.

The private `_submit` three state-write sinks, `_poll` deadline-write sink and
`_accept_phase` transition-write sink receive a two-second first check, two-second
checks during the first thirty seconds, then ten-second checks. Native owner or
maintainer entry checks, the run lock, endpoint/configuration validation,
neutralization, retry backoff, five-minute timeout and stale-result rejection
remain enforced. These sinks retain `system_internal`; draft creation remains
`operational`. No classification or authority is widened.

The `feedback-capture-20261013` evidence family executes access/opt-out cases
and an offline provider that completes before the first poll. At one second
no request is made; at two seconds native processing advances to drafting.
The regression fails against the original ten-second scheduling. Long-call
checks retain a ten-second delay. Desktop and mobile HOOT exercise the native
palette, a preserved underlying modal, draft generation, clipboard capacity
and absence of mobile input refocus. Live provider latency is not measured by
these offline fixtures.
