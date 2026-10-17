# Live content search action review

The canonical runtime review adds three Home RPCs and five frontend transports.
It removes no existing action. The later native history correction changes only
the existing Home UI action digest for its unique route; no existing risk class
changes. The module closure remains the tracked 164 modules.

`search_records` accepts an internal caller, a text query and a finite business
category list. It uses native model/field/record read access and selected-company
scope. Related labels have their own authorized model query. Native User names
retain their public-identity authority; a denied User record cannot influence a
match through the raw-SQL portal helper. Search previews exclude credential and
binary values and read ordinary business text only after record access.

Native Properties schema reads use the existing accessor, with representative
parents derived only from the caller's permitted aggregate. Elevation remains
inside the native definition accessor; there is no elevated owner enumeration
or read of private Project names/business fields. Owner-bound property predicates
prevent another schema's ordinary label from enabling a credential property.

`search_documents` delegates to the existing governed Documents facade, including
its file-content authorization and explicit provider scope. It adds no archive
write or unrestricted binary request. Provider errors are returned as a local
unavailable state. Its preview window is bounded; continuation uses the native
Documents workspace with the current query.

`get_search_result_action` validates the fixed business-model catalogue and
rechecks native record/company access. It returns a normal form action with a
small context; it cannot execute an arbitrary client-selected server action or
carry stale project defaults into another record. Native form permissions still
control subsequent editing.

The five frontend entries forward only to these exact RPCs. They add no mutation,
messaging, filesystem write, credential read, production operation or new
provider integration. The existing Documents provider integration remains the
owner of its network reads. Search does not persist an index or run a cron job.

Automated evidence covers readable description/activity/property matches,
caller-denied related records, private schema authority, public identities,
selected companies, portal denial, stale destination access, readonly Accounting,
scoped OCR results, independent provider completion and native keyboard/opening.
Offline provider fixtures exercise transport and authorization; they do not
claim real archive availability or semantic model quality.


The Home UI route is `usl-home`, with unchanged action ID/tag/menu/default-user
references. It avoids the built-in native `home` redirect without replacing that
command. Native local/global query export stores only a string in hidden browser
history; both result services recompute current caller-scoped data on return.
No router override, custom persistent storage or query URL is added. The single
existing UI digest is reviewed separately from the eight new search entries;
all unrelated action digests remain unchanged.

## Bounded search follow-up (15 October 2026)

The performance follow-up adds one named Projects category-loader transport and
changes five existing digests: three Home search/paging transports, the Projects
opening transport and `search_records`. The category loader delegates only to
`rpc:usl.home.service.search_records`; the existing Projects opening transport now
delegates only to `rpc:usl.home.service.get_search_result_action`. Home retains
explicit static RPC targets while tracking and cancelling their native promises.
The search facade validates category exclusion before partitioning the same
caller-authorized catalogue. No additional model becomes searchable.

Authorized activity matching stays inside a native ORM subquery. Its ACLs and
record rules are unchanged; no raw-SQL authorization bypass or elevated activity
read is introduced. Identical property predicates combine their exact readable
owner sets. Different keys, selection meanings, types and excluded credential
schemas do not gain another owner's authority. Schema representatives still
come from the caller's permitted task aggregate.

Canonical fresh installation, reviewed optional-module removal, two upgrades and
runtime discovery use the exact 164-module closure. Only `usl_home` and
`usl_project` identities change. The surface contains 56,939 actions; all 255
protected actions and their guards retain their prior classifications. Compiled
protected and Agent-readonly derivatives are unchanged. Native regressions observe
bounded database fetches, shared-schema statement growth, schema meaning, batch
catalogue parity, partial results and cancellation. Neither a browser crash nor
cancellation of an already running server query is claimed by these checks.

### Scoped Task planner setting

The later full native-list qualification found PostgreSQL JIT compilation costly
on the synthetic broad search. Four thin Task execution wrappers retain native
`search_fetch`, `search_count`, `_read_group` and `_read_grouping_sets` delegation. Only domains containing
`usl_search_text` enter a private context manager. It reads `jit`, temporarily sets
that setting off transaction-locally, and restores the prior value on an open,
healthy transaction. An already-off setting needs no change. Failed SQL keeps
its original exception; the caller's rollback restores transaction-local state.
An ended transaction or closed cursor requires no cleanup SQL. The compiler
returns a pure domain, and Home uses the same virtual-field execution path.

The fixed SQL changes only a local planner setting. It changes no business row,
ACL, record rule, search domain or server configuration, uses no elevation and
adds no external effect. Runtime discovery additionally changes the existing
`rpc:project.task.search_count` digest, retaining its `read_only` classification;
`search_fetch` remains private and `_read_group` remains an internal method.
Native tests observe the setting at actual SQL execution and verify restoration,
negative/compound domains, grouping, nesting, caller-owned error rollback,
readonly cursors, ended transactions and pending ORM flushes. The original and
fixed full native payload return identical ordered IDs and exact counts with
JIT safely disabled in both comparison transactions. No global JIT change or
production benchmark is part of this work.

The fourth private Pivot wrapper also produces one conservative `orm_write`
AST sink because discovery matches `_set` inside `_read_grouping_sets`, the same
heuristic already reviewed on native Project/ORM implementations. Its exact key
is classified `system_internal`, consistent with those existing private helpers.
The body scopes only the local planner setting and delegates native aggregation;
it adds no public entry or business write. Existing parent classifications and
all protected/operational/recoverable classifications remain unchanged.
