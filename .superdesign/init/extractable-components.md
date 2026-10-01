# Extractable components

Components are currently colocated in `frontend/src/App.tsx`; they have not been extracted into standalone modules. Their complete source is in `components.md`.

## Layout

- `App` — three-column desktop workspace with controls rail, conversation and inspector. Props: none; loads session and API state internally.
- `Inspector` — collapsible Tier 1 / Tier 2 / Tier 3 data panels. Props: `state`, `open`, `wideLayout`, `onClose`.

## Content components

- `ConversationTurn` — real question and agent response with status, perspectives, revisions. Props: `response`.
- `ValueCard` — labeled data card. Props: `label`, `children`.
- `LedgerTable` — complete evidence ledger table. Props: `facts`.
- `AuditEventView` — audit policy, reason, and source fact IDs. Props: `event`.
- `JsonValue` — formatted backend values. Props: `value`.
