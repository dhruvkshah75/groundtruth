import type { AuditEvent, Fact } from "./types";

export function visibleGraphNodes(
  scope: "active" | "history",
  facts: readonly { source: string; target: string }[],
  activeNodes: readonly string[],
  ledger: readonly Pick<Fact, "subject" | "object" | "superseded_by">[],
): string[] {
  const nodes = new Set<string>();
  facts.forEach((fact) => { nodes.add(fact.source); nodes.add(fact.target); });
  if (scope === "active") {
    activeNodes.forEach((node) => nodes.add(node));
    ledger.filter((fact) => fact.superseded_by !== null).forEach((fact) => {
      nodes.add(fact.subject);
      nodes.add(fact.object);
    });
  }
  return [...nodes].sort();
}

export function factColor(active: boolean, selected: boolean, related: boolean, changed: boolean, dark = false): number {
  if (dark) return changed ? 0xe8be7e : selected || related ? 0x91bdb3 : active ? 0x819b95 : 0x777777;
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
