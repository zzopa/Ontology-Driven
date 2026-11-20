"""Shared runtime bridge for governance artifacts (rules, capabilities, templates, operational day)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any


def _published_only(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in (items or []) if item.get('status') in (None, '', 'PUBLISHED')]


def match_plan_template(catalog: Any, plan: dict[str, Any], hints: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve the best matching published plan_template for a validated query plan."""
    templates = _published_only(getattr(catalog, 'plan_templates', []))
    if not templates:
        return None
    hint_codes = {
        *(c.get('code') for c in (hints or {}).get('concepts', []) if c.get('code')),
        *(s.get('code') for s in (hints or {}).get('scenarios', []) if s.get('code')),
    }
    intent = plan.get('intent', 'detail')
    expected_types = {
        'metric': ('single_metric', 'metric_query'),
        'sum': ('single_metric', 'metric_query'),
        'detail': ('flight_list', 'detail_query'),
        'count': ('count_query', 'flight_list', 'single_metric'),
        'acdm_report': ('acdm_report',),
        'turnaround': ('turnaround',),
    }.get(intent, ('single_metric',))

    scored: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for idx, tpl in enumerate(templates):
        triggers = set(tpl.get('triggerConcepts') or [])
        concept_hits = len(triggers.intersection(hint_codes))
        ptype = str(tpl.get('planType') or '').strip()
        type_match = 1 if ptype in expected_types else 0
        if concept_hits == 0 and type_match == 0:
            continue
        scored.append(((concept_hits, type_match, -idx), tpl))
    if not scored:
        return None
    best = max(scored, key=lambda x: x[0])[1]
    return {
        'code': best.get('code', ''),
        'name': best.get('name', ''),
        'planType': best.get('planType', ''),
        'requiredCaps': list(best.get('requiredCaps') or []),
        'responseTemplate': best.get('responseTemplate') or '',
        'steps': list(best.get('steps') or []),
    }


def match_rules_for_plan(catalog: Any, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect published rule_definition records relevant to the plan's metric or subject."""
    rules = _published_only(getattr(catalog, 'business_rules', []))
    if not rules:
        return []
    metric_key = plan.get('metric')
    metric_cfg = (getattr(catalog, 'metrics', {}) or {}).get(metric_key or '') if isinstance(metric_key, str) else None
    metric_rule_code = (metric_cfg or {}).get('rule_code')
    subject = plan.get('subject')
    metric_domain = str((metric_cfg or {}).get('domain') or '').upper()

    matched: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for r in rules:
        code = r.get('code', '')
        out = r.get('outputMapping') if isinstance(r.get('outputMapping'), dict) else {}
        applies_to = out.get('applies_to')
        if isinstance(applies_to, str):
            applies_to = [applies_to]
        table_hit = isinstance(applies_to, list) and subject in applies_to
        domain_hit = bool(metric_domain and str(r.get('domain') or '').upper() == metric_domain and r.get('ruleType') == 'validation')
        is_exact = bool(metric_rule_code and code == metric_rule_code)
        if not (is_exact or table_hit or domain_hit):
            continue
        matched.append(((1 if is_exact else 0, int(r.get('priority') or 100)), {
            'code': code,
            'name': r.get('name', code),
            'ruleType': r.get('ruleType', 'derived_field'),
            'versionNo': int(r.get('versionNo') or 1),
            'domain': r.get('domain', ''),
            'description': r.get('description', ''),
            'outputMapping': out,
        }))
    matched.sort(key=lambda item: (-item[0][0], -item[0][1], item[1]['code']))
    return [item[1] for item in matched]


def apply_rule_guardrails(catalog: Any, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach matched governance rules and enforce explicit whitelisted filter guardrails."""
    matched = match_rules_for_plan(catalog, plan)
    subject = plan.get('subject')
    if not subject:
        return matched
    filters_map = getattr(catalog, 'filters', {}) or {}
    attrs = (getattr(catalog, 'entities', {}).get(subject) or {}).get('attributes', {})
    conditions = plan.setdefault('conditions', [])
    existing_cols = {c.get('col') for c in conditions if isinstance(c, dict) and isinstance(c.get('col'), str)}

    for rule in matched:
        req = rule.get('outputMapping', {}).get('required_filters')
        items: list[dict[str, Any]] = []
        if isinstance(req, dict):
            for col, val in req.items():
                items.append({'col': col, 'op': '=', 'value': val})
        elif isinstance(req, list):
            items.extend(item for item in req if isinstance(item, dict))
        for item in items:
            col = item.get('col')
            if not col or col in existing_cols:
                continue
            if col in attrs or col in filters_map:
                op = item.get('op') or '='
                if op in ('=', '!=', '>', '>=', '<', '<=', 'in', 'prefix', 'contains', 'null', 'notnull'):
                    conditions.append({'table': subject, 'col': col, 'op': op, 'value': item.get('value')})
                    existing_cols.add(col)

    if matched:
        plan['applied_rules'] = [
            {k: r[k] for k in ('code', 'name', 'ruleType', 'versionNo', 'domain', 'description')}
            for r in matched
        ]
    return matched


def check_capability_gate(catalog: Any, plan: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify required capabilities, metric availability, and field/table readiness before execution."""
    caps = getattr(catalog, 'capabilities', []) or []
    caps_by_code = {str(c.get('code')): c for c in caps if c.get('code')}
    subject = plan.get('subject') or ''
    intent = plan.get('intent', 'detail')
    metric_key = plan.get('metric')
    metric_cfg = (getattr(catalog, 'metrics', {}) or {}).get(metric_key or '') if isinstance(metric_key, str) else None

    def _blocked(status: str, message: str, cap: dict[str, Any] | None = None, code: str = '') -> dict[str, Any]:
        alts = list((cap or {}).get('alternatives') or [])
        full_msg = message
        if alts:
            hint_txt = '；'.join(str(a) for a in alts[:3])
            if hint_txt not in full_msg:
                full_msg = f'{full_msg} 建议：{hint_txt}'
        return {
            'allowed': False,
            'status': status,
            'capabilityCode': code or (cap or {}).get('code', ''),
            'message': full_msg,
            'alternatives': alts,
        }

    # 1. Metric explicit PLANNED/UNAVAILABLE gate
    if metric_cfg and metric_cfg.get('availability') in ('PLANNED', 'UNAVAILABLE'):
        status = metric_cfg['availability']
        matched_cap = caps_by_code.get(metric_cfg.get('rule_code') or '')
        if not matched_cap:
            matched_cap = next(
                (c for c in caps if c.get('status') in ('PLANNED', 'UNAVAILABLE')
                 and str(c.get('domain') or '').upper() == str(metric_cfg.get('domain') or '').upper()),
                None,
            )
        detail_note = metric_cfg.get('formula_desc') or metric_cfg.get('description') or ''
        cap_note = (matched_cap or {}).get('fallbackMessage') or ''
        reason = detail_note or cap_note
        msg = f"指标「{metric_cfg.get('name', metric_key)}」当前状态为 {status}（暂未开放或缺少数据源）"
        if reason:
            msg += f"：{reason}"
        elif cap_note and cap_note not in msg:
            msg += f"：{cap_note}"
        return _blocked(status, msg, matched_cap, metric_cfg.get('rule_code') or '')

    # 2. Plan template requiredCaps gate
    if template:
        for cap_code in template.get('requiredCaps') or []:
            cap = caps_by_code.get(cap_code)
            if cap and cap.get('status') in ('PLANNED', 'UNAVAILABLE'):
                msg = cap.get('fallbackMessage') or f"所需数据能力「{cap.get('name', cap_code)}」当前不可用（{cap.get('status')}）。"
                return _blocked(cap['status'], msg, cap, cap_code)

    # 3. Direct capability gate for reports / tables / blocked columns
    if intent == 'acdm_report':
        rpt_cap = caps_by_code.get('acdm.reports')
        if rpt_cap and rpt_cap.get('status') in ('PLANNED', 'UNAVAILABLE'):
            msg = rpt_cap.get('fallbackMessage') or 'A-CDM 专题报告能力当前未开放。'
            return _blocked(rpt_cap['status'], msg, rpt_cap, 'acdm.reports')

    requested_cols = {
        *(c.get('col') for c in (plan.get('conditions') or []) if isinstance(c, dict) and isinstance(c.get('col'), str)),
        *(g for g in (plan.get('group_by') or []) if isinstance(g, str)),
    }
    for cap in caps:
        status = cap.get('status')
        if status not in ('PLANNED', 'UNAVAILABLE'):
            continue
        cov = cap.get('coverageJson') if isinstance(cap.get('coverageJson'), dict) else {}
        cov_tables = cov.get('tables') or []
        if subject and isinstance(cov_tables, list) and subject in cov_tables:
            msg = cap.get('fallbackMessage') or f"能力「{cap.get('name', cap.get('code'))}」当前状态为 {status}，暂不可查询。"
            return _blocked(status, msg, cap)
        blocked_cols = set(cov.get('blocked_fields') or []) | set(cov.get('missing_columns') or [])
        hit_cols = requested_cols.intersection(blocked_cols)
        if hit_cols:
            msg = cap.get('fallbackMessage') or f"涉及尚未接入的数据字段（{', '.join(sorted(hit_cols))}），当前暂不可查询。"
            return _blocked(status, msg, cap)

    return {'allowed': True, 'status': 'AVAILABLE', 'capabilityCode': (template or {}).get('requiredCaps', [''])[0] if template and template.get('requiredCaps') else ''}


def _period_label(plan: dict[str, Any]) -> str:
    period = plan.get('query_context', {}).get('slots', {}).get('period')
    if isinstance(period, (list, tuple)) and len(period) == 2:
        start, end = date.fromisoformat(period[0]), date.fromisoformat(period[1]) - timedelta(days=1)
        return str(start) if start == end else f'{start}至{end}'
    conditions = plan.get('conditions') or []
    starts = [str(c.get('value') or '')[:10] for c in conditions if c.get('op') in ('>=', 'prefix') and re.match(r'^\d{4}-\d{2}-\d{2}', str(c.get('value') or ''))]
    if starts:
        boundary = next((str(c.get('value'))[:10] for c in conditions if c.get('op') == '<'
                         and re.match(r'^\d{4}-\d{2}-\d{2}', str(c.get('value') or ''))), None)
        if boundary:
            end = date.fromisoformat(boundary) - timedelta(days=1)
            if str(end) != starts[0]:
                return f'{starts[0]}至{end}'
        return starts[0]
    prefixes = [str(c.get('value') or '') for c in conditions if c.get('op') == 'prefix' and str(c.get('value') or '')]
    return prefixes[0] if prefixes else ''


def render_template_summary(catalog: Any, plan: dict[str, Any], evidence: dict[str, Any], main_total: int) -> str | None:
    """Safely render plan_template.responseTemplate using only verified fact values."""
    tpl = plan.get('plan_template')
    raw_tpl = str((tpl or {}).get('responseTemplate') or '').strip()
    if not raw_tpl:
        return None
    facts = evidence.get('facts') or []
    intent = plan.get('intent')
    period = _period_label(plan)
    subject_name = catalog.entities.get(plan.get('subject') or '', {}).get('name', plan.get('subject') or '')

    if intent in ('metric', 'sum'):
        if len(facts) != 1 or plan.get('group_by'):
            return None
        fact = facts[0]
        metric = fact.get('metric') or {}
        values = fact.get('values') or {}
        val_alias = metric.get('value_alias') or metric.get('field') or 'total'
        val = values.get(val_alias)
        if val is None:
            for cand in (metric.get('code'), metric.get('key'), 'total', 'mx'):
                if cand and cand in values and values[cand] is not None:
                    val = values[cand]
                    break
        if val is None:
            return None
        vars_map = {
            'metricName': metric.get('name') or fact.get('name') or '',
            'value': str(val),
            'unit': str(metric.get('unit') or ''),
            'count': str(values.get('n', main_total)),
            'total': str(values.get('n', main_total)),
            'n': str(values.get('n', main_total)),
            'period': period,
            'subject': subject_name,
        }
        rendered = raw_tpl
        if rendered.startswith('今日') and period:
            rendered = f'{period} ' + rendered[2:]
        for k, v in vars_map.items():
            rendered = rendered.replace('{{' + k + '}}', v).replace('{' + k + '}', v)
        if '{' in rendered or '}' in rendered:
            return None
        if vars_map['unit'] and not rendered.endswith(vars_map['unit']) and '{unit}' not in raw_tpl and '{{unit}}' not in raw_tpl:
            rendered = f"{rendered} {vars_map['unit']}"
        if 'n' in values and '条' not in rendered:
            rendered = f"{rendered}（匹配 {values['n']} 条记录）。"
        elif not rendered.endswith('。'):
            rendered += '。'
        return rendered

    if intent == 'detail':
        # Detail queries must show concrete record identities/details, never collapse into a bare count sentence.
        if '{count}' in raw_tpl or '{{count}}' in raw_tpl or '{total}' in raw_tpl or '{{total}}' in raw_tpl:
            if not any(tok in raw_tpl for tok in ('{items}', '{{items}}', '{details}', '{{details}}')):
                return None

    if intent in ('detail', 'count'):
        vars_map = {
            'count': str(main_total),
            'total': str(main_total),
            'period': period,
            'subject': subject_name,
            'metricName': subject_name,
            'value': str(main_total),
            'unit': '条',
        }
        if '{count}' not in raw_tpl and '{{count}}' not in raw_tpl:
            return None
        rendered = raw_tpl
        for k, v in vars_map.items():
            rendered = rendered.replace('{{' + k + '}}', v).replace('{' + k + '}', v)
        if '{' in rendered or '}' in rendered:
            return None
        return rendered

    return None
