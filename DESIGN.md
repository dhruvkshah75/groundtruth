---
name: GroundTruth
description: "An evidence-first conversation and inspection workspace."
colors:
  accent: "#765184"
  accent-hover: "#62406f"
  surface: "#fff"
  surface-raised: "#f8f6f9"
  line: "#eee9f0"
  line-strong: "#e4dee7"
  text: "#332b36"
  muted: "#695e6e"
  quiet: "#75697b"
  user-bubble: "#f3eff5"
  danger: "#a63439"
  connected: "#25816c"
  revision-text: "#875a15"
  graph-stage: "#f8f6f9"
  graph-node: "#765184"
  graph-node-related: "#563064"
  graph-active: "#8b7596"
  graph-selected: "#765184"
  graph-changed: "#a06a1c"
  graph-superseded: "#817a85"
typography:
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: ".875rem"
    lineHeight: 1.65
  answer:
    fontSize: ".875rem"
    lineHeight: 1.7
  detail:
    fontSize: ".8125rem"
    lineHeight: 1.6
  label:
    fontSize: ".75rem"
    lineHeight: 1.5
  page-title:
    fontSize: ".875rem"
    fontWeight: 500
  welcome:
    fontSize: "1.5rem"
    fontWeight: 500
    letterSpacing: "-.03em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: ".8125rem"
    lineHeight: 1.55
  graph-node:
    fontSize: ".8125rem"
    fontWeight: 500
    lineHeight: 1.4
  graph-edge:
    fontSize: ".8125rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  graph-label: "7px"
  icon: "8px"
  card: "9px"
  navigation: "12px"
  composer: "20px"
  pill: "999px"
spacing:
  compact: "4px"
  control: "8px"
  item: "12px"
  section: "16px"
  mobile-gutter: "20px"
  header: "24px"
  reading-gutter: "40px"
components:
  send-button:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.navigation}"
    width: "32px"
    height: "32px"
  send-button-hover:
    backgroundColor: "{colors.accent-hover}"
  icon-button:
    textColor: "{colors.muted}"
    rounded: "{rounded.icon}"
    width: "32px"
    height: "32px"
  navigation-item:
    textColor: "{colors.muted}"
    rounded: "{rounded.navigation}"
    padding: "9px 12px"
    typography: "{typography.body}"
  navigation-item-active:
    backgroundColor: "{colors.user-bubble}"
    textColor: "{colors.text}"
  composer:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.composer}"
    padding: "6px"
  perspective-card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "12px"
  status-badge:
    rounded: "{rounded.pill}"
    padding: "5px 8px"
  graph-node-label:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.graph-label}"
    padding: "4px 7px"
    typography: "{typography.graph-node}"
---

# Design System: GroundTruth

## Overview

**Creative North Star: "Operate"**

Operate mode: a white and lilac workspace organized around one current conversation and the evidence behind it. Quiet navigation and a restrained reading column give answers room; system inspection has its own page. The belief graph shares the same white and lilac surfaces, with readable node names and relationship predicates.

The supplied Figma Home - Response frame informs composition rather than feature scope. Existing GroundTruth naming and BrainCircuit/Lucide icons remain; account, avatar, and case-management patterns are outside this implementation.

**Key Characteristics:**

- White canvas with muted purple text and lilac selection surfaces.
- Compact navigation, a centered reading column, and a persistent composer.
- Flat evidence surfaces with subtle one-pixel rules.
- A light interactive graph with purple nodes, amber revisions, and gray superseded relationships.

## Colors

Purple is the shared workspace and graph accent; green retains its existing positive evidence and connection roles. The frontmatter records values extracted from the implemented styles, not a new palette.

- **Muted purple** (`accent`) marks action, focus, icons, and loaded scenarios; `accent-hover` deepens the send action.
- **White and lilac neutrals** (`surface`, `surface-raised`, `user-bubble`) distinguish the canvas, inspector backing, active navigation, and user messages. `line` and `line-strong` supply subtle dividers and control borders.
- **Purple-gray text** (`text`, `muted`, `quiet`) separates primary reading, supporting detail, and helper text. `danger`, `connected`, and `revision-text` retain semantic error, connection, and revision feedback.
- **Graph palette:** `graph-stage` follows the raised lilac surface, resolved from CSS in Three.js. Nodes use `graph-node`, deepening to `graph-node-related` when selected or related to the selected fact. Relationships use `graph-active`, `graph-selected`, `graph-changed`, and `graph-superseded`; changed amber takes priority over selected purple in `factColor`. Labels use the same white surface, purple-gray text, and subtle borders as the rest of the app.

The graph keeps the light app theme. Its stage has no dark fog, dust field, or separate dark text/control palette.

## Typography

The root stack is Inter followed by UI/system sans-serif fallbacks; the implementation does not require a separately downloaded font to render. Technical values use the system monospace stack.

- **Body:** 14px for user messages and chat input on desktop; user messages use 1.65 line height. Answers use 14px/1.7 and a 72ch maximum line length.
- **Titles:** the workspace heading is 14px, weight 500; the empty-chat greeting is 24px, weight 500, with -0.03em tracking, becoming 20px on compact screens.
- **Detail:** 13px/1.6 for evidence and inspection prose. Labels commonly use 12px/1.5; status and trace badges use smaller local sizes.
- **Graph labels:** 13px/1.4 in the inherited sans-serif stack; node names use weight 500, relationship predicates weight 400. Hovered/selected node labels reveal a 12px connected-fact count. Names are capped at 24ch with ellipsis and a full-text title; relationship underscores become readable spaces.
- **Technical data:** retain monospace identifiers and tabular numerals for inspector data, tables, provenance, and fact lists. Uppercase treatment is limited to existing metadata labels and badges.

## Layout

The viewport-height workspace uses a 232px left sidebar and a flexible main area. Hiding the sidebar changes its grid column to zero and releases that space. The header is 60px high. Conversation and composer each use a centered outer width of at most 760px, including 40px side padding: the desktop reading area is 680px wide. The conversation scrolls independently; the composer remains below it.

At viewport widths of 760px or less, the sidebar becomes a fixed dismissible drawer, at most 280px wide and leaving at least 48px of viewport outside it. Chat gutters reduce to 20px, composer gutters to 16px, the textarea becomes 16px, and evidence blocks lose their desktop left indent. Escape, backdrop, and navigation selection close the compact drawer. Three perspective cards stack whenever the chat container is at most 640px wide, including narrow desktop content areas; pose values become two columns.

The system inspector uses a pale backing and two columns of bordered sections, reducing to one column at 1000px. Its third section spans the grid. The embedded graph occupies the available main area and uses on-demand filter and detail panels capped at 280px and 360px. The camera adjusts its fitting distance with viewport aspect ratio so the network remains usable on narrow screens. The 700px graph breakpoint further compacts toolbar content; the global compact workspace breakpoint remains 760px. Legacy standalone graph grid selectors are not the embedded workspace layout.

## Elevation & Depth

Chat, navigation, evidence cards, and the composer are flat at rest: tone and one-pixel lines carry hierarchy. Shadows identify overlays. The compact workspace drawer uses `12px 0 40px rgb(42 34 45 / 10%)`; embedded graph panels use `12px 0 35px rgb(42 34 45 / 12%)` or its negative horizontal counterpart; the fact dialog uses `0 24px 70px rgb(42 34 45 / 20%)`. The graph has actual Three.js depth, neutral lighting, and a subtle grid on the raised lilac surface. Revision emphasis uses amber rather than glow. Do not translate that depth into ornamental card elevation.

## Shapes

Use rounded controls within a restrained, line-based interface: 8px icon controls, 9px evidence cards, 12px navigation/send buttons, and 20px composer/user bubbles. Status chips are pill-shaped; graph labels use compact 7px corners and 4px by 7px padding. Inspector nesting uses smaller local radii. Preserve circular graph nodes and actual connecting edges rather than replacing the network with static cards.

## Components

### Navigation and scenario controls

Navigation is a quiet, icon-led list with 40px minimum item height, 12px corners, 9px by 12px padding, and 14px text. Hover uses the raised surface; the active page uses the user-bubble surface and weight 500. Chat, System inspector, Belief graph, and Simulation share the same left navigation. When space allows, drag a tab to either side of the workspace. Shift+Left Arrow places it on the left, and Shift+Enter places it on the right. A one-pixel divider adjusts both pane widths and supports arrow keys. The sidebar shows saved chats and graph controls whenever their views are open. Thin, translucent scrollbars appear during scrolling and hide when idle. Scenario buttons remain simple action labels and show a check for the loaded preset. Retain existing BrainCircuit/Lucide icons. The visible assistant name is lowercase `agent` in the header, response identity, and document title.

### Buttons and composer

The icon button is 32px square with an 8px radius and transparent background, gaining the raised surface on hover. The send action is 32px square with 12px corners, purple background, white icon, and a deeper purple hover. Disabled controls visibly reduce emphasis; the send button has a separate pale disabled state.

The composer has a one-pixel strong border, 20px corners, and 6px internal padding. Its border turns purple on focus within. The textarea starts at one line, grows as text wraps or gains line breaks, and scrolls beyond 160px. It retains a programmatically associated label for assistive technology. Enter submits; Shift+Enter adds a line. Other focusable controls use a 2px purple outline with 3px offset. API problems remain visible with retry/dismiss controls.

Simulation dropdowns use the current theme surface, text, border, and selection colors. Browsers that support styling native pickers apply those colors to the open menu and its selected option.

### Evidence cards and badges

Perspective cards have a white background, one-pixel subtle border, 9px radius, and 12px padding. Keep evidence and execution traces readable rather than adding decorative media. Revision panels use restrained amber tints; superseded audit facts have dashed borders. Grounding and provider badges retain textual meaning as well as their colors. Inspector sections use expandable summaries and light nested cards.

### Belief graph

Render the network with Three.js and interactive orbit, zoom, camera reset, node selection, and relationship selection. Restore names for all nodes and readable predicates for relationships. Hovered and selected node labels reveal their connected-fact count. Screen-space collision placement prioritizes focused labels, then node names, changed relationship labels, and ordinary relationship labels. It tries nearby positions within viewport bounds and hides labels only when no non-overlapping position fits. When a node label moves away from its original position, a thin dotted SVG leader line connects the actual sphere center to the relocated label. These one-pixel lines have no arrowheads, preserving the distinction from graph relationships and keeping mobile names associated with their nodes. The graph's nodes, edges, and facts remain intact; details and the accessible fact list retain every fact.

Changed relationships remain amber and superseded relationships gray, with textual/dashed distinctions in supporting UI. Graph labels use white surfaces, purple-gray text, subtle borders, and lilac/purple focus states. Filters, selection details, and the fact dialog reuse the same light workspace surfaces.

Navigation and composer transitions use 160ms easing; graph drawer transforms use 200ms easing. Labels are repositioned when the camera or focus state changes. Respect the existing reduced-motion CSS that removes state transitions. Continuous graph rendering and the loading spinner have separate behavior; this document does not claim they are disabled by the reduced-motion rule.

## Do's and Don'ts

### Do:

- Do keep the 232px desktop sidebar fully collapsible and the compact drawer dismissible.
- Do keep conversation content within the 760px outer column, including its 40px desktop side padding.
- Do stack perspective cards when the chat container is at most 640px wide.
- Do retain textual status, focus feedback, and the accessible graph fact list.
- Do show node names and readable relationship predicates, using collision placement and focused-label priority to manage density.
- Do connect displaced node labels to their sphere centers with thin dotted leader lines without arrowheads.

### Don't:

- Don't import account, avatar, case-management, or multiple-chat features from the visual reference.
- Don't reintroduce a dark graph stage, fog, or dust field; keep its surfaces aligned with the white/lilac app theme.
- Don't use decorative shadows or production raster assets to replace the current flat UI and interactive graph.
- Don't alter API, evidence, or graph semantics through visual changes.

This is an implementation scan of `frontend/src/app.css`, `frontend/src/belief-graph.css`, `frontend/src/beliefGraphAppearance.ts`, `frontend/src/beliefGraphLabels.ts`, and the corresponding workspace/graph components. The [original reference](https://www.figma.com/design/i9pLwoqVaWRwZYNWz7yAne/caseaimatch-design--Community-?node-id=2-672), **Home - Response** (`2:2675`), records composition intent only. No new branding or feature scope is implied.
