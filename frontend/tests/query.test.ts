import assert from "node:assert/strict";
import test from "node:test";
import { composeQuestion, parseEventLines, formatValue, trustedParentOrigin } from "../lib/query.ts";

test("form exposes all submitted conditions in the question", () => {
  assert.equal(composeQuestion({ domain: "合同", company: "信科", keyword: "采购", month: "2026-03", intent: "count" }), "信科 2026年3月 合同 采购 统计记录数量");
});
test("empty optional filters do not invent conditions", () => {
  assert.equal(composeQuestion({ domain: "全部业务", company: "", keyword: "李康平", month: "", intent: "detail" }), "李康平 查询详细信息");
});
test("NDJSON buffers partial events and accepts an unterminated final line", () => {
  assert.deepEqual(parseEventLines('{"type":"progress"}\n{"ty'), { lines: ['{"type":"progress"}'], rest: '{"ty' });
  assert.deepEqual(parseEventLines('{"type":"result"}', true), { lines: ['{"type":"result"}'], rest: "" });
});
test("all nested and null fields remain inspectable", () => {
  assert.equal(formatValue(null), "—");
  assert.equal(formatValue({ name: "会议", nested: [1, 2] }), JSON.stringify({ name: "会议", nested: [1, 2] }, null, 2));
});
test("iframe messages only use the exact trusted parent origin", () => {
  assert.equal(trustedParentOrigin(null, "http://127.0.0.1:8088", []), "http://127.0.0.1:8088");
  assert.equal(trustedParentOrigin("http://evil.test", "http://127.0.0.1:8088", ["http://host.test:9000"]), null);
  assert.equal(trustedParentOrigin("http://host.test:9000", "http://127.0.0.1:8088", ["http://host.test:9000"]), "http://host.test:9000");
});
