# Read a project's team notes

Open a project's **Notes** tab to read its Markdown files. Choose another note
from the list or follow a link in the text. **Open in Obsidian** opens the same
file in a copy of the team vault on your device. To update the text, edit and
sync it through the team's usual Git workflow. **Refresh notes** reads the
current checkout. The tab also refreshes when it becomes visible again.
The desktop or mobile app opens notes using the
[Obsidian URI protocol](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI).

## Connect a team vault

Use a separate Git checkout intended for team projects. Configure its existing
sync process to check out the approved branch, then mount the checkout read-only
at the same location in all Odoo workers. Set `USL_PROJECT_NOTES_VAULT` to that
absolute directory. Set `USL_PROJECT_NOTES_NAME` to the name that teammates use
for the vault in Obsidian. It defaults to the checkout directory name.
The checkout belongs to the database named by `ODOO_DB_NAME`. Set
`USL_PROJECT_NOTES_DATABASE` explicitly on a multi-database server. Other
databases on that server keep their own empty Notes tab.

Within the vault use `Projects/<Odoo project ID>/`, with one Markdown file per
note. For example, project 42 can have `Projects/42/README.md` and
`Projects/42/Decisions/Review.md`. A README or index opens first. Use
`[[Decisions/Review|Review checklist]]` or `[Review checklist](Decisions/Review.md)`
to link notes in that project. A bare filename also resolves if it identifies
one note. Headings, tables, lists and inline code render in Odoo. Images and
embeds appear as text. Use Obsidian for the complete vault rendering or for
a note over 4 MiB.

Odoo reads the project under the caller's normal permissions for each list or
note request. A person or delegated Agent can call
`project.project.get_team_notes(path=None)` on that project. Odoo returns paths,
the selected note's rendered HTML and its content digest. Source files and Git
history stay in the team checkout. Device and Git access to a separately held
copy of the vault remain under the team's own access controls.
