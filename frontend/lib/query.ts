export type QueryForm = {
  domain?: string;
  intent?: string;
  date?: string;
  direction?: string;
  airline?: string;
  keyword?: string;
  // Legacy compatibility fields
  company?: string;
  month?: string;
};

// These controls construct visible natural language, not hidden or unsupported SQL parameters.
export function composeQuestion(form: QueryForm): string {
  const isLegacy = (form.company !== undefined && form.airline === undefined && form.direction === undefined && form.date === undefined) ||
    (form.month !== undefined && form.date === undefined) ||
    (form.domain === "全部业务");

  if (isLegacy) {
    const period = form.month ? `${form.month.slice(0, 4)}年${Number(form.month.slice(5, 7))}月` : "";
    const scope = [form.company?.trim(), period, form.domain === "全部业务" ? "" : form.domain, form.keyword?.trim()].filter(Boolean).join(" ");
    const action = { detail: "查询详细信息", count: "统计记录数量", sum: "查询金额合计" }[form.intent || "detail"] || "查询详细信息";
    return `${scope} ${action}`.trim();
  }

  const domain = (form.domain || "实际执飞航班").trim();
  const intent = form.intent || "status";
  const date = (form.date || "").trim();
  const direction = (form.direction || "").trim();
  const airline = (form.airline || "").trim();
  const keyword = (form.keyword || "").trim();

  if (domain === "实际执飞航班") {
    if (intent === "count") {
      return (date && !airline && !keyword) ? (date + direction + "执飞航班量") : [date, direction, airline, keyword, "执飞航班量"].filter(Boolean).join(" ").trim();
    }
    if (intent === "detail") {
      const flightPart = keyword ? (keyword.match(/^[A-Z0-9]{2}\d+/i) ? `航班 ${keyword}` : keyword) : "";
      return (date && !airline && !flightPart) ? (date + direction + "执飞明细") : [date, direction, airline, flightPart, "执飞明细"].filter(Boolean).join(" ").trim();
    }
    const flightPart = keyword ? (keyword.match(/^[A-Z0-9]{2}\d+/i) ? `航班 ${keyword}` : keyword) : "";
    return (date && !airline && !flightPart) ? (date + direction + "航班执行情况") : [date, direction, airline, flightPart, "航班执行情况"].filter(Boolean).join(" ").trim();
  }

  if (domain === "航班计划动态") {
    if (intent === "count") {
      return (date && !airline && !keyword) ? (date + direction + "计划航班量") : [date, direction, airline, keyword, "计划航班量"].filter(Boolean).join(" ").trim();
    }
    if (intent === "detail") {
      const flightPart = keyword ? (keyword.match(/^[A-Z0-9]{2}\d+/i) ? `航班 ${keyword}` : keyword) : "";
      return (date && !airline && !flightPart) ? (date + direction + "航班明细") : [date, direction, airline, flightPart, "航班明细"].filter(Boolean).join(" ").trim();
    }
    return [date, direction, airline, keyword, "航班动态"].filter(Boolean).join(" ").trim();
  }

  if (domain === "航空公司") {
    return ["查询航空公司", airline || keyword].filter(Boolean).join(" ").trim();
  }

  if (domain === "机场") {
    return ["查询机场", keyword || airline].filter(Boolean).join(" ").trim();
  }

  if (domain === "停机位") {
    return ["查询机位", keyword].filter(Boolean).join(" ").trim();
  }

  if (domain === "登机口") {
    return ["查询登机口", keyword].filter(Boolean).join(" ").trim();
  }

  if (domain === "飞机/航空器") {
    return ["查询飞机", keyword].filter(Boolean).join(" ").trim();
  }

  if (domain === "延误与异常原因") {
    return [date, keyword, "延误原因"].filter(Boolean).join(" ").trim();
  }

  if (domain === "航班生产量(旅客/货邮)") {
    const action = intent === "count" ? "生产量统计" : "航班生产量";
    return [date, direction, airline, keyword, action].filter(Boolean).join(" ").trim();
  }

  if (domain === "保障过程与时刻") {
    return [date, direction, keyword, "保障时刻明细"].filter(Boolean).join(" ").trim();
  }

  const action = { detail: "明细", count: "统计数量", status: "情况" }[intent] || "";
  return [date, direction, airline, domain, keyword, action].filter(Boolean).join(" ").trim();
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
