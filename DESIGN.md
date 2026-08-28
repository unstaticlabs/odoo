---
name: USL Odoo Distribution
description: Native Odoo operations paired with calm, governed official documents.
colors:
  paper: "#FFFFFF"
  document-ink: "#17212B"
  document-muted: "#5F6B76"
  document-rule: "#D8DEE4"
  document-wash: "#F5F7F9"
  document-accent-default: "#714B67"
  qualification: "#8A2D3C"
typography:
  display:
    fontFamily: "Lato, Arial, sans-serif"
    fontSize: "24pt"
    fontWeight: 700
    lineHeight: "27pt"
  title:
    fontFamily: "Lato, Arial, sans-serif"
    fontSize: "14.4pt"
    fontWeight: 700
    lineHeight: "18pt"
  section:
    fontFamily: "Lato, Arial, sans-serif"
    fontSize: "12pt"
    fontWeight: 700
    lineHeight: "14pt"
  body:
    fontFamily: "Lato, Arial, sans-serif"
    fontSize: "10pt"
    fontWeight: 400
    lineHeight: "12pt"
  label:
    fontFamily: "Lato, Arial, sans-serif"
    fontSize: "7pt"
    fontWeight: 700
    lineHeight: "8pt"
    letterSpacing: "normal"
  mono:
    fontFamily: "SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  square: "0"
spacing:
  hairline-gap: "1mm"
  compact: "3mm"
  section: "4mm"
  paragraph: "5pt"
  title-gap: "8mm"
components:
  document-title-block:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.document-ink}"
    typography: "{typography.display}"
    rounded: "{rounded.square}"
    padding: "1mm 0 8mm"
  document-callout:
    backgroundColor: "{colors.document-wash}"
    textColor: "{colors.document-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.square}"
    padding: "3pt"
  document-qualification-banner:
    backgroundColor: "{colors.qualification}"
    textColor: "{colors.paper}"
    typography: "{typography.body}"
    rounded: "{rounded.square}"
    padding: "3pt"
---

# Design System: USL Odoo Distribution

## Overview

**Creative North Star: "Polished Odoo Standard"**

The operating interface should feel like a careful part of Odoo, not a branded layer placed over it. Native settings blocks, list and form views, status bars, badges, buttons, fields, notebooks and chatter provide the interaction language. USL-specific design comes from the order of information, the clarity of state and consequence, and restrained operational copy.

Issued documents form a complementary governed folio: white A4 paper, embedded Lato, dark ink, muted metadata, hairline rules, pale tonal grouping and one narrow company accent. Invoices, accounting statements, official correspondence and signature certificates share this visual grammar while preserving the hierarchy required by each document family.

**Key Characteristics:**

- Native Odoo controls and responsive behavior for operational work.
- Readiness, health and the next safe action before low-level configuration.
- A quiet, high-contrast print system with generous whitespace and disciplined tables.
- Status color used semantically and sparingly, never as decoration.
- Shared document primitives with family-specific content structure.

## Colors

The document palette is a cool, restrained neutral field with one company-controlled accent; the Odoo interface inherits the installed platform theme rather than duplicating its color tokens.

### Primary

- **Company Accent** (#714B67): A narrow rule that carries company identity through the governed header. This value is the default only when the company has no configured primary color.

### Secondary

- **Qualification Red** (#8A2D3C): Reserved for conspicuous synthetic or non-production qualification banners; it is never a general call-to-action color.

### Neutral

- **Paper White** (#FFFFFF): The fixed ground for official pages and print-oriented previews.
- **Document Ink** (#17212B): Primary titles, body copy, totals and decisive evidence.
- **Slate Metadata** (#5F6B76): Dates, references, legal identity and supporting notes.
- **Hairline Grey** (#D8DEE4): Table rules and quiet structural separation.
- **Cool Wash** (#F5F7F9): Scope summaries and emphasized accounting rows without added elevation.

### Named Rules

**The One Accent Rule.** Company color appears as a narrow identity signal; it must not flood tables, totals or body copy.

**The Semantic Status Rule.** In Odoo, success, warning, danger and informational colors retain their native meanings. Never repurpose those colors for branding, and never make color the only carrier of state.

## Typography

**Display Font:** Lato (with Arial and sans-serif fallbacks)
**Body Font:** Lato (with Arial and sans-serif fallbacks)
**Label/Mono Font:** Lato for labels; the native Odoo monospace stack for technical revisions and hashes in the operating interface.

**Character:** Official output uses one embedded humanist sans-serif family to remain authoritative, economical and highly legible in French and English. Weight, size, alignment and whitespace create hierarchy; ornamental type does not.

### Hierarchy

- **Display** (Lato, weight 700, 24pt, line-height 27pt): Bold and dominant, used once for the document identity.
- **Title** (Lato, weight 700, 14.4pt, line-height 18pt): Bold subject or family-level heading, especially in correspondence.
- **Section** (Lato, weight 700, 12pt, line-height 14pt): Bold structural heading separating major content groups.
- **Body** (Lato, weight 400, 10pt, line-height 12pt): Regular reading text and dense table content with no paragraph indent.
- **Label** (Lato, weight 700, 7pt, line-height 8pt): Compact and uppercase for short print metadata labels only.
- **Technical UI** (native Odoo monospace stack, weight 400, 14px, line-height 1.5): Renderer revisions, hashes and other operator-facing machine identifiers.

### Named Rules

**The One Document Voice Rule.** Every governed document family uses the same embedded Lato family and hierarchy; family identity comes from structure, not a new typeface.

**The Metadata Stays Secondary Rule.** References, dates, legal lines and provenance remain legible but never compete with the title, recipient, result or total.

## Layout

Operational screens compose native Odoo views. Settings present three blocks in reading order: official-document readiness, isolated renderer health and configuration, then visual review. Related values use standard setting rows and content groups; dense technical paths remain subordinate to the status they support. Letter forms use the native header for lifecycle actions and status, a two-column identity group, a notebook for body and supporting material, and chatter for collaboration.

Official documents use fixed A4 geometry with 18 mm side margins, 25 mm top space and 17 mm bottom space. The governed header and footer remain inside the trim box. Titles occupy roughly two-thirds of the width while reference and date align in the remaining right column. Tables use full available width, right-align numeric evidence and preserve visible hierarchy through indentation, weight and pale row washes. Portrait is the default; accounting statements may use landscape only when their real column count requires it.

On smaller screens, native Odoo reflow owns stacking, touch sizing and control placement. Critical status and available actions must remain visible; desktop column layouts may stack, but information must not be hidden to preserve a composition.

### Named Rules

**The Fixed Paper, Fluid Controls Rule.** Issued PDFs keep governed A4 geometry; Odoo controls reflow through native responsive behavior.

**The Readiness Before Configuration Rule.** Lead settings with the user-facing operational state and its recovery action, then reveal transport and trust details.

## Elevation & Depth

Official documents use no shadows. Depth comes from whitespace, typography, hairline rules and pale tonal grouping. Operational screens inherit Odoo's native elevation behavior; USL add-ons do not add bespoke card shadows, floating panels or decorative layering.

### Named Rules

**The Flat Official Record Rule.** Paper surfaces and report structures stay flat so they print consistently and remain legible in monochrome.

## Shapes

Official-document primitives are square and rectilinear. Rules, washes, banners and table boundaries align to the page grid without decorative rounding or clipping. Odoo controls retain the platform's native corner treatment; do not force the square print language onto buttons, badges or fields.

## Components

### Operational Settings

- **Structure:** Use native app, block and setting elements rather than a custom dashboard or card grid.
- **State:** Put a concise, readable status near the setting label; use a native badge only for compact machine state.
- **Recovery:** Pair a failed or incomplete state with one nearby link-style action that explains what it checks or opens.
- **Detail:** Keep technical revision, engine and trust-path data in subordinate content groups.

### Workflow Actions

- **Primary:** At most one lifecycle-advancing action is visually primary at a time, such as Finalize or Mark as Sent.
- **Secondary:** Download, correction and cancellation use native secondary treatments and appear only when their state permits them.
- **State:** The native status bar remains the canonical lifecycle visualization.
- **Immutability:** Issued output is corrected through a visible superseding version, never an in-place edit disguised by the interface.

### Governed Header and Footer

- **Header:** Company mark or name at left, legal identity at right, followed by one thin company-accent rule.
- **Footer:** Document family and immutable reference at left, current and total page count at right.
- **Continuation:** The same header and footer grammar keeps multi-page records attributable and correctly ordered.

### Title Block

- **Structure:** Large document identity at left; immutable reference and date form a quiet, right-aligned register.
- **Spacing:** Preserve a decisive gap before recipient, scope or evidence content.

### Metadata Labels

- **Style:** Short uppercase labels in muted color and compact bold type.
- **Use:** Reserve for recurring metadata such as reference, recipient and scope; do not turn ordinary prose into labels.

### Tables and Totals

- **Structure:** Hairline top, header and bottom rules; no boxed grid by default.
- **Alignment:** Text reads from the left and numbers align to the right with tabular figures.
- **Hierarchy:** Indentation expresses accounting depth; bold weight and pale wash distinguish section, control and total rows.
- **Totals:** The authoritative total is visually clear without oversized color blocks.

### Scope Callout

- **Style:** A full-width cool wash with compact text and no border or shadow.
- **Use:** Summarize filters, perimeter or the meaning of evidence before a dense table.

### Qualification Banner

- **Style:** A full-width, high-contrast red band with centered bold text.
- **Use:** Only synthetic fixtures, qualification packs or other explicitly non-production output may use it.

## Do's and Don'ts

### Do:

- **Do** preserve native Odoo navigation, controls, semantics, focus behavior and responsive reflow.
- **Do** make readiness, responsibility, consequence and the next safe action immediately scannable.
- **Do** keep one shared document header, title, table, callout and footer grammar across governed families.
- **Do** use whitespace and alignment before adding borders, fills or status color.
- **Do** test official output in portrait, required landscape cases, monochrome and long French copy.

### Don't:

- **Don't** turn Document Templates into a visual template editor or expose production source for user customization.
- **Don't** introduce a custom card dashboard, promotional hero, decorative iconography or non-native navigation into an Odoo operating surface.
- **Don't** use qualification red or native semantic status colors as general brand accents.
- **Don't** hide an unhealthy renderer, incomplete identity or unavailable action behind silent fallback behavior.
- **Don't** give each document family an unrelated palette, typeface or decorative identity.
