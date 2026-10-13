# Task navigation action-risk review — 14 October 2026

Runtime discovery uses the canonical 164-module closure. It adds and removes no
action keys. Only `usl_project` and `usl_home` module identities change, and only
one normalized action digest changes: `rpc:project.milestone.action_view_tasks`.

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
