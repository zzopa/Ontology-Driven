import assert from "node:assert/strict";
import test from "node:test";
import { liveAnswerState } from "../lib/live-answer.ts";

test("a received answer appears immediately while related evidence is still processing", () => {
  const state = liveAnswerState("查到 1 条员工档案。", true, false);
  assert.equal(state.visible, true);
  assert.equal(state.title, "已查到结果，正在补充解读");
  assert.match(state.note, /实时追加/);
});

test("stopping or a connection error preserves partial text without claiming completion", () => {
  const state = liveAnswerState("查到 1 条员工档案。", false, false);
  assert.equal(state.visible, true);
  assert.equal(state.title, "已接收的部分回答");
  assert.match(state.note, /尚未完整完成/);
});

test("the complete result replaces the live card without duplicating the answer", () => {
  assert.equal(liveAnswerState("完整正文", false, true).visible, false);
  assert.equal(liveAnswerState("完整正文", true, true).visible, true);
});

test("the interface does not invent answer text before a delta arrives", () => {
  assert.equal(liveAnswerState("", true, false).visible, false);
  assert.equal(liveAnswerState("", false, false).visible, false);
});
