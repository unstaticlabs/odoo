# Interaction Affordances

## Cursor contract

The Accounting product follows familiar desktop interaction conventions:

- links, enabled buttons, menu items and other semantic actions use the pointer;
- clickable list cells and expandable group labels use the pointer;
- disabled actions use the not-allowed cursor;
- editable text keeps the text cursor;
- drag handles and column resize handles keep their direct-manipulation
  cursors;
- non-clickable totals, labels and read-only presentation remain neutral.

The convention is implemented once in the isolated
`rebuild_account_migration` backend asset. New custom components should use
native semantic elements (`a`, `button`) or the appropriate ARIA role instead
of adding screen-specific cursor rules. The shared rule intentionally takes
precedence over neutral component defaults; explicit disabled, text-entry,
drag, resize, edit-row and sample-data states then restore their more specific
cursor.

## Accessibility boundary

A pointer is only a visual affordance. Every action must still expose a
semantic role, accessible name, keyboard activation and visible focus
treatment. Disabled state must use the native `disabled` attribute or
`aria-disabled="true"` where a native control is not possible.

The stylesheet must not label ordinary text, non-open rows or passive cards as
clickable. Cursor changes never replace authorization checks or Odoo's action
handling.
