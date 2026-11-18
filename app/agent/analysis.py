"""Bounded multi-query planning and deterministic comparisons over existing plans."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import calendar
import json
import re
import unicodedata
from time import perf_counter

from app.benchmarks import AIRPORTS, DATASET, METRICS, TABLE
from app.execution import ExecutionTrace
from app.query import Compiler, execute, json_safe

MAX_QUERIES = 12
SPECIAL = ('analysis', 'forecast')
EXTRA_ALIASES = {
    'planned_flight_count': ['计划量'],
    'operated_flight_count': ['执行量', '实际航班量', '实际执行架次', '执行架次', '实际架次'],
    'dep_flight_count': ['出港航班量', '出港架次', '离港航班量', '出港实际执行架次', '出港执行架次', '出港执飞架次'],
    'arr_flight_count': ['进港航班量', '进港架次', '到港航班量', '进港实际执行架次', '进港执行架次', '进港执飞架次'],
    'dep_pax_sum': ['出港旅客吞吐量', '出港旅客量'],
    'arr_pax_sum': ['进港旅客吞吐量', '进港旅客量'],
    'bridge_stand_rate_all': ['全量进出港靠桥率', '进出港全量靠桥率', '全量双边靠桥率', '双向靠桥率'],
    'dep_delay_count': ['出港延误', '离港延误', '出港晚点', '离港晚点'],
    'arr_delay_count': ['进港延误', '到港延误', '进港晚点', '到港晚点'],
    'flight_delay_count': ['航班延误', '全口径延误', '延误总架次', '延误架次'],
    'short_delay_5m_count': ['放行延误1至5分钟', '放行延误1到5分钟', '放行延误1-5分钟', '放行延误1~5分钟', '1至5分钟放行延误', '短延误架次', '短延误'],
}
DATE_TOKEN = (r'\d{4}-\d{1,2}-\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]|'
              r'\d{4}年\d{1,2}月|\d{4}-\d{2}(?!-)|今天|今日|昨天|昨日|前天|本周|上周|本月|上月|'
              r'本季度|上季度|今年|去年|最近两周|近两周|最近一周|近一周|(?:最近|近)\d{1,2}天')
WINDOW_TOKEN = rf'(?:{DATE_TOKEN})(?:\s*(?:至|到|~|～|—|–)\s*(?:{DATE_TOKEN}))?'
ORDERED_COMPARISON = re.compile(rf'({WINDOW_TOKEN})\s*(?:比|较|相比|相较)(?:于)?\s*({WINDOW_TOKEN})')
COMPARISON_WORDS = r'对比|比较|对照|相比|相较'
COMPARISON_OUTPUTS = r'增长百分比|增长率|变化率|增减幅度|增幅|降幅|增减量|差值|差额|相差(?:了?多少)?|[多少]了?多少'
COMPARISON_PRESENTATION = (rf'(?:计算|算(?:一下)?|求)?\s*(?:{COMPARISON_OUTPUTS})'
                           r'|列出|给出|(?:这)?两(?:天|日|期)(?:的)?|各(?:天|日|期)(?:的)?|各自|数值')
BENCHMARK_SOURCE_REQUEST = re.compile(
    r'(?:并(?:且)?|同时)?\s*(?:请\s*)?(?:说明|注明|标明|列出|标注|给出|附上)\s*'
    r'(?:(?:各(?:个)?|每个)机场(?:的)?\s*)?(?:数据)?(?:来源|出处)(?=\s*[，,。；;？?！!]|\s*$)'
)


def expand_coordinated_metrics(text):
    # e.g. "出港和进港客座率" -> "出港客座率和进港客座率"
    # e.g. "出港与进港的客座率" -> "出港客座率与进港客座率"
    pat = (r'([出进离到]港)\s*(和|与|及|以及|、)\s*([出进离到]港)\s*(?:的)?\s*'
           r'(客座率|上座率|旅客量|旅客人数|旅客吞吐量|实际执行架次|实际执飞架次|执飞架次|执行架次|实飞架次|实际架次|航班量|架次|延误架次|延误|晚点架次|晚点|准点率|正常率|靠桥率|平均延误时长|平均延误)')
    def repl(m):
        d1, conj, d2, noun = m.group(1), m.group(2), m.group(3), m.group(4)
        return f'{d1}{noun}{conj}{d2}{noun}'
    text = re.sub(pat, repl, text)
    if re.search(r'分别|各自|各个|对比|是多少|分别.*多少', text):
        pat_both = (r'(?<![全双])进出港\s*(?:的)?\s*'
                    r'(客座率|上座率|旅客量|旅客人数|旅客吞吐量|实际执行架次|实际执飞架次|执飞架次|执行架次|实飞架次|实际架次|航班量|架次|延误架次|延误|晚点架次|晚点|准点率|正常率|平均延误时长|平均延误)')
        text = re.sub(pat_both, r'出港\1和进港\1', text)
    text = re.sub(
        r'(登机桥|廊桥)\s*(?:的\s*)?(使用(?:次数|了?多少次|几次))'
        r'(\s*(?:、|[，,]|和|与|以及|及)\s*)(?=平均使用时长)',
        r'\1\2\3\1', text,
    )
    # “平均登机、清洁、配餐、加油时长”共用平均时长口径。
    node = r'登机|清洁|保洁|配餐|加油|下客|靠桥|除冰'
    separator = r'\s*(?:、|[，,]|和|与|以及|及)\s*'
    return re.sub(
        rf'平均\s*((?:{node})(?:{separator}(?:{node}))+)(?:\s*的)?\s*(?:时长|耗时)',
        lambda m: '、'.join(f'平均{item.replace("清洁", "保洁")}时长' for item in re.split(separator, m[1])),
        text,
    )


def normalize_dates(text, anchor):
    text = re.sub(r'(\d{1,2})月(\d{1,2})日\s*(至|到|~|～)\s*(\d{1,2})日',
                  lambda m: f'{anchor.year}-{int(m[1]):02d}-{int(m[2]):02d}至{anchor.year}-{int(m[1]):02d}-{int(m[4]):02d}', text)
    return re.sub(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]',
                  lambda m: f'{int(m[1] or anchor.year):04d}-{int(m[2]):02d}-{int(m[3]):02d}', text)


def shift_month(day, months):
    n = day.year * 12 + day.month - 1 + months
    year, month = divmod(n, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1]))


def windows(text, anchor):
    from app.agent.coordinator import period
    today = anchor.date()
    tokens = re.findall(DATE_TOKEN, text)
    explicit = [t for t in tokens if re.fullmatch(r'\d{4}-\d{2}-\d{2}', t)]
    parts = re.findall(WINDOW_TOKEN, text)
    if len(parts) > 1:
        values = []
        for token in parts:
            w, _ = windows(token, anchor)
            values.extend(w)
        return list(dict.fromkeys(values)), 'day'
    kind = 'day'
    if re.search(r'最近两周|近两周', text):
        w = [str(today - timedelta(days=13)), str(today + timedelta(days=1))]
    elif '上周' in text or '本周' in text:
        start = today - timedelta(days=today.weekday())
        if '上周' in text:
            start -= timedelta(days=7)
            end = start + timedelta(days=7)
        else:
            end = today + timedelta(days=1)
        w, kind = [str(start), str(end)], 'week'
    elif '上月' in text or '本月' in text:
        start = today.replace(day=1)
        if '上月' in text:
            w = [str(shift_month(start, -1)), str(start)]
        else:
            w = [str(start), str(today + timedelta(days=1))]
        kind = 'month'
    elif '季度' in text:
        start = today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1)
        w = [str(shift_month(start, -3)), str(start)] if '上季度' in text else [str(start), str(today + timedelta(days=1))]
        kind = 'quarter'
    elif '今年' in text or '去年' in text:
        start = today.replace(month=1, day=1)
        w = [str(start.replace(year=start.year - 1)), str(start)] if '去年' in text else [str(start), str(today + timedelta(days=1))]
        kind = 'year'
    elif len(tokens) == 1 and re.fullmatch(r'\d{4}年\d{1,2}月|\d{4}-\d{2}', tokens[0]):
        nums = re.findall(r'\d+', tokens[0])
        start = date(int(nums[0]), int(nums[1]), 1)
        w, kind = [str(start), str(shift_month(start, 1))], 'month'
    else:
        w = period(text, anchor)
        if len(explicit) > 1:
            kind = 'range'
    return ([tuple(w)] if w else []), kind


def comparison_window(window, kind, mode):
    start, end = map(date.fromisoformat, window)
    if mode == 'yoy':
        return str(shift_month(start, -12)), str(shift_month(end, -12))
    if kind in ('month', 'quarter', 'year'):
        months = {'month': 1, 'quarter': 3, 'year': 12}[kind]
        return str(shift_month(start, -months)), str(shift_month(end, -months))
    days = 7 if kind == 'week' else (end - start).days
    return str(start - timedelta(days=days)), str(end - timedelta(days=days))


def matched_metrics(question, catalog):
    matches = []
    preferred = {'planned_flight_count': 0, 'operated_flight_count': 0, 'pax_total_sum': 0, 'total_load_factor': 0}
    for key, metric in catalog.metrics.items():
        if key.startswith('benchmark_'):
            continue
        for alias in {metric.get('name', ''), *metric.get('aliases', []), *EXTRA_ALIASES.get(key, [])}:
            if len(alias) < 2:
                continue
            for m in re.finditer(re.escape(alias), question, re.I):
                matches.append((m.start(), m.end(), key, alias))
    selected = []
    for item in sorted(matches, key=lambda m: (-(m[1] - m[0]), preferred.get(m[2], 1), m[0], m[2])):
        if not any(a < item[1] and item[0] < b for a, b, _, _ in selected):
            selected.append(item)
    return sorted(selected)


def match_analysis_child(text, queries, catalog):
    text_clean = unicodedata.normalize('NFKC', text).strip()
    if text_clean.isdigit():
        idx = int(text_clean)
        if 1 <= idx <= len(queries):
            return queries[idx - 1]
    m_idx = re.search(r'第([一二三四五六七八九十]|\d+)项', text_clean)
    if m_idx:
        num = int(m_idx[1]) if m_idx[1].isdigit() else '一二三四五六七八九十'.index(m_idx[1]) + 1
        if 1 <= num <= len(queries):
            return queries[num - 1]
        raise ValueError(f'序号超出范围，本期共{len(queries)}项。')

    scores = [0] * len(queries)
    keywords = ['出港', '离港', '进港', '到港', '始发', '靠桥', '延误', '晚点', '正常', '放行', '起飞', '客座率', '客座', '旅客', '货邮', '航班量', '架次']
    for i, q in enumerate(queries):
        lbl = q.get('label', '')
        plan = q.get('plan', {})
        conds = plan.get('conditions', [])
        m_key = q.get('metric_key') or plan.get('metric')
        m_cfg = catalog.metrics.get(m_key, {}) if catalog and hasattr(catalog, 'metrics') else {}

        dir_val = next((c['value'] for c in conds if c.get('col') == 'direction'), None)
        if dir_val == 'DEP' and re.search(r'出港|离港', text_clean):
            scores[i] += 10
        elif dir_val == 'ARR' and re.search(r'进港|到港', text_clean):
            scores[i] += 10

        m_names = [m_cfg.get('name', ''), *m_cfg.get('aliases', [])]
        for name in sorted(set(m_names), key=len, reverse=True):
            if name and name in text_clean:
                scores[i] += len(name) * 2
                break

        lbl_head = lbl.split(' · ')[0]
        if lbl_head and lbl_head in text_clean:
            scores[i] += len(lbl_head) * 3

        for kw in keywords:
            if kw in text_clean and (kw in lbl or any(kw in n for n in m_names)):
                scores[i] += len(kw) * 2

    best_score = max(scores) if scores else 0
    if best_score > 0 and scores.count(best_score) == 1:
        return queries[scores.index(best_score)]
    return None


METRIC_DETAIL_CONDITIONS = {
    'release_delay_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'clearance_delay_time', 'op': '>', 'value': 0},
    ],
    'takeoff_delay_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'takeoff_delay_time', 'op': '>', 'value': 0},
    ],
    'dep_delay_count': [
        {'col': 'direction', 'op': '=', 'value': 'DEP'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'dep_delay_time', 'op': '>', 'value': 0},
    ],
    'arr_delay_count': [
        {'col': 'direction', 'op': '=', 'value': 'ARR'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'arr_delay_time', 'op': '>', 'value': 0},
    ],
    'flight_delay_count': [
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'flight_delay_time', 'op': '>', 'value': 0},
    ],
    'short_delay_5m_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'clearance_delay_time', 'op': '>=', 'value': 1},
        {'col': 'clearance_delay_time', 'op': '<=', 'value': 5},
    ],
    'avg_dep_delay_minutes': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'dep_delay_time', 'op': '>', 'value': 0},
    ],
    'avg_arr_delay_minutes': [
        {'col': 'is_off_in', 'op': '=', 'value': 'A'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'arr_delay_time', 'op': '>', 'value': 0},
    ],
    'dep_flight_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'arr_flight_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'A'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'origin_flight_count': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'ori_act', 'op': '=', 'value': 'Y'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'operated_flight_count': [
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'cancel_count': [
        {'col': 'abnormal_flight_stat_cd', 'op': '=', 'value': 'CAN'},
    ],
    'remote_stand_flights': [
        {'col': 'craft_site_is_remote', 'op': '=', 'value': 'Y'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'regular_flight_count': [
        {'col': 'flight_task_cd', 'op': '=', 'value': 'W/Z'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'domestic_flight_count': [
        {'col': 'attribute', 'op': '=', 'value': '2403'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'international_flight_count': [
        {'col': 'attribute', 'op': '=', 'value': '2401'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'regional_flight_count': [
        {'col': 'attribute', 'op': '=', 'value': '2402'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'dep_load_factor': [
        {'col': 'direction', 'op': '=', 'value': 'DEP'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'fpa_available_seat', 'op': '>', 'value': 0},
    ],
    'arr_load_factor': [
        {'col': 'direction', 'op': '=', 'value': 'ARR'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'fpa_available_seat', 'op': '>', 'value': 0},
    ],
    'total_load_factor': [
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
        {'col': 'fpa_available_seat', 'op': '>', 'value': 0},
    ],
    'dep_pax_sum': [
        {'col': 'direction', 'op': '=', 'value': 'DEP'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'arr_pax_sum': [
        {'col': 'direction', 'op': '=', 'value': 'ARR'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'release_on_time_rate': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'takeoff_on_time_rate': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'dep_on_time_rate': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'arr_on_time_rate': [
        {'col': 'is_off_in', 'op': '=', 'value': 'A'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'origin_dep_on_time_rate': [
        {'col': 'is_off_in', 'op': '=', 'value': 'D'},
        {'col': 'ori_act', 'op': '=', 'value': 'Y'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'bridge_stand_rate': [
        {'col': 'direction', 'op': '=', 'value': 'ARR'},
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'bridge_stand_rate_all': [
        {'col': 'is_operated_flight', 'op': '=', 'value': 'Y'},
    ],
    'key_short_turnaround_count': [
        {'col': 'is_both_operated', 'op': '=', 'value': 'Y'},
        {'col': 'sched_turnaround_minutes', 'op': '<', 'value': 45},
        {'col': 'sched_turnaround_minutes', 'op': '>', 'value': 0},
    ],
}


def build_metric_detail_plan(plan, catalog, metric_key=None):
    """Convert a metric plan into a detailed records plan while preserving its specific business filters."""
    detail_plan = deepcopy(plan)
    metric_key = metric_key or detail_plan.get('metric')
    m_cfg = catalog.metrics.get(metric_key, {}) if catalog and hasattr(catalog, 'metrics') and metric_key else {}
    subj = m_cfg.get('base_table') or detail_plan.get('subject') or 'mv_acdm_dyn_flight_detail'
    detail_plan['subject'] = subj
    detail_plan['intent'] = 'detail'
    detail_plan.pop('metric', None)
    detail_plan.pop('aggregate_target', None)
    detail_plan.pop('group_by', None)
    detail_plan.pop('limit', None)
    detail_plan.pop('order_by', None)

    conds = detail_plan.setdefault('conditions', [])
    for c in conds:
        c['table'] = subj
    existing_pairs = {(c['col'], c.get('op', '=')) for c in conds}
    existing_cols = {c['col'] for c in conds if c.get('op') == '='}

    injected = METRIC_DETAIL_CONDITIONS.get(metric_key, [])
    for inj in injected:
        op = inj.get('op', '=')
        col = inj['col']
        if (col, op) not in existing_pairs and (op != '=' or col not in existing_cols):
            conds.append({'table': subj, **inj})
            existing_pairs.add((col, op))
            if op == '=':
                existing_cols.add(col)

    for k, v in m_cfg.get('default_filters', {}).items():
        if k not in existing_cols and (k, '=') not in existing_pairs and isinstance(v, (str, int, float, bool)):
            conds.append({'table': subj, 'col': k, 'op': '=', 'value': v})
            existing_cols.add(k)
            existing_pairs.add((k, '='))

    m_name = m_cfg.get('name') or metric_key or '指标'
    detail_plan['disp'] = f'{m_name}明细记录'
    return detail_plan


def _is_analysis_detail_followup(text):
    """Return True only for short drill-down requests targeting a prior multi-section analysis."""
    clean = unicodedata.normalize('NFKC', text).strip()
    if not clean:
        return False
    if clean.isdigit() or re.search(r'第(?:[一二三四五六七八九十]|\d+)项', clean):
        return True
    if len(clean) > 24:
        return False
    if re.search(r'\d{4}[-/.年]\d{1,2}|今天|今日|昨天|昨日|前天|大前天|近\d+天|最近', clean):
        return False
    if re.search(r'多少|几[条班架个次]|覆盖率|占比|比例|正常率|靠桥率|放行率|同比|环比|对比|预测|预估|按.{1,6}(?:列出|统计|分布|排名|排行)', clean):
        return False
    return bool(re.search(r'明细|详情|清单|动态|记录|数据|列表', clean))


_CHAIN_STEP_SPLIT_RE = re.compile(
    r'(?:[？?。！!；;]\s*(?:(?:然后|接着|随后|紧接着|之后)(?:再)?(?:问|追问|查一下|查看|查询|查|看下|看看|看)?'
    r'|(?:再|最后)(?:问|追问|查一下|查看|查询|查|看下|看看|看)?)'
    r'|[，,]\s*(?:(?:然后|接着|随后|紧接着|之后)(?:再)?(?:问|追问|查一下|查看|查询|查|看下|看看|看)?'
    r'|再(?:问|追问|查一下|查看|查询|查|看下|看看|看)'
    r'|最后(?:问|追问|查一下|查看|查询|查|看下|看看|看|再|(?=(?:列出|展示|显示|统计)))))'
)
_CHAIN_FIRST_PREFIX_RE = re.compile(r'^(?:先|首先)(?:问|查一下|查看|查询|查|看下|看看|看)?\s*')
_CHAIN_STEP_PREFIX_RE = re.compile(
    r'^(?:然后|接着|随后|紧接着|之后|再|最后)(?:再)?(?:问|追问|查一下|查看|查询|查|看下|看看|看)?\s*'
)


def split_chained_questions(text: str) -> list[str]:
    """Split a single-message sequential multi-turn prompt into ordered sub-questions."""
    clean = unicodedata.normalize('NFKC', text or '').strip()
    if not clean or re.search(r'报告|简报|看板|预测|预估', clean):
        return []
    parts: list[str] = []
    if _CHAIN_STEP_SPLIT_RE.search(clean):
        raw_parts = _CHAIN_STEP_SPLIT_RE.split(clean)
        parts = [p.strip(' \t\r\n，,。；;！!') for p in raw_parts if p and p.strip(' \t\r\n，,。；;！!？?')]
    elif len(re.findall(r'[？?]', clean)) >= 2:
        raw_parts = re.split(r'[？?]+', clean)
        parts = [p.strip(' \t\r\n，,。；;！!') for p in raw_parts if p and p.strip(' \t\r\n，,。；;！!？?')]
    if len(parts) < 2:
        return []
    cleaned_steps: list[str] = []
    for idx, part in enumerate(parts):
        step = _CHAIN_FIRST_PREFIX_RE.sub('', part) if idx == 0 else _CHAIN_STEP_PREFIX_RE.sub('', part)
        step = step.strip(' \t\r\n，,。；;！!')
        if not step:
            return []
        cleaned_steps.append(step)
    return cleaned_steps


def _chained_step_label(step_idx: int, step_q: str, child: dict, catalog) -> tuple[str, list[str]]:
    slots = (child.get('query_context') or {}).get('slots') or {}
    step_period = list(slots.get('period') or [])
    period_text = ''
    if len(step_period) == 2:
        try:
            s_d = date.fromisoformat(step_period[0])
            e_d = date.fromisoformat(step_period[1]) - timedelta(days=1)
            period_text = str(s_d) if s_d == e_d else f'{s_d}至{e_d}'
        except ValueError:
            period_text = ''
    dir_val = slots.get('direction') or next(
        (c.get('value') for c in child.get('conditions', []) if c.get('col') in ('direction', 'is_off_in')),
        None,
    )
    dir_lbl = {'DEP': '出港', 'D': '出港', 'ARR': '进港', 'A': '进港'}.get(str(dir_val or '').upper(), '全部')
    subj = child.get('subject') or ''
    is_flight_subj = subj in ('ops_ask_views.flight_execution', 'std_flight_core', 'mv_acdm_dyn_flight_detail')
    subj_name = catalog.entities.get(subj, {}).get('name', subj)
    metric_key = child.get('metric')
    if metric_key and metric_key in catalog.metrics:
        m_name = catalog.metrics[metric_key].get('name', metric_key)
        base_lbl = f'{m_name}（{dir_lbl}）' if (is_flight_subj and dir_lbl != '全部' and dir_lbl not in m_name) else m_name
    elif child.get('intent') == 'detail':
        base_lbl = f'{subj_name}（{dir_lbl}明细）' if is_flight_subj else f'{subj_name}明细'
    elif child.get('intent') == 'count':
        base_lbl = f'{subj_name}（{dir_lbl}）' if is_flight_subj else f'{subj_name}数量'
    else:
        base_lbl = f'步骤{step_idx} · {step_q}'
    full_label = f'{base_lbl} · {period_text}' if period_text else base_lbl
    return full_label, step_period


def plan_chained_queries(coordinator, question, steps, anchor, previous=None, day_mode='AUTO'):
    """Plan sequential sub-questions by chaining each step's plan as previous_plan for the next."""
    catalog = coordinator.catalog
    cap = next((c for c in getattr(catalog, 'capabilities', []) if c.get('code') == 'acdm.analysis'), None)
    if cap and cap.get('status') in ('PLANNED', 'UNAVAILABLE'):
        return coordinator.notice('unsupported', '多步连续查询能力尚未发布或已停用。', question, anchor, {})
    if len(steps) > MAX_QUERIES:
        raise ValueError(f'本轮最多支持 {MAX_QUERIES} 步连续查询，请拆分后提问')
    prev_plan = previous
    queries = []
    for idx, step_q in enumerate(steps, 1):
        child = coordinator.plan(step_q, previous_plan=prev_plan, day_mode=day_mode, _single=True)
        if child.get('outcome'):
            msg = child.get('answer') or '需要补充条件'
            return coordinator.notice(child['outcome'], f'第 {idx} 步（{step_q}）：{msg}', question, anchor, {})
        if child.get('metric') and catalog.metrics.get(child['metric'], {}).get('availability', 'AVAILABLE') != 'AVAILABLE':
            raise ValueError('指标尚未发布：' + str(child['metric']))
        label, step_period = _chained_step_label(idx, step_q, child, catalog)
        queries.append({
            'id': f'q{idx}',
            'label': label,
            'metric_key': child.get('metric') or f'step_{idx}_{child.get("intent", "query")}',
            'object': '',
            'period_index': 0,
            'period': step_period,
            'question': step_q,
            'plan': child,
        })
        prev_plan = child
    last_slots = deepcopy((prev_plan.get('query_context') or {}).get('slots') or {})
    return {
        'subject': queries[0]['plan']['subject'],
        'intent': 'analysis',
        'chained': True,
        'queries': queries,
        'comparison_mode': None,
        'airport_benchmark': False,
        'conditions': [],
        'group_by': [],
        'focus_tables': [],
        'query_context': {'question': question, 'anchor': anchor.isoformat(), 'slots': last_slots},
    }


def plan(coordinator, question, anchor, previous=None, day_mode='AUTO', selected_item=None, confirmed_period=None):
    """Return None for ordinary questions; every generated child is validated normally."""
    from app.agent.cross_day import is_cross_day_request, make_plan as make_cross_day_plan
    catalog = coordinator.catalog
    text = expand_coordinated_metrics(normalize_dates(question, anchor))
    if isinstance(previous, dict) and previous.get('query_context') and re.fullmatch(r'(?:那)?(?:同比|环比|去年同期)(?:呢)?[？?]?', text):
        text = re.sub(r'同比|环比', '', previous['query_context']['question']) + ('同比' if '去年' in text or '同比' in text else '环比')
    prior = previous if isinstance(previous, dict) and previous.get('intent') == 'analysis' else None
    if prior and prior.get('analysis_kind') == 'cross_day_reconciliation':
        short_clean = text.strip()
        if len(short_clean) <= 28:
            follow_ws, _ = windows(short_clean, anchor)
            stripped = re.sub(WINDOW_TOKEN, '', short_clean)
            stripped = re.sub(r'^(?:那|那么|换成|改成|换查|改查|只看|仅看|再看|再查|看看|查查)?(?:出港|进港|进出港)?(?:的|呢|吗|呀|吧|看看|查查)?[\s，,。；;？?！!]*$', '', stripped)
            if not stripped and (follow_ws or re.search(r'出港|进港|进出港', short_clean)):
                target_period = list(follow_ws[0]) if follow_ws else prior.get('period')
                return make_cross_day_plan(coordinator, short_clean, anchor, previous=prior, day_mode=day_mode, confirmed_period=target_period)
    if prior and prior.get('queries'):
        if selected_item is not None or _is_analysis_detail_followup(text):
            lookup_text = str(selected_item) if selected_item is not None else text
            matched_child = match_analysis_child(lookup_text, prior['queries'], catalog)
            if matched_child:
                child_plan = deepcopy(matched_child['plan'])
                m_key = matched_child.get('metric_key') or child_plan.get('metric')
                detail_plan = build_metric_detail_plan(child_plan, catalog, m_key)
                if coordinator:
                    detail_plan = coordinator._save_query_context(
                        detail_plan, question, detail_plan.get('query_context', {}).get('slots', {}), anchor
                    )
                return detail_plan
            choices = [(q['label'].split(' · ')[0], str(i + 1)) for i, q in enumerate(prior['queries'])]
            return coordinator.notice(
                'clarification_needed',
                f'当前包含 {len(prior["queries"])} 个分项，请指定要查看哪一项的明细（例如“出港明细”或“第二项明细”）：',
                question, anchor, {}, slot='item', choices=choices
            )
        if re.fullmatch(r'(?:那)?(?:出港|进港)(?:呢)?[？?]?', text):
            direction = '出港' if '出港' in text else '进港'
            text = re.sub(r'出港|进港', '', prior['query_context']['question']) + direction
        elif re.fullmatch(r'(?:那)?(?:同比|环比|去年同期)(?:呢)?[？?]?', text):
            text = re.sub(r'同比|环比', '', prior['query_context']['question']) + ('同比' if '去年' in text or '同比' in text else '环比')
    if re.search(r'预测|预估', text):
        cap = next((c for c in catalog.capabilities if c.get('code') == 'acdm.forecast'), None)
        if not cap or cap.get('status') != 'AVAILABLE':
            return coordinator.notice('unsupported', '短期预测能力尚未发布或已停用。', question, anchor, {})
        from app.agent.forecast import make_plan
        return make_plan(coordinator, question, anchor, day_mode)
    if is_cross_day_request(text):
        return make_cross_day_plan(
            coordinator, question, anchor,
            previous=previous, day_mode=day_mode, confirmed_period=confirmed_period,
        )
    chained_steps = split_chained_questions(question)
    if chained_steps:
        return plan_chained_queries(coordinator, question, chained_steps, anchor, previous=previous, day_mode=day_mode)
    peers = [(code, name) for code, name in AIRPORTS.items()
             if code in text.upper() or any(t in text for t in (name, name[:2]))]
    peer_query = any(code != 'WUH' for code, _ in peers) and not re.search(r'飞往|飞到|航线|目的|到达|起飞.*到|从.+到', text)
    city = r'(?:武汉|长沙|南京|郑州|WUH|CSX|NKG|CGO)'
    if re.search(city + r'(?:机场|天河|黄花|禄口|新郑)?\s*(?:到|至|往|飞往|飞到|->)\s*' + city, text, re.I) or re.search(r'(?:去|飞)(?:长沙|南京|郑州)', text):
        peer_query = False
    if re.search(r'明细|记录|条|列表|清单', text) and not re.search(r'对比|同比|环比', text):
        peer_query = False
    matches = matched_metrics(text, catalog)
    keys = list(dict.fromkeys(m[2] for m in matches))
    if len(keys) > 1 and 'flight_count' in keys:
        fc_matches = [m for m in matches if m[2] == 'flight_count' and m[3] in ('架次', '多少架次', '有多少航班')]
        if fc_matches and len(keys) > len(set(m[2] for m in fc_matches)):
            matches = [m for m in matches if m not in fc_matches]
            keys = list(dict.fromkeys(m[2] for m in matches))
    airlines = []
    text_without_flights = re.sub(r'(?:[A-Za-z]{2}|[A-Za-z]\d|\d[A-Za-z])\d{2,4}[A-Za-z]?', '', text)
    for ref in sorted(coordinator.references, key=lambda r: -len(r['term'])):
        if ref.get('role') == 'airline' and ref['term'] in text_without_flights and not any(a['value'] == ref['value'] for a in airlines):
            airlines.append(ref)
    ws, kind = windows(text, anchor)
    mode = 'yoy' if '同比' in text or '去年同期' in text else 'mom' if '环比' in text else None
    ordered_comparison = ORDERED_COMPARISON.search(text)
    wants_delta = bool(re.search(COMPARISON_OUTPUTS, text))
    compare = ordered_comparison or re.search(COMPARISON_WORDS, text)
    requested = (peer_query or mode or wants_delta or len(keys) > 1 or len(airlines) > 1
                 or (len(ws) > 1 and (compare or re.search(r'分别|和|与', text))))
    if not requested:
        return None
    if re.search(r'报告|简报|看板', text):
        return None
    capability = 'airport.benchmark' if peer_query else 'acdm.analysis'
    cap = next((c for c in catalog.capabilities if c.get('code') == capability), None)
    if not cap or cap.get('status') != 'AVAILABLE':
        return coordinator.notice('unsupported', '这项分析能力尚未发布或已停用。', question, anchor, {})
    if not ws:
        return coordinator.notice('clarification_needed', '请指定对比的业务日期或期间。', question, anchor, {})
    if mode and len(ws) != 1:
        raise ValueError('同比或环比请选择一个本期窗口')
    if mode:
        start, end = map(date.fromisoformat, ws[0])
        if start >= anchor.date():
            raise ValueError('当前业务日尚未结束，请选择完整历史日进行同环比，或先查询当天累计')
        if end > anchor.date() and start < anchor.date():
            if re.search(r'最近两周|近两周|最近一周|近一周|(?:最近|近)\d{1,2}天', text):
                start -= timedelta(days=1)
            ws = [(str(start), str(anchor.date()))]
        ws.append(comparison_window(ws[0], kind, mode))
    elif wants_delta or (compare and len(ws) == 2):
        if len(ws) != 2:
            raise ValueError('请指定要比较的两个业务日期或期间，以计算差值和增长率。')
        if ordered_comparison:
            ws = [windows(term, anchor)[0][0] for term in ordered_comparison.groups()]
        else:
            ws = sorted(ws, reverse=True)
        mode = 'explicit'
    common = text
    for a, b, _, _ in reversed(matches):
        common = common[:a] + ' ' + common[b:]
    common = ORDERED_COMPARISON.sub(r'\1 \2', common)
    common = re.sub(WINDOW_TOKEN, ' ', common)
    if mode:
        common = re.sub(COMPARISON_PRESENTATION, ' ', common)
    common_stop_words = [
        '分别多少架次', '各是多少架次', '是多少架次', '多少架次', '分别多少班', '各多少班', '多少班',
        '有几班', '有几条', '有几架', '是多少分钟', '多少分钟', '几分钟',
        '是多少', '分别是什么', '分别有哪些', '分别是多少', '各是多少',
        '分别是', '具体是哪些', '有哪些', '分别统计', '分别查询', '去年同期', '同比', '环比',
        '对标', '对比', '比较', '对照', '相较', '相比', '分别', '变化', '增长', '以及', '和', '与', '及', '、', '至',
        '多少', '架次', '班次', '人次', '条数', '数量', '指标', '数据'
    ]
    common_stop_words.sort(key=len, reverse=True)
    common = re.sub('|'.join(re.escape(w) for w in common_stop_words), ' ', common)
    common = re.sub(r'[，,。；;？?：:、\s]+', ' ', common)
    common = ' '.join(common.split())
    for ref in airlines:
        common = common.replace(ref['term'], ' ')
    if len(airlines) > 1 and re.search(r'不含|排除|以外|不是', text):
        raise ValueError('请将比较的航司对象与排除条件分别说明')
    metrics = [(key, catalog.metrics[key]['name']) for key in keys]
    if peer_query:
        if day_mode == 'OPERATIONAL' or '运营日' in text:
            raise ValueError('机场日对标数据使用自然日，无法转换为运营日日切口径')
        if TABLE not in catalog.entities:
            raise ValueError('机场对标表尚未发布，请先运行机场对标种子脚本')
        metrics = [(key, name) for key, (name, _, _, aliases) in METRICS.items() if any(a in text for a in aliases)]
        if not metrics:
            raise ValueError('请指定航班量、旅客吞吐量、客座率、国内执行率或单机座位数')
        common = re.sub(WINDOW_TOKEN, ' ', ORDERED_COMPARISON.sub(r'\1 \2', text))
        common = BENCHMARK_SOURCE_REQUEST.sub(' ', common)
        if mode:
            common = re.sub(COMPARISON_PRESENTATION, ' ', common)
        common = re.sub(COMPARISON_WORDS + r'|去年同期|同比|环比|对标|分别统计|分别查询|分别是多少|各是多少|是多少|是什么|分别是|具体是哪些|有哪些|多少|分别|变化|增长|以及|和|与|及|、|至|到|~|～', ' ', common)
        for _, name in peers:
            common = common.replace(name, '').replace(name[:2], '')
        for code, _ in peers:
            common = re.sub(re.escape(code), '', common, flags=re.I)
        for _, (_, _, _, aliases) in METRICS.items():
            for alias in sorted(aliases, key=len, reverse=True):
                common = common.replace(alias, '')
        common = re.sub(r'机场|天河|黄花|禄口|新郑|实际|执行|统计|查询|查看|请|多少|是多少|分别|的|数据|[\s，,？?。；;]+', '', common)
        if common:
            raise ValueError('机场日对标未覆盖这些附加条件：' + common)
    elif not metrics:
        raise ValueError('请指定已发布的统计指标')
    if len(metrics) * len(ws) * max(len(peers) if peer_query else len(airlines), 1) > MAX_QUERIES:
        raise ValueError('本轮最多12个子查询，请缩小指标、期间或比较对象')
    queries = []
    for key, name in metrics:
        for index, window in enumerate(ws):
            start, end = map(date.fromisoformat, window)
            last_day = end - timedelta(days=1)
            period_text = str(start) if start == last_day else f'{start}至{last_day}'
            objects = peers if peer_query else [(ref['term'], ref['value']) for ref in airlines] or [('', '')]
            for obj, obj_name in objects:
                q = f'{period_text} {common} {obj if not peer_query else ""} {name}'
                if peer_query:
                    child = benchmark_plan(catalog, key, obj, window, q, anchor)
                    label = AIRPORTS[obj] + ' · ' + name
                else:
                    child = coordinator.plan(q, day_mode=day_mode, _single=True)
                    label = (obj + ' · ' if obj else '') + name
                if child.get('outcome'):
                    message = child.get('answer') or '子查询需要补充条件'
                    return coordinator.notice(child['outcome'], f'{label}：{message}', question, anchor, {})
                if catalog.metrics.get(child.get('metric'), {}).get('availability', 'AVAILABLE') != 'AVAILABLE':
                    raise ValueError('指标尚未发布：' + name)
                queries.append({'id': f'q{len(queries) + 1}', 'label': label + ' · ' + period_text,
                                'metric_key': key, 'object': obj, 'period_index': index,
                                'period': list(window), 'question': q, 'plan': child})
    return {'subject': queries[0]['plan']['subject'], 'intent': 'analysis', 'queries': queries,
            'comparison_mode': mode, 'airport_benchmark': peer_query, 'conditions': [], 'group_by': [], 'focus_tables': [],
            'query_context': {'question': text, 'anchor': anchor.isoformat(), 'slots': {'period': list(ws[0])}}}


def benchmark_plan(catalog, key, code, window, question, anchor):
    if code == 'WUH':
        home = {'benchmark_flight_count': 'operated_flight_count', 'benchmark_passengers': 'pax_total_sum',
                'benchmark_load_factor': 'total_load_factor', 'benchmark_execution_rate': 'flight_execution_rate',
                'benchmark_seats': 'benchmark_home_seats'}[key]
        metric = catalog.metrics.get(home)
        if not metric:
            raise ValueError('本场尚未发布对应指标：' + home)
        target, field = metric['base_table'], metric['date_field']
        groups = []
    else:
        home, target, field = key, TABLE, 'stat_date'
        groups = ['airport_code', 'is_simulated', 'dataset_id']
    conditions = [{'col': field, 'op': '>=', 'value': window[0]}, {'col': field, 'op': '<', 'value': window[1]}]
    if code != 'WUH':
        conditions.append({'col': 'airport_code', 'op': '=', 'value': code})
    elif key == 'benchmark_execution_rate':
        conditions.append({'col': 'attribute', 'op': '=', 'value': '2403'})
    child = {'subject': target, 'intent': 'metric', 'metric': home, 'conditions': conditions,
             'group_by': groups, 'focus_tables': [], 'day_mode': 'CALENDAR',
             'query_context': {'question': question, 'anchor': anchor.isoformat(), 'slots': {'period': list(window)}}}
    return Compiler(catalog).validate(child)


def finalize(plan, catalog, day_mode):
    from app.core import _finalize_governance_plan
    if plan['intent'] == 'forecast':
        plan['base_plan'] = _finalize_governance_plan(plan['base_question'], plan['base_plan'], catalog, day_mode)
    elif plan.get('analysis_kind') == 'cross_day_reconciliation':
        return plan
    else:
        if not 1 <= len(plan.get('queries', [])) <= MAX_QUERIES:
            raise ValueError('复合查询数量无效')
        for child in plan['queries']:
            child['plan'] = _finalize_governance_plan(child['question'], child['plan'], catalog, day_mode)
    return plan


def blank(question, plan, catalog):
    return {'ok': True, 'outcome': 'answered', 'question': question, 'plan': plan, 'intent': plan['intent'],
            'source': '运营总控·受控分析', 'parse': {'subject': plan['subject'], 'intent': plan['intent'], 'conditions': []},
            'answer': '', 'sql': '', 'sql_params': [], 'main_total': 0, 'main_rows': [], 'related': [],
            'field_meta': {}, 'graph': {'nodes': [], 'edges': []}, 'graph_tables': [],
            'visual_meta': {'kind': 'none'}, 'claims': [], 'answer_validation': 'deterministic',
            'ontology_version': catalog.version, 'elapsed': 0}


def numeric_value(result):
    metric = result.get('metric') or {}
    field = metric.get('value_alias') or metric.get('field') or (result.get('plan') or {}).get('metric')
    rows = result.get('main_rows') or []
    if len(rows) != 1 or result.get('outcome') != 'answered':
        return None
    value = rows[0].get(field) if field else rows[0].get('total', rows[0].get('n'))
    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def calculate_comparison(current, baseline, unit):
    if current is None or baseline is None:
        return {'difference': None, 'growth_pct': None, 'difference_unit': '百分点' if unit == '%' else unit}
    return {'difference': round(current - baseline, 4),
            'growth_pct': round((current - baseline) / abs(baseline) * 100, 4) if baseline else None,
            'difference_unit': '百分点' if unit == '%' else unit}


def benchmark_sources(sub):
    if sub.get('outcome') != 'answered':
        return []
    tables = list(dict.fromkeys(
        f"{f['source']['database']}.{f['source']['table']}" for f in sub.get('evidence', {}).get('facts', [])
    ))
    if not tables:
        return []
    sources = []
    for row in sub.get('main_rows', []):
        dataset = row.get('dataset_id')
        if row.get('is_simulated') is True:
            label = '固定种子生成的模拟测试数据' if dataset == DATASET else '模拟测试数据'
        else:
            label = '本地对标数据' if dataset else '本地同步实际运行数据'
        details = '，'.join(tables + ([f'数据集 {dataset}'] if dataset else []))
        sources.append(f'{label}（{details}）')
    return list(dict.fromkeys(sources))


def run(conn, question, plan, catalog, trace, on_event=None, access=None):
    from app import core
    if plan['intent'] == 'forecast':
        from app.agent.forecast import run_forecast
        return run_forecast(conn, question, plan, catalog, trace, access)
    if plan.get('analysis_kind') == 'cross_day_reconciliation':
        from app.agent.cross_day import run_cross_day
        return run_cross_day(conn, question, plan, catalog, trace, on_event, access)
    started = perf_counter()
    result = blank(question, plan, catalog)
    sections, cache, lines, all_facts, claims = [], {}, [], [], []
    airport_sources = {}
    trace.start('analysis', '执行分项分析', '每项使用已校验计划、同一只读数据库快照；失败项独立保留状态')
    for child in plan['queries']:
        child_plan = child['plan']
        cache_key = json.dumps({k: v for k, v in child_plan.items() if k not in ('query_context', 'disp')}, sort_keys=True, default=str)
        event_id = child['id']
        offset = perf_counter() - trace.started
        def forward(event):
            if event.get('type') == 'step':
                step = {**event['step'], 'id': event_id + ':' + event['step']['id'],
                        'label': child['label'] + ' · ' + event['step']['label'],
                        'started_at': round(offset + event['step']['started_at'], 3)}
                trace.steps[step['id']] = step
                if on_event:
                    on_event({'type': 'step', 'query_id': event_id, 'step': step})
        if cache_key in cache:
            sub = deepcopy(cache[cache_key])
        else:
            cur = conn.cursor()
            try:
                cur.execute('SAVEPOINT analysis_item')
                try:
                    sub = core.do_ask(conn, child['question'], on_event=forward, use_model=False, use_cache=False,
                                      access=access, prepared_plan=child_plan)
                except Exception as exc:
                    cur.execute('ROLLBACK TO SAVEPOINT analysis_item')
                    if not isinstance(exc, ValueError):
                        core.logger.exception('分项查询失败：%s', event_id)
                    sub = blank(child['question'], child_plan, catalog)
                    sub.update(ok=False, outcome='unsupported', answer=str(exc)[:200] if isinstance(exc, ValueError) else '查询执行失败，请检查执行过程或稍后重试')
                cur.execute('RELEASE SAVEPOINT analysis_item')
            finally:
                cur.close()
            cache[cache_key] = deepcopy(sub)
        marked = sub.get('is_simulated', False)
        simulated = marked or any(row.get('is_simulated') is True for row in sub.get('main_rows', []))
        if child_plan['subject'] == TABLE and sub.get('main_rows'):
            expected_days = (date.fromisoformat(child['period'][1]) - date.fromisoformat(child['period'][0])).days
            actual_days = sum(int(r.get('n', 0)) for r in sub['main_rows'])
            if actual_days != expected_days:
                sub['coverage_assessment'] = {'status': 'PARTIAL', 'complete': False,
                    'message': f'请求{expected_days}天，只观测到{actual_days}天，不以零补齐或生成完整期间排名'}
                sub['answer'] += '\n' + sub['coverage_assessment']['message']
        sub['data_origin'] = 'simulated' if simulated else 'observed'
        sub['is_simulated'] = simulated
        if simulated and not marked:
            sub['answer'] = '【模拟测试数据】' + sub['answer']
            sub['claims'] = [{'text': '本项为模拟测试数据，不代表机场实际运行。', 'fact_ids': [], 'kind': 'limitations'}] + sub.get('claims', [])
            sub.pop('answer_document', None)
        metric = sub.get('metric') or catalog.metrics.get(child_plan.get('metric'), {})
        value, unit = numeric_value(sub), metric.get('unit', '')
        if not unit and child_plan.get('intent') == 'count' and child_plan.get('subject') in (
            'ops_ask_views.flight_execution', 'std_flight_core', 'mv_acdm_dyn_flight_detail',
        ):
            unit = '架次'
        formula_desc = metric.get('formula_desc') or metric.get('description') or ''
        sample_row = sub.get('main_rows', [{}])[0] if sub.get('main_rows') else {}
        sample_count = sample_row.get('n')
        num_val = sample_row.get('__num')
        den_val = sample_row.get('__den')

        sub_rows = sub.get('main_rows') or []
        is_detail_sub = sub.get('intent') == 'detail'
        detail_total = int(sub.get('main_total') or len(sub_rows))
        calc_proc = None
        if num_val is not None and den_val is not None and value is not None:
            try:
                num_f = float(num_val)
                den_f = float(den_val)
                num_str = f"{int(num_f):,}" if num_f.is_integer() else f"{num_f:,.2f}"
                den_str = f"{int(den_f):,}" if den_f.is_integer() else f"{den_f:,.2f}"
                n_str = f"（样本: {sample_count:,} 架次）" if sample_count else ""
                calc_proc = f"计算过程：{num_str} ÷ {den_str} × 100% = {value:g}%{n_str}"
            except (ValueError, TypeError):
                pass
        elif is_detail_sub:
            calc_proc = f"明细查询：共命中 {detail_total:,} 条记录（展示前 {len(sub_rows)} 条）"
        elif sample_count and value is not None:
            calc_proc = f"统计范围：{sample_count:,} 条记录"
        elif len(sub_rows) > 1:
            calc_proc = f"维度统计：已按分组统计前 {len(sub_rows)} 项"

        section = {**{k: v for k, v in child.items() if k not in ('plan', 'question')},
                   'result': sub, 'value': value, 'unit': unit, 'is_simulated': simulated,
                   'formula_desc': formula_desc, 'calculation_process': calc_proc,
                   'sample_count': sample_count, 'num_val': num_val, 'den_val': den_val}
        sections.append(section)
        status = sub.get('outcome', 'answered')
        if value is not None:
            status_text = f'{value:g} {unit}'.strip()
        elif is_detail_sub:
            first_line = str(sub.get('answer') or '').splitlines()[0] if sub.get('answer') else ''
            status_text = first_line or f'共 {detail_total:,} 条明细'
        else:
            status_text = sub['answer']

        lines.append(child['label'] + '：' + status_text + ('（模拟测试数据）' if simulated else ''))

        display_val = (
            value if value is not None
            else (f"共 {detail_total:,} 条明细" if is_detail_sub
                  else (f"TOP {len(sub_rows)} 项" if len(sub_rows) > 1 else None))
        )
        result['main_rows'].append({
            '项目': child['label'],
            '数值': display_val,
            '单位': unit,
            '计算依据': (f"{calc_proc} | " if calc_proc else "") + formula_desc,
            '状态': status,
            '来源': '模拟测试数据' if simulated else '本地观测数据',
        })
        for fact in sub.get('evidence', {}).get('facts', []):
            all_facts.append({**fact, 'id': child['id'] + ':' + fact['id']})
        claims.append({'text': lines[-1], 'fact_ids': [child['id'] + ':' + f['id'] for f in sub.get('evidence', {}).get('facts', [])]})
        if calc_proc or formula_desc:
            basis = '；'.join(filter(None, (calc_proc, formula_desc)))
            claims.append({'text': f"{child['label']} 计算依据：{basis}", 'fact_ids': [child['id'] + ':' + f['id'] for f in sub.get('evidence', {}).get('facts', [])], 'kind': 'methodology'})
        if plan.get('airport_benchmark'):
            code = child['object']
            entry = airport_sources.setdefault(code, {'airport_code': code, 'airport_name': AIRPORTS[code], 'sources': []})
            for source in benchmark_sources(sub):
                if source not in entry['sources']:
                    entry['sources'].append(source)
        if on_event:
            on_event({'type': 'query_result', 'query_id': event_id, 'section': section})
            on_event({'type': 'progress', 'stage': 'analysis', 'percent': 20 + round(65 * len(sections) / len(plan['queries'])),
                      'message': f'已处理{len(sections)}/{len(plan["queries"])}项，正在整理分项事实…'})
    comparisons = []
    if plan.get('comparison_mode'):
        for section in sections:
            if section['period_index'] != 0:
                continue
            base = next((s for s in sections if s['period_index'] == 1 and s['metric_key'] == section['metric_key'] and s['object'] == section['object']), None)
            if base:
                comparable = all(s['result'].get('coverage_assessment', {}).get('status') != 'PARTIAL' for s in (section, base))
                delta = calculate_comparison(section['value'] if comparable else None, base['value'], section['unit'])
                comparisons.append({'current_id': section['id'], 'baseline_id': base['id'], **delta})
                label = f"{section['label']} 相比 {base['label'].rsplit(' · ', 1)[-1]}"
                difference, growth = delta['difference'], delta['growth_pct']
                if difference is None:
                    lines.append(f'{label}：数据不足，无法计算差值和增长率。')
                else:
                    rate = f'{growth:+.2f}%' if growth is not None else '基期为零，无法计算'
                    lines.append(f"{label}：差值 {difference:+g} {delta['difference_unit']}，增长率 {rate}。")
                claims.append({'text': lines[-1], 'fact_ids': [f['id'] for f in all_facts if f['id'].startswith((section['id'] + ':', base['id'] + ':'))]})
    if airport_sources:
        result['airport_sources'] = list(airport_sources.values())
        lines.append('【各机场数据来源】')
        for code, entry in airport_sources.items():
            source_text = '；'.join(entry['sources']) or '本次未获取可核验的来源记录。'
            line = f"{entry['airport_name']}（{code}）：{source_text}"
            lines.append(line)
            section_ids = [s['id'] + ':' for s in sections if s['object'] == code]
            claims.append({'text': line, 'fact_ids': [f['id'] for f in all_facts if f['id'].startswith(tuple(section_ids))]})
    failed = [s for s in sections if s['result'].get('outcome') != 'answered'
              or (s['value'] is None and not (s['result'].get('main_rows')))
              or s['result'].get('coverage_assessment', {}).get('status') == 'PARTIAL']
    simulated = any(s['is_simulated'] for s in sections)
    boundary = '本结果仅反映本地已同步记录，日期连续不证明同步完整。'
    if simulated:
        boundary += '包含模拟测试数据，不能作为机场实际排名或经营结论。'
    result.update(sections=sections, comparisons=comparisons, partial=bool(failed), is_simulated=simulated,
                  main_total=len(sections), answer='\n'.join(lines + [boundary]),
                  claims=claims + [{'text': boundary, 'fact_ids': [], 'kind': 'limitations'}],
                  evidence={'facts': all_facts, 'coverage': [], 'queried_at': date.today().isoformat()},
                  field_meta={plan['subject']: {key: {'label': key} for key in ('项目', '数值', '单位', '计算依据', '状态', '来源')}},
                  elapsed=round(perf_counter() - started, 3))
    if len(failed) == len(sections):
        outcomes = {s['result'].get('outcome') for s in sections}
        result['outcome'] = outcomes.pop() if len(outcomes) == 1 else 'no_data'
    trace.finish('analysis', f'已处理{len(sections)}项，{len(failed)}项无完整数值结果')
    result['execution_trace'] = trace.snapshot()
    return result
