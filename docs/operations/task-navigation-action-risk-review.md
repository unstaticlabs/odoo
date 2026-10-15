# Task navigation action-risk review — 14 October 2026

Runtime discovery uses the canonical 164-module closure. It adds two action
keys: the Home activity client transport and the internal model-label elevation
sink. It removes no keys. Only `usl_project` and `usl_home` module identities
change. Three normalized digests change against the target: the milestone task
action, Home activity response and its existing internal elevation sink. Existing
classifications remain unchanged; the new client is `transport` to native
`mail.activity.action_open_document`, and the new sink is `system_internal`.

The override calls the native milestone action first, then adds its display
name and active model/identifier values to the returned action dictionary. The
native task domain, project/milestone creation defaults and single-task direct
form behavior remain intact. These assignments change an in-memory response,
not a persistent action or business record. Reads retain caller permissions;
there is no `sudo()`, record mutation or external effect. The entry retains its
existing `read_only` classification and no authority is widened.

Task IDs are rendered from existing readable records through an inherited
Kanban view. The title helper reads a milestone or project under the caller's
permissions and keeps the native fallback when it cannot read that record.
Home uses native `search_disable_custom_filters` context for explicit metric
choices: this suppresses automatic saved-favorite activation for that action,
while manual favorites and the ordinary My Tasks entry retain native behavior.
No security domain, record rule or company context is relaxed.

The `task-navigation-20261014` evidence family covers the returned milestone
identity and creation defaults, native single-task form behavior, the rendered
milestone board/list journey, actual Home metrics with a default saved search,
and agreement between metric counts and record domains. Reverting milestone
identity and Home metric precedence makes both regressions fail again;
restoring them passes the same cases. Live synthetic browser reload preserves
the requested record population through the native Shared domain facet.

Home activities read their translated generic model label with metadata-only
`sudo()`. Ordinary Project users cannot read `ir.model`; a fresh authenticated
HTTP request previously failed the entire widget at that label. Activity
summary, record name, due date and identifiers remain reads under the caller.
No business record is elevated and no permission is granted.

Native Odoo permits assignees to see their activity even when its linked record
is unreadable. Home retains that work and delegates opening to native
`mail.activity.action_open_document`: it returns the target when readable and
the native activity form otherwise. Direct access to the inaccessible target
still fails. A regression covers both destinations and the HTTP journey from
Home to My Activities. Summary already precedes Linked to in upstream Odoo;
this delivery verifies the existing order rather than adding another reorder.
