"""Bounded, catalog-grounded planning for one authorized operations query."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from app.agent.coordinator_lexicon import (
    ACTUAL,
    BEIJING,
    CORE,
    DATE_RANGE_STRIP_RE,
    DAY_MODE_STRIP_RE,
    FLIGHT_COUNT_METRICS,
    FOLLOW_UP,
    FOLLOWUP_ACTION_PREFIXES,
    FOLLOWUP_DATE_STRIP_RE,
    FOLLOWUP_SHORT_PREFIXES,
    GROUND_BRIEFING_NAME,
    IDENTIFIER_STRIP_RE,
    REPORT_NOUNS_RE,
    REPORT_RESIDUAL_STRIP_RE,
    SEQUENCE,
    TURNAROUND_RESIDUAL_STRIP_RE,
    VERSION,
    VIEW_DETAIL_EXTRA_ALIASES,
    VIEW_DETAIL_RE,
    VIEW_TURNAROUND_RE,
    detect_basis,
    detect_day_mode,
    detect_direction,
    detect_sequence,
    get_basis_options,
    get_basis_replace_pattern,
    get_cancel_status_filter,
    get_default_period_labels,
    get_direction_label,
    get_direction_options,
    get_extra_attribute_aliases,
    get_followup_day_shift_patterns,
    get_followup_fillers,
    get_identifier_columns,
    get_literal_filter_operators,
    get_metric_residual_terms,
    get_relative_day_offsets,
    get_residual_vocabulary,
    get_sequence_label,
    ground_briefing_display_residual,
    is_count,
    json_object,
    month_year_window,
    strip_display_field_clauses,
)
from app.agent.governance_runtime import (
    apply_rule_guardrails,
    check_capability_gate,
    match_plan_template,
)
from app.planner import (
    apply_acdm_metric_slots,
    apply_home_airport_scope,
    extract_acdm_dimensions_and_topn,
    extract_home_airport_city_and_dir,
    match_acdm_report,
    resolve_concept_metric,
)
from app.query import Compiler


def period(question, anchor):
    text = re.sub(
        r'(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})[日号]?',
        lambda m: f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}',
        question,
    )

    def _md_replace(m):
        mo = m.group(1) or m.group(3) or m.group(5)
        da = m.group(2) or m.group(4) or m.group(6)
        if mo and da:
            try:
                return date(anchor.year, int(mo), int(da)).isoformat()
            except ValueError:
                pass
        return m.group(0)

    pat = (
        r'(?<![0-9A-Za-z\-])(\d{1,2})月(\d{1,2})[日号]?'
        r'|(?<![0-9A-Za-z\-])(\d{1,2})[-/\.](\d{1,2})[日号]'
        r'|(?<![0-9A-Za-z\-])(\d{1,2})[-/\.](\d{1,2})(?![0-9A-Za-z\-]|:(?:\d)|\s*(?:架|班|条|分|米|座|元|个|位|人|点|%|折))'
    )
    text = re.sub(pat, _md_replace, text)
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', text)
    if dates:
        start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        if start > end:
            raise ValueError('开始日期不能晚于结束日期')
        return [start.isoformat(), (end + timedelta(days=1)).isoformat()]
    today = anchor.date()
    month_year = month_year_window(question, today)
    if month_year:
        return list(month_year[0])
    for words, days in get_relative_day_offsets():
        if any(word in question for word in words):
            start = today - timedelta(days=days)
            return [start.isoformat(), (start + timedelta(days=1)).isoformat()]
    match = re.search(r'(?:最近|近)([1-9]\d?)天', question)
    if match or re.search(r'最近一周|近一周', question):
        days = int(match[1]) if match else 7
        return [(today - timedelta(days=days - 1)).isoformat(), (today + timedelta(days=1)).isoformat()]
    if re.search(r'本周', question):
        monday = today - timedelta(days=today.weekday())
        return [monday.isoformat(), (today + timedelta(days=1)).isoformat()]
    if re.search(r'上周', question):
        monday = today - timedelta(days=today.weekday() + 7)
        return [monday.isoformat(), (monday + timedelta(days=7)).isoformat()]
    return None


def _extract_identifiers(text):
    """Extract deduplicated aircraft registration and flight number tokens."""
    identifiers = re.sub(r'\d{4}-\d{2}-\d{2}', ' ', text.upper())
    tails = list(
        re.finditer(
            r'(?<![A-Z0-9_])(B-[A-Z0-9]{4,5}|B(?=[A-Z0-9]{0,4}[0-9])[A-Z0-9]{4,5})(?![A-Z0-9_])',
            identifiers,
        )
    )
    numbers = [
        m for m in re.finditer(r'(?<![A-Z0-9])((?:[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])\d{2,4}[A-Z]?)(?![A-Z0-9])', identifiers)
        if not any(t.start() <= m.start() < t.end() for t in tails)
    ]
    extracted = {}
    for key, values in [
        ('aircraft', [m[1].replace('-', '') for m in tails]),
        ('flight', [m[1] for m in numbers]),
    ]:
        values = list(dict.fromkeys(values))
        if values:
            extracted[key] = values[0] if len(values) == 1 else values
    return extracted


def _extract_references(text, references, excluded_terms=()):
    """Match catalog reference dictionary entries while avoiding report/city collisions."""
    # Fixed A-CDM report nouns must not falsely collide with short airline names (e.g. "通用" -> GP).
    ref_q = re.sub(r'(?:A-?CDM|通用报告?|完整运行报告|运行[日周月季年]报|地服(?:公司)?(?:运行)?简报)', ' ', text, flags=re.I)
    for term in excluded_terms:
        if term:
            ref_q = ref_q.replace(term, ' ')
    matched = []
    for ref in sorted(references, key=lambda item: -len(item['term'])):
        term = re.escape(ref['term'])
        pattern = r'(?<![A-Za-z0-9])' + term + r'(?![A-Za-z0-9])' if re.fullmatch(r'[A-Za-z0-9]{1,3}', ref['term']) else term
        found = re.search(pattern, ref_q, re.I)
        if found:
            before = ref_q[max(0, found.start() - 5):found.start()]
            after = ref_q[found.end():found.end() + 3]
            op = '!=' if re.search(r'不是|不含|排除|除去|除了', before) or '以外' in after else '='
            matched.append({**ref, 'op': op})
    # Longer names own shorter overlapping dictionary entries.
    chosen = {}
    for ref in matched:
        chosen.setdefault((ref['role'], str(ref['value'])), ref)
    return list(chosen.values())


def slots(question, anchor, references, catalog=None):
    q = unicodedata.normalize('NFKC', question)
    result = {}
    window = period(q, anchor)
    if window:
        result['period'] = window
    day_mode = detect_day_mode(q)
    if day_mode:
        result['day_mode'] = day_mode

    # 剥离纯字段展示从句后再判定口径与方向，避免“显示计划落地和实际落地时间”干扰主查询口径
    query_body, _ = strip_display_field_clauses(q, catalog=catalog)
    basis_q = query_body
    has_operated_metric = False
    has_planned_metric = False
    if catalog is not None and hasattr(catalog, 'metrics'):
        for m_key, cfg in catalog.metrics.items():
            if m_key in FLIGHT_COUNT_METRICS:
                continue
            for alias in sorted({cfg.get('name', ''), *cfg.get('aliases', [])}, key=len, reverse=True):
                if alias and alias in basis_q:
                    if m_key == 'operated_flight_count' or cfg.get('base_table') == ACTUAL:
                        has_operated_metric = True
                    elif m_key == 'planned_flight_count' or cfg.get('base_table') == CORE:
                        has_planned_metric = True
                    basis_q = basis_q.replace(alias, ' ')

    basis = detect_basis(
        basis_q,
        has_planned_metric=has_planned_metric,
        has_operated_metric=has_operated_metric,
        full_text=query_body,
    )
    if basis:
        result['basis'] = basis

    direction = detect_direction(query_body)
    if direction:
        result['direction'] = direction

    excluded_ref_terms = []
    if catalog is not None:
        explicit_dir, origin_city, dest_city, _home_terms, consumed = extract_home_airport_city_and_dir(q, catalog)
        if 'direction' not in result and explicit_dir:
            result['direction'] = explicit_dir
        if origin_city:
            result['origin_city'] = origin_city
        if dest_city:
            result['dest_city'] = dest_city
        excluded_ref_terms = [origin_city, dest_city, *consumed]

    seq = detect_sequence(q)
    if seq:
        result['sequence'] = seq

    result.update(_extract_identifiers(q))

    refs = _extract_references(q, references, excluded_terms=excluded_ref_terms)
    if refs:
        result['references'] = refs
    return result


class Coordinator:
    def __init__(self, catalog, chat=None, references=(), trace=None):
        self.catalog, self.chat, self.trace = catalog, chat, trace
        self.references = list(references)
        self.compiler = Compiler(catalog)
        self._previous_plan = None
        self._history = []
        self._turn_ignored_terms = []

    def _save_query_context(self, plan, question, expected, anchor):
        """Attach normalized query context and ignored terms for subsequent turns."""
        ctx = plan.get('query_context') or {}
        ctx.update({'question': question, 'slots': deepcopy(expected), 'anchor': anchor.isoformat()})
        ctx.setdefault('raw_question', getattr(self, '_raw_question', question))
        ctx.setdefault('normalized_question', getattr(self, '_normalized_question', question))
        if self._turn_ignored_terms:
            ctx['ignored_terms'] = list(dict.fromkeys(t for t in self._turn_ignored_terms if t))
        plan['query_context'] = ctx
        return plan

    @staticmethod
    def _replace_identifier(text, old_value, new_value='', *, is_aircraft=False):
        """Replace flight or aircraft registration tokens cleanly on word boundaries."""
        tokens = old_value if isinstance(old_value, list) else [old_value] if old_value else []
        updated = text
        for tok in tokens:
            s = str(tok)
            if is_aircraft and s.upper().startswith('B'):
                pat = r'(?<![A-Za-z0-9])B-?' + re.escape(s[1:]) + r'(?![A-Za-z0-9])'
            else:
                pat = r'(?<![A-Za-z0-9])' + re.escape(s) + r'(?![A-Za-z0-9])'
            updated = re.sub(pat, new_value, updated, count=1, flags=re.I)
        return re.sub(r'\s{2,}', ' ', updated).strip()

    @staticmethod
    def _apply_slot_delta(confirmed, delta):
        """Merge extracted slot overrides while keeping flight and aircraft mutually exclusive."""
        if 'flight' in delta:
            confirmed.pop('aircraft', None)
        if 'aircraft' in delta:
            confirmed.pop('flight', None)
        confirmed.update(delta)
        return confirmed

    def _period_choices(self, anchor):
        return [(label, period(label, anchor)) for label in get_default_period_labels()]

    def _strip_followup_fillers(self, text, delta, allow_flights_word=False):
        """Strip resolved slot tokens, dates, identifiers and conversational fillers."""
        rest = text
        for ref in delta.get('references', []):
            if ref.get('term'):
                rest = rest.replace(ref['term'], '')
        if self.catalog is not None and (delta.get('origin_city') or delta.get('dest_city')):
            _, _, _, _home_terms, consumed = extract_home_airport_city_and_dir(text, self.catalog)
            for term in [*consumed, delta.get('origin_city', ''), delta.get('dest_city', '')]:
                if term:
                    rest = rest.replace(term, '')
        rest = FOLLOWUP_DATE_STRIP_RE.sub('', rest)
        rest = DAY_MODE_STRIP_RE.sub('', rest)
        rest = IDENTIFIER_STRIP_RE.sub('', rest)
        for token in get_followup_fillers(
            allow_flights_word=allow_flights_word,
            has_sequence=bool(delta.get('sequence')),
        ):
            rest = rest.replace(token, '')
        return re.sub(r'[\s，,。；;？?！!：:、（）()/]', '', rest)

    def _followup_slots(self, question, prior_slots, anchor):
        q = unicodedata.normalize('NFKC', question).strip()
        delta = slots(q, anchor, self.references, self.catalog)
        rest = q
        base_day = None
        if isinstance(prior_slots.get('period'), list) and prior_slots['period']:
            try:
                base_day = date.fromisoformat(str(prior_slots['period'][0])[:10])
            except ValueError:
                base_day = None
        shift_pats = get_followup_day_shift_patterns()
        if base_day and re.search(shift_pats['prev'], q):
            d_val = base_day - timedelta(days=1)
            delta['period'] = [d_val.isoformat(), (d_val + timedelta(days=1)).isoformat()]
            rest = re.sub(shift_pats['prev'], '', rest)
        elif base_day and re.search(shift_pats['next'], q):
            d_val = base_day + timedelta(days=1)
            delta['period'] = [d_val.isoformat(), (d_val + timedelta(days=1)).isoformat()]
            rest = re.sub(shift_pats['next'], '', rest)
        elif 'period' not in delta:
            ref_day = base_day or anchor.date()
            m_dom = re.search(r'(?<![0-9\-/年月日])([12]?\d|3[01])[号日](?!\d)', q)
            m_md_sep = re.search(r'(?<![0-9A-Za-z\-])(\d{1,2})[\./\-](\d{1,2})[号日]?', q)
            if m_md_sep:
                try:
                    d_val = date(ref_day.year, int(m_md_sep[1]), int(m_md_sep[2]))
                    delta['period'] = [d_val.isoformat(), (d_val + timedelta(days=1)).isoformat()]
                    rest = rest.replace(m_md_sep.group(0), '', 1)
                except ValueError:
                    pass
            elif m_dom:
                try:
                    d_val = date(ref_day.year, ref_day.month, int(m_dom[1]))
                    delta['period'] = [d_val.isoformat(), (d_val + timedelta(days=1)).isoformat()]
                    rest = rest.replace(m_dom.group(0), '', 1)
                except ValueError:
                    pass
        return delta, rest

    def _extract_followup_metric_or_mod(self, q, previous_plan, anchor):
        """Recognize metric swaps or dimension/TopN follow-ups referencing a prior query."""
        prior_slots = (previous_plan.get('query_context') or {}).get('slots') or {}
        delta, rest = self._followup_slots(q, prior_slots, anchor)
        has_prefix = q.startswith(FOLLOWUP_ACTION_PREFIXES)
        has_suffix = bool(re.search(r'(?:呢|吗|呀|吧|看|统计)[？?]?$', q))
        metric_hits = [
            (len(alias), key, alias)
            for key, cfg in self.catalog.metrics.items()
            if key not in FLIGHT_COUNT_METRICS
            for alias in [cfg.get('name', ''), *cfg.get('aliases', [])]
            if alias and alias in q
        ]
        if metric_hits:
            _, metric_key, metric_alias = max(metric_hits)
            if has_prefix or has_suffix or (previous_plan.get('metric') and not delta.get('period')):
                remainder = rest.replace(metric_alias, '')
                remainder = self._strip_followup_fillers(remainder, delta)
                if has_prefix or has_suffix:
                    remainder = re.sub(r'有多少|多少|是', '', remainder)
                if not remainder:
                    return {'metric_key': metric_key, 'metric_term': metric_alias, 'mods': []}, delta
        if previous_plan.get('metric') or previous_plan.get('intent') == 'metric':
            target = previous_plan.get('subject') or 'mv_acdm_dyn_flight_detail'
            group_by, top_opts, mod_terms = extract_acdm_dimensions_and_topn(q, self.catalog, target=target, allow_topn=True)
            if group_by or top_opts:
                remainder = rest
                for term in sorted(set(mod_terms), key=len, reverse=True):
                    if term:
                        remainder = remainder.replace(term, '')
                remainder = self._strip_followup_fillers(remainder, delta)
                remainder = re.sub(r'统计|排名|排行|趋势|分布|看|前|名', '', remainder)
                if not remainder:
                    return {'metric_key': None, 'metric_term': None, 'mods': mod_terms}, delta
        return None, delta

    def _sync_identifiers(self, updated, prior_slots, delta):
        if 'flight' in delta:
            new_fl = delta['flight'] if isinstance(delta['flight'], str) else delta['flight'][0]
            updated = self._replace_identifier(updated, prior_slots.get('flight'), new_fl, is_aircraft=False)
            updated = self._replace_identifier(updated, prior_slots.get('aircraft'), new_fl, is_aircraft=True)
        if 'aircraft' in delta:
            new_ac = delta['aircraft'] if isinstance(delta['aircraft'], str) else delta['aircraft'][0]
            updated = self._replace_identifier(updated, prior_slots.get('flight'), new_ac, is_aircraft=False)
            updated = self._replace_identifier(updated, prior_slots.get('aircraft'), new_ac, is_aircraft=True)
        return updated

    @staticmethod
    def _sync_references_and_cities(updated, prior_slots, delta, ignored):
        if delta.get('references'):
            new_refs = delta['references']
            new_roles = {r.get('role') for r in new_refs if r.get('role')}
            old_refs = prior_slots.get('references') or []
            replaced_first = False
            for old_ref in old_refs:
                term = old_ref.get('term')
                if not term:
                    continue
                if not new_roles or old_ref.get('role') in new_roles or len(old_refs) == len(new_refs) == 1:
                    ignored.append(term)
                    if not replaced_first and len(new_refs) == 1 and term in updated:
                        updated = updated.replace(term, new_refs[0]['term'], 1)
                        replaced_first = True
                    elif term in updated:
                        updated = updated.replace(term, '', 1)
        for city_key in ('origin_city', 'dest_city'):
            if delta.get(city_key) and prior_slots.get(city_key) and prior_slots[city_key] != delta[city_key]:
                old_city = prior_slots[city_key]
                ignored.append(old_city)
                if old_city in updated:
                    updated = updated.replace(old_city, delta[city_key], 1)
        return updated

    def _sync_period_direction_basis(self, updated, prior_slots, delta):
        if delta.get('period') and len(delta['period']) == 2 and prior_slots.get('period') != delta['period']:
            start_d = date.fromisoformat(delta['period'][0])
            end_d = date.fromisoformat(delta['period'][1]) - timedelta(days=1)
            rep_day = start_d.isoformat() if start_d == end_d else f'{start_d.isoformat()}至{end_d.isoformat()}'
            date_expr = rf'{FOLLOWUP_DATE_STRIP_RE.pattern}|大前天|昨天|昨日|前一天|前天|今天|今日'
            updated = re.sub(date_expr, rep_day, updated, count=1)
        if delta.get('direction') and prior_slots.get('direction') != delta['direction']:
            dir_label = get_direction_label(delta['direction'])
            dir_pat = r'进出港|不分方向|全部方向|出港|离港|进港|到港'
            if re.search(dir_pat, updated):
                updated = re.sub(dir_pat, dir_label, updated, count=1)
        if delta.get('basis') and prior_slots.get('basis') != delta['basis']:
            # 保护纯展示字段子句，避免把“显示计划落地”错误改写为“显示实际执飞落地”
            main_part, removed_clauses = strip_display_field_clauses(updated, catalog=self.catalog)
            target_part = main_part if removed_clauses else updated
            if delta['basis'] == 'planned':
                rep_basis = '计划' if '航班' in target_part else '计划航班'
                target_part = re.sub(get_basis_replace_pattern('actual'), rep_basis, target_part, count=1)
            elif delta['basis'] == 'actual':
                target_part = re.sub(get_basis_replace_pattern('planned'), '实际执飞', target_part, count=1)
            updated = (target_part + ''.join(removed_clauses)) if removed_clauses else target_part
        return updated

    def _sync_metric_or_mod(self, updated, metric_or_mod, previous_plan, ignored):
        if not metric_or_mod:
            return updated
        prev_metric = (previous_plan or {}).get('metric')
        prev_target = (previous_plan or {}).get('subject') or 'mv_acdm_dyn_flight_detail'
        _, _, old_mods = extract_acdm_dimensions_and_topn(updated, self.catalog, target=prev_target, allow_topn=True)
        if metric_or_mod.get('metric_key'):
            if prev_metric and prev_metric in self.catalog.metrics:
                cfg = self.catalog.metrics[prev_metric]
                old_aliases = sorted({cfg.get('name', ''), *cfg.get('aliases', [])}, key=len, reverse=True)
                replaced_metric = False
                for alias in old_aliases:
                    if alias and alias in updated:
                        ignored.append(alias)
                        if not replaced_metric:
                            updated = updated.replace(alias, metric_or_mod['metric_term'], 1)
                            replaced_metric = True
                        else:
                            updated = updated.replace(alias, '', 1)
                if not replaced_metric:
                    updated = f"{updated} {metric_or_mod['metric_term']}".strip()
            else:
                updated = f"{updated} {metric_or_mod['metric_term']}".strip()
            for mt in old_mods:
                if mt:
                    ignored.append(mt)
        if metric_or_mod.get('mods'):
            for mt in sorted(set(old_mods), key=len, reverse=True):
                if mt and mt in updated:
                    updated = updated.replace(mt, '', 1)
                    ignored.append(mt)
            updated = f"{updated} {' '.join(metric_or_mod['mods'])}".strip()
        return updated

    def _sync_view_and_count(self, updated, prior_slots, delta, view_switch, follow_text):
        if delta.get('sequence') and prior_slots.get('sequence') != delta['sequence']:
            seq_label = get_sequence_label(delta['sequence'])
            seq_pat = r'(?:最后|最晚|最迟|最早|第一)一?班|末班|首班|最后|最晚|最迟|最早'
            if re.search(seq_pat, updated):
                updated = re.sub(seq_pat, seq_label, updated, count=1)
            else:
                updated = f'{updated} {seq_label}'.strip()
        if delta.get('sequence'):
            updated = re.sub(r'有多少班|有多少条|有多少|多少班|多少条|多少|有几班|有几条|几班|几条|总数', '', updated)
        if view_switch == 'turnaround':
            updated = re.sub(
                r'(?:实际执飞|实际执行|实际航班|计划航班|航班动态|详细信息|详情|明细|动态|进出港|出港|进港|离港|到港|有多少班|有多少条|有多少|多少班|多少条|多少|有几班|有几条|列出|有哪些)',
                '',
                updated,
            )
            if not VIEW_TURNAROUND_RE.search(updated):
                updated = f'{updated} 地服保障情况'.strip()
        elif view_switch == 'detail':
            updated = re.sub(
                r'A-?CDM.*?(?:记录|作业)|地服|地面|过站|保障(?:情况|进度|状态|诊断)?|作业|诊断|甘特图?|时序图?|时间轴|时间线|流程|过程|情况',
                '',
                updated,
                flags=re.I,
            )
            for m_c in getattr(self.catalog, 'metrics', {}).values():
                for alias in [m_c.get('name', ''), *m_c.get('aliases', [])]:
                    if alias and len(alias) >= 2 and alias in updated:
                        updated = updated.replace(alias, ' ')
            updated = re.sub(
                r'有多少班|有多少条|有多少|多少班|多少条|多少架次|多少|有几班|有几条|几班|几条|总数|架次',
                '明细',
                updated,
            )
            if not re.search(r'航班|动态|详情|明细', updated):
                updated = f'{updated} 航班动态'.strip()
        if follow_text and is_count(follow_text) and prior_slots.get('intent') == 'detail':
            updated = re.sub(r'航班动态|动态|详细信息|详情|明细|清单|列表', '多少班', updated)
        return updated

    def _sync_question_with_delta(
        self, question, prior_slots, delta, metric_or_mod=None, previous_plan=None, view_switch=None, follow_text=None
    ):
        """Sync inherited question text with updated slots so stale terms never leak into _residual."""
        ignored = []
        updated = self._sync_identifiers(question, prior_slots, delta)
        updated = self._sync_references_and_cities(updated, prior_slots, delta, ignored)
        updated = self._sync_period_direction_basis(updated, prior_slots, delta)
        updated = self._sync_metric_or_mod(updated, metric_or_mod, previous_plan, ignored)
        updated = self._sync_view_and_count(updated, prior_slots, delta, view_switch, follow_text)
        return re.sub(r'\s{2,}', ' ', updated).strip(), ignored

    def _should_break_clarification(self, raw_input, clarification, anchor):
        """Return True when free-text input starts a brand-new query instead of answering clarification."""
        if re.search(r'能问什么|可以问什么|支持哪些|可查范围|对比|同比|环比|分别统计|分别查询', raw_input):
            return True
        slot = clarification.get('slot')
        options = clarification.get('options') or []
        confirmed = clarification.get('confirmed') or {}
        if any(unicodedata.normalize('NFKC', str(o.get('label', ''))).strip() == raw_input for o in options):
            return False
        if slot == 'item' and (raw_input.isdigit() or re.search(r'^第(?:[一二三四五六七八九十]|\d+)项', raw_input)):
            return False
        delta, rest = self._followup_slots(raw_input, confirmed, anchor)
        if slot and slot in delta and not self._strip_followup_fillers(rest, delta, allow_flights_word=True):
            return False
        if slot == 'identifier' and (delta.get('flight') or delta.get('aircraft')):
            if not self._strip_followup_fillers(rest, delta, allow_flights_word=True):
                return False
        if not slot and not options:
            if delta and not self._strip_followup_fillers(rest, delta, allow_flights_word=False):
                return False
            return True
        has_report = bool(match_acdm_report(raw_input, self.catalog))
        has_metric = any(
            alias in raw_input
            for cfg in self.catalog.metrics.values()
            for alias in [cfg.get('name', ''), *cfg.get('aliases', [])]
            if alias
        )
        has_entity = any(
            alias in raw_input
            for spec in self.catalog.entities.values()
            for alias in [spec.get('name', ''), *spec.get('aliases', [])]
            if alias
        )
        has_action = bool(
            re.search(
                r'查询|统计|查一下|显示|列出|有哪些|多少|几班|几架|几条|哪一班|哪个|如何|怎么样|情况|过站|保障|甘特|报告|简报|正常率|靠桥率|起降|延误|航班$',
                raw_input,
            )
        )
        return bool(has_report or has_metric or (has_entity and (has_action or len(delta) >= 2)) or (has_action and (delta or len(raw_input) >= 6)))

    def _view_switch_kind(self, q, rest, delta, previous_plan, pronoun_expanded=False):
        """Detect follow-up view switches between turnaround diagnostics and flight detail."""
        prior_slots = (previous_plan.get('query_context') or {}).get('slots') or {}
        prior_id = prior_slots.get('flight') or prior_slots.get('aircraft')
        new_id = delta.get('flight') or delta.get('aircraft')
        if prior_id:
            id_ok = (
                not new_id
                or pronoun_expanded
                or (isinstance(new_id, str) and new_id == prior_id)
                or q.startswith(('换成', '改成', '换查', '改查', '再查', '再看', '那', '那么'))
            )
            if not id_ok:
                return None
        elif not (prior_slots.get('period') or previous_plan.get('metric') or previous_plan.get('conditions')):
            return None
        if re.search(r'正常率|靠桥率|放行率|起飞率|报告|简报|排行|排名|趋势', q, re.I):
            return None
        if VIEW_TURNAROUND_RE.search(q):
            if not isinstance(prior_id, str) or not prior_id:
                return None
            clean = VIEW_TURNAROUND_RE.sub('', rest)
            clean = self._strip_followup_fillers(clean, delta, allow_flights_word=True)
            clean = re.sub(r'查看|查一下|看一下|查|看', '', clean).strip()
            if clean == '':
                return 'turnaround'
        if VIEW_DETAIL_RE.search(q) or (previous_plan.get('intent') == 'turnaround' and (delta.get('basis') or is_count(q))):
            if not VIEW_TURNAROUND_RE.search(q):
                clean = re.sub(
                    r'实际航班动态|计划航班动态|航班动态|动态|详细信息|详情|明细|列出来|列出明细|列出|有哪些|有多少条|多少条|有多少班|多少班|有几班|有几条|总数|有多少|多少|清单|列表|分别是什么|分别是|具体是哪些|具体有哪些|具体',
                    '',
                    rest,
                )
                prev_metric = previous_plan.get('metric')
                if prev_metric and self.catalog and hasattr(self.catalog, 'metrics') and prev_metric in self.catalog.metrics:
                    m_cfg = self.catalog.metrics[prev_metric]
                    for alias in [m_cfg.get('name', ''), *m_cfg.get('aliases', [])]:
                        if alias:
                            clean = clean.replace(alias, '')
                for alias in VIEW_DETAIL_EXTRA_ALIASES:
                    clean = clean.replace(alias, '')
                clean = self._strip_followup_fillers(clean, delta, allow_flights_word=True)
                clean = re.sub(r'查看|查一下|看一下|查|看', '', clean).strip()
                if clean == '':
                    return 'detail'
        return None

    def _is_follow_up(self, question, previous_plan, anchor, pronoun_expanded=False):
        if not previous_plan or not isinstance(previous_plan, dict) or not previous_plan.get('query_context'):
            return False
        q = unicodedata.normalize('NFKC', question).strip()
        if not q or len(q) > 28:
            return False
        if FOLLOW_UP.fullmatch(q):
            return True
        metric_or_mod, delta = self._extract_followup_metric_or_mod(q, previous_plan, anchor)
        if metric_or_mod is not None:
            return True
        prior_slots = (previous_plan.get('query_context') or {}).get('slots') or {}
        _, rest = self._followup_slots(q, prior_slots, anchor)
        if self._view_switch_kind(q, rest, delta, previous_plan, pronoun_expanded=pronoun_expanded):
            return True
        if match_acdm_report(q, self.catalog) and (
            q.startswith(('换成', '改成', '换查', '改查', '那', '那么', '再查', '再看'))
            or (previous_plan.get('intent') == 'acdm_report' and not delta.get('period') and q.endswith(('呢', '吗', '呀', '吧', '？', '?')))
        ):
            clean_rpt = re.sub(r'^(?:那|那么|其中)?(?:换成|改成|换查|改查|再查|再看|看|查看)?', '', rest)
            clean_rpt = re.sub(REPORT_NOUNS_RE, '', clean_rpt, flags=re.I)
            if not self._strip_followup_fillers(clean_rpt, delta):
                return True
        if re.search(
            r'靠桥率|正常率|放行率|起飞率|旅客|货邮|航班量|过站|甘特|作业|保障|报告|简报|排名|趋势|多少[班架条家]|几[班架条家]|有哪些|列出|航班$',
            q,
        ) and not q.startswith(('换成', '改成', '换查', '改查', '那看', '再查', '再看', '只看', '仅看')):
            return False
        if not q.startswith(FOLLOWUP_SHORT_PREFIXES):
            if not pronoun_expanded and 'basis' in delta and ('period' in delta or 'flight' in delta or 'aircraft' in delta) and re.search(r'详情|明细', q):
                return False
        if not delta and not re.search(r'详情|明细|列出来|列出明细|有哪些|多少条|多少$', q):
            return False
        rest = self._strip_followup_fillers(rest, delta)
        if re.search(r'详情|明细|列出来|列出明细|有哪些|多少条|多少$', q):
            rest = re.sub(r'详情|详细信息|动态|明细|列出来|列出|有哪些|有多少条|多少条|有多少|多少', '', rest)
        return rest == ''

    def _expand_pronouns(self, text, previous_plan):
        if not previous_plan or not isinstance(previous_plan, dict):
            return text
        if re.search(r'(?<![A-Z0-9])(?:(?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{2,4}[A-Z]?|B-?[A-Z0-9]{4,5})(?![A-Z0-9])', text, re.I):
            return text
        prior_slots = (previous_plan.get('query_context') or {}).get('slots') or {}
        flight_id = prior_slots.get('flight')
        aircraft_id = prior_slots.get('aircraft')
        if isinstance(flight_id, list):
            flight_id = flight_id[0] if flight_id else None
        if isinstance(aircraft_id, list):
            aircraft_id = aircraft_id[0] if aircraft_id else None
        updated = text
        if aircraft_id and re.search(r'这架飞机|该飞机|这架', updated):
            updated = re.sub(r'这架飞机|该飞机|这架', f' {aircraft_id} ', updated)
        target_id = flight_id or aircraft_id
        if target_id and re.search(r'这班航班|这个航班|该航班|这班|(?<![A-Za-z])它(?![A-Za-z])', updated):
            updated = re.sub(r'这班航班|这个航班|该航班|这班|(?<![A-Za-z])它(?![A-Za-z])', f' {target_id} ', updated)
        return re.sub(r'\s{2,}', ' ', updated).strip()

    def _record_follow_up(self, question, previous_plan, anchor):
        if not previous_plan or previous_plan.get('subject') not in (CORE, ACTUAL):
            return None
        subject = previous_plan['subject']
        if subject not in self.catalog.entities:
            return None
        hidden = set(self.catalog.overrides.get('sensitive_fields', []))
        field = None
        for key, attr in self.catalog.entities[subject]['attributes'].items():
            if key in hidden:
                continue
            aliases = [attr.get('label', ''), *attr.get('aliases', []), *get_extra_attribute_aliases(key)]
            terms = '|'.join(re.escape(term) for term in aliases if term)
            if terms and re.fullmatch(
                rf'(?:请问|那|那么)?(?:这班航班|这个航班|该航班|这架飞机|该飞机|飞机|航班|它)?的?'
                rf'(?:{terms})(?:是|在|为)?(?:哪里|哪儿|哪个|哪座|哪|什么|多少|几号)?[？?。]*',
                question,
            ):
                field = key
                break
        if not field:
            return None
        context = previous_plan.get('query_context') or {}
        identity = context.get('result_identity') or {}
        expected = deepcopy(context.get('slots') or {})
        if previous_plan.get('intent') != 'detail' or identity.get('id') is None or not identity.get('flight_no'):
            return self.notice(
                'clarification_needed',
                '上一轮没有唯一可定位的航班，请提供具体航班号。',
                question,
                anchor,
                {},
                'identifier',
            )
        expected.pop('sequence', None)
        expected.pop('aircraft', None)
        expected.update(flight=identity['flight_no'], intent='detail')
        if identity.get('direction'):
            expected['direction'] = identity['direction']
        plan = self.validate(self._base(subject, 'detail', expected), question, expected, anchor)
        if plan.get('outcome'):
            return plan
        # Bind to the returned record so later synchronization cannot change the referent.
        plan['conditions'].append({'table': subject, 'col': 'id', 'op': '=', 'value': identity['id']})
        plan.update(answer_field=field, follow_up=True)
        plan['query_context']['result_identity'] = deepcopy(identity)
        return self.compiler.validate(plan)

    def notice(self, outcome, text, question, anchor, confirmed, slot=None, choices=(), **extra):
        result = {'outcome': outcome, 'answer': text, **extra}
        if outcome == 'clarification_needed':
            result['clarification'] = {
                'original_question': question,
                'anchor': anchor.isoformat(),
                'confirmed': confirmed,
                'slot': slot,
                'question': text,
                'options': [{'id': str(i), 'label': label, 'value': value} for i, (label, value) in enumerate(choices, 1)],
                'catalog_version': self.catalog.version,
            }
        return result

    def _resolve_clarification_turn(self, raw_input, clarification, choice_id):
        """Resume a pending clarification turn and return (original, anchor, confirmed, early_notice)."""
        original = clarification['original_question']
        anchor = datetime.fromisoformat(clarification['anchor']).astimezone(BEIJING)
        confirmed = deepcopy(clarification.get('confirmed', {}))
        if clarification.get('catalog_version') != self.catalog.version:
            return original, anchor, confirmed, self.notice(
                'clarification_needed',
                '业务目录已更新，请重新确认原问题的条件。',
                original,
                anchor,
                {},
                'restart',
                [('重新解析原问题', True)],
            )
        slot = clarification.get('slot')
        options = clarification.get('options', [])
        selected = next((option for option in options if option['id'] == choice_id), None) if choice_id else None
        if choice_id and not selected:
            raise ValueError('澄清选项无效，请重新选择')
        if not selected and not choice_id and options:
            selected = next(
                (o for o in options if unicodedata.normalize('NFKC', str(o.get('label', ''))).strip() == raw_input),
                None,
            )
        if selected:
            if slot != 'restart':
                delta = {slot: selected['value']} if slot else {}
                if delta:
                    original, new_ignored = self._sync_question_with_delta(original, confirmed, delta)
                    self._turn_ignored_terms.extend(new_ignored)
                    self._apply_slot_delta(confirmed, delta)
        elif slot == 'item' and raw_input:
            confirmed['item'] = raw_input
        elif slot != 'restart':
            delta, _ = self._followup_slots(raw_input, confirmed, anchor)
            if delta:
                original, new_ignored = self._sync_question_with_delta(original, confirmed, delta)
                self._turn_ignored_terms.extend(new_ignored)
                self._apply_slot_delta(confirmed, delta)
            resolved_slot = (
                (slot in delta)
                or (slot == 'identifier' and bool(delta.get('flight') or delta.get('aircraft')))
                or (not slot and bool(delta))
            )
            if not resolved_slot:
                if not options:
                    original = f'{original} {raw_input}'.strip()
                else:
                    return original, anchor, confirmed, self.notice(
                        'clarification_needed',
                        clarification['question'],
                        original,
                        anchor,
                        confirmed,
                        slot,
                        [(o['label'], o['value']) for o in options],
                    )
        return original, anchor, confirmed, None

    def _resolve_followup_turn(self, original, previous_plan, anchor):
        """Inherit prior turn context when the user sends a short follow-up."""
        confirmed = {}
        follow_up = False
        view_switch = None
        metric_or_mod = None
        pronoun_expanded = False
        if previous_plan:
            expanded = self._expand_pronouns(original, previous_plan)
            if expanded != original:
                original = expanded
                follow_up = True
                pronoun_expanded = True
        follow_text = original
        if not self._is_follow_up(follow_text, previous_plan, anchor, pronoun_expanded=pronoun_expanded):
            return original, confirmed, follow_up, view_switch, metric_or_mod, None
        prior = previous_plan.get('query_context') if isinstance(previous_plan, dict) else None
        if not prior:
            return original, confirmed, follow_up, view_switch, metric_or_mod, self.notice(
                'clarification_needed', '请补充完整问题，上一轮没有可继承的业务口径。', original, anchor, {}
            )
        follow_up = True
        original = prior['question']
        self._turn_ignored_terms.extend(prior.get('ignored_terms') or [])
        prior_slots = deepcopy(prior.get('slots', {}))
        if match_acdm_report(follow_text, self.catalog):
            rpt_phrase = re.sub(
                r'^(?:那|那么|其中)?(?:换成|改成|换查|改查|再查|再看|只看|仅看)?|(?:的呢|的吗|的呀|的吧|呢|吗|呀|吧|？|\?)+$',
                '',
                follow_text,
            ).strip()
            delta_rpt, _ = self._followup_slots(follow_text, prior_slots, anchor)
            confirmed = (
                {'period': delta_rpt['period']}
                if delta_rpt.get('period')
                else ({'period': prior_slots['period']} if prior_slots.get('period') else {})
            )
            self._turn_ignored_terms = []
            if confirmed.get('period') and not delta_rpt.get('period'):
                s_d = date.fromisoformat(confirmed['period'][0])
                e_d = date.fromisoformat(confirmed['period'][1]) - timedelta(days=1)
                p_txt = s_d.isoformat() if s_d == e_d else f'{s_d.isoformat()}至{e_d.isoformat()}'
                original = f'{p_txt} {rpt_phrase}'.strip()
            else:
                original = rpt_phrase
            return original, confirmed, follow_up, view_switch, metric_or_mod, None

        metric_or_mod, delta = self._extract_followup_metric_or_mod(follow_text, previous_plan, anchor)
        _, rest_view = self._followup_slots(follow_text, prior_slots, anchor)
        view_switch = (
            None
            if (metric_or_mod and metric_or_mod.get('metric_key'))
            else self._view_switch_kind(follow_text, rest_view, delta, previous_plan, pronoun_expanded=pronoun_expanded)
        )
        confirmed = deepcopy(prior_slots)
        original, new_ignored = self._sync_question_with_delta(
            original,
            prior_slots,
            delta,
            metric_or_mod=metric_or_mod,
            previous_plan=previous_plan,
            view_switch=view_switch,
            follow_text=follow_text,
        )
        self._turn_ignored_terms.extend(new_ignored)
        self._apply_slot_delta(confirmed, delta)
        if view_switch == 'turnaround':
            for stale_key in ('intent', 'basis', 'direction', 'sequence', 'metric', 'references'):
                confirmed.pop(stale_key, None)
        elif metric_or_mod and metric_or_mod.get('metric_key'):
            confirmed.pop('intent', None)
            if self.catalog.metrics.get(metric_or_mod['metric_key'], {}).get('base_table') not in (CORE, ACTUAL):
                confirmed.pop('basis', None)
        elif metric_or_mod and metric_or_mod.get('mods'):
            confirmed.pop('intent', None)
        if view_switch == 'detail' or delta.get('sequence'):
            confirmed['intent'] = 'count' if is_count(follow_text) and not delta.get('sequence') else 'detail'
        elif view_switch != 'turnaround':
            if re.search(r'详情|详细信息|动态|明细|列出|有哪些', follow_text):
                confirmed['intent'] = 'detail'
            elif is_count(follow_text):
                confirmed['intent'] = 'count'
        return original, confirmed, follow_up, view_switch, metric_or_mod, None

    def plan(self, question, previous_plan=None, clarification=None, choice_id=None, day_mode='AUTO', history=None, _single=False):
        anchor = datetime.now(BEIJING)
        confirmed = {}
        follow_up = False
        self._previous_plan = previous_plan
        self._history = list(history or [])
        self._turn_ignored_terms = []
        view_switch = None
        metric_or_mod = None
        raw_input = unicodedata.normalize('NFKC', question).strip()
        original = raw_input
        self._raw_question = raw_input
        self._normalized_question = original
        if clarification and not choice_id and self._should_break_clarification(original, clarification, anchor):
            clarification = None
        if not _single and not clarification:
            from app.agent.analysis import plan as analysis_plan
            try:
                expanded = analysis_plan(self, original, anchor, previous_plan, day_mode)
            except ValueError as exc:
                return self.notice('clarification_needed', str(exc), raw_input, anchor, {})
            if expanded is not None:
                return expanded
        if clarification:
            original, anchor, confirmed, early = self._resolve_clarification_turn(original, clarification, choice_id)
            if early is not None:
                return early
        else:
            original, confirmed, follow_up, view_switch, metric_or_mod, early = self._resolve_followup_turn(
                original, previous_plan, anchor
            )
            if early is not None:
                return early
        expected = slots(original, anchor, self.references, self.catalog)
        expected.update(confirmed)
        if (view_switch == 'detail' or expected.get('intent') == 'detail') and previous_plan and previous_plan.get('metric') and (not metric_or_mod or not metric_or_mod.get('metric_key')):
            from app.agent.analysis import build_metric_detail_plan
            det_plan = build_metric_detail_plan(previous_plan, self.catalog)
            return self._save_query_context(det_plan, question, expected, anchor)
        if 'day_mode' not in expected and day_mode in ('OPERATIONAL', 'CALENDAR'):
            expected['day_mode'] = day_mode
        if clarification and not _single:
            from app.agent.analysis import plan as analysis_plan
            try:
                expanded = analysis_plan(
                    self,
                    original,
                    anchor,
                    previous_plan,
                    day_mode,
                    selected_item=expected.get('item'),
                    confirmed_period=expected.get('period'),
                )
            except ValueError as exc:
                return self.notice('clarification_needed', str(exc), original, anchor, expected)
            if expanded is not None:
                return expanded
        is_dim_breakdown = bool(re.search(r'(?:按|分|各).*(?:分别统计|分别查询)', original))
        comparison = re.search(r'对比|同比|环比', original) or (re.search(r'分别统计|分别查询', original) and not is_dim_breakdown)
        if comparison and not re.search(GROUND_BRIEFING_NAME, original):
            norm_plan = self._try_normalize_and_replan(
                original, anchor, reject_comparison=True, allow_clarification=True
            )
            if norm_plan is not None:
                return norm_plan
            return self.notice('unsupported', '首版一次处理一项查询，请先选择一个统计对象或期间。', original, anchor, expected)
        if re.search(r'能问什么|可以问什么|支持哪些|可查范围|能查什么|可以查什么', original):
            return self.notice(
                'answered',
                '当前可查询：' + '、'.join(s['name'] for s in self.catalog.entities.values())
                + '。支持明细、筛选、排序、计数及已发布指标；口径有歧义时会先确认。',
                original,
                anchor,
                expected,
            )
        if self.trace:
            self.trace.start('coordinator', '总控识别业务与口径', '从全部授权业务目录中识别对象，检查关键歧义与条件完整性')
        try:
            planned = self._record_follow_up(raw_input, previous_plan, anchor) if not clarification else None
            if planned is None:
                planned = self._plan(original, anchor, expected)
            if follow_up and isinstance(planned, dict) and not planned.get('outcome'):
                planned['follow_up'] = True
            return planned
        except ValueError as exc:
            return self.notice('clarification_needed', '查询条件尚不能完整确定：' + str(exc)[:300], original, anchor, expected)
        except Exception:
            if self.trace:
                self.trace.finish('coordinator', '业务规划请求未完成', state='failed')
            raise
        finally:
            if self.trace and self.trace.steps['coordinator']['state'] == 'running':
                self.trace.finish('coordinator', '本轮业务识别与口径检查完成')

    @property
    def _is_mock_model(self):
        try:
            from unittest.mock import NonCallableMock
            return isinstance(self.chat, NonCallableMock)
        except Exception:
            return False

    def _try_normalize_and_replan(self, question, anchor, *, reject_comparison=False, allow_clarification=False):
        """Run one semantic normalization pass and re-match rules without duplicating fallback logic."""
        if not self.chat or self._is_mock_model:
            return None
        norm = self._normalize_with_model(question, anchor)
        if not norm or norm == question:
            return None
        if reject_comparison and re.search(r'对比|同比|环比|分别统计|分别查询', norm):
            return None
        norm_expected = slots(norm, anchor, self.references, self.catalog)
        norm_plan = self._plan(norm, anchor, norm_expected, allow_normalize=False)
        if not isinstance(norm_plan, dict):
            return None
        outcome = norm_plan.get('outcome')
        if not outcome or (allow_clarification and outcome == 'clarification_needed'):
            if 'query_context' in norm_plan:
                norm_plan['query_context']['raw_question'] = question
                norm_plan['query_context']['normalized_question'] = norm
            return norm_plan
        return None

    def _normalize_with_model(self, question, anchor):
        if not self.chat or self._is_mock_model:
            return None
        self._finish_rule_trace('规则尚未完整匹配，转入语义标准化')
        stage = 'semantic_normalize_' + str(len(self.trace.steps)) if self.trace else None
        if self.trace:
            self.trace.start(stage, '语义标准化', '将口语、错字或缩写对齐到已发布业务术语', [question])
        today_str = anchor.strftime('%Y-%m-%d')
        dims = [d.get('name') for d in getattr(self.catalog, 'dimensions', {}).values() if d.get('name')]
        dim_str = '、'.join(dims[:15]) if dims else '航班属性、航司、进出港、机型、航站楼、延误原因'
        metric_names = [m.get('name') for m in getattr(self.catalog, 'metrics', {}).values() if m.get('name')]
        metric_str = '、'.join(metric_names) if metric_names else '实际执行航班架次、出港执飞架次、全口径航班正常率、1-5分钟短延误架次'
        prompt = (
            f"你是民航运行与数据查询系统的语义标准化助手。基准业务日期（今天）为 {today_str}。\n"
            f"系统支持的分析维度包括：{dim_str}。\n"
            f"系统常用指标包括：{metric_str}。\n"
            "你的任务是将用户的口语化、含错别字或缩写的提问，转写为规范的标准问数表达。\n"
            "转写规则：\n"
            "1. 纠正常见错别字（如'保障时常'->'保障时长'、'保章'->'保障'、'撤轮挡'->'撤轮挡'）。\n"
            f"2. 口语化日期（如'11-12'、'11/12'、'11.12'）补全为当前基准年份 '{anchor.year}-MM-DD'；相对日期（如'今天'）转为具体日期 '{today_str}'。\n"
            "3. 口语化通俗表达对齐为标准术语（如'在地上停了多久/地面存放/停放多久/停留时间/过站耗时'->'过站平均保障时长'）。\n"
            "4. 将枚举列举式的分组表达对齐为标准维度（如'按国内、国际、地区分别统计'对齐为'按航班属性统计'；'按T2、T3'对齐为'按航站楼'；'按进港出港'对齐为'按进出港'）。\n"
            "5. 将统计指标口语对齐为已发布指标标准名称（如'实际执行架次/实飞架次'对齐为'实际执行航班架次'；'放行延误1至5分钟/短延误'对齐为'1-5分钟短延误架次'）；严禁把定语修饰词（如'有效过站航班'中的'有效'）错配成另一个无关指标（如'有效航班架次'）拼在一起，单句只能输出一个最贴合原意的目标指标。\n"
            "6. 严禁丢失用户的极值、排名或Top-N意图（如'哪个...最多/最高/最少/最低'、'峰值'、'前N名'），绝对不能把求最大值改写成普通的'按...统计'。\n"
            f"示范：'CA8203今天在地面存放多久' -> '查询 {today_str} CA8203 过站平均保障时长'\n"
            f"示范：'2026-11-02至2026-11-15，按国内、国际、地区分别统计实际执行架次' -> '按航班属性统计 2026-11-02 至 2026-11-15 实际执行航班架次'\n"
            f"示范：'2026-11-02至2026-11-15，放行延误1至5分钟的航班多少架次？' -> '查询 2026-11-02 至 2026-11-15 1-5分钟短延误架次'\n"
            "示范：'2026-11-02至2026-11-15，哪个具体日期、哪个小时的起降架次最多？' -> '2026-11-02至2026-11-15 哪个小时起降架次最多'\n"
            "7. 保持用户原意，仅输出一句标准化后的规范问数表达，严禁任何额外解释、标点废话或前后缀。"
        )
        try:
            norm = self.chat(prompt, question, max_tokens=80, temperature=0.0).strip()
            norm = norm.strip('"`\' \n')
            if not norm or norm == question or norm.startswith('{') or norm.startswith('[') or len(norm) > 150:
                if self.trace:
                    self.trace.finish(stage, '未获得新的标准表达，继续检查原问题', state='skipped')
                return None
            norm = re.sub(r'^(规范表达|标准化|纠正后|规范后)[：:]\s*', '', norm).strip()
            if self.trace:
                self.trace.finish(
                    stage,
                    '已生成标准表达，重新匹配本体规则' if norm and norm != question else '标准表达未变化',
                    [norm] if norm else [],
                    state='completed' if norm and norm != question else 'skipped',
                )
            return norm if norm and norm != question else None
        except Exception:
            if self.trace:
                self.trace.finish(stage, '语义标准化请求未完成，保留原问题继续处理', state='failed')
            return None

    def _finish_rule_trace(self, message):
        if self.trace:
            for stage, step in reversed(self.trace.steps.items()):
                if stage.startswith(('rule_match_', 'rule_rematch_')) and step['state'] == 'running':
                    self.trace.finish(stage, message, step['details'])
                    break

    def _plan(self, question, anchor, expected, allow_normalize=True):
        if not self.trace:
            return self._plan_rules(question, anchor, expected, allow_normalize)
        stage = ('rule_match_' if allow_normalize else 'rule_rematch_') + str(len(self.trace.steps))
        self.trace.start(
            stage,
            '直接匹配本体规则' if allow_normalize else '标准化后重新匹配',
            '匹配业务对象、指标、日期与筛选条件',
            [question],
        )
        try:
            plan = self._plan_rules(question, anchor, expected, allow_normalize)
        except Exception:
            if self.trace.steps[stage]['state'] == 'running':
                self.trace.finish(stage, '规则匹配未完成', state='failed')
            raise
        if self.trace.steps[stage]['state'] == 'running':
            self.trace.finish(stage, '已生成本体规则计划' if not plan.get('outcome') else '已识别业务边界或待确认条件', [question])
        return plan

    def _detect_metric(self, question, expected, non_flight_explicit, hints, is_list_ask):
        catalog = self.catalog
        cm_codes = {cm.get('concept_code') for cm in (getattr(catalog, 'concept_metrics', []) or []) if cm.get('concept_code')}
        metric_text = question
        for spec in catalog.entities.values():
            metric_text = metric_text.replace(spec['name'], '')
        metric_hits = [
            (len(alias), key)
            for key, cfg in catalog.metrics.items()
            for alias in [cfg.get('name', ''), *cfg.get('aliases', [])]
            if alias and alias in metric_text
        ]
        metric = max(metric_hits)[1] if metric_hits else None
        if metric in (None, *FLIGHT_COUNT_METRICS) and not is_list_ask and not non_flight_explicit:
            if '取消' in question and 'cancel_count' in catalog.metrics:
                metric = 'cancel_count'
            elif re.search(r'远机位.*外保障|外保障.*远机位|外保障.*(?:架次|航班)|远机位.*(?:架次|航班)', question) and 'remote_stand_flights' in catalog.metrics:
                metric = 'remote_stand_flights'
            elif (is_count(question) or '占比' in question or '比例' in question or '分布' in question or '排名' in question or '排行' in question) and ('延误' in question or '晚点' in question or '异常原因' in question or '不正常原因' in question):
                if expected.get('direction') == 'ARR' and 'arr_delay_count' in catalog.metrics:
                    metric = 'arr_delay_count'
                elif expected.get('direction') == 'DEP' and 'dep_delay_count' in catalog.metrics:
                    metric = 'dep_delay_count'
                elif 'flight_delay_count' in catalog.metrics:
                    metric = 'flight_delay_count'
                elif 'dep_delay_count' in catalog.metrics:
                    metric = 'dep_delay_count'
            else:
                metric_concept_codes = [
                    c['code']
                    for c in hints.get('concepts', [])
                    if c.get('concept_type') == 'METRIC' or (c.get('code') in cm_codes and c.get('concept_type') != 'ATTRIBUTE')
                ]
                resolved_cm = None
                if metric_concept_codes:
                    dir_hint = expected.get('direction') if expected.get('direction') in ('DEP', 'ARR') else None
                    resolved_cm = resolve_concept_metric(catalog, metric_concept_codes, dir_hint)
                if resolved_cm:
                    metric = resolved_cm
                elif not metric:
                    top_sc = (hints.get('scenarios') or [None])[0]
                    if top_sc and top_sc.get('metric_code') in catalog.metrics and top_sc.get('code') not in ('MetricQuery', 'FlightListQuery'):
                        metric = top_sc['metric_code']
            if not metric and not non_flight_explicit:
                cand_gb, _, _ = extract_acdm_dimensions_and_topn(question, catalog, target=CORE, allow_topn=True)
                if cand_gb:
                    if 'flight_delay_count' in catalog.metrics and ('延误' in question or '异常' in question):
                        metric = 'flight_delay_count'
                    elif 'operated_flight_count' in catalog.metrics and ('外保障' in question or '靠桥' in question or '机位' in question or '执行' in question or '实飞' in question):
                        metric = 'operated_flight_count'
                    elif 'flight_count' in catalog.metrics and re.search(r'排名|排行|趋势|分布|展示|占比|按天|按日|按月|按小时|按.*', question):
                        metric = 'flight_count'
            if not metric and re.search(r'存放多久|停放多久|停了多久|停留多久|在地上停|在地面停|地面存放|地面停放|过站.*保障.*时长|过站保障平均时长|过站平均保障时长', question):
                if 'avg_turnaround_service_minutes' in catalog.metrics:
                    metric = 'avg_turnaround_service_minutes'
            if not metric and re.search(r'登机桥.*使用.*次|登机桥保障总次|登机桥使用总次', question):
                if 'air_bridge_usage_count' in catalog.metrics:
                    metric = 'air_bridge_usage_count'
            if not metric and re.search(r'国内|国际|港澳台', question) and re.search(r'实际执行|执行|实飞|执飞', question):
                if 'operated_flight_count' in catalog.metrics:
                    metric = 'operated_flight_count'
        if metric in FLIGHT_COUNT_METRICS:
            if 'remote_stand_flights' in catalog.metrics and re.search(r'远机位.*外保障|外保障.*远机位', question):
                metric = 'remote_stand_flights'
            elif 'operated_flight_count' in catalog.metrics and ('外保障' in question or '靠桥' in question or '机位' in question or '执行' in question or '实飞' in question or '航站楼' in question or '国内' in question or '国际' in question):
                metric = 'operated_flight_count'
        if metric and expected.get('flight'):
            metric_filters = []
            apply_acdm_metric_slots(question, catalog, metric, metric_filters)
            aircraft_types = {c['value'] for c in metric_filters if c['col'] in ('craft_type_icao_cd', 'aircraft_type')}
            flights = expected['flight'] if isinstance(expected['flight'], list) else [expected['flight']]
            flights = [flight for flight in flights if flight not in aircraft_types]
            if flights:
                expected['flight'] = flights[0] if len(flights) == 1 else flights
            else:
                expected.pop('flight')
        if expected.get('basis') == 'actual' and metric in FLIGHT_COUNT_METRICS:
            metric = None
        return metric

    @staticmethod
    def _is_turnaround_request(question, expected, metric):
        has_flight_target = bool(expected.get('flight') or expected.get('aircraft') or re.search(r'这班|那班|这个航班|那个航班|该航班', question))
        return bool(
            not metric
            and not is_count(question)
            and not re.search(r'有哪些|列表|清单|排名|排行|平均|时长|统计', question)
            and (
                re.search(
                    r'甘特|保障诊断|作业诊断|过站.{0,6}(?:诊断|进度|时序|保障|情况|状况|如何|怎么样)'
                    r'|(?:过站|配餐|保洁|加油|装卸|登机).{0,6}(?:保障|作业|进度|完成|情况|状况)'
                    r'|保障(?:情况|进度|状态|节点|流程|过程|时间轴|时间线)',
                    question,
                )
                or (has_flight_target and re.search(r'时序图?|时间轴|时间线|鱼骨图?|时序', question))
            )
        )

    def _plan_turnaround(self, question, anchor, expected, subject):
        if expected.get('period') and date.fromisoformat(expected['period'][1]) - date.fromisoformat(expected['period'][0]) != timedelta(days=1):
            return self.notice(
                'clarification_needed',
                '保障诊断需要定位单个业务日，请确认日期。',
                question,
                anchor,
                expected,
                'period',
                self._period_choices(anchor),
            )
        if not (expected.get('flight') or expected.get('aircraft')):
            return self.notice('clarification_needed', '请提供具体航班号或机号，以定位保障记录。', question, anchor, expected, 'identifier')
        remaining = TURNAROUND_RESIDUAL_STRIP_RE.sub('', self._residual(question, expected, subject, None))
        if remaining:
            return self.notice('clarification_needed', '保障诊断还有未能确认的附加条件，请明确一个航班或机号及业务日。', question, anchor, expected)
        plan = self._base(subject, 'turnaround', expected)
        plan.update(
            query_identifier=expected.get('flight') or expected['aircraft'],
            flight_date=expected['period'][0],
            conditions=[],
        )
        return self._save_query_context(plan, question, expected, anchor)

    def _plan_rules(self, question, anchor, expected, allow_normalize=True):
        catalog = self.catalog
        report = match_acdm_report(
            question,
            catalog,
            bounds_fn=lambda _: ('range', *expected['period']) if expected.get('period') else None,
            today_d=anchor.date(),
        )
        if report:
            return self.validate(report, question, expected, anchor)
        safeguard_entry = r'(?:保障节点.{0,6}(?:实测)?录入|(?:实测)?录入.{0,6}保障节点)'
        if re.search(safeguard_entry, question) and not re.search(r'原始记录|明细|清单', question):
            return self._plan_safeguard_entry(question, anchor, expected, safeguard_entry)
        candidates = catalog.retrieve(question)
        explicit = [table for table in candidates if any(alias.lower() in question.lower() for alias in catalog.entities[table]['aliases'])]
        flight_words = bool(re.search(r'航班|起飞|落地|飞走|离地|执飞|执行', question))
        if 'dim_airline' in explicit and flight_words and re.search(r'排名|排行|分布|各[个]?航司|按航司', question):
            explicit = [t for t in explicit if t != 'dim_airline']
        if 'dim_aircraft' in explicit and flight_words and not expected.get('aircraft') and not re.search(r'机号|机龄|机型|注册号|尾号|飞机号|飞机基础|航空器', question):
            explicit = [t for t in explicit if t != 'dim_aircraft']
        if 'dim_airport' in explicit and '维表' not in question:
            explicit = [t for t in explicit if t != 'dim_airport']
        if 'dim_abnormal_reason' in explicit and '维表' not in question and (
            flight_words or expected.get('period') or re.search(r'占比|分布|分类|展示|排名|排行|架次|延误|率', question)
        ):
            explicit = [t for t in explicit if t != 'dim_abnormal_reason']
        if 'dim_stand' in explicit and '维表' not in question and (
            flight_words or expected.get('period') or re.search(r'占比|靠桥|远机位|近机位|外保障|率|按天|架次|航班', question)
        ):
            explicit = [t for t in explicit if t != 'dim_stand']
        if 'dim_air_bridge' in explicit and '维表' not in question and (
            expected.get('period') or re.search(r'次|使用|保障|率', question)
        ):
            explicit = [t for t in explicit if t != 'dim_air_bridge']
        flight_entities = {CORE, ACTUAL, 'mv_acdm_dyn_flight_detail', 'mv_acdm_turnaround_pair', 'mv_acdm_flight_calendar', 'std_flight_air_bridge', 'std_flight_safeguard_node'}
        non_flight_explicit = [t for t in explicit if t not in flight_entities]
        hints = catalog.resolve_ontology_hints(question) if hasattr(catalog, 'resolve_ontology_hints') else {'synonyms': [], 'concepts': [], 'scenarios': []}
        is_list_ask = bool(
            expected.get('intent') == 'detail'
            or re.search(r'有哪些|是哪些|哪些航班|哪几班|航班明细|明细|清单|列表|前\s*\d+\s*(?:个|班)?航班|\d+\s*个航班', question)
        )
        metric = self._detect_metric(question, expected, non_flight_explicit, hints, is_list_ask)

        subject = expected.get('subject')
        if not subject and is_list_ask and re.search(r'放行延误.*(?:最长|降序|排序)|(?:最长|降序).*放行延误', question):
            if 'mv_acdm_dyn_flight_detail' in catalog.entities:
                subject = 'mv_acdm_dyn_flight_detail'
                expected['intent'] = 'detail'
        if not subject and metric:
            subject = catalog.metrics[metric].get('base_table')
        if not subject and explicit:
            subject = explicit[0]
        if not subject and (flight_words or expected.get('flight') or expected.get('aircraft') or expected.get('origin_city') or expected.get('dest_city') or expected.get('basis')):
            subject = CORE

        turnaround = self._is_turnaround_request(question, expected, metric)
        if turnaround:
            for key in ('flight', 'aircraft'):
                if isinstance(expected.get(key), list):
                    return self.notice(
                        'clarification_needed',
                        '本轮保障诊断请选择一个身份。',
                        question,
                        anchor,
                        expected,
                        key,
                        [(value, value) for value in expected[key]],
                    )
            subject = 'acdm.tb_dyn_flight' if re.search(r'ACDM.*(?:记录|作业)', question, re.I) else CORE
        if subject and subject not in catalog.entities:
            return self.notice('unsupported', '当前授权目录没有这个业务对象，请先接入或配置查询权限。', question, anchor, expected)

        if metric in FLIGHT_COUNT_METRICS and not expected.get('basis'):
            if allow_normalize:
                norm_plan = self._try_normalize_and_replan(question, anchor)
                if norm_plan is not None:
                    return norm_plan
            return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected, 'basis', get_basis_options(detailed=True))
        if subject in (CORE, ACTUAL) and not metric and not turnaround:
            if not expected.get('basis'):
                residual = self._residual(question, expected, subject, None)
                if residual and allow_normalize:
                    norm_plan = self._try_normalize_and_replan(question, anchor)
                    if norm_plan is not None:
                        return norm_plan
                return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected, 'basis', get_basis_options(detailed=True))
            subject = ACTUAL if expected['basis'] == 'actual' else CORE
        if expected.get('sequence') and not expected.get('direction') and subject in (CORE, ACTUAL):
            return self.notice('clarification_needed', '请确认比较出港、进港，还是全部方向。', question, anchor, expected, 'direction', get_direction_options())
        if subject:
            temporal = catalog.entities[subject].get('query_semantics', {})
            if (temporal.get('date_required') or turnaround) and not expected.get('period'):
                if allow_normalize:
                    norm_plan = self._try_normalize_and_replan(question, anchor)
                    if norm_plan is not None:
                        return norm_plan
                return self.notice('clarification_needed', '请确认要查询的业务日期。', question, anchor, expected, 'period', self._period_choices(anchor))
        if turnaround:
            return self._plan_turnaround(question, anchor, expected, subject)

        if expected.get('intent') == 'detail' or (not expected.get('intent') and is_list_ask and not is_count(question)):
            if metric:
                cfg = catalog.metrics.get(metric, {})
                self._turn_ignored_terms.extend([cfg.get('name', ''), *cfg.get('aliases', [])])
                _, _, mod_terms = extract_acdm_dimensions_and_topn(question, catalog, target=subject, allow_topn=True)
                _, slot_terms = apply_acdm_metric_slots(question, catalog, metric, [])
                self._turn_ignored_terms.extend([*mod_terms, *slot_terms, '多少'])
            metric = None
            expected['intent'] = 'detail'
        if subject:
            try:
                candidate = self._base(
                    subject,
                    expected.get('intent') or ('count' if is_count(question) else 'detail'),
                    expected,
                    metric=metric,
                    question=question,
                )
            except ValueError:
                if self.chat:
                    return self._model(question, anchor, expected, None)
                raise
            if metric:
                candidate.update(intent='metric', metric=metric, aggregate_target=subject)
                if not expected.get('sequence'):
                    group_by, top_opts, _ = extract_acdm_dimensions_and_topn(question, catalog, target=subject, allow_topn=True)
                    top_sc = (hints.get('scenarios') or [None])[0]
                    if top_sc and re.search(r'按|分|各|每|趋势|分布|排名|排行', question):
                        for dim_code in top_sc.get('dimensions') or []:
                            if dim_code in getattr(catalog, 'dimensions', {}) and dim_code not in group_by:
                                group_by.append(dim_code)
                    if group_by:
                        candidate['group_by'] = group_by
                    if top_opts:
                        candidate.update(top_opts)
            if candidate.get('intent') == 'detail' and subject == 'mv_acdm_dyn_flight_detail':
                if re.search(r'放行延误.*(?:最长|降序|排序|前\s*\d+|top)|(?:最长|降序|前\s*\d+|top).*放行延误', question, re.I):
                    m_lim = re.search(r'(?:前|top\s*)?(\d{1,2})\s*(?:个|班|条)?(?:航班)?', question, re.I)
                    lim = int(m_lim.group(1)) if (m_lim and m_lim.group(1)) else 10
                    candidate['order_by'] = [{'field': 'clearance_delay_time', 'direction': 'desc'}]
                    candidate['limit'] = lim
                    self._turn_ignored_terms.extend([
                        '放行延误', '放行延误时间', '延误时长', '延误时间', '最长', '时间最长', '最长的',
                        '降序', '升序', '降序展示', '升序展示', '展示', '是哪些', '有哪些',
                        f'前{lim}个航班', f'前{lim}个', f'前{lim}班', f'前{lim}', f'top{lim}', f'top {lim}',
                        f'{lim}个航班', f'{lim}个', f'{lim}班', '10个航班', '10个', '航班',
                    ])
            if 'stand_type' in candidate.get('group_by', []) and '外保障' in question:
                self._turn_ignored_terms.extend(['外保障', '外保障航班'])
            if expected.get('sequence'):
                time_field = (
                    'scheduled_departure' if subject == CORE and expected.get('direction') == 'DEP'
                    else 'scheduled_arrival' if subject == CORE and expected.get('direction') == 'ARR'
                    else catalog.entities[subject]['bindings'].get('time')
                )
                if not time_field:
                    return self.notice('unsupported', '此对象未定义可用于先后排序的业务时刻，请明确排序字段。', question, anchor, expected)
                candidate.update(intent='detail', order_by=[{'field': time_field, 'direction': expected['sequence']}], limit=1)
                if subject == ACTUAL:
                    candidate['flight_sequence'] = 'last' if expected['sequence'] == 'desc' else 'first'
            if not self._residual(question, expected, subject, metric, hints=hints):
                return self.validate(candidate, question, expected, anchor, hints=hints)
        if allow_normalize:
            norm_plan = self._try_normalize_and_replan(question, anchor)
            if norm_plan is not None:
                return norm_plan
        if not self.chat:
            return self.notice('clarification_needed', '当前规则无法完整理解这个问法，且内网模型不可用。请明确对象、条件和所需结果。', question, anchor, expected)
        explicit_object = subject and (catalog.entities[subject]['name'] in question or subject.lower() in question.lower())
        basis_locked = (
            (expected.get('basis') == 'actual' and subject == ACTUAL)
            or (expected.get('basis') == 'planned' and subject == CORE)
        )
        return self._model(question, anchor, expected, subject if (explicit_object or basis_locked) else None)

    def _plan_safeguard_entry(self, question, anchor, expected, entry_pattern):
        subject, metric = 'std_flight_safeguard_node', 'safeguard_node_recorded_count'
        breakdown = r'按(?:保障)?节点|各(?:个)?(?:保障)?节点|每(?:个)?(?:保障)?节点|去重航班数|(?:适用航班)?覆盖率|排行榜?|排名'
        grouped = bool(re.search(breakdown, question))
        required_tables = {subject, 'mv_acdm_dyn_flight_detail', 'dim_safeguard_node'} if grouped else {subject}
        if not required_tables <= self.catalog.entities.keys() or metric not in self.catalog.metrics:
            return self.notice('unsupported', '当前授权目录未开放所需的保障节点统计数据。', question, anchor, expected)
        rest = DAY_MODE_STRIP_RE.sub('', DATE_RANGE_STRIP_RE.sub('', question))
        rest = re.sub(
            rf'(?:{entry_pattern})(?:量|数(?!量))?|(?:实测)?录入(?:量|数(?!量))?|{breakdown}|记录(?:数|量)|条数|数量|多少条|几条|是多少|多少|总数|总量'
            r'|一共|共计|查询|统计|列出|给出|查看|分别|今天|今日|昨天|昨日|前天|大前天|本月|最近一周|近一周'
            r'|以及|和|与|及|有|的|了|请|呢|[\s，,。；;？?！!：:、]',
            '', rest,
        )
        if rest or any(expected.get(key) for key in ('references', 'flight', 'aircraft', 'direction', 'basis')):
            return self.notice('unsupported', '保障节点实测录入统计暂不支持这些附加条件。', question, anchor, expected)
        if not expected.get('period'):
            return self.notice('clarification_needed', '请确认要统计的日期或期间。', question, anchor, expected, 'period', self._period_choices(anchor))
        if grouped and expected.get('day_mode') == 'OPERATIONAL':
            return self.notice('unsupported', '保障节点覆盖率按北京时间自然日统计，请切换到自然日口径。', question, anchor, expected)
        plan = self._base(subject, 'metric', expected, metric=metric)
        plan.update(metric=metric, aggregate_target=subject)
        if grouped:
            for condition in plan['conditions']:
                if condition['col'] == 'safeguard_real_time':
                    condition['value'] += 'T00:00:00+08:00'
            plan.update(group_by=['safeguard_code'], safeguard_node_breakdown=True)
        gate = check_capability_gate(self.catalog, plan)
        if not gate['allowed']:
            return self.notice('unsupported', gate['message'], question, anchor, expected, capability_gate=gate)
        return self._save_query_context(self.compiler.validate(plan), question, expected, anchor)

    def _base(self, subject, intent, expected, metric=None, question=None):
        spec = self.catalog.entities[subject]
        plan = {'subject': subject, 'intent': intent, 'conditions': [], 'focus_tables': [], 'group_by': []}
        bindings = spec['bindings']
        semantics = spec.get('query_semantics', {})
        metric_df = self.catalog.metrics.get(metric, {}).get('date_field') if isinstance(metric, str) and hasattr(self.catalog, 'metrics') else None
        if metric_df and metric_df in spec.get('attributes', {}):
            time = {'table': subject, 'field': metric_df}
        else:
            time = {'table': subject, 'field': bindings['time']} if bindings.get('time') else semantics.get('time_source')
        if expected.get('period') and not time:
            raise ValueError('此对象没有可核验的业务时间映射，不能忽略日期查询全部记录')
        if expected.get('period') and time:
            start, end = expected['period']
            if subject == ACTUAL:
                start, end = start + 'T00:00:00+08:00', end + 'T00:00:00+08:00'
            plan['conditions'] += [
                {'table': time['table'], 'col': time['field'], 'op': op, 'value': value}
                for op, value in [('>=', start), ('<', end)]
            ]
        if expected.get('direction') in ('DEP', 'ARR'):
            table = subject if bindings.get('direction') else CORE
            field = bindings.get('direction') or self.catalog.entities.get(CORE, {}).get('bindings', {}).get('direction')
            if field:
                plan['conditions'].append({'table': table, 'col': field, 'op': '=', 'value': expected['direction']})
            else:
                raise ValueError('此对象没有可核验的进出港方向映射')
        if metric and question:
            apply_acdm_metric_slots(question, self.catalog, metric, plan['conditions'])
        for role, value in [('flight', expected.get('flight')), ('aircraft', expected.get('aircraft'))]:
            if not value:
                continue
            fields = get_identifier_columns(role)
            field = next((f for f in fields if f in spec['attributes']), None)
            table = subject
            if not field:
                table = CORE
                field = next((f for f in fields if f in self.catalog.entities.get(CORE, {}).get('attributes', {})), None)
            if field:
                if role == 'aircraft' and field == 'aircraft_reg':
                    # The standard model accepts both source spellings; IN keeps values bound.
                    values = value if isinstance(value, list) else [value]
                    values = list(dict.fromkeys(v for item in values for v in [item, 'B-' + item[1:]]))
                    plan['conditions'].append({'table': table, 'col': field, 'op': 'in', 'value': values})
                else:
                    plan['conditions'].append({'table': table, 'col': field, 'op': 'in' if isinstance(value, list) else '=', 'value': value})
            else:
                raise ValueError('此对象没有可核验的航班或机号映射')
        for ref in expected.get('references', []):
            if ref['table'] == subject:
                table, field = subject, ref['field']
            else:
                table = subject if ref.get('binding') in bindings or ref.get('column') in spec['attributes'] else CORE
                field = bindings.get(ref.get('binding')) or ref.get('column')
            if table in self.catalog.entities and field in self.catalog.entities[table]['attributes']:
                existing = next((c for c in plan['conditions'] if c['table'] == table and c['col'] == field and c['op'] in ('=', 'in')), None)
                if existing and ref.get('op', '=') == '=':
                    values = existing['value'] if existing['op'] == 'in' else [existing['value']]
                    existing.update(op='in', value=list(dict.fromkeys([*values, ref['value']])))
                else:
                    plan['conditions'].append({'table': table, 'col': field, 'op': ref.get('op', '='), 'value': ref['value']})
            else:
                raise ValueError('此对象缺少参考字典筛选字段映射')
        filters_map = getattr(self.catalog, 'filters', {}) or {}
        if expected.get('origin_city'):
            col_key = 'originCity' if 'originCity' in filters_map else ('origin_city' if 'origin_city' in spec.get('attributes', {}) else '')
            if col_key:
                plan['conditions'].append({'table': subject, 'col': col_key, 'op': '=', 'value': expected['origin_city']})
        if expected.get('dest_city'):
            col_key = 'destCity' if 'destCity' in filters_map else ('dest_city' if 'dest_city' in spec.get('attributes', {}) else '')
            if col_key:
                plan['conditions'].append({'table': subject, 'col': col_key, 'op': '=', 'value': expected['dest_city']})
        if intent == 'detail' and question:
            cancel_filter = get_cancel_status_filter(subject)
            if cancel_filter and '取消' in question and not any(c.get('value') in ('CAN', '取消') for c in plan['conditions']):
                stat_col, stat_val = cancel_filter
                plan['conditions'].append({'table': subject, 'col': stat_col, 'op': '=', 'value': stat_val})
        if expected.get('day_mode') in ('OPERATIONAL', 'CALENDAR'):
            plan['day_mode'] = expected['day_mode']
        apply_home_airport_scope(self.catalog, subject, plan['conditions'])
        return plan

    def _literal_filters(self, question, subject):
        aliases = {}
        for field, attr in self.catalog.entities[subject]['attributes'].items():
            for alias in [field, attr.get('label', ''), *attr.get('aliases', [])]:
                if alias:
                    aliases.setdefault(alias, set()).add(field)
        filters = []
        operators = get_literal_filter_operators()
        op_pat = '|'.join(re.escape(k) for k in sorted(operators, key=len, reverse=True))
        for alias, fields in sorted(aliases.items(), key=lambda item: -len(item[0])):
            if len(fields) != 1:
                continue
            match = re.search(
                re.escape(alias) + rf'\s*({op_pat})\s*[“\"\']?(.+?)(?=[”\"\']|的记录|的明细|[，,。；;？?]|$)',
                question,
                re.I,
            )
            if not match:
                continue
            field = next(iter(fields))
            if any(condition['col'] == field for condition, _ in filters):
                continue
            value = match[2].strip()
            attr = self.catalog.attribute(subject, field)
            value = next((key for key, label in attr.get('enums', {}).items() if label == value), value)
            filters.append(({'table': subject, 'col': field, 'op': operators[match[1]], 'value': value}, match[0].rstrip('”\"\'')))
        return filters

    def _residual(self, question, expected, subject, metric, hints=None):
        # 优先剥离纯展示字段从句（例如“，每次运行分别显示源航班ID、计划落地和实际落地时间”），让规则快路径直接命中
        rest, _ = strip_display_field_clauses(question, catalog=self.catalog, subject=subject)
        terms = [*self.catalog.entities[subject]['aliases'], self.catalog.entities[subject]['name']]
        if subject in (CORE, ACTUAL):
            terms += self.catalog.entities.get(CORE, {}).get('aliases', []) + self.catalog.entities.get(ACTUAL, {}).get('aliases', [])
        if metric and isinstance(metric, str) and metric in self.catalog.metrics:
            terms += self.catalog.metrics[metric].get('aliases', []) + [self.catalog.metrics[metric]['name']]
            terms += list(get_metric_residual_terms(metric))
            _, _, mod_terms = extract_acdm_dimensions_and_topn(question, self.catalog, target=subject, allow_topn=True)
            _, slot_terms = apply_acdm_metric_slots(question, self.catalog, metric, [])
            terms += mod_terms + slot_terms
        if not metric and '取消' in question and get_cancel_status_filter(subject):
            terms.append('取消')
        if hints is None and hasattr(self.catalog, 'resolve_ontology_hints'):
            hints = self.catalog.resolve_ontology_hints(question)
        if hints:
            terms += [s.get('term', '') for s in hints.get('synonyms', []) if s.get('term')]
            for sc in hints.get('scenarios', []):
                if sc.get('code') in ('MetricQuery', 'FlightListQuery'):
                    continue
                for kw in sc.get('matched_keywords') or sc.get('trigger_keywords') or []:
                    if kw and kw in question:
                        terms.append(kw)
        explicit_dir, origin_city, dest_city, home_terms, consumed_home = extract_home_airport_city_and_dir(question, self.catalog)
        terms += consumed_home
        if origin_city:
            terms.append(origin_city)
        if dest_city:
            terms.append(dest_city)
        terms += home_terms
        terms += list(self._turn_ignored_terms or [])
        terms += [r['term'] for r in expected.get('references', [])]
        for term in sorted(set(terms), key=len, reverse=True):
            if term:
                rest = re.sub(re.escape(term), '', rest, flags=re.I)
        for _, quote in self._literal_filters(question, subject):
            rest = rest.replace(quote, '')
        rest = DATE_RANGE_STRIP_RE.sub('', rest)
        rest = DAY_MODE_STRIP_RE.sub('', rest)
        rest = IDENTIFIER_STRIP_RE.sub('', rest)
        for token in get_residual_vocabulary():
            rest = rest.replace(token, '')
        return re.sub(r'[\s，,。；;？?！!：:、]', '', rest)

    def _route_subject_with_model(self, question, directory, cards):
        route_stage = 'model_route_' + str(len(self.trace.steps)) if self.trace else None
        if self.trace:
            self.trace.start(route_stage, '选择业务目录', '模型从授权目录选择查询主体')
        route = json_object(
            self.chat(
                '你是运营问数总控。只从授权目录选择一个业务主体，不生成SQL。返回 {"subject":"真实表键","unresolved":"有歧义时说明"}。目录：'
                + json.dumps(directory, ensure_ascii=False),
                question,
                max_tokens=500,
                timeout=20,
            )
        )
        subject = self._table_key(route.get('subject'))
        if self.trace:
            self.trace.finish(
                route_stage,
                '已选择授权业务对象' if subject in cards else '业务对象仍需确认',
                [subject] if subject in cards else [],
                state='completed' if subject in cards else 'skipped',
            )
        return subject

    def _build_model_prompt(self, subject, question, expected, directory):
        selected = {subject, *self.catalog.relation_targets(question, subject)}
        selected.update(c['table'] for c in self._base(subject, 'detail', expected)['conditions'])
        context = {table: spec for table, spec in self.catalog.context('', selected).items() if table in selected}
        hidden = set(self.catalog.overrides.get('sensitive_fields', []))
        for spec in context.values():
            spec['fields'] = {
                key: {k: v for k, v in attr.items() if v and not (k == 'label' and v == key)}
                for key, attr in spec['fields'].items()
                if key not in hidden
            }
        metrics = {
            key: {k: v for k, v in metric.items() if k in ('name', 'aliases', 'base_table', 'unit', 'date_field', 'availability')}
            for key, metric in self.catalog.metrics.items()
            if metric.get('base_table') == subject
        }
        dimensions = {key: {k: v for k, v in dim.items() if k in ('name', 'aliases')} for key, dim in self.catalog.dimensions.items()}
        prompt = (
            '仅生成一个结构化查询计划，禁止SQL。subject及conditions.table必须填写目录键如dim_airline，不能填中文名称。'
            '严格保留问题全部筛选、排序与粒度，不添加用户没要求的条件。'
            '未明确计划/实际、必要业务日期或统计口径时填写 clarification，不猜默认。'
            'clarification仅填写确实需要用户回答的疑问句；没有缺失条件时必须为空字符串，不能填需求解释或总结。'
            '实际执行航班使用 ops_ask_views.flight_execution.execution_time；不能用计划 flight_date 替代。'
            '基础资料没有日期要求。排序前N用order_by和limit，不是统计指标。'
            '用户未要求排序时order_by必须为空数组；未要求分组时group_by=[]且time_bucket=null。'
            '普通记录条数使用intent=count且metric=null；只有确实询问已定义业务指标时才使用metric。'
            'confirmed中的日期、方向、航司、航班号等条件由系统自动添加，请不要重复输出这些条件。'
            '已发布指标（metric）的内置口径（如有效过站、已执飞、非A/N、非CAN等）已在公式中自动过滤，严禁在conditions中额外添加 is_both_operated="Y"、valid_block_sequence=true、is_operated_flight="Y" 等原文未出现的内部标志字段筛选。'
            '每个筛选须提供grounding:{table,col,quote}，quote逐字引用原问题或确认条件；编码可来自参考字典。'
            '只用已发布指标，不计算新公式。返回 {subject,intent:detail|count|sum|metric,conditions:[{table,col,op,value}],'
            'focus_tables:[],aggregate_target,metric,group_by:[],time_bucket:null,order_by:[{field,direction:asc|desc}],limit:null,'
            'grounding:[{table,col,quote}],clarification:"",unsupported:""}。op仅=,!=,>,>=,<,<=,prefix,contains,in,null,notnull。'
            '\n所选业务对象：' + json.dumps({key: directory[key] for key in selected if key in directory}, ensure_ascii=False)
            + '\n详细结构：' + json.dumps(context, ensure_ascii=False)
            + '\n指标与维度：' + json.dumps({'metrics': metrics, 'dimensions': dimensions}, ensure_ascii=False)
        )
        return prompt, context

    def _build_model_payload(self, question, anchor, expected):
        prior_ctx = (self._previous_plan or {}).get('query_context') if isinstance(self._previous_plan, dict) else None
        payload_dict = {
            'question': question,
            'confirmed': expected,
            'today': anchor.date().isoformat(),
            'references': expected.get('references', []),
        }
        if prior_ctx and prior_ctx.get('question') and prior_ctx['question'] != question:
            payload_dict['previous_turn'] = {
                'question': prior_ctx['question'],
                'subject': self._previous_plan.get('subject'),
                'intent': self._previous_plan.get('intent'),
                'metric': self._previous_plan.get('metric'),
                'slots': prior_ctx.get('slots', {}),
            }
        if self._history:
            payload_dict['conversation_history'] = list(self._history[-6:])
        return json.dumps(payload_dict, ensure_ascii=False)

    def _sanitize_model_plan(self, raw_plan, context):
        plan = {
            key: value
            for key, value in raw_plan.items()
            if key in ('subject', 'intent', 'conditions', 'focus_tables', 'aggregate_target', 'metric', 'group_by', 'time_bucket', 'order_by', 'limit', 'grounding')
        }
        if plan.get('intent') not in ('detail', 'count', 'sum', 'metric'):
            raise ValueError('模型只能规划明细、计数或已定义指标查询')
        plan['subject'] = self._table_key(plan.get('subject'))
        if plan.get('metric') is not None and not isinstance(plan['metric'], str):
            raise ValueError('单次查询只能指定一个已发布的指标；多个指标请使用分别或对比查询')
        for key in ('conditions', 'focus_tables', 'group_by', 'grounding', 'order_by'):
            if plan.get(key) is None:
                plan[key] = []
        for condition in plan.get('conditions', []):
            if not isinstance(condition, dict):
                raise ValueError('筛选条件必须为对象')
            if condition.get('table'):
                condition['table'] = self._table_key(condition['table'])
                if not condition['table']:
                    raise ValueError('筛选条件引用了未知业务对象')
        for proof in plan.get('grounding', []):
            if not isinstance(proof, dict):
                raise ValueError('条件依据必须为对象')
            if proof.get('table'):
                proof['table'] = self._table_key(proof['table'])
        plan['focus_tables'] = [self._table_key(table) for table in plan.get('focus_tables', [])]
        if plan.get('aggregate_target'):
            plan['aggregate_target'] = self._table_key(plan['aggregate_target'])
            if not plan['aggregate_target']:
                raise ValueError('统计目标不是授权业务对象')
        if plan.get('subject') not in context:
            raise ValueError('主体不在授权详细目录中')
        return plan

    def _model(self, question, anchor, expected, subject):
        self._finish_rule_trace('规则尚未完整匹配，转入模型辅助规划')
        cards = self.catalog.query_capabilities()
        directory = {key: {k: v for k, v in card.items() if k != 'fields'} for key, card in cards.items()}
        if not subject:
            subject = self._route_subject_with_model(question, directory, cards)
            if subject not in cards:
                return self.notice('clarification_needed', '请明确要查询的业务对象：' + '、'.join(c['name'] for c in cards.values()), question, anchor, expected)
        prompt, context = self._build_model_prompt(subject, question, expected, directory)
        payload = self._build_model_payload(question, anchor, expected)
        error = ''
        for attempt in range(2):
            stage = 'model_plan_' + str(len(self.trace.steps)) if self.trace else None
            if self.trace:
                self.trace.start(stage, '模型辅助规划' if not attempt else '模型计划纠偏', '仅生成受本体约束的结构化计划，不生成 SQL')
            try:
                raw_plan = json_object(
                    self.chat(
                        prompt + ('\n上次计划校验失败：' + error + '。保留原条件修正；无法修正请澄清。' if error else ''),
                        payload,
                        max_tokens=2200,
                        timeout=40,
                    )
                )
                if raw_plan.get('unsupported'):
                    if self.trace:
                        self.trace.finish(stage, '请求尚未映射到已发布能力', state='skipped')
                    return self.notice('unsupported', '这个要求尚未映射到已发布查询能力，请明确业务对象或指标；不能由模型补造数据与口径。', question, anchor, expected)
                uncertainty = str(raw_plan.get('clarification') or raw_plan.get('unresolved') or '')
                if uncertainty and re.search(r'[？?]|请|是否|需要|缺少|确认|不明确|哪', uncertainty):
                    if self.trace:
                        self.trace.finish(stage, '查询条件仍需用户确认', state='skipped')
                    return self.notice('clarification_needed', '业务对象或附加条件仍不明确，请补充要查询的条件、日期或指标。', question, anchor, expected)
                raw_plan.pop('unresolved', None)
                plan = self._sanitize_model_plan(raw_plan, context)
                validated = self.validate(plan, question, expected, anchor)
                if self.trace:
                    self.trace.finish(stage, '结构化计划已通过本体校验' if not validated.get('outcome') else '已识别业务边界或待确认条件')
                return validated
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                error = str(exc)[:300]
                if self.trace:
                    self.trace.finish(stage, '计划校验未通过，尝试纠偏' if not attempt else '纠偏后仍需澄清', state='failed')
        return self.notice('clarification_needed', '当前问法未能生成完整且可核验的查询计划，请明确条件或口径。' + error, question, anchor, expected)

    def _table_key(self, value):
        if isinstance(value, str) and value in self.catalog.entities:
            return value
        matches = [key for key, spec in self.catalog.entities.items() if isinstance(value, str) and value in [spec['name'], *spec['aliases']]]
        return matches[0] if len(matches) == 1 else None

    def _validate_acdm_report(self, plan, question, expected, anchor, hints):
        if not expected.get('period'):
            return self.notice('clarification_needed', '请确认报告的业务日期或期间。', question, anchor, expected, 'period', self._period_choices(anchor))
        start, end = map(date.fromisoformat, expected['period'])
        ground = plan.get('report_kind') == 'ground_briefing'
        if ground and end - start != timedelta(days=1):
            return self.notice('clarification_needed', '地服运行简报按单个业务日生成，请确认一个日期。', question, anchor, expected, 'period', self._period_choices(anchor))
        rest = DATE_RANGE_STRIP_RE.sub('', question)
        if ground:
            from app.acdm_reports import GROUND_BRIEFING_CUTOFF
            rest = ground_briefing_display_residual(re.sub(GROUND_BRIEFING_NAME, '', rest), GROUND_BRIEFING_CUTOFF)
            if rest is None:
                message = f'地服运行简报的正常率固定统计至 {GROUND_BRIEFING_CUTOFF:%H:%M}。如需其他时间，请单独查询该时段的指标。'
                return self.notice('unsupported', message, question, anchor, expected)
        rest = REPORT_RESIDUAL_STRIP_RE.sub('', rest)
        if (rest or any(expected.get(key) for key in ('references', 'flight', 'aircraft'))
                or (not ground and any(expected.get(key) for key in ('direction', 'basis')))):
            return self.notice('unsupported', '这份报告使用已发布的整份报告口径，暂不支持附加筛选；请单独查询所需对象或指标。', question, anchor, expected)
        plan.update(report_start=start.isoformat(), report_end=(end - timedelta(days=1)).isoformat())
        tpl = match_plan_template(self.catalog, plan, hints)
        if tpl:
            plan['plan_template'] = tpl
        gate = check_capability_gate(self.catalog, plan, template=plan.get('plan_template'))
        if not gate['allowed']:
            return self.notice('unsupported', gate['message'], question, anchor, expected, capability_gate=gate)
        plan = self.compiler.validate(plan)
        self._save_query_context(plan, question, expected, anchor)
        plan['coordinator_version'] = VERSION
        return plan

    def _normalize_plan_conditions(self, subject, conditions, required):
        normalized_conditions = []
        for raw in conditions:
            if not isinstance(raw, dict):
                raise ValueError('筛选条件必须是对象')
            condition = {**raw, 'table': raw.get('table') or subject}
            same_field = [c for c in required if (c['table'], c['col']) == (condition['table'], condition.get('col'))]
            if not same_field and {condition['table'], subject} <= {CORE, ACTUAL}:
                same_field = [c for c in required if c['col'] == condition.get('col')]
            equivalent = next(
                (c for c in same_field if c['op'] == condition.get('op') and self._equal_value(c['value'], condition.get('value'), c['table'], c['col'])),
                None,
            )
            if equivalent:
                normalized_conditions.append(deepcopy(equivalent))
                continue
            dates = [c for c in same_field if c['op'] in ('>=', '<') and isinstance(c['value'], str) and re.match(r'^\d{4}-\d{2}-\d{2}', c['value'])]
            value = str(condition.get('value', ''))
            # Normalize confirmed calendar-day notation before SQL, never a different date or field.
            if len(dates) == 2:
                lower = next(c for c in dates if c['op'] == '>=')
                upper = next(c for c in dates if c['op'] == '<')
                start_iso = lower['value'][:10]
                end_incl = (date.fromisoformat(upper['value'][:10]) - timedelta(days=1)).isoformat()
                m_dt = re.fullmatch(
                    r'(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?',
                    value.strip(),
                )
                if m_dt:
                    v_date, v_time = m_dt[1], m_dt[2]
                    if condition.get('op') in ('=', 'prefix') and v_date == start_iso == end_incl and v_time in (None, '00:00:00'):
                        normalized_conditions.extend(deepcopy(dates))
                        continue
                    if condition.get('op') == '>=' and v_date == start_iso and v_time in (None, '00:00:00'):
                        normalized_conditions.append(deepcopy(lower))
                        continue
                    if (
                        (condition.get('op') == '<' and v_date == upper['value'][:10] and v_time in (None, '00:00:00'))
                        or (condition.get('op') in ('<=', '<') and v_date == end_incl and v_time in (None, '23:59:59', '23:59:00'))
                    ):
                        normalized_conditions.append(deepcopy(upper))
                        continue
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    equivalent = next((c for c in dates if c['op'] == condition.get('op') and c['value'][:10] == value), None)
                    if equivalent:
                        normalized_conditions.append(deepcopy(equivalent))
                        continue
            normalized_conditions.append(condition)

        for condition in required:
            equivalents = [
                c for c in normalized_conditions
                if isinstance(c, dict) and c.get('op') == condition['op'] and (c.get('table') or subject, c.get('col')) == (condition['table'], condition['col'])
            ]
            if equivalents and condition not in equivalents:
                # Missing table ownership is harmless; changing the value/operator is not.
                if not any({**c, 'table': c.get('table') or subject} == condition for c in equivalents):
                    raise ValueError('计划改变了明确条件：' + condition['col'])
            if condition not in [{**c, 'table': c.get('table') or subject} for c in normalized_conditions]:
                normalized_conditions.append(condition)
        return normalized_conditions

    def _validate_extra_condition_grounding(self, subject, conditions, required, grounded, question):
        flight_cols = set(get_identifier_columns('flight'))
        aircraft_cols = set(get_identifier_columns('aircraft'))
        for condition in conditions:
            if not isinstance(condition, dict):
                raise ValueError('筛选条件必须是对象')
            normalized = {**condition, 'table': condition.get('table') or subject}
            if normalized in required:
                continue
            attr = self.catalog.attribute(normalized['table'], condition.get('col'))
            quotes = [
                g.get('quote')
                for g in grounded
                if isinstance(g, dict) and g.get('col') == condition.get('col') and (g.get('table') or subject) == normalized['table']
            ]
            if not any(isinstance(quote, str) and quote and quote in question for quote in quotes):
                raise ValueError('额外条件没有原问题依据：' + str(condition.get('col')))
            values = condition.get('value') if condition.get('op') == 'in' else [condition.get('value')]
            for value in values:
                enum = attr.get('enums', {}).get(str(value), attr.get('enums', {}).get(str(value).lower(), ''))
                ref = any(str(r['value']) == str(value) and r['term'] in question for r in self.references)
                ident_token = (
                    (condition.get('col') in flight_cols and re.fullmatch(r'(?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{2,4}[A-Z]?', str(value), re.I))
                    or (condition.get('col') in aircraft_cols and re.fullmatch(r'B-?[A-Z0-9]{4,5}', str(value), re.I))
                )
                if condition.get('op') in ('null', 'notnull'):
                    if not re.search(r'空|缺失|未填|有记录|有时刻', question):
                        raise ValueError('用户没有要求空值筛选')
                elif not any(
                    str(value).lower() in quote.lower()
                    or (enum and enum in quote)
                    or (ref and any(r['term'] in quote and str(r['value']) == str(value) for r in self.references))
                    for quote in quotes
                    if isinstance(quote, str)
                ):
                    raise ValueError('筛选值缺少可核验来源')
                if not any(
                    condition['col'].lower() in quote.lower()
                    or (attr.get('label') and attr['label'] in quote)
                    or (enum and enum in quote)
                    or any(alias in quote for alias in attr.get('aliases', []))
                    or ref
                    or ident_token
                    for quote in quotes
                    if isinstance(quote, str)
                ):
                    raise ValueError('条件字段与原文依据不匹配')

    def _validate_metric_and_modifiers(self, plan, subject, question, expected, anchor):
        if plan.get('metric'):
            if not isinstance(plan['metric'], str):
                raise ValueError('单次查询只能指定一个已发布的指标')
            if plan.get('intent') not in ('metric', 'sum'):
                raise ValueError('明细或普通记录计数不能附加模型选择的指标')
            metric = self.catalog.metrics.get(plan['metric'])
            if not metric:
                return self.notice(
                    'unsupported',
                    f"指标「{plan['metric']}」当前状态为 UNAVAILABLE（暂未开放）：该口径尚未上线或缺少源数据字段。",
                    question,
                    anchor,
                    expected,
                )
            if expected.get('basis') == 'actual' and plan['metric'] in FLIGHT_COUNT_METRICS:
                raise ValueError('实际执行架次应统计实际执行视图')
            if metric.get('base_table') and metric['base_table'] != subject:
                raise ValueError('指标的已发布业务对象与计划主体不一致')
            if subject == 'std_flight_production' and not metric.get('default_filters', {}).get('is_local_leg'):
                return self.notice('unsupported', '生产量指标尚未限定航段及重复数量口径，请先发布可核验的指标定义；可查询原始明细。', question, anchor, expected)
        if expected.get('sequence'):
            if not plan.get('order_by') or plan.get('limit') != 1:
                raise ValueError('首末记录必须保留排序和一条记录限制')
            if plan['order_by'][0]['direction'] != expected['sequence']:
                raise ValueError('首末记录排序方向与问题不一致')
            if subject == ACTUAL and plan['order_by'][0]['field'] != 'execution_time':
                raise ValueError('实际首末航班必须按实际执行时刻排序')
            if subject == ACTUAL:
                plan['flight_sequence'] = 'last' if expected['sequence'] == 'desc' else 'first'
        if plan.get('group_by') and not re.search(r'按|分|各|每|趋势|分布|哪[个一]?(?:具体)?(?:日期|天|日|小时|时段|航司|航空公司|机型|航站楼|机位)|高峰(?:小时|时段)|排名|排行', question):
            raise ValueError('用户没有要求分组统计')
        if plan.get('time_bucket') and not re.search(r'按|每|逐|趋势|分布', question):
            raise ValueError('用户没有要求时间分组')
        if plan.get('order_by') and not expected.get('sequence') and not re.search(r'排序|升序|降序|从大到小|从小到大|前\s*\d+|最高|最低|最多|最少|最长|最短', question):
            raise ValueError('用户没有要求改变记录排序')
        target = plan.get('aggregate_target')
        if target and target != subject and not any(alias in question for alias in self.catalog.entities[target]['aliases']):
            raise ValueError('用户没有请求更换统计对象')
        if not expected.get('sequence') and plan.get('limit') is not None:
            requested = re.search(r'(?:前|top)\s*([1-9]\d{0,2})|([1-9]\d{0,2})\s*(?:个|班|条)(?:航班)?', question, re.I)
            req_limit = int(requested.group(1) or requested.group(2)) if requested else None
            peak_one = plan['limit'] == 1 and re.search(
                r'最高是哪|最多是哪|峰值|高峰小时是哪个|高峰时段是哪个|哪一小时最多|哪个小时最多|哪[个一]?(?:具体)?(?:日期|天|日|小时|时段|航司|航空公司|机型|航站楼|机位).{0,12}最[多高大少低]',
                question,
            )
            if not peak_one and (not requested or plan['limit'] != req_limit):
                raise ValueError('模型添加了用户未要求的记录条数限制')
        if not plan.get('metric') and expected.get('intent') != 'detail' and is_count(question) and plan.get('intent') != 'count':
            raise ValueError('数量问题必须保留计数意图')
        return None

    def validate(self, plan, question, expected, anchor, hints=None):
        plan = deepcopy(plan)
        if plan.get('metric') is not None and not isinstance(plan['metric'], str):
            raise ValueError('单次查询只能指定一个已发布的指标')
        subject = plan['subject']
        if subject not in self.catalog.entities:
            raise ValueError('业务对象不在授权目录中')
        spec = self.catalog.entities[subject]
        semantics = spec.get('query_semantics', {})
        if plan.get('intent') == 'acdm_report':
            return self._validate_acdm_report(plan, question, expected, anchor, hints)
        actual_check_q = question
        for term in sorted(set(self._turn_ignored_terms or []), key=len, reverse=True):
            if term:
                actual_check_q = actual_check_q.replace(term, '')
        if expected.get('basis') == 'actual' and subject not in (CORE, ACTUAL) and not plan.get('metric') and re.search(
            r'执飞|执行|起降|起飞|落地|实际.*(?:航班|架次)|(?:航班|架次).*实际', actual_check_q
        ):
            if semantics.get('basis') != 'actual' and not semantics.get('time_label', '').startswith('实际'):
                return self.notice(
                    'clarification_needed',
                    '这个对象尚未声明实际执行统计口径。请确认按该对象的原始记录查询，或明确改查实际执飞航班。',
                    question,
                    anchor,
                    expected,
                )
        if subject in (CORE, ACTUAL) and not plan.get('metric'):
            if not expected.get('basis'):
                return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected, 'basis', get_basis_options(detailed=False))
            if expected['basis'] == 'actual' and subject != ACTUAL:
                raise ValueError('实际执行必须使用实际执飞视图和实际发生日')
            if expected['basis'] == 'planned' and subject == ACTUAL:
                raise ValueError('计划查询不能改用实际执行记录')
        if spec.get('query_semantics', {}).get('date_required') and not expected.get('period'):
            return self.notice('clarification_needed', '请确认业务日期。', question, anchor, expected, 'period', self._period_choices(anchor))
        plan.setdefault('conditions', [])
        plan.setdefault('focus_tables', [])
        plan.setdefault('group_by', [])
        base = self._base(subject, plan.get('intent', 'detail'), expected, metric=plan.get('metric'), question=question)
        conditions = plan['conditions']
        if not isinstance(conditions, list):
            raise ValueError('条件必须为数组')
        required = base['conditions'] + [condition for condition, _ in self._literal_filters(question, subject)]
        plan['conditions'] = conditions = self._normalize_plan_conditions(subject, conditions, required)
        grounded = plan.pop('grounding', [])
        self._validate_extra_condition_grounding(subject, conditions, required, grounded, question)
        early_notice = self._validate_metric_and_modifiers(plan, subject, question, expected, anchor)
        if early_notice is not None:
            return early_notice
        hints = hints or (self.catalog.resolve_ontology_hints(question) if hasattr(self.catalog, 'resolve_ontology_hints') else None)
        residual = self._residual(question, expected, subject, plan.get('metric'), hints=hints)
        if re.search(r'\d|大于|小于|不含|排除|不等于', residual) and not grounded and not self._literal_filters(question, subject):
            raise ValueError('未完整解析问题的附加筛选条件')
        if expected.get('day_mode') in ('OPERATIONAL', 'CALENDAR') and 'day_mode' not in plan:
            plan['day_mode'] = expected['day_mode']
        apply_rule_guardrails(self.catalog, plan)
        tpl = match_plan_template(self.catalog, plan, hints)
        if tpl:
            plan['plan_template'] = tpl
        gate = check_capability_gate(self.catalog, plan, template=plan.get('plan_template'))
        if not gate['allowed']:
            return self.notice('unsupported', gate['message'], question, anchor, expected, capability_gate=gate)
        plan = self.compiler.validate(plan)
        plan['disp'] = spec['name']
        self._save_query_context(plan, question, expected, anchor)
        plan['coordinator_version'] = VERSION
        return plan

    def _equal_value(self, left, right, table, field):
        if isinstance(left, list) and isinstance(right, list):
            return sorted(map(str, left)) == sorted(map(str, right))
        if str(left) == str(right):
            return True
        attr = self.catalog.attribute(table, field)
        if 'timestamp' in attr['type']:
            try:
                a = datetime.fromisoformat(str(left).replace('Z', '+00:00'))
                b = datetime.fromisoformat(str(right).replace('Z', '+00:00'))
                return (a.replace(tzinfo=BEIJING) if a.tzinfo is None else a) == (b.replace(tzinfo=BEIJING) if b.tzinfo is None else b)
            except ValueError:
                return False
        if any(kind in attr['type'] for kind in ('int', 'numeric', 'double', 'real')):
            try:
                a, b = Decimal(str(left)), Decimal(str(right))
                return a.is_finite() and b.is_finite() and a == b
            except InvalidOperation:
                return False
        return False
