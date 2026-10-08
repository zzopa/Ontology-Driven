import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import type { StreamEvent } from "../lib/types.ts";

// Next resolves extensionless browser imports. Limit the equivalent Node test
// resolution to this one dependency, without changing production API code.
const resolution = registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === "./query" && context.parentURL?.endsWith("/lib/api.ts") ? "./query.ts" : specifier, context);
  },
});
const { streamQuestion } = await import("../lib/api.ts");
resolution.deregister();

test("streamQuestion delivers complete NDJSON events before later batches and EOF", { timeout: 2000 }, async t => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const fetchMock = t.mock.method(globalThis, "fetch", async () => new Response(body, { status: 200 }));
  const events: StreamEvent[] = [];
  let resolveFirst!: () => void, resolveSecond!: () => void, resolveResult!: () => void;
  const first = new Promise<void>(resolve => { resolveFirst = resolve; });
  const second = new Promise<void>(resolve => { resolveSecond = resolve; });
  const result = new Promise<void>(resolve => { resolveResult = resolve; });
  let finished = false;
  const abort = new AbortController();
  const running = streamQuestion("查找张浩的情况", abort.signal, event => {
    events.push(event);
    if (events.length === 1) resolveFirst();
    if (events.length === 2) resolveSecond();
    if (event.type === "result") resolveResult();
  }, "conversation-test").then(() => { finished = true; });
  const encode = (value: string) => new TextEncoder().encode(value);

  // The next JSON line is incomplete and its later batch does not exist yet.
  controller.enqueue(encode('{"type":"answer_delta","delta":"查到 1 条档案。"}\n{"type":"answer_d'));
  await first;
  assert.deepEqual(events.map(event => ({ type: event.type, delta: event.delta })), [{ type: "answer_delta", delta: "查到 1 条档案。" }]);
  assert.equal(finished, false);

  controller.enqueue(encode('elta","delta":"\\n补充已核验履历。"}\n{"type":"result","data":'));
  await second;
  assert.deepEqual(events.map(event => event.type), ["answer_delta", "answer_delta"]);
  assert.equal(events[1].delta, "\n补充已核验履历。");
  assert.equal(finished, false);

  controller.enqueue(encode('{"ok":true,"answer":"查到 1 条档案。\\n补充已核验履历。"}}\n'));
  await result;
  assert.deepEqual(events.map(event => event.type), ["answer_delta", "answer_delta", "result"]);
  assert.equal(events.filter(event => event.type === "answer_delta").map(event => event.delta).join(""), events[2].data?.answer);
  assert.equal(finished, false, "the result callback also runs before the response body closes");

  const [path, init] = fetchMock.mock.calls[0].arguments;
  assert.equal(path, "/api/ask/stream");
  assert.equal(init?.signal, abort.signal);
  assert.deepEqual(JSON.parse(String(init?.body)), { question: "查找张浩的情况", conversation_id: "conversation-test" });
  controller.close();
  await running;
  assert.equal(finished, true);
});

test("streamQuestion retains received partial callbacks but rejects EOF without a final result", async t => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"type":"answer_delta","delta":"已核验的部分正文"}\n'));
      controller.close();
    },
  });
  t.mock.method(globalThis, "fetch", async () => new Response(body, { status: 200 }));
  const events: StreamEvent[] = [];
  await assert.rejects(streamQuestion("测试", new AbortController().signal, event => events.push(event)), /未收到完整查询结果/);
  assert.deepEqual(events, [{ type: "answer_delta", delta: "已核验的部分正文" }]);
});
