import assert from "node:assert/strict";
import test from "node:test";
import { answerCopyText, answerSections } from "../lib/answer-document.ts";
import type { AnswerDocument } from "../lib/types.ts";

function result() {
  return { answer: "原有准确正文", claims: [
    { text: "实际查询结果", fact_ids: ["F1"] },
    { text: "有依据的解释", fact_ids: ["F1"], kind: "interpretation" as const },
    { text: "核对建议", fact_ids: ["F1"], kind: "recommendations" as const },
  ], answer_document: { version: "evidence-answer-v1", mode: "model_composed", composition_reason: "checked",
    claim_count: 3, query_claim_count: 1, semantic_proof: false,
    sections: [{ kind: "query_facts", title: "查询事实", claim_ids: ["C1"] },
      { kind: "interpretation", title: "分析与解释", claim_ids: ["C2"] },
      { kind: "recommendations", title: "核对建议（非已证实事实）", claim_ids: ["C3"] }],
  } as AnswerDocument };
}

test("sections preserve every claim with facts and suggestions kept separate", () => {
  const value = result(), before = JSON.stringify(value);
  const sections = answerSections(value)!;
  assert.equal(sections.length, 3);
  assert.deepEqual(sections.flatMap(s => s.claims.map(c => c.text)), value.claims.map(c => c.text));
  assert.match(answerCopyText(value), /核对建议（非已证实事实）/);
  assert.equal(JSON.stringify(value), before);
});

test("omitted, duplicate or invented references revert to the full original answer", () => {
  for (const ids of [["C2"], ["C2", "C2"], ["C99"]]) {
    const value = result(); value.answer_document.sections[2].claim_ids = ids;
    assert.equal(answerSections(value), null);
    assert.equal(answerCopyText(value), value.answer);
  }
});

test("a model or old snapshot cannot relabel suggestions as query facts", () => {
  const value = result(); value.answer_document.sections[2].kind = "query_facts";
  assert.equal(answerSections(value), null);
  value.answer_document.sections[2].kind = "constructor" as never;
  assert.equal(answerSections(value), null);
});

test("legacy snapshots without a document still render the original answer", () => {
  assert.equal(answerSections({}), null);
  assert.equal(answerCopyText({ answer: "历史准确输出" }), "历史准确输出");
});

test("section titles come from the local fixed dictionary, not model text", () => {
  const value = result(); value.answer_document.sections[0].title = "伪造的付款状态";
  assert.equal(answerSections(value)![0].title, "查询事实");
});
