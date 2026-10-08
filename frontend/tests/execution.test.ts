import assert from "node:assert/strict";
import test from "node:test";
import { updateStep, stopSteps, formatStepElapsed } from "../lib/execution.ts";
import type { ExecutionStep } from "../lib/types.ts";

const step: ExecutionStep = { id: "sql", label: "实时查询", state: "running", message: "执行中", details: [], started_at: 1, elapsed: 0 };

test("fast steps keep millisecond precision", () => {
  assert.equal(formatStepElapsed(0.043), "43 ms");
  assert.equal(formatStepElapsed(2.25), "2.3 s");
});

test("stream updates one milestone rather than duplicating it", () => {
  const initial = updateStep([], step);
  const finished = updateStep(initial, { ...step, state: "completed", message: "命中 0 条", elapsed: 2 });
  assert.equal(finished.length, 1);
  assert.equal(finished[0].state, "completed");
  assert.equal(initial[0].state, "running");
});

test("failure retains completed steps and the active failure", () => {
  const steps = [{ ...step, id: "plan", state: "completed" as const }, step];
  const failed = stopSteps(steps, "连接失败");
  assert.equal(failed[0].state, "completed");
  assert.equal(failed[1].state, "failed");
  assert.equal(failed[1].message, "连接失败");
});

test("stop waiting does not falsely report a backend failure", () => {
  assert.equal(stopSteps([step], "停止等待", true)[0].state, "interrupted");
});
