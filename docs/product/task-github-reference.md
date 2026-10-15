# GitHub references on tasks

Use **GitHub URL** on a task to keep its canonical issue, pull request or
repository link. The form uses Odoo's native URL field: save the complete URL
and use its link control to open it. A populated task card shows a compact
**GitHub** link rather than the full address. Empty tasks do not show a card
link. Task access and company rules apply normally.

The field is `project.task.github_url`, a normal Char field available to views,
search and generic model APIs. Odoo's existing URL widget already supplies the
reusable presentation; this feature adds no parallel URL type or GitHub service.
It does not fetch GitHub metadata or synchronize issue state.

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

`usl_project` version `saas~19.3.1.0.13` supplies the new field and a versioned
post-migration. The same idempotent transition runs when the module is first
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
