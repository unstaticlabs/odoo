# Canonical navigation QA guide

This guide explains how to test the canonical-navigation changes as a user.
Use synthetic QA data only. Keep live e-invoicing and e-reporting disabled.

## The rule to keep in mind

The address bar describes the workspace that is currently on screen.

- Browser **Back** and **Forward** must restore the workspace attached to that
  history entry.
- Reloading or copying the visible URL must reopen the same workspace.
- Moving normally to a different menu or application must start with that
  destination's own defaults. Filters from the screen being left must not
  follow the user.
- Switching between list and kanban inside the same action may keep the same
  search because it is still the same business workspace.

## 1. The cross-menu filter regression

This is the quickest smoke test for the reported defect.

1. Open **Customers** or another list with several records.
2. Apply a filter and add a group, for example **Companies** grouped by
   **Country**.
3. Notice that the filter and group are visible in the address-bar URL.
4. Use the application menu to open **Expenses** or another unrelated list.

Expected:

- Expenses opens with its own normal default filters.
- The customer filter, grouping, columns, page and selection are absent from
  the Expenses URL.
- No red outline, red warning, or “workspace could not be restored” filter is
  shown.

Now press browser **Back**.

Expected:

- Customers returns with the original filter and group.
- Its URL is the same configured URL seen before leaving.

Press browser **Forward**.

Expected:

- Expenses returns with only its own defaults.
- The customer state still does not appear.

## 2. List-to-record continuity

Use a list with enough records to have more than one result page.

1. Apply two filters and commit the search.
2. Add one or more groups.
3. Sort a column.
4. Hide one optional column and show another.
5. Move to a later page.
6. Select two or three visible records.
7. Open one record.
8. Press browser **Back**.

Expected:

- The same filters, groups, sort, visible columns, page and still-valid
  selection return.
- The address bar already contains the configured workspace; no Share command
  is needed.

Press browser **Forward**. The record and its action context should return.

## 3. Reload and ordinary URL copying

1. Configure a list as in the previous test.
2. Reload the page, then perform a hard reload.
3. Copy the URL directly from the address bar.
4. Open it in a new tab.
5. Close the original tab and open the copied URL once more.

Expected:

- Each load recreates the same meaningful workspace.
- Restoration does not depend on the original tab or its session storage.
- Two tabs can then be changed independently without rewriting each other's
  filters or company scope.

## 4. View switching

1. Configure a list filter.
2. Switch from list to kanban and back using the view switcher.

Expected:

- The committed search remains because both views belong to the same action.
- The chosen view type is visible in the URL and survives reload.
- Browser Back and Forward move through committed view changes naturally.

Then open a different menu or application. The old search must not cross that
action boundary.

## 5. Company context

Use test companies and users only.

1. Open the same list in two tabs.
2. Select a different allowed company in each tab.
3. Apply a different filter in each tab.
4. Reload both tabs and move Back and Forward in each.

Expected:

- Each URL contains its explicit company scope.
- Each tab keeps its own company and filter.
- One tab never silently adopts the other tab's company.

For a restricted-user check, open a URL containing a company the user cannot
access.

Expected:

- The workspace is blocked with a generic, useful explanation.
- No inaccessible company, record name, count, or filter value is revealed.
- The result is never broadened to another company or an unfiltered dataset.

## 6. Accounting reports

Open a report implemented by the Community accounting add-ons, such as the
Balance Sheet or Profit and Loss.

1. Choose a company.
2. Set a custom period.
3. Add a comparison period.
4. Change available posting, journal, account, partner, analytic or grouping
   options.
5. Fold or unfold a stable report section.
6. Copy the visible address-bar URL into another tab.

Expected:

- The other tab shows the same report type, period, comparison and supported
  filters.
- The URL contains readable semantic options, not a temporary wizard record
  or a complete internal controller object.
- Supported fold state survives without exposing report values in the URL.
- The downloaded report uses the same configured options.

## 7. Small and large selections

For a small selection, select a few safe records and use Back, reload and a
copied URL.

Expected:

- The selected IDs are restored when they remain accessible.
- The URL stays reasonably short.

For a domain-wide, large or sensitive selection, use the product's saved
workspace flow.

Expected:

- The URL uses a durable workspace reference instead of a giant list of IDs.
- A user without permission receives the generic unavailable screen.
- Losing access to the saved workspace never falls back to an unfiltered list.

## 8. Invalid, stale and legacy links

Try representative legacy links:

- an existing `/odoo/...` bookmark;
- a `/web#...` action, model or record link;
- a normal record link from chatter, email or a document.

Expected:

- Valid links still open and may safely normalize to the canonical form.

In disposable data, also try a copied link after deleting its target record,
and a deliberately malformed filter or missing workspace reference.

Expected:

- Odoo does not crash or jump to an unrelated home screen.
- It does not silently show a broader dataset.
- The user gets a generic recovery explanation and a safe route back.

## 9. Mobile and responsive smoke

Repeat the cross-menu regression, Back/Forward, reload and copied-link tests at
a narrow mobile viewport.

Expected:

- Navigation remains usable.
- The same URL semantics apply.
- Mobile's intentionally hidden row checkboxes do not erase the portable
  selection.
- `/scoped_app` use inside the installed app remains compatible, while copied
  links use the equivalent `/odoo` route.

## What to include in a defect report

Include:

- the URL before the action;
- the URL after the action;
- the source and destination menu names;
- whether the action was a normal menu click, view switch, Back, Forward or
  reload;
- the expected filter, group, company, page and selection;
- a screenshot of any warning or unexpected search chip;
- whether the problem also occurs in a newly opened tab.

Do not include production data, private report values, access tokens or
confidential document contents.
