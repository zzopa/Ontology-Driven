import assert from "node:assert/strict";
import test from "node:test";
import { formatBytes, formatDate, formatDurationMs, SYNC_JOB_LABELS, SYNC_STATUS_LABELS } from "../lib/admin.ts";

test("sync formatting helpers handle edge durations and sizes", () => {
  assert.equal(formatDurationMs(0), "< 1s");
  assert.equal(formatDurationMs(450), "450 ms");
  assert.equal(formatDurationMs(89894), "1m 30s");
  assert.equal(formatDurationMs(12500), "12.5 s");
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1024), "1.0 KB");
  assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  assert.equal(formatDate(null), "—");
});

test("sync job and status dictionaries cover all pipeline states", () => {
  for (const key of ["full_sync", "full_pipeline", "flights_scheduled", "flights_fims", "flights_acdm", "production", "codeshares", "master_data"]) {
    assert.ok(SYNC_JOB_LABELS[key], `missing job label: ${key}`);
  }
  for (const status of ["running", "completed", "partial", "failed", "interrupted"]) {
    assert.ok(SYNC_STATUS_LABELS[status], `missing status label: ${status}`);
  }
});
