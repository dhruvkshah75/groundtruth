/** Format identifiers for display without changing the underlying value. */
export function displayText(value: string): string {
  return value.replaceAll("_", " ");
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  }).format(date);
}

export function isTimestampField(key: string): boolean {
  return /(?:^|\.)(?:.*_at|timestamp|.*_timestamp)$/i.test(key);
}

export function isInternalIdField(key: string): boolean {
  return /^(?:observation_id|fact_id|event_id|root_fact_id|tool_call_id|evidence_ids|input_fact_ids|output_fact_ids|superseded_by)$/i.test(key);
}

export function fieldLabel(key: string): string {
  const parts = key.split(".");
  const last = parts[parts.length - 1];
  const unit = last.endsWith("_cm") ? " (cm)" : "";
  if (unit) parts[parts.length - 1] = last.slice(0, -3);
  const label = parts.map((part) => displayText(part.replace(/([a-z0-9])([A-Z])/g, "$1 $2"))).join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1) + unit;
}

/** Flatten nested records into table columns while keeping arrays as values. */
export function flattenFields(record: Record<string, unknown>, prefix = ""): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (isInternalIdField(key)) continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
      Object.assign(result, flattenFields(value as Record<string, unknown>, path));
    } else {
      result[path] = value;
    }
  }
  return result;
}

export function environmentTableData(records: Record<string, Record<string, unknown>>, idField: string) {
  const rows = Object.entries(records).map(([id, record]) => ({ id, fields: flattenFields(record) }));
  const duplicateId = rows.every(({ id, fields }) => fields[idField] === undefined || fields[idField] === id);
  const columns = [...new Set(rows.flatMap(({ fields }) => Object.keys(fields)))].filter((key) => key !== idField || !duplicateId);
  return { rows, columns };
}
