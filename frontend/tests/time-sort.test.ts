import assert from "node:assert/strict";
import test from "node:test";
import { parseTemporalSortKey, sortTemporalItems } from "../lib/time-sort.ts";

test("sorts hourly trend points in chronological ascending order", () => {
  const input = [
    { label: "12:00", value: 37 },
    { label: "08:00", value: 36 },
    { label: "23:00", value: 14 },
    { label: "01:00", value: 3 },
    { label: "05:00", value: 1 },
  ];
  const sorted = sortTemporalItems(input, pt => pt.label);
  assert.deepEqual(
    sorted.map(pt => pt.label),
    ["01:00", "05:00", "08:00", "12:00", "23:00"],
  );
});

test("uses full time_key for cross-midnight operational-day series", () => {
  const input = [
    { label: "01:00", value: 5, time_key: "2026-11-10 01:00" },
    { label: "23:00", value: 18, time_key: "2026-11-09 23:00" },
    { label: "06:00", value: 22, time_key: "2026-11-09 06:00" },
  ];
  const sorted = sortTemporalItems(input, pt => pt.time_key || pt.label, pt => pt.label);
  assert.deepEqual(
    sorted.map(pt => pt.label),
    ["06:00", "23:00", "01:00"],
  );
});

test("sorts dates, months, quarters, and Chinese hour labels chronologically", () => {
  assert.deepEqual(
    sortTemporalItems(["2026-11-15", "2026-11-02", "2026-11-09"], x => x),
    ["2026-11-02", "2026-11-09", "2026-11-15"],
  );
  assert.deepEqual(
    sortTemporalItems(["2026-11", "2026-02", "2026-09"], x => x),
    ["2026-02", "2026-09", "2026-11"],
  );
  assert.deepEqual(
    sortTemporalItems(["2026-Q4", "2026-Q1", "2026-Q3"], x => x),
    ["2026-Q1", "2026-Q3", "2026-Q4"],
  );
  assert.deepEqual(
    sortTemporalItems(["12时", "2时", "09时"], x => x),
    ["2时", "09时", "12时"],
  );
});

test("preserves original order for non-temporal categories", () => {
  const airlines = [
    { label: "南方航空（CZ）", value: 120 },
    { label: "东方航空（MU）", value: 98 },
    { label: "中国国航（CA）", value: 75 },
  ];
  assert.deepEqual(
    sortTemporalItems(airlines, x => x.label).map(x => x.label),
    ["南方航空（CZ）", "东方航空（MU）", "中国国航（CA）"],
  );
  assert.equal(parseTemporalSortKey("南方航空（CZ）"), null);
});
