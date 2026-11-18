import assert from "node:assert/strict";
import test from "node:test";
import { queryFlow, queryFlowExpressions } from "../lib/query-flow.ts";
import { stopSteps } from "../lib/execution.ts";
import type { ExecutionStep } from "../lib/types.ts";

function step(id: string, started_at: number, elapsed: number, state: ExecutionStep["state"] = "completed"): ExecutionStep {
  return { id, label: id, state, message: id, details: [], started_at, elapsed };
}

test("a direct match leaves unused semantic branches unvisited, without invented timings", () => {
  const flow = queryFlow([step("rule_match_1", 1, .02), step("plan", 1.02, .01)], true, 2);
  assert.equal(flow.nodes.find(n => n.id === "rules")?.state, "completed");
  for (const id of ["normalize", "rematch", "model"]) {
    const node = flow.nodes.find(n => n.id === id)!;
    assert.equal(node.state, "unvisited");
    assert.equal(node.recorded, false);
  }
});

test("the running semantic stage ticks from its own start and freezes after completion", () => {
  const steps = [step("rule_match_1", 0, .1), step("semantic_normalize_2", .1, 0, "running")];
  assert.equal(queryFlow(steps, true, 3.1).nodes.find(n => n.id === "normalize")?.elapsed, 3);
  steps[1] = { ...steps[1], elapsed: 2, state: "completed" };
  const flow = queryFlow(steps, false, 20);
  assert.equal(flow.nodes.find(n => n.id === "normalize")?.elapsed, 2);
  assert.equal(flow.current, undefined);
});

test("nested and concurrent evidence intervals are counted once", () => {
  const flow = queryFlow([step("answer", 1, 9), step("answer_batch_1", 2, 5), step("answer_layout", 7, 3)], false, 10);
  assert.equal(flow.nodes.find(n => n.id === "answer")?.elapsed, 9);
});

test("analysis child stages retain their category and accumulate separate attempts", () => {
  const flow = queryFlow([step("q1:rule_match_1", 0, 1), step("q2:rule_match_1", 2, 1),
    step("q1:model_plan_3", 3, 1, "failed"), step("q1:model_plan_4", 4, 2)], false, 6);
  assert.equal(flow.nodes.find(n => n.id === "rules")?.elapsed, 2);
  assert.equal(flow.nodes.find(n => n.id === "model")?.elapsed, 3);
  assert.equal(flow.nodes.find(n => n.id === "model")?.state, "completed");
});

test("stopping the browser freezes active durations and retains the interrupted state", () => {
  const stopped = stopSteps([step("semantic_normalize_1", 1, 0, "running")], "停止等待", true, 4);
  const node = queryFlow(stopped, false, 30).nodes.find(n => n.id === "normalize")!;
  assert.equal(node.state, "interrupted");
  assert.equal(node.elapsed, 3);
});

test("old snapshots do not claim that unrecorded semantic branches ran or were skipped", () => {
  const flow = queryFlow([step("coordinator", 0, 2), step("sql", 2, 1), step("done", 3, .1)], false, 3.1);
  assert.equal(flow.nodes.find(n => n.id === "normalize")?.state, "unrecorded");
  assert.equal(queryFlow([], true, 0).nodes.every(n => n.state === "pending"), true);
});

test("question conversion only displays a standard expression after a completed normalization", () => {
  const steps = [step("rule_match_1", 0, .1), step("semantic_normalize_2", .1, 0, "running")];
  steps[0].details = ["原始问法"];
  steps[1].details = ["原始问法"];
  assert.deepEqual(queryFlowExpressions(steps, true), { original: "原始问法", standard: "正在生成标准表达…" });
  assert.equal(queryFlowExpressions(steps, true, "当前用户问题").original, "当前用户问题");
  steps[1] = { ...steps[1], state: "completed", details: ["规范业务问法"] };
  assert.equal(queryFlowExpressions(steps, false).standard, "规范业务问法");
});

test("failed, interrupted and legacy conversions never fabricate a normalized question", () => {
  for (const state of ["failed", "interrupted", "running"] as const) {
    assert.equal(queryFlowExpressions([step("semantic_normalize_1", 0, 1, state)], false).standard, "标准化未完成，保留原始问法");
  }
  assert.equal(queryFlowExpressions([step("semantic_normalize_1", 0, 1, "skipped")], false).standard, "保留原始问法");
  assert.equal(queryFlowExpressions([step("done", 1, .1)], false).standard, "本轮未记录标准表达");
  assert.equal(queryFlowExpressions([step("rule_match_1", 0, .1), step("done", 1, .1)], false).standard, "本轮未使用语义标准化");
});
