export type QueryForm = { domain: string; company: string; keyword: string; month: string; intent: string };

// These controls construct visible natural language, not hidden or unsupported SQL parameters.
export function composeQuestion(form: QueryForm): string {
  const period = form.month ? `${form.month.slice(0, 4)}年${Number(form.month.slice(5, 7))}月` : "";
  const scope = [form.company.trim(), period, form.domain === "全部业务" ? "" : form.domain, form.keyword.trim()].filter(Boolean).join(" ");
  const action = { detail: "查询详细信息", count: "统计记录数量", sum: "查询金额合计" }[form.intent] || "查询详细信息";
  return `${scope} ${action}`.trim();
}

export function parseEventLines(buffer: string, final = false): { lines: string[]; rest: string } {
  const parts = buffer.split("\n");
  const rest = final ? "" : parts.pop() || "";
  return { lines: parts.filter(line => line.trim()), rest };
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

export function trustedParentOrigin(requested: string | null, ownOrigin: string, allowed: string[]): string | null {
  if (!requested || requested === ownOrigin) return ownOrigin;
  return allowed.includes(requested) ? requested : null;
}
