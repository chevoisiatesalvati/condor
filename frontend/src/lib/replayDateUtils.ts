/** ISO UTC string -> YYYY-MM-DD for date inputs. */
export function isoToDateInput(iso: unknown): string {
  if (!iso || typeof iso !== "string") return "";
  const match = iso.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : "";
}

/** YYYY-MM-DD -> ISO UTC at start or end of day. */
export function dateInputToIso(date: string, endOfDay: boolean): string {
  if (!date) return "";
  return endOfDay ? `${date}T23:59:59Z` : `${date}T00:00:00Z`;
}

export function addDaysToDateInput(date: string, days: number): string {
  if (!date) return "";
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return "";
  parsed.setUTCDate(parsed.getUTCDate() + days);
  return parsed.toISOString().slice(0, 10);
}

export function todayDateInput(): string {
  return new Date().toISOString().slice(0, 10);
}
