import type { AnswerKind, QueryResult } from "./types";

const TITLES: Record<AnswerKind, string> = {
  query_facts: "查询事实", findings: "主要发现", interpretation: "分析与解释",
  limitations: "证据边界", recommendations: "核对建议（非已证实事实）",
};

// A malformed or older snapshot must show its original answer, not a subset.
export function answerSections(result: Pick<QueryResult, "claims" | "answer_document">) {
  const doc = result.answer_document, claims = result.claims || [];
  if (!doc || doc.version !== "evidence-answer-v1" || doc.claim_count !== claims.length ||
      !Number.isInteger(doc.query_claim_count) || doc.query_claim_count < 0 || doc.query_claim_count > claims.length ||
      !Array.isArray(doc.sections) || !doc.sections.length || doc.sections.length > 5) return null;
  const seen = new Set<string>(), kinds = new Set<AnswerKind>();
  const sections: { kind: AnswerKind; title: string; claims: NonNullable<QueryResult["claims"]> }[] = [];
  for (const section of doc.sections) {
    if (!section || typeof section.kind !== "string" || !Object.hasOwn(TITLES, section.kind) || kinds.has(section.kind) ||
        !Array.isArray(section.claim_ids) || !section.claim_ids.length) return null;
    const selected: NonNullable<QueryResult["claims"]> = [];
    for (const cid of section.claim_ids) {
      if (typeof cid !== "string" || !/^C[1-9]\d*$/.test(cid) || seen.has(cid)) return null;
      const index = Number(cid.slice(1)) - 1, claim = claims[index];
      if (!claim || typeof claim.text !== "string" || !Array.isArray(claim.fact_ids) ||
          section.kind !== (index < doc.query_claim_count ? "query_facts" : claim.kind || "findings")) return null;
      seen.add(cid); selected.push(claim);
    }
    kinds.add(section.kind);
    sections.push({ kind: section.kind, title: TITLES[section.kind], claims: selected });
  }
  if (seen.size !== claims.length || doc.query_claim_count > 0 && sections[0]?.kind !== "query_facts") return null;
  return sections;
}

export function answerCopyText(result: Pick<QueryResult, "claims" | "answer_document" | "answer">) {
  const sections = answerSections(result);
  return sections ? sections.map(s => `${s.title}\n${s.claims.map(c => c.text).join("\n")}`).join("\n\n") : result.answer;
}
