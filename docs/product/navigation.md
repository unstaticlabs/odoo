# Navigation contract

The browser address is part of the user's workspace. A useful Odoo page must
survive refresh and browser Back/Forward without silently changing the records
the user was reviewing.

## Durable URL state

Stored window actions expose the following user-selected state in the URL:

- active search filters as a domain;
- grouping and list ordering;
- list page within a bounded interactive window;
- the native action, menu, record and view identifiers.

Changing any of those values adds a browser-history entry. Back and Forward
restore both the visible URL and the corresponding records. Starting a new
search returns a paginated list to its first page. Values are encoded once, so
ordinary text containing `%`, `+`, `&`, spaces, apostrophes or non-ASCII
characters remains usable after a refresh.

Only portable user choices belong in this state. Action domains, security
context, current selections, unsaved edits, scroll position and other
session-only UI state stay out of the URL. A domain reconstructed from a URL
uses Odoo's native **Shared** search facet; it preserves the result set without
claiming to recreate the exact original search-control labels.

Route offsets are accepted only from 0 through 10,000. A larger or malformed
offset is discarded before the first list query and the URL is canonicalized
to the first page. This keeps shared links useful without allowing a crafted
URL to force an arbitrarily expensive database offset. Page navigation beyond
that window remains available in the current session but is not written into a
shareable URL.

## Projects navigation

The **Projects** app name already opens the project overview, so the redundant
first **Projects** section is removed. The signed-in user's favorite active
projects sit after the primary task links and before the less-frequent
**Reporting** and **Configuration** sections.
Selecting a favorite opens that project directly. Favoriting or unfavoriting a
project refreshes the menu after the change is saved. Saving a project name,
archive status, sequence, template status, company or visibility change also
refreshes the menu, so an existing favorite cannot keep a stale label or remain
visible after it leaves the user's scope.

The menu includes at most 12 favorites, ordered by project sequence, name and
identifier. This keeps the tablet and mobile navigation payload bounded for
users with large portfolios. The Project overview remains the complete place
to browse and manage every favorite; this cap does not remove or change any
project preference.

Favorite entries are generated through the current user's ordinary
`project.project` search. Record rules, selected companies, archived records
and project-template filtering therefore apply before a name or destination is
sent to the browser. The implementation does not use `sudo` and does not add a
new access path.

On narrow desktop and tablet screens, favorite and native sections move into
Odoo's **More** menu as space runs out; mobile uses the application menu. When
favorites change while the number of hidden items stays the same, the **More**
menu now compares the actual section identities and refreshes immediately. The
previous count-only check could leave a removed favorite visible until another
resize. No horizontal page scrolling is required.

## Ownership and upgrade notes

Favorite-project entries and refresh behavior belong to `usl_project`. The URL
history behavior is a focused Web client patch because the action controller,
search model, list pager and list sorter must share one state lifecycle. Remove
that core patch if upstream Odoo adopts equivalent durable search, sort and
pagination routes.

The `usl_project` module version is bumped for the asset update. The change adds
no stored fields or persistent business data, so it needs no data migration and
is safe to roll back to the prior asset behavior.
