---
name: USL Home
description: A quiet Odoo-native daily routing surface for action, continuation, and exception review.
colors:
  canvas: "var(--o-gray-100, #f8f9fa)"
  surface: "var(--o-view-background-color, #fff)"
  border: "var(--border-color, #dee2e6)"
  text: "var(--o-main-text-color, #212529)"
  muted: "var(--o-gray-600, #6c757d)"
  brand: "var(--o-brand-primary, #714b67)"
  action: "var(--o-action, #017e84)"
  danger: "var(--o-danger, #dc3545)"
  warning: "#9a6700"
  success: "var(--o-success, #198754)"
  soft: "color-mix(in srgb, var(--o-gray-100, #f8f9fa) 82%, var(--o-view-background-color, #fff))"
  skeleton: "var(--o-gray-200, #e9ecef)"
typography:
  title:
    fontFamily: "inherit"
    fontSize: "1.75rem"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "inherit"
    fontSize: "1.08rem"
    fontWeight: 650
    lineHeight: 1.2
  body:
    fontFamily: "inherit"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "inherit"
    fontSize: "0.78rem"
    fontWeight: 600
    lineHeight: 1.25
rounded:
  skeleton: "8px"
  control-panel: "12px"
  widget: "14px"
spacing:
  xxs: "0.25rem"
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.25rem"
  xl: "2rem"
components:
  widget-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.widget}"
    padding: "1rem"
  accounting-alert:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control-panel}"
    padding: "0.75rem"
  attention-row:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    padding: "0 1rem"
  status-chip:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.text}"
    rounded: "{rounded.skeleton}"
    padding: "0.25rem 0.5rem"
  favorite-destination:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    padding: "0"
  skeleton-row:
    backgroundColor: "{colors.skeleton}"
    rounded: "{rounded.skeleton}"
    height: "3.25rem"
---

# Design System: USL Home

## Overview

**Creative North Star: "The Quiet Routing Desk"**

USL Home is a calm daily operating surface inside the native Odoo 19 shell. It behaves like a well-arranged desk: current obligations are easy to scan, exact working destinations are easy to resume, and human-review or accounting exceptions remain visible without turning Home into a dashboard product.

The surface inherits Odoo and Bootstrap typography, controls, navbar behavior, icon set, semantic variables, and action language. Its own visual contribution is deliberately restrained: a light canvas, content-sized white cards, fine borders, compact operational copy, softly backed metadata and icons, and a small amount of semantic color reserved for state. Customization is available but visually secondary to action and continuation.

**Key Characteristics:**

- Native Odoo shell and control vocabulary.
- Activities and My Tasks lead the daily scan.
- Flat, bordered cards with compact, content-sized interiors.
- Exact destinations and exceptions are presented as dense direct action rows.
- Modernized icons clarify widget purpose, destination kind, and exception class without becoming ornament.
- Semantic color communicates urgency or completion; it does not decorate.
- Customization changes visibility and order without creating a separate dashboard-builder world.

## Colors

The palette is the active Odoo theme expressed through local semantic aliases, with neutral surfaces carrying most of the interface and status colors used sparingly.

### Primary

- **Odoo Brand:** The inherited brand color is used for the three-pixel keyboard focus outline and native primary controls.

### Secondary

- **Operational Action:** The inherited action color marks actionable hover states on favorite destinations and accounting alert tiles.

### Tertiary

- **Exception Red:** Reserved for overdue, failed, blocked, and load-error states.
- **Due-Today Amber:** Reserved for activities due today.
- **Clear-State Green:** Used by check-circle icons when no work needs attention.

### Neutral

- **Working Canvas:** The light Odoo gray behind all cards.
- **Operational Surface:** The inherited view background used by cards and the customization panel.
- **Hairline Border:** The inherited border color used to group cards, rows, signal cells, and alert tiles.
- **Primary Text:** The inherited main text color for headings, values, and actions.
- **Muted Metadata:** The inherited medium gray for company scope, descriptions, secondary lines, disabled destinations, and empty states.
- **Soft Operational Fill:** A live mix of the inherited light-gray canvas and view surface behind icons, status chips, count pills, signal strips, and neutral hover states.
- **Skeleton Gray:** The two adjacent Odoo grays used by the loading shimmer.

### Named Rules

**The Semantic-Only Color Rule.** Status colors report urgency, failure, blockage, or completion; neutral surfaces carry everything else.

**The Theme-Inheritance Rule.** Use Odoo CSS variables with safe fallbacks so Home follows the active backend theme instead of establishing a competing palette.

## Typography

**Display Font:** None; Home does not use marketing-scale display type.
**Body Font:** The inherited Odoo system sans-serif stack.
**Heading Font:** The inherited Odoo heading stack, preferring SF Pro Display where available.

**Character:** Compact, plainspoken, and operational. Hierarchy comes from restrained weight and size shifts rather than oversized typography, decorative faces, or all-caps labels.

### Hierarchy

- **Location:** The native Odoo navbar owns the Home title; the cockpit does not repeat it inside the page.
- **Headline** (650, compact): Widget titles and the customization heading.
- **Body** (400, native Odoo body scale): Actions, row titles, explanatory copy, and configuration labels.
- **Label** (600, compact): Timing, pipeline status, and small operational counts; tabular numerals are used for task signals and alert counts.

### Named Rules

**The No-Hero Rule.** Home is an Operate surface: its title identifies the place and never expands into a promotional hero.

**The One-Line Scan Rule.** Row titles and metadata truncate with ellipses where needed so timing, status, and actions remain visible.

## Layout

The page scrolls inside the Odoo content area and uses responsive outer padding from one to two rem, capped at 1440 pixels. The header, customization panel, and card grid share that centered maximum width.

Desktop layout uses a 12-column grid with 1.25-rem gaps. Activities and Favorite Views span seven columns; My Tasks and AI Pipelines span five; Accounting & Compliance Alerts spans the full width. This creates two content-sized columns without forcing matched heights. Favorite destinations form a dense two-column list on desktop and collapse to one column below 768 pixels. Below 992 pixels every widget becomes full-width and accounting alert tiles reduce from five columns to two. Below 576 pixels the page padding tightens, company scope and actions stack, Refresh and Customize remain side by side at equal width, customization becomes one column, task signals become two-by-two, and accounting alerts become a single column.

The first viewport keeps the native navbar title visible, followed by a compact company-scope and action row, then Activities and My Tasks in the default order. Refresh and Customize sit together as secondary header actions; the app does not introduce an app grid or alternate navigation shell.

**The Content-Sized Card Rule.** Cards follow their content and use `margin-top: auto` only to align a card's own footer; do not manufacture a dashboard-like wall of equal-height panels.

## Elevation & Depth

The surface is flat by default and defines no box shadows. Depth and grouping come from the light-gray canvas, white card surfaces, one-pixel borders, and internal dividers. Hover states use a neutral fill or an action-colored border rather than lift or translation.

### Named Rules

**The Flat-By-Default Rule.** Do not add ambient shadows to Home cards; boundary, tone, and spacing provide the hierarchy.

## Shapes

The form language is gently rounded but still native and workmanlike. Main widgets use the largest local radius (14px), customization and accounting alert tiles use the middle radius (12px), and icon backplates, status chips, count pills, attention markers, and loading skeletons use the smallest radius (8px). One-pixel borders remain visible, and widget overflow is clipped to preserve rounded silhouettes. Row actions stay rectangular and borderless within their parent cards so the card, not each row, is the primary container.

## Components

### Buttons

- **Shape:** Inherited native Odoo and Bootstrap control geometry.
- **Primary:** The My Tasks footer uses the native primary button because it is the strongest continuation action on the surface.
- **Secondary:** Customize uses the native secondary button; Refresh uses the lighter native treatment.
- **Tertiary:** Footer navigation, retry, reorder, drag, and removal actions use native link or small outline variants.
- **Responsive Header Actions:** Both controls retain native variants, a minimum 2.375-rem target height, and equal flexible width when the header stacks on compact mobile.
- **Hover / Focus:** Local focus-visible treatment is a three-pixel brand-tinted outline with a two-pixel offset. Native button hover and disabled behavior remain intact.

### Cards / Containers

- **Corner Style:** Gently curved widget corners; slightly tighter customization and alert-tile corners.
- **Background:** Operational surfaces sit on the working canvas.
- **Shadow Strategy:** No shadows.
- **Border:** A one-pixel inherited border defines the outer card and internal subdivisions.
- **Internal Padding:** One-rem headers and content rhythm, with three-quarter-rem compact actions and cells.

### Icon Markers

- **Widget Headers:** A 2.25-rem soft square carries a native Font Awesome glyph for the card's purpose.
- **Rows and Destinations:** Attention markers are 1.75 rem; favorite and accounting markers are 2 rem. Destination glyphs reflect task, project, accounting, AI, view, record, or fallback location kind.
- **Semantics:** Warning, exception, action, and brand hues map to meaning. Icons remain decorative beside textual labels and keep `aria-hidden` ownership.

### Action Rows

- **Style:** Full-width, borderless rows at least 3.5rem tall, separated by hairline dividers.
- **Content:** A compact semantic icon, truncated title, and muted metadata lead; timing, status, or an arrow remains fixed at the trailing edge.
- **Hover / Disabled:** Attention and pipeline rows gain a neutral background. Favorite destinations change to the action color; unavailable favorites become muted and disabled.

### Favorite Destinations

- **Density:** A two-column desktop list fits more exact routes without expanding the card into a launcher; each route remains a single 3.5-rem action row.
- **Metadata:** The saved destination kind and company scope share one compact secondary line, separated by a middle dot when both are present.
- **Icons / Availability:** Kind-specific native icons make routes easier to distinguish. Unavailable destinations replace the icon with a ban mark and expose removal controls.

### Semantic Status Treatments

- **Pipeline Status:** Review uses a compact neutral chip; failed and blocked use a lightly tinted danger chip plus matching icon and text.
- **Activity Timing:** Overdue and today remain plain compact labels so deadline text stays immediately readable.
- **Count Treatment:** Tabular numerals are neutral by default. Nonzero overdue and change-requested task counts turn danger red; nonzero due-soon counts turn amber; stage counts sit in small soft pills.

### Task Signals

- **Style:** Four equal cells divided by one-pixel rules, collapsing to a two-by-two matrix on small screens.
- **Content:** A tabular numeric value sits above a small muted label on a soft operational fill; semantic color appears only when a count is active.

### Accounting Alert Tiles

- **Style:** Five compact, bordered action tiles inside the full-width accounting card; they collapse to two and then one column.
- **Content:** A key-specific native icon, bold tabular count, muted alert label, and trailing arrow form a single direct navigation target.
- **Hover / Focus:** Hover changes the border to the action color; the shared focus outline remains visible.

### Loading and Empty States

- **Loading:** Three 3.25rem skeleton rows use an animated neutral gradient. Motion stops when reduced motion is requested.
- **Errors:** Each widget can fail independently and offers a local retry without suppressing working widgets.
- **Empty:** Centered muted copy and a green check icon communicate that no attention is required.

### Customization Panel

- **Style:** A bordered two-column panel directly below the header, collapsing to one column on small screens.
- **Behavior:** Checkboxes control visibility; drag handles and labeled arrow buttons control order; small outline buttons add supported destinations.
- **Visibility:** Drag handles and favorite ordering controls appear only while customization is open.

### Add to Home Menu Item

- **Style:** A native Odoo cog-menu dropdown item with a Home icon.
- **Behavior:** It appears only for supported window actions and saves the current action, record or search state as an exact destination; success and failure use native notifications.

## Do's and Don'ts

### Do:

- **Do** preserve the native Odoo navbar, button variants, dropdowns, icons, typography, and semantic variables.
- **Do** keep Activities, tasks, resumable destinations, human handoffs, and accounting exceptions scannable as direct routes.
- **Do** keep the two-column desktop favorite density, exact destination metadata, and one-column narrow-screen reading order.
- **Do** pair semantic icon and count treatments with textual labels; color is reinforcement, never the only signal.
- **Do** use visible focus, semantic roles, accessible names, keyboard activation, and reduced-motion behavior.
- **Do** keep errors local to their widget and retain successful content elsewhere on Home.
- **Do** collapse the card grid and signal groups at the shipped responsive breakpoints.

### Don't:

- **Don't** turn Home into an app launcher, analytics dashboard, or alternate navigation shell.
- **Don't** add decorative gradients, promotional hero typography, dense charting, or ornamental status color.
- **Don't** replace Odoo-owned theme tokens, native button variants, or native Font Awesome icons with a custom shell or parallel design system.
- **Don't** use shadow to manufacture hierarchy that the shipped borders, surfaces, and spacing already provide.
- **Don't** expose customization controls during routine use or let customization dominate the first viewport.
- **Don't** hide unavailable, failed, or blocked states without an explanation or next action.
