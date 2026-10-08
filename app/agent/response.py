"""Compose every checked claim without changing facts, numbers or SQL plans."""
from copy import deepcopy
import json

from app.agent.prompts import COMPOSITION_PROMPT


SECTION_TITLES = {
    'query_facts': '查询事实',
    'findings': '主要发现',
    'interpretation': '分析与解释',
    'limitations': '证据边界',
    'recommendations': '核对建议（非已证实事实）',
}


def claim_entries(claims, query_claim_count):
    if type(query_claim_count) is not int or not 0 <= query_claim_count <= len(claims):
        raise ValueError('查询事实分组数量无效')
    entries = []
    for i, claim in enumerate(claims, 1):
        kind = 'query_facts' if i <= query_claim_count else claim.get('kind', 'findings')
        if kind not in SECTION_TITLES or (kind == 'query_facts' and i > query_claim_count):
            raise ValueError('回答结论分类无效')
        entries.append({'id': 'C%d' % i, 'text': claim['text'],
                        'fact_ids': deepcopy(claim['fact_ids']), 'kind': kind})
    return entries


def default_layout(entries):
    return [{'kind': kind, 'claim_ids': [item['id'] for item in entries if item['kind'] == kind]}
            for kind in SECTION_TITLES if any(item['kind'] == kind for item in entries)]


def validate_layout(value, entries):
    if not isinstance(value, dict) or set(value) != {'sections'}:
        raise ValueError('编排只能指定结论分组，不能生成新正文')
    sections = value['sections']
    if not isinstance(sections, list) or not 1 <= len(sections) <= len(SECTION_TITLES):
        raise ValueError('编排分组无效')
    index = {item['id']: item for item in entries}
    seen, kinds = set(), set()
    for section in sections:
        if not isinstance(section, dict) or set(section) != {'kind', 'claim_ids'}:
            raise ValueError('编排包含未经允许的正文或属性')
        kind, ids = section['kind'], section['claim_ids']
        if not isinstance(kind, str) or kind not in SECTION_TITLES or kind in kinds:
            raise ValueError('编排分类无效或重复')
        if not isinstance(ids, list) or not ids:
            raise ValueError('编排必须引用结论')
        for cid in ids:
            if (not isinstance(cid, str) or cid not in index or cid in seen or
                    index[cid]['kind'] != kind):
                raise ValueError('编排引用无效、重复或改变结论性质')
            seen.add(cid)
        kinds.add(kind)
    if seen != set(index):
        raise ValueError('编排遗漏了已核验结论')
    if any(item['kind'] == 'query_facts' for item in entries) and sections[0]['kind'] != 'query_facts':
        raise ValueError('查询事实必须放在首组')
    return deepcopy(sections)


def compose_document(question, plan, evidence, claims, query_claim_count,
                     chat=None, budget=18000, timeout=20, trace=None):
    """Bounded layout call; new interpretations belong to checked analysis.

    Raw data already used lossless batches. This stage receives ALL checked
    claims, never a first-N or truncated excerpt, and may only arrange them.
    """
    entries = claim_entries(claims, query_claim_count)
    layout = default_layout(entries)
    report = {'version': 'evidence-answer-v1', 'mode': 'deterministic',
              'composition_reason': 'no_analysis', 'query_claim_count': query_claim_count,
              'claim_count': len(entries), 'semantic_proof': False}
    if trace:
        trace.start('answer_layout', '分析结论与内容编排',
                    '保留查询事实、已核验结论与证据边界；解释和建议与事实分开')
    if entries and len(entries) > query_claim_count and chat:
        payload = json.dumps({'question': question, 'plan': plan,
                              'coverage': evidence.get('coverage', []),
                              'claims': entries}, ensure_ascii=False, default=str)
        if len(payload) > budget:
            report['composition_reason'] = 'budget_exceeded'
        else:
            try:
                content = chat(COMPOSITION_PROMPT, payload, max_tokens=1800,
                               timeout=timeout, enable_thinking=False)
            except Exception:
                report['composition_reason'] = 'provider_error'
            else:
                try:
                    layout = validate_layout(json.loads(content), entries)
                except (ValueError, TypeError):
                    report['composition_reason'] = 'invalid_layout'
                else:
                    report.update(mode='model_composed', composition_reason='checked')
    elif not chat:
        report['composition_reason'] = 'model_or_composition_disabled'
    report['sections'] = [{**section, 'title': SECTION_TITLES[section['kind']]} for section in layout]
    if trace:
        trace.finish('answer_layout', '内容编排通过结论引用校验' if report['mode'] == 'model_composed' else
                     '使用完整确定性分组；原有事实和结论未丢弃',
                     ['编排状态：' + report['composition_reason'],
                      '保留 %d/%d 条已核验结论；编排不新增正文、金额或 SQL' % (len(entries), len(entries))])
    return report
