# Task GitHub reference action review

The full runtime discovery used the exact 164-module distribution registry.
It adds one private ORM write sink in
`project.task._usl_migrate_github_references`. No existing action key was removed,
no existing action digest changed, and no existing classification changed.
Only the `usl_project` source identity changes.

The helper runs from module installation and the version 13 post-migration.
It has no ordinary RPC or controller entry. It copies one unambiguous valid
legacy GitHub URL into an empty canonical field, including archived records.
It never overwrites a canonical URL, deletes legacy task JSON or rewrites a
project property definition. It introduces no external-provider call.

Normal users edit the canonical Char field under native task ACLs and company
rules. The form and Kanban use the native URL widget. Legacy presentation is
readonly, with matching duplicates hidden; this does not impose a new API
restriction on task properties. Other-property edits retain the full native
properties list.

Evidence includes the clean-install Project suite (41 methods), desktop and
mobile HOOT (15 each), four authenticated native tours, and narrow reverted /
restored proofs for the field, migration and legacy presentation. A real upgrade
copied two eligible URLs while preserving all four original raw legacy rows and
project definitions. The preservation gate and repeated upgrade passed.
