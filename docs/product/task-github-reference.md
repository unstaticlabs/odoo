# Task link

Use **Link** on a task to keep the main link to this work in another system: a
pull request, an issue, a document, a deploy run. The form uses Odoo's native
URL field: save the complete URL and use its link control to open it. A
populated task card shows a compact **Link** rather than the full address. Empty
tasks do not show a card link. Task access and company rules apply normally.
The feedback card shows the same field in its Delivery group.

The field is `project.task.github_url`, a normal Char field available to views,
search and generic model APIs. The technical name predates the generic label and
is kept so no column is renamed. Odoo's existing URL widget already supplies the
reusable presentation; this feature adds no parallel URL type or external
service. It does not fetch metadata, validate the address or synchronize state.

## Deprecated pull request field

The feedback card used to carry its own **Pull request** field,
`usl_feedback_pr_url`. It is deprecated: its values are copied into **Link**
when Link is empty (never over an existing Link) by the `usl_feedback` upgrade
to `saas~19.3.2.0.20`, and no screen or agent reads it any more. The column
stays in place for history.

## Existing GitHub properties

The module upgrade copies a value only when the task has no canonical URL and
exactly one populated Char property named GitHub contains an explicit HTTP(S)
URL for github.com or www.github.com. Invalid values, ambiguous properties and
existing canonical references are preserved without guessing or overwriting.
Archived tasks and projects are included. Repeating the transition is safe.

The task form presents the canonical field as the editable GitHub source. A
legacy value that exactly matches a verified canonical GitHub URL is hidden in
the form and card. Other legacy values stay visible as **Legacy GitHub**, read
only, so users can inspect or copy them before choosing a canonical reference.
Clearing or changing the canonical field reveals an unmatched legacy value.
Other properties, such as Sources, retain their normal editing behavior.

Legacy task JSON and project property definitions remain stored. This is a
presentation transition, not physical deletion: release preservation freezes
existing project rows. Retiring the old definition is a separate data operation.
The scoped task property widgets preserve the complete native property list
when another property changes, including legacy values hidden from rendering.
They do not change the generic Properties framework or restrict model API
permissions beyond native task access.

## Upgrade and maintenance

`usl_project` version `saas~19.3.1.0.13` supplied the field and a versioned
post-migration; version `saas~19.3.1.0.19` reruns it for values still unmigrated and logs the count. The same idempotent transition runs when the module is first
installed on an existing database. It writes only a missing `github_url`; it
never writes `task_properties` or `task_properties_definition`.

Odoo's Properties value has different representations: ORM record access is a
mapping, while native `read()` returns the definition/type/label/value list.
The migration uses batched native read format to identify the legacy field;
frontend value editing retains the complete list used by native updates. Tests
compare stored JSON directly so a display-only comparison cannot mask loss.

Qualify upgrades with synthetic old-shape records, conflicting canonical
references, invalid/empty properties and archived tasks. Check native task
read/write denial in another company and rerun the preservation gate. UI tests
must edit an unrelated property while the matched legacy value is hidden, then
verify that the saved list still contains that value.
