# Task swimlanes

Use the existing **Group By** menu in a task Kanban board. The first grouping
sets the columns; the second creates horizontal lanes. For example, Stage and
Priority let you compare priorities across the same stages. Stage and Assignees
show each person's work across those stages.

Removing the second grouping returns to the ordinary Odoo board. My Tasks keeps
its personal stages. Filters, saved searches, company selection and the native
List view remain available. With more than two groupings, Kanban identifies that
it displays the first two; List supports deeper nesting.

## Reading the board

The column headers are shared across all lanes. Each cell shows native task
cards and its membership count. A task assigned to two people can appear in two
assignee lanes; these counts describe memberships rather than a unique-task
total. Unassigned groups retain their native identity. Unset selection values appear
as **None**, including in the Move controls.

“No tasks” means the loaded native groups prove the cell is empty. “Load lanes”
means that part of the grouping is not loaded yet. Closed cells have an explicit
Open action. The board does not turn an omitted page into a zero count.

Group pages are bounded, with continuation controls for columns and each
column's lanes. Task cards use native leaf pagination. Ordinary single-group
boards retain native behavior. Empty matrices show their real groups rather
than invented sample memberships.

If a saved two-axis search refers to a deleted or inaccessible grouping, the
board explains the problem and shows ordinary task results with the same
filters. Remove the unavailable grouping using the existing search controls;
no inaccessible field or property metadata is guessed.

## Moving and creating tasks

Drag a task between cells on desktop, or use **Move** for keyboard and mobile
access. A move changes both grouping values together through native task write
permissions. Moving one assignee or tag membership preserves the other
memberships. To make a task unassigned while it has other memberships, use the
task form; the board does not clear those memberships for you.

Date buckets, properties and read-only grouping values remain useful to read.
Changing an ambiguous value opens the native task form. Keeping such a lane
unchanged still allows a move along the other writable dimension. A stale card
or a denied destination reloads native state rather than overwriting new work.
Cross-cell drops change grouping values; native ordering determines the saved
position. Reordering inside a cell uses native resequencing.

Cell creation uses both exact native defaults when available. Existing leaf
cells can use native quick create; empty or ambiguous cells use the native task
form. The top New button opens the ordinary form. Creating from a column sets
that column's exact default and lets the user choose the lane.

## Implementation and qualification

The feature is scoped to USL Project's registered task view. It reuses native
Project cards, search, nested group data and task writes. It adds no persistent
lane model, preference storage, global core patch, permission grant or alternate
grouping menu. Related group labels are resolved only for returned group IDs
through caller-scoped reads; inaccessible labels recover as “Unavailable.”

A task move checks native access, locks the task, verifies its source
memberships, prepares both changes and performs one write. Empty expanded
groups cannot validate a forged source membership. Native constraints and
company rules remain authoritative. Unsupported changed dimensions recover in
the native form instead of guessing a date or replacing a property dictionary.
