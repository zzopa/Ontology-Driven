/**
 * Chronological sorting helpers for all time-based charts (trend, hourly, date, month, week, quarter).
 */

export type TemporalSortKey = [number, number, number, number, number, number, number];

export function parseTemporalSortKey(raw: string | number | null | undefined): TemporalSortKey | null {
  if (raw == null) return null;
  if (typeof raw === "number") {
    if (!Number.isInteger(raw)) return null;
    if (raw >= 0 && raw <= 23) return [8, 0, 0, 0, raw, 0, 0];
    if (raw >= 1900 && raw <= 2100) return [5, raw, 0, 0, 0, 0, 0];
    return null;
  }

  const text = String(raw).trim();
  if (!text || text === "未标注" || text === "—" || text === "-") return null;

  // Full datetime: 2026-11-09 12:00(:00) / 2026-11-09T12:00:00+08:00 / 2026-11-09 12时
  let m = text.match(
    /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[T\s]+(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?|时|点)/,
  );
  if (m) {
    return [0, Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5] || 0), Number(m[6] || 0)];
  }

  // Date: 2026-11-09 / 2026年11月9日
  m = text.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})[日号]?$/);
  if (m) {
    return [1, Number(m[1]), Number(m[2]), Number(m[3]), 0, 0, 0];
  }

  // Year-Month: 2026-11 / 2026年11月
  m = text.match(/^(\d{4})[-/.年](\d{1,2})月?$/);
  if (m) {
    const mo = Number(m[2]);
    if (mo >= 1 && mo <= 12) return [2, Number(m[1]), mo, 0, 0, 0, 0];
  }

  // Year-Week: 2026-W45 / 2026年第45周
  m = text.match(/^(\d{4})(?:[-\s]?W|年第?)(\d{1,2})周?$/i);
  if (m) {
    return [3, Number(m[1]), Number(m[2]), 0, 0, 0, 0];
  }

  // Year-Quarter: 2026-Q1 / 2026年第1季度
  m = text.match(/^(\d{4})(?:[-\s]?Q|年第?)([1-4])(?:季度|季)?$/i);
  if (m) {
    return [4, Number(m[1]), Number(m[2]), 0, 0, 0, 0];
  }

  // Year: 2026 / 2026年
  m = text.match(/^(\d{4})年?$/);
  if (m) {
    const yr = Number(m[1]);
    if (yr >= 1900 && yr <= 2100) return [5, yr, 0, 0, 0, 0, 0];
  }

  // Month-Day: 11-09 / 11月9日
  m = text.match(/^(\d{1,2})[-/.月](\d{1,2})[日号]?$/);
  if (m) {
    const mo = Number(m[1]);
    const da = Number(m[2]);
    if (mo >= 1 && mo <= 12 && da >= 1 && da <= 31) return [6, 0, mo, da, 0, 0, 0];
  }

  // Month only: 11月
  m = text.match(/^(\d{1,2})月$/);
  if (m) {
    const mo = Number(m[1]);
    if (mo >= 1 && mo <= 12) return [7, 0, mo, 0, 0, 0, 0];
  }

  // Time / Hour: 12:00 / 12:00:00 / 12:00-13:00 / 12时 / 12点 / pure 0..23
  m = text.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*[-~～]\s*\d{1,2}:\d{2})?$/);
  if (m) {
    const hr = Number(m[1]);
    const mn = Number(m[2]);
    const sc = Number(m[3] || 0);
    if (hr >= 0 && hr <= 23 && mn >= 0 && mn <= 59) return [8, 0, 0, 0, hr, mn, sc];
  }

  m = text.match(/^(\d{1,2})\s*[时点](?:\s*[-~～]\s*\d{1,2}\s*[时点])?$/);
  if (m) {
    const hr = Number(m[1]);
    if (hr >= 0 && hr <= 23) return [8, 0, 0, 0, hr, 0, 0];
  }

  if (/^\d{1,2}$/.test(text)) {
    const hr = Number(text);
    if (hr >= 0 && hr <= 23) return [8, 0, 0, 0, hr, 0, 0];
  }

  return null;
}

function compareKeys(a: TemporalSortKey, b: TemporalSortKey): number {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

/**
 * Sort items in ascending chronological order when their labels/timeKeys represent time points.
 * Leaves non-temporal lists untouched.
 */
export function sortTemporalItems<T>(
  items: T[],
  getKey: (item: T) => string | number | null | undefined,
  getLabel?: (item: T) => string,
): T[] {
  if (items.length <= 1) return items;
  const parsed = items.map((item, idx) => {
    const rawKey = getKey(item);
    const label = getLabel ? getLabel(item) : String(rawKey ?? "");
    const key = parseTemporalSortKey(rawKey) ?? parseTemporalSortKey(label);
    return { item, idx, key, isUnlabeled: label.trim() === "未标注" };
  });

  const validCount = parsed.filter(p => p.key !== null).length;
  const allTemporalOrUnlabeled = parsed.every(p => p.key !== null || p.isUnlabeled);
  if (validCount < 2 || !allTemporalOrUnlabeled) {
    return items;
  }

  return [...parsed]
    .sort((a, b) => {
      if (a.key && b.key) {
        const cmp = compareKeys(a.key, b.key);
        return cmp !== 0 ? cmp : a.idx - b.idx;
      }
      if (a.key) return -1;
      if (b.key) return 1;
      return a.idx - b.idx;
    })
    .map(p => p.item);
}
