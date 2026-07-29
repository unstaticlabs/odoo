# Canonical navigation state

Last updated: 2026-07-29

## Product invariant

The visible backend URL is the portable source of truth for the meaningful
workspace. Opening the same canonical URL recreates the same business context
for every authorized user, subject to that user's companies, model access and
record rules. Browser history may add transient state, but neither history
state nor session storage may be required to interpret a canonical URL.

The contract is URL-first, not URL-everything:

- business-visible, portable state is encoded in the URL;
- small safe selections may be encoded directly;
- large, sensitive or complex state uses an access-controlled durable
  workspace reference;
- scroll, focus, unfinished input and other transient presentation state stays
  in the current history entry;
- secrets, unrestricted contexts, unsaved values and private contents are
  never serialized.

## Current Odoo 19 state ownership

The fork started this work at `e9c1248f0f2`, based on upstream
`8a44ecc8da96e341ac472fec27352d138ed2edd7`.

| State | Current owner | Current restoration boundary |
| --- | --- | --- |
| Action, model, record, parent record and view type | `/odoo/...` path plus the action service controller stack | Portable when the path contains enough information |
| Debug and language | URL query | Portable |
| Company scope | `cids` browser cookie and user context | Shared between tabs; not represented by the ordinary workspace URL |
| Search query, filters, grouping and ordering | `SearchModel`; serialized as hidden `globalState` during controller transitions | Browser history and copied session storage only |
| Shared search link | `SearchModel.generateQueryString()` and the special **Share** command | A different URL is manufactured only when the command is invoked |
| Search panel presentation | hidden `globalState` | Browser history/session only |
| List/kanban data position and selection | relational model/controller state | Mounted controller or browser history only |
| Optional list columns | browser local storage keyed by view | Browser profile only; leaks between tabs with the same view |
| Form notebook page | form controller local state | Browser history only |
| Pivot, graph and calendar configuration | individual model/controller local state | Browser history only |
| Custom interactive accounting report filters and folding | transient report wizard and client-action local state | Mounted controller/history; the URL contains only the transient wizard record |
| Breadcrumb display names | session/controller cache and permission-aware breadcrumb RPC | Current session |

`action_service.js` deliberately calls `router.hideKeyFromUrl("globalState")`.
It saves `current_action` and `current_state` in session storage and may reuse
them when reconstructing a controller. This is useful as a same-tab
optimization but cannot be the portable contract. `router.js` stores
`nextState` inside History API entries; a copied address-bar URL cannot recover
hidden values from those entries.

## Version 1 URL contract

Canonical query parameters use stable field names, model names, action XML IDs
or durable server IDs. They never contain component IDs, complete controller
objects or serialized `globalState`.

The canonical parameter order is:

1. `nv`
2. `cids`
3. `ws`
4. `view_type`
5. collection search and layout state
6. result position and selection
7. parent collection state for a record
8. report state
9. `lang`
10. `debug`
11. unknown backward-compatible parameters in lexical order

`nv=1` identifies this contract. Existing valid URLs without `nv` remain valid
and are normalized after successful loading.

### Standard action and record state

Odoo's existing readable path remains authoritative:

```text
/odoo/<action-or-model>/<record>
```

The query may add:

| Parameter | Meaning |
| --- | --- |
| `cids` | Explicit active company IDs, active company first and remaining IDs sorted |
| `view_type` | Non-default list, kanban, pivot, graph, calendar, activity or form view |
| `tab` | Stable form page name for the active business-relevant notebook page |

When a form is opened from a configured collection, the collection parameters
remain on the record route while the standard action/active-parent path keeps
the business parent context. Browser Back therefore targets the exact visible
collection URL instead of a hidden controller snapshot. `parent_` parameters
are reserved for custom embedded actions that need a second collection scope;
they use the same semantic field names and are ignored by actions that do not
support such nesting.

### Collection state

| Parameter | Meaning |
| --- | --- |
| `domain` | Normalized Odoo domain representing committed filters and search terms |
| `groupBy` | Ordered JSON array of stable field/group interval names |
| `orderBy` | Ordered JSON array of `{name, asc}` terms |
| `favorite` | Durable `ir.filters` ID when a saved favorite is active; the semantic domain remains the fallback |
| `panel` | Search-panel choices keyed by stable field name |
| `columns` | Sorted comma-separated optional field names that are visible (`-` means none) |
| `offset` | Result offset |
| `limit` | Result page size when it differs from the view default |
| `selection` | Sorted comma-separated IDs for a bounded safe selection |
| `active` | Active record inside the current result set when no form path represents it |
| `date` / `scale` | Calendar focus date and scale |
| `measures`, `rows`, `columnsBy`, `pivot_order` | Pivot axes, measures and semantic column ordering |
| `graph`, `stacked`, `cumulated` | Graph configuration |

Filter domains are normalized deterministically. Order is preserved where it
changes meaning, such as grouping priority and sort precedence. Set-like IDs
and field lists are unique and sorted.

Small direct selections are bounded by both record count and URL length. A
domain-wide selection is stored as a permission-checked `selection_mode` inside
a durable workspace; it is never expanded into the visible query. A larger
selection, confidential filter, or any state that would exceed the URL budget
is likewise represented by `ws`.

### Accounting report state

Interactive reports use their stable report type and semantic option names:

```text
report, company, period, anchor, date_from, date_to, moves,
comparison, comparison_from, comparison_to, group,
journals, accounts, partners, analytic_plans, analytics,
search, collapsed
```

The transient calculation wizard ID is not canonical. Legacy wizard routes
continue to load and are normalized after the report options have been
validated. Fold state uses stable report group keys rather than preview-line
record IDs.

### Durable workspace state

`ws=<uuid>` refers to a persistent workspace record. The record stores the same
versioned semantic schema used by expanded URLs, never a client controller
snapshot. It has:

- an owner;
- explicit permitted users and an optional internal-user sharing mode;
- company scope;
- target model/action/report identity;
- creation and last-use timestamps;
- an immutable public UUID distinct from its database ID;
- a versioned semantic state document.

Normal Odoo access checks, company rules and target-model record rules are
reapplied on every restoration. Workspace access never grants access to its
target data. Missing, revoked or partially inaccessible workspaces fail closed
with an explanation and an accessible recovery action.

## History policy

| Change | History operation |
| --- | --- |
| Open/close a record, change view, commit filters/grouping/sort, change result page, commit report period/comparison/options | Push |
| Selection changes, visible columns, form tab, report fold state, canonical cleanup | Replace |
| Scroll, focus, hovered/floating UI, unfinished search text, dimensions | Current history entry only |

The router must flush a meaningful state change synchronously before an action
transition. Back restores the complete prior collection URL; Forward restores
the subsequent record or workspace. Restoration never writes a duplicate
history entry.

## Permission and failure policy

- Requested companies are validated before the target action loads. An
  inaccessible company blocks restoration; it never falls back to the user's
  broader cookie scope.
- Record, model and workspace access is checked using the current user's normal
  environment. No workspace payload is read with `sudo`.
- Missing selected records, invalid filters, stale report options and removed
  actions never activate defaults that broaden the result. The client either
  applies a meaning-preserving permitted subset or uses an empty safe domain
  and explains what could not be restored.
- Error UI does not interpolate inaccessible record names, counts, companies or
  filter values.
- Recovery offers the accessible parent action or the user's home action.

## Compatibility and implementation decision

Three approaches were compared:

1. keep upstream Odoo's hidden `globalState` and improve the special Share
   command;
2. replace routing with a parallel single-page application;
3. extend Odoo's router, action/search/view hooks and report client actions with
   a versioned semantic URL layer, adding a small server model only for durable
   references and link generation.

Option 3 is used. Option 1 cannot restore an address-bar URL in a closed or
independent tab. Option 2 would fork actions, breadcrumbs, forms, security and
mobile behavior. The semantic extension keeps standard `/odoo` paths, legacy
`/web` hashes, menus, favorites and controller lifecycle.

The latest public Odoo 19 branch was inspected through
`upstream/19.0` at `fd9c4dc83c6`, and public master was inspected at
`c8044051cfd`. Relevant later fixes for dynamic form-action restoration,
virtual controller switching and oversized History API values are already in
the approved baseline or were retained where compatible. Upstream still hides
`globalState` and does not make normal URLs canonical.

The maintained OCA `web` 18.0 add-on catalogue and
`account-financial-reporting` 18.0 were also inspected. They provide useful
view and accounting-report extensions but no general, maintained semantic
navigation/workspace layer to reuse. The fork therefore implements this as an
isolated custom add-on and keeps accounting option handling in the existing
Community report engine.
