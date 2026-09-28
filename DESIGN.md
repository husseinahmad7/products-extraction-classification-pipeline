---
name: Evidence Pipeline
description: Compact evidence-docket interface for extraction and review.
colors:
  ink: "#172b42"
  muted: "#536174"
  nav: "#172f4c"
  line: "#d7dfe9"
  accent: "#4937b9"
  focus: "#654acb"
  selected: "#eeeafd"
  surface: "#ffffff"
  workspace: "#f3f5f8"
  button-border: "#b8c5d4"
  input-border: "#aab8cb"
  neutral-hover: "#edf0f6"
  primary-hover: "#392791"
  code-surface: "#f0f3f8"
  nav-label: "#dbe5f0"
  nav-hover: "#294661"
  nav-current: "#344b70"
  nav-focus: "#ddd0ff"
  nav-meta: "#c7d5e4"
  badge-neutral-bg: "#e7edf4"
  badge-neutral-text: "#37475b"
  success-bg: "#def0e8"
  success-text: "#245845"
  pending-bg: "#f7eac5"
  pending-text: "#735306"
  failure-bg: "#f9e0e1"
  failure-text: "#862934"
  derived-bg: "#e9e4fa"
  derived-text: "#54418f"
  error-bg: "#fbe9e9"
  error-text: "#852733"
  error-border: "#e5b5bb"
  operation-bg: "#e9eef8"
  operation-border: "#c9d5e9"
typography:
  display:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: "2.6rem"
    fontWeight: 650
    lineHeight: 1.22
    letterSpacing: "-.025em"
  headline:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: "2rem"
    fontWeight: 650
    lineHeight: 1.22
    letterSpacing: "-.025em"
  title:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: "1.2rem"
    lineHeight: 1.22
  body:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: "1rem"
    lineHeight: 1.55
  label:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: ".9rem"
    fontWeight: 650
  table:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: ".86rem"
  metadata:
    fontFamily: "\"Segoe UI\", system-ui, sans-serif"
    fontSize: ".78rem"
    fontWeight: 400
  mono:
    fontFamily: "ui-monospace, \"Cascadia Code\", Consolas, monospace"
    fontSize: ".84rem"
rounded:
  badge: "4px"
  field: "5px"
  control: "6px"
  operation: "8px"
  panel: "12px"
spacing:
  tight: ".25rem"
  control-gap: ".5rem"
  compact: ".75rem"
  regular: "1rem"
  panel: "1.5rem"
  shell: "2rem"
  spacious: "2.5rem"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: ".5rem .8rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: ".5rem .8rem"
  button-secondary-hover:
    backgroundColor: "{colors.neutral-hover}"
  input-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: ".65rem .75rem"
    width: "100%"
  lifecycle-navigation:
    backgroundColor: "{colors.nav}"
    textColor: "{colors.nav-label}"
    rounded: "{rounded.control}"
    padding: ".85rem .75rem"
  lifecycle-navigation-current:
    backgroundColor: "{colors.nav-current}"
    textColor: "{colors.surface}"
  setup-navigation:
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    padding: ".5rem .8rem"
  setup-navigation-current:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.accent}"
  badge-neutral:
    backgroundColor: "{colors.badge-neutral-bg}"
    textColor: "{colors.badge-neutral-text}"
    rounded: "{rounded.badge}"
    padding: ".25rem .5rem"
  badge-success:
    backgroundColor: "{colors.success-bg}"
    textColor: "{colors.success-text}"
    rounded: "{rounded.badge}"
    padding: ".25rem .5rem"
  badge-pending:
    backgroundColor: "{colors.pending-bg}"
    textColor: "{colors.pending-text}"
    rounded: "{rounded.badge}"
    padding: ".25rem .5rem"
  badge-failure:
    backgroundColor: "{colors.failure-bg}"
    textColor: "{colors.failure-text}"
    rounded: "{rounded.badge}"
    padding: ".25rem .5rem"
  badge-derived:
    backgroundColor: "{colors.derived-bg}"
    textColor: "{colors.derived-text}"
    rounded: "{rounded.badge}"
    padding: ".25rem .5rem"
  resource-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "{spacing.panel}"
  evidence-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: ".5rem .8rem"
  evidence-field-selected:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.ink}"
  resource-table:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.table}"
    width: "100%"
  operation-notice:
    backgroundColor: "{colors.operation-bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.operation}"
    padding: "{spacing.regular}"
  error-notice:
    backgroundColor: "{colors.error-bg}"
    textColor: "{colors.error-text}"
    rounded: "{rounded.control}"
    padding: ".9rem 1rem"
---

# Design System: Evidence Pipeline

## Overview

**Creative North Star: "Evidence Docket"**

Evidence Docket is the approved visual direction: a compact operational workspace with ink-blue navigation, cool pale surroundings, white document surfaces and violet actions or selection. Rules, aligned tables and explicit state labels make saved evidence and review decisions easy to locate. The interface uses system typography and small outline icons; the implemented surface has no raster imagery.

This is a code-observed scan of dashboard/src/styles.css and dashboard/src/App.tsx, grounded in PRODUCT.md and the approved dashboard direction. The descriptive names are provisional labels for existing values, not a new creative direction. Rendered visual, accessibility and browser verification were not performed: the Windows browser sandbox was unavailable, and no screenshots exist. Component snippets document implemented styling and illustrative data; they are not evidence of a visual or accessibility pass.

**Key Characteristics:**

- Compact system typography with monospace for code and hashes.
- Flat document surfaces separated by tonal changes and thin rules.
- Violet actions and selection, with named semantic states.
- Responsive stacking and a reduced-motion override.

## Colors

The palette combines cool document neutrals with a single violet action family and muted semantic state pairs. Frontmatter contains the normative values; the names below explain their use.

### Primary

- **Action Violet / Deep Action Violet** (`accent`, `primary-hover`): primary actions and their hover state.
- **Focus Violet / Navigation Focus** (`focus`, `nav-focus`): visible keyboard focus on light and dark surfaces respectively.
- **Selected Violet** (`selected`): selected rows, setup sections and evidence fields.

### Secondary

These are state colors, not competing brand accents.

- **Success Surface / Ink** (`success-bg`, `success-text`): active, published, succeeded, observed and approved states.
- **Pending Surface / Ink** (`pending-bg`, `pending-text`): open, review required, awaiting review and running states.
- **Failure Surface / Ink** (`failure-bg`, `failure-text`): failed, rejected, invalid, quarantined and missing states.
- **Derived Surface / Ink** (`derived-bg`, `derived-text`): transformed or derived evidence.
- **Error Notice Surface / Ink / Rule** (`error-bg`, `error-text`, `error-border`): an explanatory alert, visually distinct from a compact state badge.

### Neutral

- **Document Ink / Muted Ink** (`ink`, `muted`): primary reading and secondary context.
- **Cool Workspace / Document White / Code Paper** (`workspace`, `surface`, `code-surface`): surrounding workspace, working documents and code blocks.
- **Document Rule / Control Rule / Input Rule** (`line`, `button-border`, `input-border`): structural, button and field boundaries.
- **Control Hover** (`neutral-hover`): ordinary button hover.
- **Navigation Ink / Label / Metadata** (`nav`, `nav-label`, `nav-meta`): the dark lifecycle rail and its reading hierarchy.
- **Navigation Hover / Current Navigation** (`nav-hover`, `nav-current`): pointer and current-location treatments in the lifecycle rail.
- **Neutral State Surface / Ink** (`badge-neutral-bg`, `badge-neutral-text`): states without a dedicated semantic mapping, including configured, queued and cancelled.
- **Operation Surface / Rule** (`operation-bg`, `operation-border`): the latest-operation notice.

**The Named State Rule.** Show the state name alongside its color. Preserve the distinction between configured, pending, successful, failed and derived evidence.

The sidecar's tonal ramps are synthesized previews of the extracted colors. They are not additional application tokens or approved contrast pairings.

## Typography

**Interface Font:** Segoe UI with system-ui and sans-serif fallbacks.
**Code Font:** ui-monospace with Cascadia Code, Consolas and monospace fallbacks.

The interface favors compact, familiar reading over a separate editorial display face. Headings share the interface family; code blocks and hashes use a quieter monospace role.

### Hierarchy

- **Display** (`typography.display`): the connection screen heading; it reduces to the headline size on narrow screens.
- **Headline** (`typography.headline`): page titles; the mobile override is smaller (1.7rem).
- **Title** (`typography.title`): resource and form section headings. Nested headings use the observed subordinate sizes (1.08rem and .95rem).
- **Body** (`typography.body`): prose, with an observed maximum line length (72ch).
- **Label** (`typography.label`): visible field labels.
- **Table / Metadata** (`typography.table`, `typography.metadata`): dense lists and secondary identifiers. Table numerals are tabular.
- **Mono** (`typography.mono`): JSON, locators, machine pointers and hashes. JSON disclosure blocks use the smaller metadata size.

The document root sets the rem base (15px). Heading line height is compact; prose and code use a more open reading rhythm. The extracted size steps are observed roles, not a claimed mathematical scale.

**The Machine Text Rule.** Use monospace for code and hashes; keep action labels and prose in the interface face.

## Layout

The desktop shell is a flex row with a fixed lifecycle rail (224px) and a flexible work area that can shrink without pushing the viewport wider. A white top bar separates navigation context from the main work surface. The main area uses generous shell padding relative to the dense tables (1.6rem 2rem 3rem).

A selected item opens a two-column workspace: a flexible list and detail pane with an explicit minimum (350px), separated by the panel spacing token. At the first breakpoint (1150px maximum width), the detail pane stacks below the list. At the narrow breakpoint (760px maximum width), navigation wraps across the top, page headings stack, main padding tightens (1.25rem 1rem 2rem), and controls use a larger minimum height (44px). The desktop control minimum is smaller (36px); these values record the implementation, not a completed target-size audit.

White resource panels, configuration editors and connection forms share the panel corner token. The editor has a readable maximum width (50rem), and the connection area has its own narrower maximum (38rem). Tables fill their containers and scroll horizontally when needed. Text identifiers and code wrap; timestamp cells stay on one line.

Spacing is a compact rem vocabulary rather than a forced grid. Use the frontmatter's reusable tight, control-gap, compact, regular, panel, shell and spacious steps. Component-specific padding records the smaller adjustments that already exist.

## Elevation & Depth

Depth comes from surface tone, thin borders and spatial grouping. No box shadows, floating overlays or backdrop filters are present in the scanned stylesheet. Selected content gains a pale violet fill; an evidence field also gains an accent border. This is a flat document workspace.

**The Flat Surface Rule.** Use surface tone, borders and spacing to separate work areas. The current implementation has no box shadows or hover lift.

## Shapes

Corners are gently rounded, with a small step from state badge to field to control to operation notice to document panel. Use the corresponding `rounded` token rather than inventing new corner sizes. Standard boundaries are thin (1px). Tables use horizontal rules and collapsed borders; badges are compact rectangles rather than pills.

Icons are small outline SVGs in the implementation. They support text labels, while icon-only close and dismiss controls have accessible names. No shipping raster assets were observed.

## Components

### Buttons

Primary actions use action violet with white text. Secondary actions use a white surface, ink text and a control rule. Both use the control corner and shared padding tokens. Hover changes the fill; disabled buttons reduce opacity (.5) and use the unavailable cursor. There is no separate active transform.

Visible focus uses an external outline (3px) with an offset (3px); the dark rail uses its lighter focus token. Preserve the narrow-screen control-height override.

### Inputs / Fields

Fields use white fill, ink text, the input rule and field corner. Labels stay visible above the control, and supporting hints use muted text. The caret uses action violet. Textareas resize vertically and code editors use the mono role. Errors appear as adjacent alert notices; the implementation does not define a separate invalid-field border or custom select popup.

### Navigation

Lifecycle navigation is left aligned on the ink-blue rail, with outline icons and readable labels. Hover and current location have separate surface treatments. The current item has stronger weight and `aria-current="page"`. Narrow layouts wrap the navigation at the top.

Setup navigation uses wrapping transparent buttons over a bottom rule. The current section receives selected violet, action-violet text and stronger weight. These controls are navigation buttons, not an implemented ARIA tab widget.

### Chips

State badges are compact, noninteractive text labels with a small radius and no outline. Every semantic color retains the spelled-out state. Unknown or unmapped states use the neutral pair. Underscores are replaced by spaces for display.

### Cards / Containers

Resource detail panels, editors and connection forms use the white document surface, document rule and panel corner. Content is divided with thin top rules and heading spacing. A resource panel exposes saved state, forms and decisions in normal flow; it has no modal overlay or elevation.

### Tables

Tables use a white surface, compact type, muted headers, tabular numerals and horizontal rules. The selected row has selected-violet fill. Selection is a text button inside the item cell with a visible name and secondary identifier; it uses `aria-pressed` and does not depend on clicking an otherwise inert row.

### Evidence Field Selector

The signature control pairs a machine field path with its evidence outcome. Selected state combines pale violet, an accent border and `aria-pressed`. It reveals the locator, record scope, capture hash, raw values and transforms in the detail section. Only its background changes with a short transition (120ms ease-out). The reduced-motion media query removes transitions and animations.

### Notices and Empty States

Latest-operation notices use a cool blue surface, border and the operation corner. They display a named state with the operation identifier and an available action. Error notices use the error palette and `role="alert"`. Loading states use status text; operation changes use a polite live region.

Empty collections use a white, spacious reading area, a small outline icon, a title and one concrete next step. These source-defined semantics have not been verified in a browser or assistive technology session.

## Do's and Don'ts

### Do:

- Do use the existing action violet and pale violet selection consistently.
- Do keep state text visible alongside semantic color.
- Do retain the visible focus outline, keyboard-operable controls and labelled form fields.
- Do let long identifiers, hashes and error messages wrap, and allow tables to scroll horizontally.
- Do preserve the implemented responsive stacking and reduced-motion override.

### Don't:

- Don't use success styling to imply that a queued, unverified or derived result is approved.
- Don't add decorative shadows or animated lift to the existing flat components.
- Don't replace the system interface type with a decorative display face.
- Don't hide control focus or use color as the only state indicator.
- Don't describe code inspection or these snippets as completed browser, visual or accessibility verification.
