import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const hooks = registerHooks({ resolve(specifier, context, next) {
  if (context.parentURL?.endsWith("/lib/ops.ts") && specifier === "./api") return next(new URL("../lib/api.ts", import.meta.url).href, context);
  if (context.parentURL?.endsWith("/lib/api.ts") && specifier === "./query") return next(new URL("../lib/query.ts", import.meta.url).href, context);
  return next(specifier, context);
} });
const { resourceConfirmed, timelinePosition, opsTime, opsRequest, opsTerminals, inputISO, opsNow, opsMode, opsHref, localInput, replayDataDate } = await import("../lib/ops.ts");
hooks.deregister();

test("whole-airport selection is restricted to the static admin view", () => {
  const me = { admin: true, read_only: true, data_mode: 'static-test', terminals: ['T2', 'T3'] } as Parameters<typeof opsTerminals>[0];
  assert.deepEqual(opsTerminals(me), ['ALL', 'T2', 'T3']);
  assert.deepEqual(opsTerminals({ ...me, admin: false, terminals: ['T2'] }), ['T2']);
  assert.deepEqual(opsTerminals({ ...me, read_only: false }), ['T2', 'T3']);
});

test("cross-midnight resource timing uses full dates rather than clock strings", () => {
  assert.equal(timelinePosition("2026-11-12T00:00:00+08:00", "2026-11-11T23:30:00+08:00", "2026-11-12T00:30:00+08:00"), 50);
  assert.equal(timelinePosition("2026-11-11T23:00:00+08:00", "2026-11-11T23:30:00+08:00", "2026-11-12T00:30:00+08:00"), 0);
});

test("expired and future position confirmations are never presented as valid", () => {
  const r = { confirmed_at: "2026-11-11T10:20:00+08:00" } as Parameters<typeof resourceConfirmed>[0];
  assert.equal(resourceConfirmed(r, "2026-11-11T10:32:00+08:00"), false);
  r.confirmed_at = "2026-11-11T10:31:00+08:00";
  assert.equal(resourceConfirmed(r, "2026-11-11T10:32:00+08:00"), true);
  r.confirmed_at = "2026-11-11T11:00:00+08:00";
  assert.equal(resourceConfirmed(r, "2026-11-11T10:32:00+08:00"), false);
});

test("missing and invalid timestamps do not acquire demonstration values", () => {
  assert.equal(opsTime(null), "未记录");
  assert.equal(opsTime("invalid"), "时刻无效");
  assert.throws(() => inputISO(null));
  assert.throws(() => inputISO("not-a-date"));
  assert.match(opsTime("2026-11-11T02:32:00Z"), /10:32/);
});

test("ops requests stay in their namespace and retain same-origin write markers", async () => {
  const original = globalThis.fetch;
  let seen: { path?: string; init?: RequestInit } = {};
  globalThis.fetch = async (path, init) => { seen = { path: String(path), init }; return new Response('{"ok":true}', { headers: { "Content-Type": "application/json" } }); };
  try {
    await opsRequest("/proposals?test=1", "T2", { task_id: "42" });
    assert.equal(seen.path, "/api/ops/proposals?test=1&terminal=T2&mode=replay");
    assert.equal(seen.init?.method, "POST");
    assert.equal((seen.init?.headers as Record<string, string>)["X-Hbask-Request"], "1");
    assert.deepEqual(JSON.parse(String(seen.init?.body)), { task_id: "42" });
  } finally { globalThis.fetch = original; }
});

test("the default simulation clock remains within the confirmed November 10 dataset", () => {
  const original = Date.now;
  Date.now = () => Date.parse("2026-11-12T02:32:45.123Z");
  try {
    assert.equal(opsMode(), "replay");
    assert.equal(opsNow().toISOString(), "2026-11-10T02:32:45.123Z");
    assert.equal(localInput(), "2026-11-10T10:32");
    assert.equal(inputISO("2026-11-10T10:32"), "2026-11-10T02:32:00.000Z");
    assert.equal(opsHref("/ops/dispatch", "T2", { proposal_id: "a&b" }), "/ops/dispatch?terminal=T2&mode=replay&proposal_id=a%26b");
    Date.now = () => Date.parse("2026-11-11T16:05:00Z");
    assert.equal(localInput(), "2026-11-10T00:05");
  } finally { Date.now = original; }
});

test("a selected historical instant is fixed and retained by every page and request", async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const originalFetch = globalThis.fetch;
  const at = "2026-11-10T10:00:00+08:00";
  Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { search: `?mode=replay&at=${encodeURIComponent(at)}` } } });
  let path = "";
  globalThis.fetch = async value => { path = String(value); return new Response('{}'); };
  try {
    assert.equal(replayDataDate, "2026-11-10");
    assert.equal(opsNow().toISOString(), "2026-11-10T02:00:00.000Z");
    assert.equal(localInput(), "2026-11-10T10:00");
    for (const page of ["/ops", "/ops/flights", "/ops/dispatch", "/ops/work-orders", "/ops/resources"]) {
      assert.equal(new URL(opsHref(page, "T2"), "http://test").searchParams.get("at"), at);
    }
    for (const endpoint of ["/snapshot", "/proposals", "/work-orders/id/start", "/resources"] ) {
      await opsRequest(endpoint, "T2", endpoint.endsWith("start") ? { actual_at: inputISO(localInput()) } : undefined);
      assert.equal(new URL(path, "http://test").searchParams.get("at"), at);
    }
  } finally {
    globalThis.fetch = originalFetch;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

test("explicit live mode retains the real clock and mode across navigation and requests", async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { search: "?mode=live&at=2026-11-10T10:00" } } });
  let path = "";
  globalThis.fetch = async value => { path = String(value); return new Response('{}'); };
  try {
    assert.equal(opsMode(), "live");
    assert.ok(Math.abs(opsNow().getTime() - Date.now()) < 100);
    assert.equal(opsHref("/ops/work-orders", "T2"), "/ops/work-orders?terminal=T2&mode=live");
    await opsRequest("/snapshot", "T2");
    assert.equal(path, "/api/ops/snapshot?terminal=T2&mode=live");
  } finally {
    globalThis.fetch = originalFetch;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  }
});
