import type { AuditEvent, Fact } from "./types";

export function factColor(active: boolean, selected: boolean, related: boolean, changed: boolean): number {
  return changed ? 0xe0bd7d : selected || related ? 0x78e6d8 : active ? 0x4f8f83 : 0x666b69;
}

export function highlightedFacts(
  transient: ReadonlySet<string>,
  ledger: readonly Pick<Fact, "superseded_by">[],
  events: readonly Pick<AuditEvent, "event_type" | "output_fact_ids">[],
): Set<string> {
  const result = new Set(transient);
  ledger.forEach((fact) => { if (fact.superseded_by) result.add(fact.superseded_by); });
  events.forEach((event) => {
    if (event.event_type === "belief_revision") event.output_fact_ids.forEach((id) => result.add(id));
  });
  return result;
}
