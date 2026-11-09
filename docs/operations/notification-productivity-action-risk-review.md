# Notification productivity review — 14 October 2026

People can change their own Email/In Odoo preference through the native form.
The existing private inverse receives an in-process object marker. Its backing
write may change exactly the current user's Inbox membership, with exactly the
native link/unlink command and no other fields. Serializable context values,
additional commands, another identity, and managed Agent identities receive no
exception. The marker group has no implied access roles. Native portal users
remain email-only. Existing capability validation and owner reconciliation run.

The native recipient helper reads message authors and technical identity markers
under sudo, as native notification delivery already reads recipient metadata.
It sets the effective channel to Inbox for internal Agent recipients and for
internal recipients of Agent-authored messages. External/portal recipients and
ordinary human-authored conversations retain their native email/preferences.
This is effective delivery policy: stored historical preferences, authorization
memberships and already queued email are not rewritten. Native message ACLs and
source-record access remain enforced. No upgrade rewrites persistent groups.

A separate in-process assignment marker wraps only native task assignment and
activity assignment notices. After channel routing, it removes Inbox recipients
from these duplicate notices. Email delivery, actual assignment, Activities,
ordinary conversations and audit records remain native. A forged RPC context
cannot suppress notices. No general notification off switch is introduced.

The verified projectless `todo` alias previously let native project mail add a
user whose email matched the alias. A copy of the gateway values omits To only
for this personal alias before native task creation. Original headers, explicit
alias assignees and unrelated project aliases remain intact. No service-account
name blacklist or external AI pipeline integration is added.

Runtime discovery used the exact 164-module clean-install closure. Six existing
action digests changed and three sinks were added, with no removals. The two
private identity-read sudo sinks are system internal; the additional native
user-write sink remains protected under `authorization.user.change`, subject to
the exact native self-preference exception above. Existing classifications stay
unchanged. Only `usl_access_control` and `usl_project` source identities move.
