import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import type { Message } from "../lib/admin.ts";
import type { QueryResult } from "../lib/types.ts";

const resolution = registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === "./execution" && context.parentURL?.endsWith("/lib/chat-thread.ts") ? "./execution.ts" : specifier, context);
  },
});
const { applyTurnEvent, createChatTurn, failChatTurn, resolvedChoiceId, turnsFromMessages } = await import("../lib/chat-thread.ts");
resolution.deregister();

function fakeResult(overrides: Partial<QueryResult> = {}): QueryResult {
  return {
    ok: true,
    question: "测试问题",
    source: "rule",
    parse: { subject: "mv_acdm_dyn_flight_detail", intent: "metric", conditions: [] },
    intent: "metric",
    sql: "SELECT 1",
    main_total: 1,
    main_rows: [{ val: 95 }],
    related: [],
    field_meta: {},
    graph_tables: [],
    graph: { nodes: [], edges: [] },
    page_size: 20,
    answer: "放行正常率为 97.47%。",
    claims: [],
    answer_validation: "deterministic",
    ontology_version: "35",
    elapsed: 0.25,
    execution_trace: [],
    conversation_id: "conv-1",
    message_id: "msg-1",
    outcome: "answered",
    ...overrides,
  };
}

test("multi-turn chat thread preserves earlier turns when follow-up turns arrive and update", () => {
  const t1 = applyTurnEvent(createChatTurn("t1", "昨天放行正常率是多少？", "昨天放行正常率是多少？"), {
    type: "result",
    data: fakeResult({ message_id: "m1", answer: "放行正常率为 97.47%。" }),
  });
  let t2 = createChatTurn("t2", "各航司前5名呢", "各航司前5名呢");
  t2 = applyTurnEvent(t2, { type: "answer_delta", delta: "南航 98.1%" });
  t2 = applyTurnEvent(t2, {
    type: "result",
    data: fakeResult({ message_id: "m2", question: "各航司前5名呢", answer: "南航 98.1%、东航 97.5%。" }),
  });

  const thread = [t1, t2];
  assert.equal(thread.length, 2);
  assert.equal(thread[0].result?.answer, "放行正常率为 97.47%。");
  assert.equal(thread[1].result?.answer, "南航 98.1%、东航 97.5%。");
});

test("failed clarification continuation retains clarification reference for retry", () => {
  const clar = { clarification_of: "m1", choice_id: "1" };
  const turn = failChatTurn(
    createChatTurn("t2", "实际执行（按实际起飞/落地）", "昨天CZ3357 航班情况如何（实际执行（按实际起飞/落地））", clar),
    "网络暂时中断",
    false,
  );
  assert.equal(turn.error, "网络暂时中断");
  assert.deepEqual(turn.clarification, clar);
  assert.equal(turn.displayQuestion, "昨天CZ3357 航班情况如何（实际执行（按实际起飞/落地））");
});

test("turnsFromMessages restores every historical turn in chronological order", () => {
  const msgs: Message[] = [
    {
      id: "m1",
      question: "昨天CZ3357 航班情况如何",
      status: "completed",
      model: "rule",
      created_at: "2026-11-11T07:00:00Z",
      result: fakeResult({ message_id: "m1", outcome: "clarification_needed", intent: "notice", answer: "计划还是实际？" }),
    },
    {
      id: "m2",
      question: "实际执行（按实际起飞/落地）",
      status: "completed",
      model: "rule",
      created_at: "2026-11-11T07:00:05Z",
      result: fakeResult({ message_id: "m2", outcome: "answered", answer: "1 条航班记录。" }),
    },
  ];
  const restored = turnsFromMessages(msgs);
  assert.equal(restored.length, 2);
  assert.equal(restored[0].result?.answer, "计划还是实际？");
  assert.equal(restored[1].result?.answer, "1 条航班记录。");
  assert.equal(restored[0].isHistorical, true);
  assert.equal(restored[1].isHistorical, true);
  assert.equal(resolvedChoiceId(restored, 0), undefined);
  restored[0].result!.clarification = {
    question: "计划还是实际？",
    options: [{ id: "1", label: "实际执行（按实际起飞/落地）" }, { id: "2", label: "计划航班" }],
  };
  assert.equal(resolvedChoiceId(restored, 0), "1");
});
