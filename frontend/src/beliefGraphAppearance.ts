import type { AuditEvent, Fact } from "./types";

export function factColor(active: boolean, selected: boolean, related: boolean, changed: boolean): number {
  return changed ? 0xa06a1c : selected || related ? 0x765184 : active ? 0x8b7596 : 0x817a85;
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
