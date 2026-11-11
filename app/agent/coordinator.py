"""Bounded, catalog-grounded planning for one authorized operations query."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from app.query import Compiler
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


BEIJING = timezone(timedelta(hours=8))
ACTUAL = 'ops_ask_views.flight_execution'
CORE = 'std_flight_core'
VERSION = 'ops-coordinator-v1'
FOLLOW_UP = re.compile(r'^(?:(?:那|那么)?(?:进港|出港)(?:呢|的呢)?|(?:换成|改成|再看|那)[^？?]{1,18}|明细|列出来|有哪些|多少条)[？?]?$')
SEQUENCE = re.compile(r'最后|最晚|最迟|末班|最早|第一班|首班')


def is_count(question):
    return bool(re.search(r'多少|几条|几家|几班|几架|数一数|总数|条数|数量[?？。]*$|统计.*数量',question))


def json_object(content):
    if not isinstance(content, str):
        raise ValueError('模型返回格式无效')
    value = json.JSONDecoder().raw_decode(content[content.find('{'):])[0] if '{' in content else None
    if not isinstance(value, dict):
        raise ValueError('模型需要返回 JSON 对象')
    return value


def period(question, anchor):
    text = re.sub(r'(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?',
                  lambda m: f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}', question)
    def _md_replace(m):
        try:
            return date(anchor.year, int(m[1]), int(m[2])).isoformat()
        except ValueError:
            return m[0]
    text = re.sub(r'(?<![0-9A-Za-z])(\d{1,2})月(\d{1,2})[日号]?', _md_replace, text)
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', text)
    if dates:
        start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        if start > end:
            raise ValueError('开始日期不能晚于结束日期')
        return [start.isoformat(), (end + timedelta(days=1)).isoformat()]
    today = anchor.date()
    for words, days in [(('前天',), 2), (('昨天', '昨日', '前一天'), 1), (('今天', '今日'), 0)]:
        if any(word in question for word in words):
            start = today - timedelta(days=days)
            return [start.isoformat(), (start + timedelta(days=1)).isoformat()]
    match = re.search(r'(?:最近|近)([1-9]\d?)天', question)
    if match or re.search(r'最近一周|近一周', question):
        days = int(match[1]) if match else 7
        return [(today - timedelta(days=days - 1)).isoformat(), (today + timedelta(days=1)).isoformat()]
    if re.search(r'最近一个月|近一个月|近一月', question):
        return [(today - timedelta(days=29)).isoformat(), (today + timedelta(days=1)).isoformat()]
    if '本月' in question:
        return [today.replace(day=1).isoformat(), (today + timedelta(days=1)).isoformat()]
    return None


def slots(question, anchor, references, catalog=None):
    q = unicodedata.normalize('NFKC', question)
    result = {}
    window = period(q, anchor)
    if window:
        result['period'] = window
    if '运营日' in q:
        result['day_mode'] = 'OPERATIONAL'
    elif '自然日' in q:
        result['day_mode'] = 'CALENDAR'
    planned = bool(re.search(r'计划|排班|预定|预计', q))
    actual = bool(re.search(r'实际|执飞|执行|已飞|飞走|离地|落地|已起飞|已到达', q))
    if not planned and re.search(r'起飞|降落', q):
        actual = True
    if actual and not planned:
        result['basis'] = 'actual'
    elif planned and not actual:
        result['basis'] = 'planned'
    if re.search(r'进出港|不分方向|全部方向', q):
        result['direction'] = 'BOTH'
    else:
        dep = bool(re.search(r'出港|离港|起飞|飞走|离地|出发', q))
        arr = bool(re.search(r'进港|到港|落地|降落|抵达', q))
        if dep != arr:
            result['direction'] = 'DEP' if dep else 'ARR'
    if catalog is not None:
        explicit_dir, origin_city, dest_city, _home_terms, _consumed = extract_home_airport_city_and_dir(q, catalog)
        if 'direction' not in result and explicit_dir:
            result['direction'] = explicit_dir
        if origin_city:
            result['origin_city'] = origin_city
        if dest_city:
            result['dest_city'] = dest_city
    if SEQUENCE.search(q):
        result['sequence'] = 'desc' if re.search(r'最后|最晚|最迟|末班', q) else 'asc'
    identifiers = re.sub(r'\d{4}-\d{2}-\d{2}',' ',q.upper())
    tails = list(re.finditer(r'(?<![A-Z0-9_])(B-[A-Z0-9]{4,5}|B(?=[A-Z0-9]{0,4}[0-9])[A-Z0-9]{4,5})(?![A-Z0-9_])', identifiers))
    numbers = [m for m in re.finditer(r'(?<![A-Z0-9])((?:[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])\d{2,4}[A-Z]?)(?![A-Z0-9])', identifiers)
               if not any(t.start()<=m.start()<t.end() for t in tails)]
    for key,values in [('aircraft',[m[1].replace('-','') for m in tails]),('flight',[m[1] for m in numbers])]:
        values=list(dict.fromkeys(values))
        if values:
            result[key]=values[0] if len(values)==1 else values
    for ref in sorted(references, key=lambda item: -len(item['term'])):
        term = re.escape(ref['term'])
        pattern = r'(?<![A-Za-z0-9])' + term + r'(?![A-Za-z0-9])' if re.fullmatch(r'[A-Za-z0-9]{1,3}', ref['term']) else term
        if re.search(pattern, q, re.I):
            found = re.search(pattern, q, re.I)
            before, after = q[max(0,found.start()-5):found.start()], q[found.end():found.end()+3]
            result.setdefault('references', []).append({**ref, 'op':'!=' if re.search(r'不是|不含|排除|除去|除了',before) or '以外' in after else '='})
    # Longer names own shorter overlapping dictionary entries.
    chosen = {}
    for ref in result.get('references', []):
        chosen.setdefault((ref['role'], str(ref['value'])), ref)
    if chosen:
        result['references'] = list(chosen.values())
    else:
        result.pop('references', None)
    return result


class Coordinator:
    def __init__(self, catalog, chat=None, references=(), trace=None):
        self.catalog, self.chat, self.trace = catalog, chat, trace
        self.references = list(references)
        self.compiler = Compiler(catalog)

    def notice(self, outcome, text, question, anchor, confirmed, slot=None, choices=(), **extra):
        result = {'outcome': outcome, 'answer': text, **extra}
        if outcome == 'clarification_needed':
            result['clarification'] = {
                'original_question': question, 'anchor': anchor.isoformat(),
                'confirmed': confirmed, 'slot': slot, 'question': text,
                'options': [{'id': str(i), 'label': label, 'value': value}
                            for i, (label, value) in enumerate(choices, 1)],
                'catalog_version': self.catalog.version,
            }
        return result

    def plan(self, question, previous_plan=None, clarification=None, choice_id=None, day_mode='AUTO'):
        anchor = datetime.now(BEIJING)
        confirmed = {}
        original = unicodedata.normalize('NFKC', question).strip()
        if clarification and not choice_id and len(original) > 12 and re.search(r'查询|统计|查一下|显示|哪些|多少|几班|几架|哪一班|哪个', original) and (
                any(alias in original for spec in self.catalog.entities.values() for alias in spec['aliases']) or
                re.search(r'执行|执飞|起飞|飞走|落地|航班|架次', original)):
            clarification = None
        if clarification:
            original = clarification['original_question']
            anchor = datetime.fromisoformat(clarification['anchor']).astimezone(BEIJING)
            confirmed = deepcopy(clarification.get('confirmed', {}))
            if clarification.get('catalog_version') != self.catalog.version:
                return self.notice('clarification_needed', '业务目录已更新，请重新确认原问题的条件。',
                                   original, anchor, {}, 'restart', [('重新解析原问题', True)])
            slot = clarification.get('slot')
            options = clarification.get('options', [])
            selected = next((option for option in options if option['id'] == choice_id), None) if choice_id else None
            if choice_id and not selected:
                raise ValueError('澄清选项无效，请重新选择')
            if selected:
                if slot != 'restart':
                    confirmed[slot] = selected['value']
            elif slot != 'restart':
                parsed = slots(question, anchor, self.references, self.catalog)
                if slot in parsed:
                    confirmed[slot] = parsed[slot]
                elif slot == 'identifier' and (parsed.get('flight') or parsed.get('aircraft')):
                    confirmed.update({key: parsed[key] for key in ('flight', 'aircraft') if key in parsed})
                elif not options:
                    original += '；补充条件：' + question
                else:
                    return self.notice('clarification_needed', clarification['question'], original, anchor,
                                       confirmed, slot, [(o['label'], o['value']) for o in options])
        elif previous_plan and FOLLOW_UP.fullmatch(original):
            prior = previous_plan.get('query_context')
            if prior:
                original = prior['question']
                confirmed = deepcopy(prior['slots'])
                confirmed.update(slots(question, anchor, self.references, self.catalog))
                if re.search(r'明细|列出|有哪些', question):
                    confirmed['intent'] = 'detail'
                elif re.search(r'多少', question):
                    confirmed['intent'] = 'count'
            else:
                return self.notice('clarification_needed', '请补充完整问题，上一轮没有可继承的业务口径。', original, anchor, {})
        expected = slots(original, anchor, self.references, self.catalog)
        expected.update(confirmed)
        if 'day_mode' not in expected and day_mode in ('OPERATIONAL', 'CALENDAR'):
            expected['day_mode'] = day_mode
        if re.search(r'对比|同比|环比|分别统计|分别查询', original):
            return self.notice('unsupported', '首版一次处理一项查询，请先选择一个统计对象或期间。', original, anchor, expected)
        if re.search(r'能问什么|可以问什么|支持哪些|可查范围', original):
            return self.notice('answered', '当前可查询：' + '、'.join(s['name'] for s in self.catalog.entities.values()) +
                               '。支持明细、筛选、排序、计数及已发布指标；口径有歧义时会先确认。', original, anchor, expected)
        if self.trace:
            self.trace.start('coordinator', '总控识别业务与口径', '从全部授权业务目录中识别对象，检查关键歧义与条件完整性')
        try:
            return self._plan(original, anchor, expected)
        except ValueError as exc:
            return self.notice('clarification_needed', '查询条件尚不能完整确定：' + str(exc)[:300], original, anchor, expected)
        except Exception:
            if self.trace:
                self.trace.finish('coordinator','业务规划请求未完成',state='failed')
            raise
        finally:
            if self.trace and self.trace.steps['coordinator']['state']=='running':
                self.trace.finish('coordinator', '本轮业务识别与口径检查完成')

    def _plan(self, question, anchor, expected):
        catalog = self.catalog
        report = match_acdm_report(question, catalog,
            bounds_fn=lambda _: ('range', *expected['period']) if expected.get('period') else None,
            today_d=anchor.date())
        if report:
            return self.validate(report, question, expected, anchor)
        candidates = catalog.retrieve(question)
        explicit = [table for table in candidates if any(alias.lower() in question.lower() for alias in catalog.entities[table]['aliases'])]
        flight_words = bool(re.search(r'航班|起飞|落地|飞走|离地|执飞|执行', question))
        if 'dim_airline' in explicit and flight_words and re.search(r'排名|排行|分布|各[个]?航司|按航司', question):
            explicit = [t for t in explicit if t != 'dim_airline']
        non_flight_explicit = [t for t in explicit if t not in (CORE, ACTUAL)]
        hints = catalog.resolve_ontology_hints(question) if hasattr(catalog, 'resolve_ontology_hints') else {'synonyms': [], 'concepts': [], 'scenarios': []}
        cm_codes = {cm.get('concept_code') for cm in (getattr(catalog, 'concept_metrics', []) or []) if cm.get('concept_code')}
        is_list_ask = bool(expected.get('intent') == 'detail' or re.search(r'有哪些|列表|清单|明细|哪几班|航班明细', question))
        metric_text = question
        for spec in catalog.entities.values():
            metric_text = metric_text.replace(spec['name'],'')
        metric_hits = [(len(alias), key) for key, cfg in catalog.metrics.items()
                       for alias in [cfg.get('name', ''), *cfg.get('aliases', [])] if alias and alias in metric_text]
        metric = max(metric_hits)[1] if metric_hits else None
        if metric in (None, 'flight_count', 'flight_total_count') and not is_list_ask and not non_flight_explicit:
            metric_concept_codes = [
                c['code'] for c in hints.get('concepts', [])
                if c.get('concept_type') == 'METRIC' or (c.get('code') in cm_codes and c.get('concept_type') != 'ATTRIBUTE')
            ]
            resolved_cm = None
            if metric_concept_codes:
                dir_hint = expected.get('direction') if expected.get('direction') in ('DEP', 'ARR') else None
                resolved_cm = resolve_concept_metric(catalog, metric_concept_codes, dir_hint)
            if resolved_cm:
                metric = resolved_cm
            elif is_count(question) and ('延误' in question or '晚点' in question):
                cand_delay = 'arr_delay_count' if expected.get('direction') == 'ARR' else 'dep_delay_count'
                if cand_delay in catalog.metrics:
                    metric = cand_delay
            elif is_count(question) and '取消' in question and 'cancel_count' in catalog.metrics:
                metric = 'cancel_count'
            elif not metric:
                top_sc = (hints.get('scenarios') or [None])[0]
                if top_sc and top_sc.get('metric_code') in catalog.metrics and top_sc.get('code') not in ('MetricQuery', 'FlightListQuery'):
                    metric = top_sc['metric_code']
            if not metric and not non_flight_explicit and re.search(r'排名|排行|趋势|分布', question) and 'flight_count' in catalog.metrics:
                cand_gb, _, _ = extract_acdm_dimensions_and_topn(question, catalog, target=CORE, allow_topn=True)
                if cand_gb:
                    metric = 'flight_count'
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
        if expected.get('basis') == 'actual' and metric in ('flight_count', 'flight_total_count'):
            metric = None
        subject = expected.get('subject')
        if not subject and metric:
            subject = catalog.metrics[metric].get('base_table')
        if not subject and explicit:
            subject = explicit[0]
        if not subject and (flight_words or expected.get('flight') or expected.get('aircraft') or expected.get('origin_city') or expected.get('dest_city')):
            subject = CORE
        turnaround = bool(re.search(r'甘特|保障诊断|作业诊断|过站.*(?:诊断|进度|时序)', question))
        if turnaround:
            for key in ('flight','aircraft'):
                if isinstance(expected.get(key),list):
                    return self.notice('clarification_needed','本轮保障诊断请选择一个身份。',question,anchor,expected,
                                       key,[(value,value) for value in expected[key]])
            subject = 'acdm.tb_dyn_flight' if re.search(r'ACDM.*(?:记录|作业)', question, re.I) else CORE
        if subject and subject not in catalog.entities:
            return self.notice('unsupported', '当前授权目录没有这个业务对象，请先接入或配置查询权限。', question, anchor, expected)
        if metric in ('flight_count', 'flight_total_count') and not expected.get('basis'):
            return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected,
                               'basis', [('实际执行（按实际起飞/落地）', 'actual'), ('计划航班（按计划业务日）', 'planned')])
        if subject in (CORE, ACTUAL) and not metric and not turnaround:
            if not expected.get('basis'):
                return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected,
                                   'basis', [('实际执行（按实际起飞/落地）', 'actual'), ('计划航班（按计划业务日）', 'planned')])
            subject = ACTUAL if expected['basis'] == 'actual' else CORE
        if expected.get('sequence') and not expected.get('direction') and subject in (CORE, ACTUAL):
            return self.notice('clarification_needed', '请确认比较出港、进港，还是全部方向。', question, anchor, expected,
                               'direction', [('出港', 'DEP'), ('进港', 'ARR'), ('进出港合并比较', 'BOTH')])
        if subject:
            temporal = catalog.entities[subject].get('query_semantics', {})
            if (temporal.get('date_required') or turnaround) and not expected.get('period'):
                return self.notice('clarification_needed', '请确认要查询的业务日期。', question, anchor, expected,
                                   'period', [(label, period(label, anchor)) for label in ('昨天', '今天', '前天')])
        if turnaround:
            if expected.get('period') and date.fromisoformat(expected['period'][1])-date.fromisoformat(expected['period'][0]) != timedelta(days=1):
                return self.notice('clarification_needed','保障诊断需要定位单个业务日，请确认日期。',question,anchor,expected,
                                   'period',[(label,period(label,anchor)) for label in ('昨天','今天','前天')])
            if not (expected.get('flight') or expected.get('aircraft')):
                return self.notice('clarification_needed', '请提供具体航班号或机号，以定位保障记录。', question, anchor, expected, 'identifier')
            remaining = re.sub(r'过站|保障|作业|诊断|甘特图?|时序图?', '', self._residual(question,expected,subject,None))
            if remaining:
                return self.notice('clarification_needed','保障诊断还有未能确认的附加条件，请明确一个航班或机号及业务日。',question,anchor,expected)
            plan = self._base(subject, 'turnaround', expected)
            plan.update(query_identifier=expected.get('flight') or expected['aircraft'],
                        flight_date=expected['period'][0], conditions=[])
            plan['query_context'] = {'question': question, 'slots': expected, 'anchor': anchor.isoformat()}
            return plan
        if expected.get('intent') == 'detail':
            metric = None
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
                    return self._model(question,anchor,expected,None)
                raise
            if metric:
                candidate.update(intent='metric', metric=metric, aggregate_target=subject)
                if not expected.get('sequence'):
                    group_by, top_opts, _ = extract_acdm_dimensions_and_topn(
                        question, catalog, target=subject, allow_topn=True
                    )
                    top_sc = (hints.get('scenarios') or [None])[0]
                    if top_sc and re.search(r'按|分|各|每|趋势|分布|排名|排行', question):
                        for dim_code in top_sc.get('dimensions') or []:
                            if dim_code in getattr(catalog, 'dimensions', {}) and dim_code not in group_by:
                                group_by.append(dim_code)
                    if group_by:
                        candidate['group_by'] = group_by
                    if top_opts:
                        candidate.update(top_opts)
            if expected.get('sequence'):
                time_field = ('scheduled_departure' if subject == CORE and expected.get('direction') == 'DEP' else
                              'scheduled_arrival' if subject == CORE and expected.get('direction') == 'ARR' else
                              catalog.entities[subject]['bindings'].get('time'))
                if not time_field:
                    return self.notice('unsupported', '此对象未定义可用于先后排序的业务时刻，请明确排序字段。', question, anchor, expected)
                candidate.update(intent='detail', order_by=[{'field': time_field, 'direction': expected['sequence']}], limit=1)
                if subject == ACTUAL:
                    candidate['flight_sequence'] = 'last' if expected['sequence'] == 'desc' else 'first'
            if not self._residual(question, expected, subject, metric, hints=hints):
                return self.validate(candidate, question, expected, anchor, hints=hints)
        if not self.chat:
            return self.notice('clarification_needed', '当前规则无法完整理解这个问法，且内网模型不可用。请明确对象、条件和所需结果。',
                               question, anchor, expected)
        explicit_object = subject and (catalog.entities[subject]['name'] in question or subject.lower() in question.lower())
        return self._model(question, anchor, expected, subject if explicit_object else None)

    def _base(self, subject, intent, expected, metric=None, question=None):
        spec = self.catalog.entities[subject]
        plan = {'subject': subject, 'intent': intent, 'conditions': [], 'focus_tables': [], 'group_by': []}
        bindings = spec['bindings']
        semantics = spec.get('query_semantics', {})
        metric_df = self.catalog.metrics.get(metric, {}).get('date_field') if metric and hasattr(self.catalog, 'metrics') else None
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
            plan['conditions'] += [{'table': time['table'], 'col': time['field'], 'op': op, 'value': value}
                                   for op, value in [('>=', start), ('<', end)]]
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
            fields = {'flight': ['flight_no', 'FLIGHT_NO'], 'aircraft': ['aircraft_reg', 'reg_no', 'CRAFT_NO']}[role]
            field = next((f for f in fields if f in spec['attributes']), None)
            table = subject
            if not field:
                table = CORE
                field = next((f for f in fields if f in self.catalog.entities.get(CORE,{}).get('attributes',{})),None)
            if field:
                if role == 'aircraft' and field == 'aircraft_reg':
                    # The standard model accepts both source spellings; IN keeps values bound.
                    values=value if isinstance(value,list) else [value]
                    values=list(dict.fromkeys(v for item in values for v in [item,'B-'+item[1:]]))
                    plan['conditions'].append({'table': table, 'col': field, 'op': 'in', 'value': values})
                else:
                    plan['conditions'].append({'table': table, 'col': field, 'op': 'in' if isinstance(value,list) else '=', 'value': value})
            else:
                raise ValueError('此对象没有可核验的航班或机号映射')
        for ref in expected.get('references', []):
            if ref['table'] == subject:
                table, field = subject, ref['field']
            else:
                table = subject if ref.get('binding') in bindings or ref.get('column') in spec['attributes'] else CORE
                field = bindings.get(ref.get('binding')) or ref.get('column')
            if table in self.catalog.entities and field in self.catalog.entities[table]['attributes']:
                existing = next((c for c in plan['conditions'] if c['table']==table and c['col']==field and c['op'] in ('=','in')),None)
                if existing and ref.get('op','=') == '=':
                    values = existing['value'] if existing['op']=='in' else [existing['value']]
                    existing.update(op='in',value=list(dict.fromkeys([*values,ref['value']])))
                else:
                    plan['conditions'].append({'table': table, 'col': field, 'op': ref.get('op','='), 'value': ref['value']})
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
        operators = {'等于':'=', '为':'=', '是':'=', '=':'=', '不等于':'!=', '大于':'>', '小于':'<', '包含':'contains'}
        for alias, fields in sorted(aliases.items(), key=lambda item:-len(item[0])):
            if len(fields) != 1:
                continue
            match = re.search(re.escape(alias) + r'\s*(不等于|等于|大于|小于|包含|为|是|=)\s*[“\"\']?(.+?)(?=[”\"\']|的记录|的明细|[，,。；;？?]|$)', question, re.I)
            if not match:
                continue
            field = next(iter(fields))
            if any(condition['col'] == field for condition, _ in filters):
                continue
            value = match[2].strip()
            attr = self.catalog.attribute(subject,field)
            value = next((key for key,label in attr.get('enums',{}).items() if label == value),value)
            filters.append(({'table':subject,'col':field,'op':operators[match[1]],'value':value},match[0].rstrip('”\"\'')))
        return filters

    def _residual(self, question, expected, subject, metric, hints=None):
        rest = question
        terms = [*self.catalog.entities[subject]['aliases'], self.catalog.entities[subject]['name']]
        if subject in (CORE, ACTUAL):
            terms += self.catalog.entities.get(CORE, {}).get('aliases', []) + self.catalog.entities.get(ACTUAL, {}).get('aliases', [])
        if metric:
            terms += self.catalog.metrics[metric].get('aliases', []) + [self.catalog.metrics[metric]['name']]
            if metric in ('dep_delay_count', 'arr_delay_count'):
                terms += ['延误', '晚点']
            elif metric == 'cancel_count':
                terms += ['取消']
            _, _, mod_terms = extract_acdm_dimensions_and_topn(question, self.catalog, target=subject, allow_topn=True)
            _, slot_terms = apply_acdm_metric_slots(question, self.catalog, metric, [])
            terms += mod_terms + slot_terms
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
        if explicit_dir or origin_city or dest_city:
            terms += home_terms
        terms += [r['term'] for r in expected.get('references', [])]
        for term in sorted(set(terms), key=len, reverse=True):
            if term:
                rest = re.sub(re.escape(term), '', rest, flags=re.I)
        for _, quote in self._literal_filters(question,subject):
            rest = rest.replace(quote,'')
        date_pat = r'(?:\d{4}(?:-\d{2}-\d{2}|[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}[日号]?)|(?<!\d)\d{1,2}月\d{1,2}[日号]?)'
        rest = re.sub(rf'{date_pat}(?:\s*(?:到|至|-|~)\s*{date_pat})?|(?:最近|近)\d+天|最近一个月|近一个月|近一月', '', rest)
        rest = re.sub(r'(?:按|采用|使用)?(?:生产)?(?:运营日|自然日)(?:口径)?', '', rest)
        rest = re.sub(r'(?<![A-Za-z0-9])(?:[A-Z0-9]{2}\d{2,4}[A-Z]?|B-?[A-Z0-9]{4,5})(?![A-Za-z0-9])', '', rest, flags=re.I)
        vocabulary = ('请帮我', '帮我', '查一下', '查一查', '看一下', '查看', '查询', '列出', '显示', '统计', '昨天', '昨日', '前一天', '前天', '今天', '今日', '最近一周', '近一周', '本月', '实际', '执行了', '执行', '执飞', '计划', '排班', '预定', '进出港', '出港', '进港', '离港', '到港', '起飞', '降落', '落地', '飞走', '离地', '从本场', '本场', '最晚', '最迟', '最后', '最早', '第一班', '首班', '末班', '哪一班', '哪班', '哪一个', '哪个', '有哪些', '有什么', '有多少', '多少', '几条', '几家', '几班', '几架', '数量', '总数', '总量', '总共', '一共', '合计', '全部', '所有', '明细', '列表', '清单', '记录', '信息', '数据', '情况', '架次', '班次', '人次', '人数', '分布', '排名', '排行', '趋势', '各个', '各', '按', '小时', '航司', '航班', '的', '是', '有', '了', '个', '中', '里', '一个', '一班', '一条', '班', '条', '家', '请问', '请', '查', '吗', '呢')
        for token in sorted(vocabulary, key=len, reverse=True):
            rest = rest.replace(token, '')
        return re.sub(r'[\s，,。；;？?！!：:、]', '', rest)

    def _model(self, question, anchor, expected, subject):
        cards = self.catalog.query_capabilities()
        directory = {key: {k: v for k, v in card.items() if k != 'fields'} for key, card in cards.items()}
        if not subject:
            route = json_object(self.chat('你是运营问数总控。只从授权目录选择一个业务主体，不生成SQL。返回 {"subject":"真实表键","unresolved":"有歧义时说明"}。目录：' +
                                          json.dumps(directory, ensure_ascii=False), question, max_tokens=500, timeout=20))
            subject = self._table_key(route.get('subject'))
            if subject not in cards:
                return self.notice('clarification_needed', '请明确要查询的业务对象：' + '、'.join(c['name'] for c in cards.values()), question, anchor, expected)
        selected = {subject, *self.catalog.relation_targets(question,subject)}
        selected.update(c['table'] for c in self._base(subject,'detail',expected)['conditions'])
        context = {table:spec for table,spec in self.catalog.context('',selected).items() if table in selected}
        hidden = set(self.catalog.overrides.get('sensitive_fields', []))
        for spec in context.values():
            spec['fields'] = {key: {k:v for k,v in attr.items() if v and not (k=='label' and v==key)}
                              for key,attr in spec['fields'].items() if key not in hidden}
        metrics = {key:{k:v for k,v in metric.items() if k in ('name','aliases','base_table','unit','date_field','availability')}
                   for key,metric in self.catalog.metrics.items() if metric.get('base_table') == subject}
        dimensions = {key:{k:v for k,v in dim.items() if k in ('name','aliases')} for key,dim in self.catalog.dimensions.items()}
        prompt = ('仅生成一个结构化查询计划，禁止SQL。subject及conditions.table必须填写目录键如dim_airline，不能填中文名称。'
                  '严格保留问题全部筛选、排序与粒度，不添加用户没要求的条件。'
                  '未明确计划/实际、必要业务日期或统计口径时填写 clarification，不猜默认。'
                  'clarification仅填写确实需要用户回答的疑问句；没有缺失条件时必须为空字符串，不能填需求解释或总结。'
                  '实际执行航班使用 ops_ask_views.flight_execution.execution_time；不能用计划 flight_date 替代。'
                  '基础资料没有日期要求。排序前N用order_by和limit，不是统计指标。'
                  '用户未要求排序时order_by必须为空数组；未要求分组时group_by=[]且time_bucket=null。'
                  '普通记录条数使用intent=count且metric=null；只有确实询问已定义业务指标时才使用metric。'
                  'confirmed中的日期、方向、航司、航班号等条件由系统自动添加，请不要重复输出这些条件。'
                  '每个筛选须提供grounding:{table,col,quote}，quote逐字引用原问题或确认条件；编码可来自参考字典。'
                  '只用已发布指标，不计算新公式。返回 {subject,intent:detail|count|sum|metric,conditions:[{table,col,op,value}],'
                  'focus_tables:[],aggregate_target,metric,group_by:[],time_bucket:null,order_by:[{field,direction:asc|desc}],limit:null,'
                  'grounding:[{table,col,quote}],clarification:"",unsupported:""}。op仅=,!=,>,>=,<,<=,prefix,contains,in,null,notnull。'
                  '\n所选业务对象：' + json.dumps({key:directory[key] for key in selected if key in directory}, ensure_ascii=False) +
                  '\n详细结构：' + json.dumps(context, ensure_ascii=False) +
                  '\n指标与维度：' + json.dumps({'metrics': metrics, 'dimensions': dimensions}, ensure_ascii=False))
        payload = json.dumps({'question': question, 'confirmed': expected, 'today': anchor.date().isoformat(),
                              'references': expected.get('references',[])}, ensure_ascii=False)
        error = ''
        for attempt in range(2):
            try:
                plan = json_object(self.chat(prompt + ('\n上次计划校验失败：' + error + '。保留原条件修正；无法修正请澄清。' if error else ''),
                                             payload, max_tokens=2200, timeout=40))
                if plan.get('unsupported'):
                    return self.notice('unsupported', '这个要求尚未映射到已发布查询能力，请明确业务对象或指标；不能由模型补造数据与口径。', question, anchor, expected)
                uncertainty = str(plan.get('clarification') or plan.get('unresolved') or '')
                if uncertainty and re.search(r'[？?]|请|是否|需要|缺少|确认|不明确|哪',uncertainty):
                    return self.notice('clarification_needed', '业务对象或附加条件仍不明确，请补充要查询的条件、日期或指标。', question, anchor, expected)
                plan.pop('unresolved',None)
                plan = {key:value for key,value in plan.items() if key in (
                    'subject','intent','conditions','focus_tables','aggregate_target','metric','group_by',
                    'time_bucket','order_by','limit','grounding')}
                if plan.get('intent') not in ('detail','count','sum','metric'):
                    raise ValueError('模型只能规划明细、计数或已定义指标查询')
                plan['subject'] = self._table_key(plan.get('subject'))
                for key in ('conditions','focus_tables','group_by','grounding','order_by'):
                    if plan.get(key) is None:
                        plan[key] = []
                for condition in plan.get('conditions',[]):
                    if not isinstance(condition,dict):
                        raise ValueError('筛选条件必须为对象')
                    if condition.get('table'):
                        condition['table'] = self._table_key(condition['table'])
                        if not condition['table']:
                            raise ValueError('筛选条件引用了未知业务对象')
                for proof in plan.get('grounding',[]):
                    if not isinstance(proof,dict):
                        raise ValueError('条件依据必须为对象')
                    if proof.get('table'):
                        proof['table'] = self._table_key(proof['table'])
                plan['focus_tables'] = [self._table_key(table) for table in plan.get('focus_tables',[])]
                if plan.get('aggregate_target'):
                    plan['aggregate_target'] = self._table_key(plan['aggregate_target'])
                    if not plan['aggregate_target']:
                        raise ValueError('统计目标不是授权业务对象')
                if plan.get('subject') not in context:
                    raise ValueError('主体不在授权详细目录中')
                return self.validate(plan, question, expected, anchor)
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                error = str(exc)[:300]
        return self.notice('clarification_needed', '当前问法未能生成完整且可核验的查询计划，请明确条件或口径。' + error,
                           question, anchor, expected)

    def _table_key(self, value):
        if isinstance(value,str) and value in self.catalog.entities:
            return value
        matches = [key for key,spec in self.catalog.entities.items() if isinstance(value,str) and
                   value in [spec['name'], *spec['aliases']]]
        return matches[0] if len(matches) == 1 else None

    def validate(self, plan, question, expected, anchor, hints=None):
        plan = deepcopy(plan)
        subject = plan['subject']
        if subject not in self.catalog.entities:
            raise ValueError('业务对象不在授权目录中')
        spec = self.catalog.entities[subject]
        semantics = spec.get('query_semantics',{})
        if plan.get('intent') == 'acdm_report':
            if not expected.get('period'):
                return self.notice('clarification_needed', '请确认报告的业务日期或期间。', question, anchor, expected,
                                   'period', [(label, period(label, anchor)) for label in ('昨天', '今天', '前天')])
            start, end = map(date.fromisoformat, expected['period'])
            if plan.get('report_kind') == 'ground_briefing' and end-start != timedelta(days=1):
                return self.notice('clarification_needed', '地服运行简报按单个业务日生成，请确认一个日期。', question, anchor,
                                   expected, 'period', [(label, period(label, anchor)) for label in ('昨天', '今天', '前天')])
            rest = re.sub(r'\d{4}(?:-\d{2}-\d{2}|年\d{1,2}月\d{1,2}日)|(?:最近|近)\d+天', '', question, flags=re.I)
            rest = re.sub(r'A-?CDM|通用报告|完整运行报告|运行[日周月]报|地服(?:公司)?(?:运行)?简报|保障节点|保障质量|登机桥|单桥|录入|排行|周转|看板|生成|报告|请|给我|帮我|查看|查询|昨天|昨日|今天|今日|前天|最近一周|近一周|本月|与|和|及|的|[\s，,。；;？?：:]', '', rest, flags=re.I)
            if rest or any(expected.get(key) for key in ('references', 'flight', 'aircraft', 'direction', 'basis')):
                return self.notice('unsupported', '这份报告使用已发布的整份报告口径，暂不支持附加筛选；请单独查询所需对象或指标。', question, anchor, expected)
            plan.update(report_start=start.isoformat(), report_end=(end-timedelta(days=1)).isoformat())
            tpl = match_plan_template(self.catalog, plan, hints)
            if tpl:
                plan['plan_template'] = tpl
            gate = check_capability_gate(self.catalog, plan, template=plan.get('plan_template'))
            if not gate['allowed']:
                return self.notice('unsupported', gate['message'], question, anchor, expected, capability_gate=gate)
            plan = self.compiler.validate(plan)
            plan['query_context'] = {'question': question, 'slots': deepcopy(expected), 'anchor': anchor.isoformat()}
            plan['coordinator_version'] = VERSION
            return plan
        if expected.get('basis')=='actual' and subject not in (CORE,ACTUAL) and not plan.get('metric') and re.search(
                r'执飞|执行|起降|起飞|落地|实际.*(?:航班|架次)|(?:航班|架次).*实际',question):
            if semantics.get('basis')!='actual' and not semantics.get('time_label','').startswith('实际'):
                return self.notice('clarification_needed','这个对象尚未声明实际执行统计口径。请确认按该对象的原始记录查询，或明确改查实际执飞航班。',
                                   question,anchor,expected)
        if subject in (CORE, ACTUAL) and not plan.get('metric'):
            if not expected.get('basis'):
                return self.notice('clarification_needed', '你要查询计划航班，还是实际执行航班？', question, anchor, expected,
                                   'basis', [('实际执行', 'actual'), ('计划航班', 'planned')])
            if expected['basis'] == 'actual' and subject != ACTUAL:
                raise ValueError('实际执行必须使用实际执飞视图和实际发生日')
            if expected['basis'] == 'planned' and subject == ACTUAL:
                raise ValueError('计划查询不能改用实际执行记录')
        if spec.get('query_semantics', {}).get('date_required') and not expected.get('period'):
            return self.notice('clarification_needed', '请确认业务日期。', question, anchor, expected, 'period',
                               [(label, period(label, anchor)) for label in ('昨天', '今天', '前天')])
        plan.setdefault('conditions', [])
        plan.setdefault('focus_tables', [])
        plan.setdefault('group_by', [])
        base = self._base(subject, plan.get('intent', 'detail'), expected, metric=plan.get('metric'), question=question)
        conditions = plan['conditions']
        if not isinstance(conditions, list):
            raise ValueError('条件必须为数组')
        required = base['conditions']
        required += [condition for condition,_ in self._literal_filters(question,subject)]
        normalized_conditions = []
        for raw in conditions:
            if not isinstance(raw,dict):
                raise ValueError('筛选条件必须是对象')
            condition = {**raw,'table':raw.get('table') or subject}
            same_field = [c for c in required if (c['table'],c['col'])==(condition['table'],condition.get('col'))]
            equivalent = next((c for c in same_field if c['op']==condition.get('op') and
                               self._equal_value(c['value'],condition.get('value'),c['table'],c['col'])),None)
            if equivalent:
                normalized_conditions.append(deepcopy(equivalent))
                continue
            dates = [c for c in same_field if c['op'] in ('>=','<') and isinstance(c['value'],str) and
                     re.match(r'^\d{4}-\d{2}-\d{2}',c['value'])]
            value = str(condition.get('value',''))
            # Normalize confirmed calendar-day notation before SQL, never a different date or field.
            if len(dates)==2 and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                lower = next(c for c in dates if c['op']=='>=')
                upper = next(c for c in dates if c['op']=='<')
                if condition.get('op') in ('=','prefix') and value==lower['value'][:10] and \
                        date.fromisoformat(upper['value'][:10])-date.fromisoformat(value)==timedelta(days=1):
                    normalized_conditions.extend(deepcopy(dates))
                    continue
                equivalent = next((c for c in dates if c['op']==condition.get('op') and c['value'][:10]==value),None)
                if equivalent:
                    normalized_conditions.append(deepcopy(equivalent))
                    continue
            normalized_conditions.append(condition)
        plan['conditions'] = conditions = normalized_conditions
        for condition in required:
            equivalents = [c for c in conditions if isinstance(c, dict) and c.get('op') == condition['op'] and
                           (c.get('table') or subject, c.get('col')) == (condition['table'], condition['col'])]
            if equivalents and condition not in equivalents:
                # Missing table ownership is harmless; changing the value/operator is not.
                if not any({**c, 'table': c.get('table') or subject} == condition for c in equivalents):
                    raise ValueError('计划改变了明确条件：' + condition['col'])
            if condition not in [{**c, 'table': c.get('table') or subject} for c in conditions]:
                conditions.append(condition)
        grounded = plan.pop('grounding', [])
        for condition in conditions:
            if not isinstance(condition, dict):
                raise ValueError('筛选条件必须是对象')
            normalized = {**condition, 'table': condition.get('table') or subject}
            if normalized in required:
                continue
            attr = self.catalog.attribute(normalized['table'], condition.get('col'))
            quotes = [g.get('quote') for g in grounded if isinstance(g, dict) and g.get('col') == condition.get('col') and
                      (g.get('table') or subject) == normalized['table']]
            if not any(isinstance(quote, str) and quote and quote in question for quote in quotes):
                raise ValueError('额外条件没有原问题依据：' + str(condition.get('col')))
            values = condition.get('value') if condition.get('op') == 'in' else [condition.get('value')]
            for value in values:
                enum = attr.get('enums', {}).get(str(value), attr.get('enums',{}).get(str(value).lower(),''))
                ref = any(str(r['value']) == str(value) and r['term'] in question for r in self.references)
                if condition.get('op') in ('null', 'notnull'):
                    if not re.search(r'空|缺失|未填|有记录|有时刻', question):
                        raise ValueError('用户没有要求空值筛选')
                elif not any(str(value).lower() in quote.lower() or (enum and enum in quote) or
                             (ref and any(r['term'] in quote and str(r['value']) == str(value) for r in self.references))
                             for quote in quotes if isinstance(quote,str)):
                    raise ValueError('筛选值缺少可核验来源')
                if not any(condition['col'].lower() in quote.lower() or
                           (attr.get('label') and attr['label'] in quote) or (enum and enum in quote) or
                           any(alias in quote for alias in attr.get('aliases', [])) or ref
                           for quote in quotes if isinstance(quote,str)):
                    raise ValueError('条件字段与原文依据不匹配')
        if plan.get('metric'):
            if plan.get('intent') not in ('metric','sum'):
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
            if expected.get('basis') == 'actual' and plan['metric'] in ('flight_count', 'flight_total_count'):
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
                plan['flight_sequence'] = 'last' if expected['sequence']=='desc' else 'first'
        if plan.get('group_by') and not re.search(r'按|分|各|每|趋势|分布|哪个(?:小时|时段)|高峰(?:小时|时段)|排名|排行',question):
            raise ValueError('用户没有要求分组统计')
        if plan.get('time_bucket') and not re.search(r'按|每|逐|趋势|分布',question):
            raise ValueError('用户没有要求时间分组')
        if plan.get('order_by') and not expected.get('sequence') and not re.search(r'排序|升序|降序|从大到小|从小到大|前\s*\d+|最高|最低',question):
            raise ValueError('用户没有要求改变记录排序')
        target = plan.get('aggregate_target')
        if target and target!=subject and not any(alias in question for alias in self.catalog.entities[target]['aliases']):
            raise ValueError('用户没有请求更换统计对象')
        if not expected.get('sequence') and plan.get('limit') is not None:
            requested = re.search(r'(?:前|top)\s*([1-9]\d{0,2})',question,re.I)
            peak_one = plan['limit'] == 1 and re.search(r'最高是哪|最多是哪|峰值|高峰小时是哪个|高峰时段是哪个|哪一小时最多|哪个小时最多|哪[个一](?:小时|时段).{0,10}最[多高]', question)
            if not peak_one and (not requested or plan['limit'] != int(requested[1])):
                raise ValueError('模型添加了用户未要求的记录条数限制')
        if not plan.get('metric') and expected.get('intent')!='detail' and is_count(question) and plan.get('intent')!='count':
            raise ValueError('数量问题必须保留计数意图')
        hints = hints or (self.catalog.resolve_ontology_hints(question) if hasattr(self.catalog, 'resolve_ontology_hints') else None)
        residual = self._residual(question,expected,subject,plan.get('metric'),hints=hints)
        if re.search(r'\d|大于|小于|不含|排除|不等于',residual) and not grounded and not self._literal_filters(question,subject):
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
        plan['query_context'] = {'question': question, 'slots': deepcopy(expected), 'anchor': anchor.isoformat()}
        plan['coordinator_version'] = VERSION
        return plan

    def _equal_value(self, left, right, table, field):
        if isinstance(left,list) and isinstance(right,list):
            return sorted(map(str,left))==sorted(map(str,right))
        if str(left)==str(right):
            return True
        attr=self.catalog.attribute(table,field)
        if 'timestamp' in attr['type']:
            try:
                a,b=datetime.fromisoformat(str(left).replace('Z','+00:00')),datetime.fromisoformat(str(right).replace('Z','+00:00'))
                return (a.replace(tzinfo=BEIJING) if a.tzinfo is None else a)==(b.replace(tzinfo=BEIJING) if b.tzinfo is None else b)
            except ValueError:
                return False
        if any(kind in attr['type'] for kind in ('int','numeric','double','real')):
            try:
                a,b=Decimal(str(left)),Decimal(str(right))
                return a.is_finite() and b.is_finite() and a==b
            except InvalidOperation:
                return False
        return False
