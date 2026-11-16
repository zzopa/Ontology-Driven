"""Question interpretation using only the current catalog and configured bindings."""
from datetime import date, timedelta
from decimal import Decimal
import json
import re

from app.query import Compiler
from app.agent.planning import reconcile_anchor


_ACDM_DIM_PATTERNS = {
    'hour': r'(?:按|逐|分|各|每|各个)小时|小时(?:走势|趋势|分布)',
    'peak_hour': r'高峰小时|高峰时段|最忙小时|哪[个一](?:小时|时段).{0,10}最[多高]',
    'airline': r'(?:按|分|各|各个)(?:航司|航空公司)|哪个航司',
    'direction': r'(?:按|分)进出港|按方向',
    'date': r'(?:按|逐|每)(?:天|日)|各日',
    'week': r'(?:按|逐|每|各)周',
    'month': r'(?:按|逐|每|各|各个)月',
    'origin_city': r'从哪个城市|来源城市|从哪里来|起站城市',
    'dest_city': r'到哪个城市|去哪儿|目的站城市|飞到哪',
    'terminal': r'(?:按|分|各|各个)航站楼',
    'stand_type': r'(?:按|分|各)机位类型',
    'aircraft_type': r'(?:按|分|各|各个)机型',
    'delay_reason': r'按延误原因|延误原因分布',
    'agency': r'(?:按|分|各)代理|(?:按|分)航班代理',
    'attribute': r'(?:按|分|各)(?:航班)?属性|按国际国内|分国内国际',
    'flight_task_cd': r'(?:按|分|各)(?:飞行)?任务(?:性质|码)?',
    'stand_code': r'(?:按|分|各)(?:停)?机位(?!类型)',
    'gate_code': r'(?:按|分|各)登机口',
    'operation_plan_day': r'按运营计划日|按05[：:]?00运营日',
    'operation_exec_day': r'按运营执行日|按运营实飞日',
}


def extract_acdm_dimensions_and_topn(question, catalog, target=None, allow_topn=True):
    """Extract A-CDM dimension groupings, Top-N limits, sort direction, and matched phrases."""
    group_by = []
    consumed = []
    dims = getattr(catalog, 'dimensions', {}) or {}
    for d_key, d_cfg in dims.items():
        aliases = list(d_cfg.get('aliases', []) or []) + [d_cfg.get('name', ''), d_key]
        pat = _ACDM_DIM_PATTERNS.get(d_key)
        alias_hit = False
        for a in aliases:
            if a and a in question:
                if d_key == 'aircraft_type' and '重点机型' in question and not re.search(r'(?:按|分|各|各个)机型', question):
                    continue
                alias_hit = True
                consumed.append(a)
        pat_hit = re.search(pat, question) if pat else None
        if pat_hit:
            consumed.append(pat_hit.group(0))
        if alias_hit or pat_hit:
            if d_key not in group_by:
                group_by.append(d_key)
    if target == 'ops_ask_views.flight_hourly_movements' and 'peak_hour' in dims:
        group_by = list(dict.fromkeys('peak_hour' if g == 'hour' else g for g in group_by))
    extra_opts = {}
    if allow_topn and group_by:
        m_top = re.search(r'(?:前|top\s*)(\d{1,2})(?![0-9])名?', question, re.I)
        if m_top:
            extra_opts['limit'] = max(1, min(50, int(m_top.group(1))))
            consumed.append(m_top.group(0))
        else:
            m_peak_one = (
                re.search(r'最高是哪|最多是哪|峰值|高峰小时是哪个|高峰时段是哪个|哪一小时最多|哪个小时最多', question)
                or re.search(r'哪[个一](?:小时|时段).{0,10}最[多高]', question)
            )
            if m_peak_one:
                extra_opts['limit'] = 1
                extra_opts['sort_dir'] = 'desc'
                consumed.append(m_peak_one.group(0))
        m_asc = re.search(r'最低|最少|最差|倒数|拖后腿', question)
        if m_asc:
            extra_opts['sort_dir'] = 'asc'
            consumed.append(m_asc.group(0))
    return group_by, extra_opts, consumed


def match_acdm_report(question, catalog, bounds_fn=None, today_d=None):
    """Recognize A-CDM Ch.7 holistic report / briefing / safeguard board prompts."""
    if 'std_flight_core' not in catalog.entities and 'mv_acdm_dyn_flight_detail' not in catalog.entities:
        return None
    r_kind = None
    r_title = None
    if re.search(r'地服(?:公司)?(?:运行)?简报', question):
        r_kind, r_title = 'ground_briefing', '地服公司运行简报'
    elif re.search(r'A-?CDM\s*(?:通用)?报告?|通用报告?|运行[日周月季年]报|完整运行报告', question, re.I):
        r_kind, r_title = 'general', 'A-CDM 通用报告'
    elif re.search(r'保障(?:节点|质量).{0,6}(?:排行|榜|录入)|(?:单桥|登机桥).{0,4}周转', question):
        r_kind, r_title = 'safeguard_board', '保障节点质量与登机桥周转看板'
    if not r_kind:
        return None
    today_d = today_d or date.today()
    bounds = bounds_fn(question) if bounds_fn else None
    default_d = today_d if r_kind == 'ground_briefing' else (today_d - timedelta(days=1))
    p_type = 'day'
    if bounds:
        b_kind, s_val, e_excl = bounds
        if b_kind == 'range':
            s_date = date.fromisoformat(s_val)
            e_date = date.fromisoformat(e_excl) - timedelta(days=1)
            p_type = 'day' if s_date == e_date else ('week' if (e_date - s_date).days == 6 else 'custom')
        elif len(s_val) == 7:
            y, m = map(int, s_val.split('-'))
            s_date = date(y, m, 1)
            e_date = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1))
            p_type = 'month'
        else:
            s_date = e_date = date.fromisoformat(s_val)
    elif '本周' in question or '上周' in question:
        offset = 7 if '上周' in question else 0
        monday = today_d - timedelta(days=today_d.weekday() + offset)
        s_date, e_date, p_type = monday, monday + timedelta(days=6), 'week'
    else:
        s_date = e_date = default_d
    subj = 'mv_acdm_dyn_flight_detail' if 'mv_acdm_dyn_flight_detail' in catalog.entities else 'std_flight_core'
    return {
        'subject': subj,
        'disp': r_title,
        'intent': 'acdm_report',
        'report_kind': r_kind,
        'report_period_type': p_type,
        'report_start': s_date.isoformat(),
        'report_end': e_date.isoformat(),
        'conditions': [],
        'focus_tables': [],
        'group_by': [],
    }


def apply_acdm_metric_slots(question, catalog, metric, conditions, allow_actype=True):
    """Extract calibrated A-CDM attribute/agency/aircraft_type/task/remote slots for a metric."""
    metrics_map = getattr(catalog, 'metrics', {}) or {}
    if not metric or metric not in metrics_map:
        return metric, []
    eff_target = metrics_map[metric].get('base_table') or 'std_flight_core'
    eff_attrs = catalog.entities.get(eff_target, {}).get('attributes', {})
    consumed = []

    def add_slot(col_name, val, term=None):
        if term:
            consumed.append(term)
        if col_name in eff_attrs and not any(c.get('col') == col_name for c in conditions):
            conditions.append({'table': eff_target, 'col': col_name, 'op': '=', 'value': val})

    if allow_actype:
        m_actype = re.search(r'(?<![A-Za-z0-9])(B7[0-9A-Z]{2}|A3[0-9A-Z]{2}|A2[01]N|C919|ARJ21|E19[05])(?![A-Za-z0-9])', question.upper())
        if m_actype:
            act_col = 'craft_type_icao_cd' if 'craft_type_icao_cd' in eff_attrs else ('aircraft_type' if 'aircraft_type' in eff_attrs else '')
            if act_col:
                add_slot(act_col, m_actype.group(1), m_actype.group(1))
    if '地服' in question and metric not in ('ground_takeoff_on_time_rate', 'ground_release_on_time_rate'):
        add_slot('agency', '210', '地服代理' if '地服代理' in question else '地服')
    attr_hits = [('2401', '国际'), ('2402', '地区'), ('2402', '港澳台'), ('2403', '国内'), ('2404', '混合')]
    matched_attrs = [(code, kw) for code, kw in attr_hits if kw in question]
    if len({c for c, _ in matched_attrs}) == 1:
        code, kw = matched_attrs[0]
        if 'attribute' in eff_attrs:
            add_slot('attribute', code, kw)
        elif 'flight_attribute' in eff_attrs:
            add_slot('flight_attribute', code, kw)
    if '正班' in question:
        add_slot('flight_task_cd', 'W/Z', '正班')
    if '始发' in question and metric not in ('origin_dep_on_time_rate', 'origin_flight_count'):
        add_slot('ori_act', 'Y', '始发')
    if '外保障' in question or ('远机位' in question and '近机位' not in question):
        if metric not in ('remote_stand_flights',):
            add_slot('craft_site_is_remote', 'Y', '外保障' if '外保障' in question else '远机位')
    elif '近机位' in question and '远机位' not in question and metric not in ('bridge_stand_rate', 'bridge_stand_rate_all'):
        add_slot('craft_site_is_remote', 'N', '近机位')
    return metric, consumed


def home_airport_terms(catalog):
    """Return configured home airport display/city/alias tokens for semantic disambiguation."""
    home = getattr(catalog, 'home_airport', {}) or {}
    return [t for t in [home.get('cityName'), home.get('displayName'), *(home.get('aliases') or [])] if t]


_DATE_WORDS_RE = re.compile(r'昨天|昨日|前天|今天|今日|明天|明日|后天|最近|近|\d+月|\d+日|\d+号|\d{4}-\d{2}-\d{2}')
_ROUTE_STOP_SUBSTR = (
    r'进港|出港|到港|离港|计划|实际|执飞|执行|航班|班次|飞机|航线|客机|货机|'
    r'时序图|时序|甘特图|甘特|鱼骨图|多少|哪些|有哪些|有什么|有多少|一览|清单|列表|排名|排行|明细|详情|情况|状态|动态|延误|取消|正常'
)
_ROUTE_STOP_WORDS = {
    '航班', '班次', '计划', '实际', '执飞', '执行', '飞机', '客机', '货机',
    '时序图', '甘特图', '多少', '哪些', '明细', '详情', '情况', '状态', '动态',
    '一览', '清单', '列表', '排名', '排行', '统计', '走势', '趋势', '本场'
}


def _clean_city_token(tok):
    if not tok:
        return ''
    t = tok.strip()
    changed = True
    while changed:
        old = t
        t = re.sub(r'^(?:从|由|去|到|往|至|在|于|飞往|前往)+', '', t)
        t = re.sub(r'^(?:昨天|昨日|前天|今天|今日|明天|明日|后天|[日号天])+', '', t)
        t = re.sub(r'(?:的)?(?:计划|实际|执飞|执行)?(?:航班|班次|飞机|航线|客机|货机|时序图|甘特图)?$', '', t)
        t = re.sub(r'(?:有哪些|有什么|有多少|怎么样|如何|怎样)$', '', t)
        t = t.rstrip('的')
        changed = (t != old)
    return t.strip()


def _is_valid_city(tok):
    c = _clean_city_token(tok)
    if len(c) < 2 or len(c) > 10:
        return False
    if c in _ROUTE_STOP_WORDS or _DATE_WORDS_RE.search(c):
        return False
    if any(w in c for w in ('航班', '计划', '实际', '执飞', '执行', '多少', '哪些', '明细', '时序', '出港', '进港', '起飞', '到港', '离港', '飞走', '降落')):
        return False
    return True


def extract_home_airport_city_and_dir(question, catalog):
    """Extract arrival/departure disambiguation & non-home origin/dest city filters."""
    home_terms = home_airport_terms(catalog)
    if '本场' not in home_terms:
        home_terms.append('本场')
    explicit_dir = None
    consumed = []
    origin_city, dest_city = None, None

    if any(w in question for w in ('出港', '离港', '起飞', '出发')):
        explicit_dir = 'DEP'
    elif any(w in question for w in ('进港', '到港', '抵达', '到达', '落地')):
        explicit_dir = 'ARR'
    else:
        for ht in home_terms:
            m_arr = re.search(rf'(?:飞来|飞往|来|到|至){re.escape(ht)}', question)
            if m_arr:
                explicit_dir = 'ARR'
                consumed.append(m_arr.group(0))
                break
            m_dep = re.search(rf'从{re.escape(ht)}(?:飞走|起飞|出发|离港|飞往|去|往|飞)', question)
            if m_dep:
                explicit_dir = 'DEP'
                consumed.append(m_dep.group(0))
                break

    tail_lookahead = rf'(?=(?:的|了)?(?:{_ROUTE_STOP_SUBSTR}|$|[\s，,。?？]))'

    # 1. Two-point route pattern: e.g. 武汉到上海, 武汉至上海, 武汉飞上海, 从武汉飞往上海, 上海到武汉
    m_route = re.search(
        rf'(?:从|由)?\s*([^\s，,。?？\d]{{2,10}}?)\s*(?:到|至|飞往|前往|出港去|去|飞来|来|(?<![起已未])飞(?![机行班手客货])|-|—|~)\s*([^\s，,。?？\d]{{2,10}}?)(?:的)?{tail_lookahead}',
        question,
    )
    if m_route:
        raw_c1, raw_c2 = m_route.group(1), m_route.group(2)
        c1, c2 = _clean_city_token(raw_c1), _clean_city_token(raw_c2)
        if _is_valid_city(c1) and _is_valid_city(c2):
            c1_home = any(ht == c1 or c1 in ht or ht in c1 for ht in home_terms)
            c2_home = any(ht == c2 or c2 in ht or ht in c2 for ht in home_terms)
            s_idx = question.find(c1)
            pref = question.rfind('从', 0, s_idx) if s_idx != -1 else -1
            if pref != -1 and s_idx - pref <= 2:
                s_idx = pref
            e_idx = question.find(c2, s_idx if s_idx != -1 else 0) + len(c2)
            if question[e_idx:e_idx + 1] == '的':
                e_idx += 1
            matched = question[s_idx:e_idx] if s_idx != -1 and e_idx > s_idx else m_route.group(0)

            if c1_home and not c2_home:
                dest_city = c2
                explicit_dir = explicit_dir or 'DEP'
                consumed.append(matched)
            elif c2_home and not c1_home:
                origin_city = c1
                explicit_dir = explicit_dir or 'ARR'
                consumed.append(matched)
            elif not c1_home and not c2_home:
                origin_city = c1
                dest_city = c2
                consumed.append(matched)

    # 2. Single destination pattern: e.g. 到上海, 飞往北京, 去广州, 飞成都
    if not dest_city and not origin_city:
        m_to = re.search(
            rf'(?:飞往|前往|出港去|去|到|至|(?<![起已未])飞(?![机行班手客货]))\s*([^\s，,。?？\d]{{2,10}}?)(?:的)?{tail_lookahead}',
            question,
        )
        if m_to:
            c = _clean_city_token(m_to.group(1))
            if _is_valid_city(c):
                if any(ht in c or c in ht for ht in home_terms):
                    explicit_dir = explicit_dir or 'ARR'
                    consumed.append(m_to.group(0))
                else:
                    dest_city = c
                    explicit_dir = explicit_dir or 'DEP'
                    consumed.append(m_to.group(0))

    # 3. Single origin pattern: e.g. 从上海飞来, 从北京来, 上海来的
    if not dest_city and not origin_city:
        m_from = re.search(
            rf'(?:从|由)\s*([^\s，,。?？\d]{{2,10}}?)\s*(?:飞来|来的|来|出港|出发|起飞)(?:的)?{tail_lookahead}',
            question,
        )
        if not m_from:
            m_from = re.search(
                rf'([^\s，,。?？\d]{{2,10}}?)\s*(?:飞来|来的)(?:的)?{tail_lookahead}',
                question,
            )
        if m_from:
            c = _clean_city_token(m_from.group(1))
            if _is_valid_city(c):
                if any(ht in c or c in ht for ht in home_terms):
                    explicit_dir = explicit_dir or 'DEP'
                    consumed.append(m_from.group(0))
                else:
                    origin_city = c
                    explicit_dir = explicit_dir or 'ARR'
                    consumed.append(m_from.group(0))

    return explicit_dir, origin_city, dest_city, home_terms, consumed


def resolve_concept_metric(catalog, concept_codes, explicit_dir=None):
    """Resolve best published metric_code from ont_concept_metric for matched concepts."""
    metrics_map = getattr(catalog, 'metrics', {}) or {}
    concept_metrics = getattr(catalog, 'concept_metrics', []) or []
    for cc in concept_codes:
        cands = [cm for cm in concept_metrics if cm.get('concept_code') == cc]
        cands.sort(key=lambda x: -int(x.get('priority') or 0))
        picked = None
        if explicit_dir:
            picked = next((cm for cm in cands if (cm.get('condition_expr') or {}).get('direction') == explicit_dir), None)
        picked = picked or (cands[0] if cands else None)
        if picked and picked.get('metric_code') in metrics_map:
            return picked['metric_code']
        if cc == 'FlightVolume' and 'flight_count' in metrics_map:
            return 'flight_count'
    return None


_HOME_AIRPORT_SCOPED_TABLES = {
    'std_flight_core',
    'ops_ask_views.flight_execution',
    'mv_acdm_dyn_flight_detail',
    'mv_acdm_turnaround_pair',
    'ops_ask_views.flight_hourly_movements',
    'std_flight_production',
}


def apply_home_airport_scope(catalog, subject, conditions):
    """Enforce home_airport.iataCode filter on multi-airport operational fact tables when configured."""
    iata = str((getattr(catalog, 'home_airport', {}) or {}).get('iataCode') or '').strip().upper()
    if not iata or subject not in _HOME_AIRPORT_SCOPED_TABLES:
        return
    attrs = (getattr(catalog, 'entities', {}).get(subject) or {}).get('attributes', {})
    if 'airport_code' not in attrs:
        return
    if any(c.get('col') in ('airport_code', 'airportCode', 'id') for c in conditions if isinstance(c, dict)):
        return
    conditions.append({'table': subject, 'col': 'airport_code', 'op': '=', 'value': iata})


class Planner:
    def __init__(self, catalog, name_index=None, enterprise_names=None, chat=None, trace=None):
        self.catalog = catalog
        self.compiler = Compiler(catalog)
        self.name_index = name_index or {}
        self.enterprise_names = enterprise_names or {}
        self.chat = chat
        self.trace = trace

    def surname(self, question):
        # Prefer explicit compound surnames; never treat the last character of
        # a company name as part of a two-character surname.
        compounds = ('欧阳', '司马', '上官', '诸葛', '东方', '皇甫', '尉迟', '公孙',
                     '慕容', '宇文', '司徒', '司空', '夏侯', '令狐', '长孙', '南宫',
                     '独孤', '闻人', '澹台', '公羊', '赫连', '宗政', '濮阳', '淳于',
                     '单于', '太叔', '申屠', '仲孙', '轩辕', '钟离', '闾丘', '鲜于',
                     '拓跋', '百里', '东郭', '西门', '公冶', '端木', '呼延')
        term = '(?:' + '|'.join(compounds) + r'|[\u4e00-\u9fff])'
        boundary = r'(?=的|员工|人员|职工|人|同事|情况|信息|女|男|[\s，,、。?？]|或|和|及|$)'
        patterns = [r'(' + term + r')姓' + boundary,
                    r'(?:姓氏(?:为|是|[:：])?|姓)\s*[“"\']?(' + term + r')[”"\']?' + boundary]
        matches = []
        for pattern in patterns:
            for match in re.finditer(pattern, question):
                # “夏姓女员工” means surname 夏 + gender 女, not also 姓女.
                if not any(match.start() < old.end() and old.start() < match.end() for old in matches):
                    matches.append(match)
        values = {match.group(1) for match in matches}
        if len(values) > 1:
            raise ValueError('检测到多个姓氏，请一次指定一个姓氏，或明确各姓氏之间的筛选关系')
        if matches and any(re.search(r'不|非|排除|除去', question[max(0, m.start()-4):m.start()]) for m in matches):
            raise ValueError('当前姓氏规则支持包含筛选；排除姓氏请明确排除条件，不能按包含条件查询')
        return next(iter(values)) if values else None

    def complete_identity_query(self, question, plan, allow_extra=False):
        """Conservative coverage check for fallback, not a guess at missing filters."""
        spec = self.catalog.entities[plan['subject']]
        if (not spec.get('name_lookup') or plan['intent'] not in ('detail', 'count')
                or plan.get('focus_tables') or plan.get('time_bucket')):
            return False
        name_field, company_field = spec['bindings'].get('name'), spec['bindings'].get('company')
        # Multiple names cannot be treated as a complete single-identity query
        # (the current predicate compiler combines conditions with AND).
        if sum(c['col'] == name_field for c in plan['conditions']) != 1:
            return False
        if any((c.get('table') or plan['subject']) != plan['subject'] or
               (not allow_extra and c['col'] not in (name_field, company_field)) for c in plan['conditions']):
            return False
        remaining = question
        surname = self.surname(question)
        if surname:
            patterns = [re.escape(surname) + r'姓',
                        r'(?:姓氏(?:为|是|[:：])?|姓)\s*[“"\']?' + re.escape(surname) + r'[”"\']?']
            for pattern in patterns:
                remaining = re.sub(pattern, '', remaining)
        tokens = list(spec['aliases'])
        tokens += [name for table, name in self.names(question) if table == plan['subject']]
        company = self.company(question)
        tokens += [alias for alias, code in {**self.enterprise_names,
                   **self.catalog.overrides.get('enterprise_aliases', {})}.items() if code == company]
        tokens += ['查找', '查询', '查看', '检索', '列出', '统计', '详细信息', '档案信息',
                   '员工信息', '人员信息', '信息', '详情', '明细', '档案', '情况', '记录',
                   '共有', '总共', '总数', '数量', '人数', '多少', '几名', '几人', '有',
                   '名', '条', '位', '个', '人', '的', '请', '帮我', '一下', '所有', '全部']
        if allow_extra:
            for condition in plan['conditions']:
                if condition['col'] in (name_field, company_field):
                    continue
                # Only extra predicates present in the validated plan can
                # account for extra question terms. Missing predicates cannot.
                attr = self.catalog.attribute(plan['subject'], condition['col'])
                tokens += [attr['label'], *attr.get('aliases', [])]
                values = condition['value'] if isinstance(condition.get('value'), list) else [condition.get('value')]
                for value in values:
                    text = str(value)
                    if text in question:
                        tokens.append(text)
                    translated = str(attr.get('enums', {}).get(text, ''))
                    if translated and translated in question:
                        tokens.append(translated)
            tokens += ['并且', '而且', '且', '并', '中', '是', '为', '等于', '属于']
        for token in sorted(set(tokens), key=len, reverse=True):
            if token:
                remaining = remaining.replace(token, '')
        return not re.sub(r'[\s，,。.!！?？：:;；“”"\'（）()]', '', remaining)

    def normalize_conditions(self, parsed, question):
        # A provider may express LIKE rather than the plan's prefix/contains
        # operators. Only normalize literal, explicitly requested text patterns.
        for condition in parsed['conditions']:
            op = condition.get('op')
            if not isinstance(op, str) or op.lower() != 'like':
                continue
            value = condition.get('value')
            if not isinstance(value, str) or '_' in value or '\\' in value:
                continue
            if value.endswith('%') and value.count('%') == 1:
                raw, op = value[:-1], 'prefix'
            elif value.startswith('%') and value.endswith('%') and value.count('%') == 2:
                raw, op = value[1:-1], 'contains'
            else:
                continue
            if raw and raw in question:
                condition.update(op=op, value=raw)

    def company(self, question):
        candidates = {**self.enterprise_names, **self.catalog.overrides.get('enterprise_aliases', {})}
        hits = [(len(alias), alias, code) for alias, code in candidates.items() if alias in question]
        return max(hits)[2] if hits else None

    def names(self, question):
        hits = [(len(name), table, name) for table, names in self.name_index.items()
                for name in names if len(name) >= 2 and name in question]
        if not hits:
            return []
        longest = max(item[0] for item in hits)
        return [(table, name) for size, table, name in hits if size == longest]

    def period(self, question):
        today = date.today()
        match = re.search(r'(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})(?:日|号)?', question)
        if match:
            return date(*(int(value) for value in match.groups())).isoformat()
        for word, offset in [('前天', 2), ('昨天', 1), ('昨日', 1), ('今天', 0), ('今日', 0)]:
            if word in question:
                return (today - timedelta(days=offset)).isoformat()
        match_md = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})[日号]', question)
        if match_md:
            try:
                return date(today.year, int(match_md.group(1)), int(match_md.group(2))).isoformat()
            except ValueError as exc:
                raise ValueError('日期无效') from exc
        if '今年' in question:
            return str(today.year)
        if '去年' in question:
            return str(today.year - 1)
        if '上个月' in question or '上月' in question:
            year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            return '%04d-%02d' % (year, month)
        if '本月' in question or '这个月' in question:
            return '%04d-%02d' % (today.year, today.month)
        match = re.search(r'(20\d{2})[年/-](\d{1,2})月?', question)
        if match:
            month = int(match.group(2))
            if not 1 <= month <= 12:
                raise ValueError('月份无效')
            return '%s-%02d' % (match.group(1), month)
        match = re.search(r'(20\d{2})年', question)
        return match.group(1) if match else None

    def period_bounds(self, question):
        """Parse either a single date/month prefix or an explicit date range [start, end_excl)."""
        today = date.today()
        norm = re.sub(r'(\d{4})[年/](\d{1,2})[月/](\d{1,2})[日号]?',
                      lambda m: f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}', question)
        m_range = re.search(r'(20\d{2}-\d{2}-\d{2})\s*(?:到|至|~|—|-)\s*(20\d{2}-\d{2}-\d{2})', norm)
        if m_range:
            s_d = date.fromisoformat(m_range.group(1))
            e_d = date.fromisoformat(m_range.group(2))
            if s_d > e_d:
                raise ValueError('开始日期不能晚于结束日期')
            return ('range', s_d.isoformat(), (e_d + timedelta(days=1)).isoformat())
        m_md_range = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})[日号]?\s*(?:到|至|~|—|-)\s*(\d{1,2})月(\d{1,2})[日号]?', norm)
        if m_md_range:
            try:
                s_d = date(today.year, int(m_md_range.group(1)), int(m_md_range.group(2)))
                e_d = date(today.year, int(m_md_range.group(3)), int(m_md_range.group(4)))
            except ValueError as exc:
                raise ValueError('日期无效') from exc
            if s_d > e_d:
                raise ValueError('开始日期不能晚于结束日期')
            return ('range', s_d.isoformat(), (e_d + timedelta(days=1)).isoformat())
        m_days = re.search(r'(?:最近|近)\s*([1-9]\d?)\s*[天日]', norm)
        if m_days:
            n_days = int(m_days.group(1))
            return ('range', (today - timedelta(days=n_days - 1)).isoformat(), (today + timedelta(days=1)).isoformat())
        if re.search(r'(?:最近一周|近一周|过去一周|近7日)', norm):
            return ('range', (today - timedelta(days=6)).isoformat(), (today + timedelta(days=1)).isoformat())
        if re.search(r'(?:最近一个月|最近一月|近一个月|近一月|近30天|近1个月)', norm):
            return ('range', (today - timedelta(days=29)).isoformat(), (today + timedelta(days=1)).isoformat())
        single = self.period(question)
        return ('prefix', single, None) if single else None

    _AIRLINE_ALIASES = {
        '南方航空': 'CZ', '中国南方航空': 'CZ', '南航': 'CZ',
        '东方航空': 'MU', '中国东方航空': 'MU', '东航': 'MU',
        '中国国航': 'CA', '国际航空': 'CA', '国航': 'CA',
        '海南航空': 'HU', '海航': 'HU',
        '厦门航空': 'MF', '厦航': 'MF',
        '深圳航空': 'ZH', '深航': 'ZH',
        '四川航空': '3U', '川航': '3U',
        '山东航空': 'SC', '山航': 'SC',
        '春秋航空': '9C', '春秋': '9C',
        '吉祥航空': 'HO', '吉祥': 'HO',
        '上海航空': 'FM', '上航': 'FM',
        '首都航空': 'JD', '桂林航空': 'GT', '长龙航空': 'GJ',
        '祥鹏航空': '8L', '天津航空': 'GS', '奥凯航空': 'BK',
    }

    @staticmethod
    def _is_ops_catalog(catalog):
        return (
            'std_flight_core' in catalog.entities
            or 'std_flight_production' in catalog.entities
            or bool(getattr(catalog, 'scenarios', {}))
            or bool(getattr(catalog, 'concepts', {}))
        )

    def _infer_ops_subject(self, question, catalog):
        if not self._is_ops_catalog(catalog):
            return None
        if hasattr(catalog, 'resolve_ontology_hints'):
            hints = catalog.resolve_ontology_hints(question)
            for sc in hints.get('scenarios', []):
                mcode = sc.get('metric_code')
                if mcode and mcode in getattr(catalog, 'metrics', {}):
                    tbl = catalog.metrics[mcode].get('base_table') or 'std_flight_core'
                    if tbl in catalog.entities:
                        return tbl
                if 'std_flight_core' in catalog.entities:
                    return 'std_flight_core'
            for c in hints.get('concepts', []):
                for cm in getattr(catalog, 'concept_metrics', []) or []:
                    if cm.get('concept_code') == c.get('code'):
                        mcfg = catalog.metrics.get(cm.get('metric_code') or '')
                        tbl = (mcfg or {}).get('base_table') or 'std_flight_core'
                        if tbl in catalog.entities:
                            return tbl
                if 'std_flight_core' in catalog.entities:
                    return 'std_flight_core'
        if 'std_flight_production' in catalog.entities and any(w in question for w in ('货物重量', '邮件重量', '行李重量', '货邮行')):
            return 'std_flight_production'
        if 'mv_acdm_dyn_flight_detail' in catalog.entities and any(w in question for w in (
            '正常率', '准点率', '上座率', '货邮吞吐量', '旅客吞吐量', '计划航班', '执行航班', '执行率',
            '始发', '短延误', '放行延误', '起飞延误', '到港延误', '滑入', '滑出', '撤轮挡', '撤轮档', '关舱门', '开舱门', '外保障',
        )):
            return 'mv_acdm_dyn_flight_detail'
        if 'mv_acdm_turnaround_pair' in catalog.entities and any(w in question for w in ('平均保障时长', '过站保障时长', '保洁', '配餐', '加油', '除冰', '下客', '计划过站不足', '过站不足')):
            return 'mv_acdm_turnaround_pair'
        if 'ops_ask_views.flight_hourly_movements' in catalog.entities and any(w in question for w in ('高峰小时', '高峰时段', '小时架次', '起降架次')):
            return 'ops_ask_views.flight_hourly_movements'
        if 'std_flight_core' in catalog.entities and (
            any(a in question for a in self._AIRLINE_ALIASES)
            or re.search(r'(?<![A-Za-z0-9])[A-Z0-9]{2}\d{2,4}[A-Z]?(?![A-Za-z0-9])', question.upper())
            or any(w in question for w in ('出港', '进港', '离港', '到港', '延误', '准点率', '靠桥率', '机位', '登机口', '航站楼', '共享航班', '航班'))
        ):
            return 'std_flight_core'
        return None

    def _enrich_ops_plan(self, question, catalog, subject, intent, metric, group_by, conditions):
        """Enrich flight/production query plans using published concepts, synonyms, scenarios & ask settings."""
        if not self._is_ops_catalog(catalog):
            return intent, metric, group_by, conditions

        hints = catalog.resolve_ontology_hints(question) if hasattr(catalog, 'resolve_ontology_hints') else {}
        filters_map = getattr(catalog, 'filters', {}) or {}

        def has_col(col_name):
            return any(c.get('col') == col_name for c in conditions)

        def add_cond(table_name, col_name, op, val):
            if col_name in filters_map or col_name in catalog.entities.get(table_name, {}).get('attributes', {}):
                if not has_col(col_name):
                    conditions.append({'table': table_name, 'col': col_name, 'op': op, 'value': val})

        # 1. Direction cues & Home Airport disambiguation + city filters
        explicit_dir, origin_city, dest_city, _home_terms, _ = extract_home_airport_city_and_dir(question, catalog)
        if explicit_dir:
            add_cond(subject, 'direction', '=', explicit_dir)
        if origin_city:
            col_key = 'originCity' if 'originCity' in filters_map else ('origin_city' if 'origin_city' in catalog.entities.get(subject, {}).get('attributes', {}) else '')
            if col_key:
                add_cond(subject, col_key, '=', origin_city)
        if dest_city:
            col_key = 'destCity' if 'destCity' in filters_map else ('dest_city' if 'dest_city' in catalog.entities.get(subject, {}).get('attributes', {}) else '')
            if col_key:
                add_cond(subject, col_key, '=', dest_city)

        # 3. Airline, Aircraft Type & Flight No / Codeshare slots
        for a_name, a_code in sorted(self._AIRLINE_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if a_name in question:
                col_key = 'airline_code' if 'airline_code' in catalog.entities.get(subject, {}).get('attributes', {}) else 'airlineCode'
                add_cond(subject, col_key, '=', a_code)
                break
        m_actype = re.search(r'(?<![A-Za-z0-9])(B7[0-9A-Z]{2}|A3[0-9A-Z]{2}|A2[01]N|C919|ARJ21|E19[05])(?![A-Za-z0-9])', question.upper())
        if m_actype:
            act_col = 'craft_type_icao_cd' if 'craft_type_icao_cd' in catalog.entities.get(subject, {}).get('attributes', {}) else 'aircraft_type'
            add_cond(subject, act_col, '=', m_actype.group(1))
        m_fno = None if m_actype else re.search(r'(?<![A-Za-z0-9])([A-Z]{2}\d{2,4}[A-Z]?|[A-Z]\d\d{2,4}|\d[A-Z]\d{2,4})(?![A-Za-z0-9])', question.upper())
        if m_fno and 'flight_no' in catalog.entities.get(subject, {}).get('attributes', {}):
            fno = m_fno.group(1)
            if '共享' in question and 'shareFlightNo' in filters_map:
                add_cond(subject, 'shareFlightNo', '=', fno)
            else:
                add_cond(subject, 'flight_no', '=', fno)

        # 3b. A-CDM standardized attribute / agency / task / stand-type / origin cues
        metrics_map = getattr(catalog, 'metrics', {}) or {}
        if '地服' in question and metric in ('takeoff_on_time_rate', 'dep_on_time_rate') and 'ground_takeoff_on_time_rate' in metrics_map:
            metric = 'ground_takeoff_on_time_rate'
        if '始发' in question and metric in ('takeoff_on_time_rate', 'dep_on_time_rate') and 'origin_dep_on_time_rate' in metrics_map:
            metric = 'origin_dep_on_time_rate'
        if '过站' in question and '放行' in question and 'pair_release_on_time_rate' in metrics_map:
            metric = 'pair_release_on_time_rate'
        if any(w in question for w in ('全量靠桥', '进出港靠桥', '全口径靠桥', '双边靠桥')) and 'bridge_stand_rate_all' in metrics_map:
            metric = 'bridge_stand_rate_all'
        if any(w in question for w in ('纯FPA', '航线统计旅客', '纯航线')) and 'pax_fpa_sum' in metrics_map:
            metric = 'pax_fpa_sum'
        if any(w in question for w in ('短延误', '1-5分钟', '1~5分钟', '5分钟以内')) and 'short_delay_5m_count' in metrics_map:
            metric = 'short_delay_5m_count'

        eff_target = (metrics_map.get(metric, {}).get('base_table') if metric and metric in metrics_map else None) or subject
        eff_attrs = catalog.entities.get(eff_target, {}).get('attributes', {})
        if any(w in question for w in ('地服代理', '机场代理')) or ('地服' in question and metric not in ('ground_takeoff_on_time_rate',)):
            if 'agency' in eff_attrs:
                add_cond(eff_target, 'agency', '=', '210')
        attr_hits = [
            ('2401', '国际'), ('2402', '地区'), ('2402', '港澳台'),
            ('2403', '国内'), ('2404', '混合'),
        ]
        matched_attr_codes = {code for code, kw in attr_hits if kw in question}
        if len(matched_attr_codes) == 1:
            code = next(iter(matched_attr_codes))
            if 'attribute' in eff_attrs:
                add_cond(eff_target, 'attribute', '=', code)
            elif 'flight_attribute' in eff_attrs:
                add_cond(eff_target, 'flight_attribute', '=', code)
        if '正班' in question and 'flight_task_cd' in eff_attrs:
            add_cond(eff_target, 'flight_task_cd', '=', 'W/Z')
        if '始发' in question and metric not in ('origin_dep_on_time_rate', 'origin_flight_count') and 'ori_act' in eff_attrs:
            add_cond(eff_target, 'ori_act', '=', 'Y')
        if '外保障' in question or ('远机位' in question and '近机位' not in question):
            if 'craft_site_is_remote' in eff_attrs and metric not in ('remote_stand_flights',):
                add_cond(eff_target, 'craft_site_is_remote', '=', 'Y')
        elif '近机位' in question and '远机位' not in question and metric not in ('bridge_stand_rate', 'bridge_stand_rate_all'):
            if 'craft_site_is_remote' in eff_attrs:
                add_cond(eff_target, 'craft_site_is_remote', '=', 'N')

        # 4. Concept / Synonym -> Metric & Scenario selection
        top_sc = (hints.get('scenarios') or [None])[0]
        is_list_ask = any(w in question for w in ('有哪些', '列表', '清单', '明细', '哪几班', '查询航班', '航班明细', '查一下')) or (
            m_fno is not None and not any(w in question for w in ('多少', '总数', '几班', '几架', '率', '总量', '趋势', '排名'))
        )
        if is_list_ask:
            metric = None
        else:
            if explicit_dir == 'ARR' and metric == 'dep_delay_count' and 'arr_delay_count' in getattr(catalog, 'metrics', {}):
                metric = 'arr_delay_count'
            elif explicit_dir == 'DEP' and metric == 'arr_delay_count' and 'dep_delay_count' in getattr(catalog, 'metrics', {}):
                metric = 'dep_delay_count'
        if not is_list_ask and (not metric or metric in ('flight_count', 'flight_total_count')):
            # Delay & Cancel explicit concept resolution (direction-sensitive via ont_concept_metric)
            concept_codes = [c['code'] for c in hints.get('concepts', [])]
            if 'FlightDelay' in concept_codes or '延误' in question or '晚点' in question:
                cand_code = 'arr_delay_count' if explicit_dir == 'ARR' else 'dep_delay_count'
                if cand_code in getattr(catalog, 'metrics', {}):
                    metric = cand_code
                elif 'is_delayed' in filters_map:
                    add_cond(subject, 'is_delayed', '=', True)
            elif 'cancel_count' in getattr(catalog, 'metrics', {}) and '取消' in question:
                metric = 'cancel_count'
            elif top_sc and top_sc.get('metric_code') and top_sc['metric_code'] in getattr(catalog, 'metrics', {}):
                metric = top_sc['metric_code']
            elif group_by and subject == 'std_flight_core' and 'flight_count' in getattr(catalog, 'metrics', {}):
                metric = 'flight_count'
            elif concept_codes:
                resolved_cm = resolve_concept_metric(catalog, concept_codes, explicit_dir)
                if resolved_cm:
                    metric = resolved_cm

        # 5. Apply scenario dimensions & default_filters safely when metric/scenario resolved
        if is_list_ask and any(w in question for w in ('延误', '晚点')) and 'is_delayed' in filters_map:
            add_cond(subject, 'is_delayed', '=', True)
        if top_sc and not is_list_ask:
            for d in top_sc.get('dimensions') or []:
                if d in getattr(catalog, 'dimensions', {}) and d not in group_by:
                    group_by.append(d)
            for dk, dv in (top_sc.get('default_filters') or {}).items():
                if dk == 'direction' and explicit_dir:
                    continue
                if dk == 'is_local_leg' and metric and (catalog.metrics.get(metric, {}).get('base_table') or subject) != 'std_flight_production':
                    continue
                add_cond(subject, dk, '=', dv)
        if metric and not is_list_ask:
            intent = 'metric'
        elif is_list_ask:
            intent = 'detail'
            metric = None
        final_target = (metrics_map.get(metric, {}).get('base_table') if intent == 'metric' and metric and metric in metrics_map else None) or subject
        apply_home_airport_scope(catalog, final_target, conditions)
        return intent, metric, group_by, conditions

    def rules(self, question):
        catalog = self.catalog
        from app.acdm_ontology import flight_sequence, parse_question as parse_local
        if flight_sequence(question, catalog):
            return parse_local(question, catalog)
        candidates = catalog.retrieve(question)
        names = self.names(question)
        surname = self.surname(question)
        if not surname and re.search(r'姓氏|姓(?!名)', question):
            raise ValueError('检测到姓氏表达但无法安全解析，请使用“姓夏的员工”或“夏姓员工”等明确格式')
        explicit = [table for table in candidates if any(alias.lower() in question.lower()
                    for alias in catalog.entities[table]['aliases'])]
        if self._is_ops_catalog(catalog) and explicit and explicit[0] in ('dim_airline', 'std_flight_link', 'dim_stand', 'dim_gate', 'dim_airport', 'dim_aircraft'):
            ops_root = self._infer_ops_subject(question, catalog)
            if ops_root and ops_root in explicit:
                explicit = [ops_root] + [t for t in explicit if t != ops_root]
            elif ops_root and any(w in question for w in ('航班', '排名', '趋势', '统计', '架次', '共享', '延误', '动态', '多少')):
                explicit = [ops_root] + explicit
        subject = explicit[0] if explicit else (names[0][0] if names else None)
        if not subject and surname:
            people = [table for table, spec in catalog.entities.items() if spec.get('name_lookup')]
            subject = people[0] if len(people) == 1 else None
        if not subject:
            roots = [root for root in catalog.entities if catalog.relation_targets(question, root)]
            if len(roots) > 1:
                preferred = [root for root in roots if any(
                    cfg.get('default_unscoped') and any(a in question for a in cfg.get('aliases', []))
                    for cfg in catalog.overrides.get('relations', {}).get(root, {}).values())]
                roots = preferred or roots
            if len(roots) == 1:
                subject = roots[0]
        company = self.company(question)
        if not subject and company:
            roots = [table for table, spec in catalog.entities.items() if spec.get('enterprise_lookup')]
            subject = roots[0] if len(roots) == 1 else None
        if not subject and hasattr(catalog, 'metrics') and catalog.metrics:
            for m_key, m_cfg in catalog.metrics.items():
                aliases = m_cfg.get('aliases', []) + [m_cfg.get('name', ''), m_key]
                if any(a and a in question for a in aliases):
                    subject = m_cfg.get('base_table') or 'std_flight_core'
                    if subject in catalog.entities:
                        break
        if not subject:
            subject = self._infer_ops_subject(question, catalog)
        if not subject:
            raise ValueError('尚未匹配到业务对象，请说明资金、会议或其他具体业务；也可在本体管理中补充别名')
        spec = catalog.entities[subject]
        focus = catalog.relation_targets(question, subject)
        intent = ('sum' if re.search(r'总金额|总额|金额合计|工资合计|工资总额|实发合计|金额汇总|余额合计', question)
                  else 'count' if re.search(r'多少|几条|几份|几个|几名|几人|总数|数量|人数|笔数', question)
                  else 'detail')
        if intent == 'count' and '多少' in question and re.search(r'工资|薪酬|余额', question) and not re.search(r'条|人|记录|笔', question):
            intent = 'detail'
        if intent == 'sum' and any(attr.get('aggregation') == 'nonadditive' and
                any(alias in question for alias in attr.get('aliases', []))
                for attr in spec['attributes'].values()):
            raise ValueError('该字段是时点快照，不能逐笔相加；请明确账户和业务时点，或在本体中定义余额口径')
        target = focus[0] if intent in ('sum', 'count') and len(focus) == 1 else subject
        if intent in ('sum', 'count') and len(focus) > 1:
            intent = 'detail'
        metric = None
        group_by = []
        if hasattr(catalog, 'dimensions') and catalog.dimensions:
            group_by, _, _ = extract_acdm_dimensions_and_topn(question, catalog, target=target, allow_topn=False)

        global_metric = None
        if hasattr(catalog, 'metrics') and catalog.metrics:
            m_hits = []
            for m_key, m_cfg in catalog.metrics.items():
                aliases = m_cfg.get('aliases', []) + [m_cfg.get('name', ''), m_key]
                for a in aliases:
                    if a and a in question:
                        m_hits.append((len(a), m_key, m_cfg))
            if m_hits:
                best_hit = max(m_hits, key=lambda x: x[0])
                global_metric = best_hit[1]
                global_cfg = best_hit[2]

        if global_metric:
            metric = global_metric
            intent = 'metric'
            target = global_cfg.get('base_table') or subject
        elif intent == 'sum':
            metrics = catalog.entities[target]['metrics']
            matches = [(len(alias), key) for key, cfg in metrics.items()
                       for alias in cfg.get('aliases', []) if alias in question]
            metric = max(matches)[1] if matches else None
        conditions = []
        bindings = spec['bindings']
        if surname:
            if not spec.get('name_lookup') or not bindings.get('name'):
                raise ValueError('姓氏筛选需要明确人员档案及姓名字段映射，请指定员工或人员业务对象')
            conditions.append({'col': bindings['name'], 'op': 'prefix', 'value': surname})
        for table, name in names:
            if table != subject:
                catalog.path(subject, table)
            field = catalog.entities[table]['bindings'].get('name')
            if field:
                conditions.append({'table': table, 'col': field, 'op': '=', 'value': name})
        if company:
            field = bindings.get('company')
            if not field:
                raise ValueError('当前业务对象缺少企业身份映射，请先配置 company 绑定')
            conditions.append({'col': field, 'op': '=', 'value': company})
        intent, metric, group_by, conditions = self._enrich_ops_plan(
            question, catalog, subject, intent, metric, group_by, conditions
        )
        if hasattr(catalog, 'metrics'):
            if metric in (None, 'flight_count') and any(g in ('attribute', 'exec_date', 'operation_plan_day', 'operation_exec_day', 'stand_type') for g in group_by) and 'operated_flight_count' in catalog.metrics and '按' in question:
                metric = 'operated_flight_count'
                intent = 'metric'
        if metric and metric in getattr(catalog, 'metrics', {}):
            m_target = catalog.metrics[metric].get('base_table') or target
            if m_target == 'ops_ask_views.flight_hourly_movements' and 'peak_hour' in getattr(catalog, 'dimensions', {}):
                group_by = list(dict.fromkeys('peak_hour' if g == 'hour' else g for g in group_by))
            if m_target in catalog.entities:
                target = m_target
                if intent == 'metric' and subject != m_target:
                    subject = m_target
                    spec = catalog.entities[subject]
                    bindings = spec['bindings']
                    for c in conditions:
                        if c.get('table') and c['table'] != m_target and c.get('col') in (
                            list(getattr(catalog, 'filters', {}) or {}) + list(spec['attributes'].keys())
                        ):
                            c['table'] = m_target
        bounds = self.period_bounds(question) if hasattr(self, 'period_bounds') else None
        time_table = target if intent in ('sum', 'count', 'metric') else subject
        metric_df = (catalog.metrics.get(metric, {}) or {}).get('date_field') if metric and hasattr(catalog, 'metrics') else None
        time_attrs = catalog.entities[time_table]['attributes']
        if metric_df and metric_df in time_attrs:
            time_field = metric_df
        else:
            time_field = catalog.entities[time_table]['bindings'].get('time') or ('flight_date' if 'flight_date' in time_attrs else None)
        if bounds:
            if not time_field:
                raise ValueError('该业务尚未定义业务时间字段，不能用入库时间代替，请补充时间口径')
            kind, p_start, p_end = bounds
            if kind == 'range':
                conditions.append({'table': time_table, 'col': time_field, 'op': '>=', 'value': p_start})
                conditions.append({'table': time_table, 'col': time_field, 'op': '<', 'value': p_end})
            else:
                conditions.append({'table': time_table, 'col': time_field, 'op': 'prefix', 'value': p_start})
        already_has_fno = any(c.get('col') in ('flight_no', 'shareFlightNo') for c in conditions)
        already_has_actype = any(c.get('col') in ('aircraft_type', 'craft_type_icao_cd') for c in conditions)
        code = re.search(r'(?<!\w)([A-Za-z][A-Za-z0-9()（）/_-]{3,})(?!\w)', question)
        if code and any(ch.isdigit() for ch in code.group(1)) and not already_has_fno and not (already_has_actype and any(c['value'] == code.group(1).upper() for c in conditions if c.get('col') in ('aircraft_type', 'craft_type_icao_cd'))):
            code_field = bindings.get('code') or bindings.get('flight_no')
            if not code_field:
                raise ValueError('当前业务没有编号字段映射')
            conditions.append({'col': code_field, 'op': '=', 'value': code.group(1)})
        title = re.search(r'(?:名称为|名称是|标题为|标题是|名为)[“"\s]*([^”"，。]+)', question)
        if title:
            if not bindings.get('name'):
                raise ValueError('当前业务没有名称字段映射')
            title_value=re.split(r'有多少|共有多少|的情况|的明细',title.group(1),maxsplit=1)[0].strip()
            conditions.append({'col': bindings['name'], 'op': 'contains', 'value': title_value})
        threshold = re.search(r'(大于|超过|高于|小于|低于|不足)\s*(\d+(?:\.\d+)?)\s*(亿元|万元|万|元)?', question)
        non_money_unit = threshold and not threshold.group(3) and re.match(r'^\s*(?:分钟|分|小时|时|天|架次|班|%|个)', question[threshold.end():])
        if threshold and not non_money_unit:
            metrics = spec['metrics']
            defaults = [cfg for cfg in metrics.values() if cfg.get('default')]
            if len(defaults) != 1:
                raise ValueError('金额比较需要明确指标字段和单位')
            cfg = defaults[0]
            scale = {'元': Decimal(1), '万': Decimal(10000), '万元': Decimal(10000), '亿元': Decimal(100000000)}
            value = Decimal(threshold.group(2))
            if threshold.group(3):
                if cfg.get('unit') not in scale:
                    raise ValueError('指标没有可转换的金额单位')
                value *= scale[threshold.group(3)] / scale[cfg['unit']]
            conditions.append({'col': cfg['field'], 'op': '>' if threshold.group(1) in ('大于','超过','高于') else '<', 'value': value})
        bucket = None
        by_time = re.search(r'按(月份|月|年度|年|日期|天)', question)
        if by_time and not (intent == 'metric' and any(g in ('date', 'month', 'week') for g in group_by)):
            if not time_field:
                raise ValueError('当前业务缺少用于分组的业务时间字段')
            bucket = {'field': time_field, 'grain': 'month' if by_time.group(1) in ('月份','月')
                      else 'year' if by_time.group(1) in ('年度','年') else 'day'}
        extra_opts = {}
        if intent == 'metric' and group_by:
            _, extra_opts, _ = extract_acdm_dimensions_and_topn(question, catalog, target=target, allow_topn=True)
        return {'subject': subject, 'disp': spec['name'], 'intent': intent,
                'conditions': conditions, 'focus_tables': focus,
                'aggregate_target': target, 'metric': metric, 'group_by': group_by, 'time_bucket': bucket, **extra_opts}

    def parse(self, question, previous_plan=None):
        # ACDM holistic reports / daily briefings fast-path (must precede turnaround keyword check)
        rpt_plan = match_acdm_report(question, self.catalog, bounds_fn=self.period_bounds)
        if rpt_plan:
            if self.trace:
                self.trace.start('ontology', '匹配业务本体', '识别 A-CDM 专题报告与口径规则')
                self.trace.finish('ontology', '专题报告匹配完成', [f"{rpt_plan['disp']}（{rpt_plan['report_start']} ~ {rpt_plan['report_end']}）"])
                self.trace.start('intent', '解析查询条件', '锁定手册时间窗与多维度统计口径')
                self.trace.finish('intent', '报告模板与边界参数确定', [f"类型：{rpt_plan['report_kind']}", f"周期：{rpt_plan['report_period_type']}"])
                self.trace.start('model', '模型辅助解析', '专题报告固定使用校准 SQL 模板，无需模型改写')
                self.trace.finish('model', '报告模板已由规则锁定', state='skipped')
            return rpt_plan, '本体规则·A-CDM 专题报告'
        # ACDM turnaround fast-path check
        turnaround_keywords = ('过站', '保障', 'acdm', '时序', '甘特', '落后于计划', '落后', '超计划', '逾期', '时隙', 'aibt', 'sobt', 'tobt', 'tsat')
        q_lower = question.lower()
        if any(k in q_lower for k in turnaround_keywords) or any(k in question for k in ('配餐', '保洁', '加油', '装卸', '登机')):
            from app.turnaround_standard import turnaround_target
            fno, flight_date = turnaround_target(question)
            if fno:
                from app.turnaround import get_turnaround_data
                t_data = get_turnaround_data(fno, flight_date)
                if t_data:
                    subj = t_data.get('source_table') or ('std_flight_core' if 'std_flight_core' in self.catalog.entities else next(iter(self.catalog.entities)))
                    id_col = self.catalog.entities[subj].get('bindings', {}).get('flight_no', 'flight_no')
                    if id_col not in self.catalog.entities[subj].get('attributes', {}):
                        id_col = next(iter(self.catalog.entities[subj]['attributes'].keys()))
                    if self.trace:
                        self.trace.start('ontology', '匹配业务本体', '检索当前数据库表注释、业务对象与管理别名')
                        self.trace.finish('ontology', '本体检索完成', [t_data.get('source_label') or 'acdm.tb_dyn_flight'])
                        self.trace.start('intent', '解析查询条件', '过站保障时序、作业节点状态与 ACDM 时隙')
                        self.trace.finish('intent', '本体规则解析完成', [
                            f'航班连班：{t_data["flight_pair"]}',
                            f'飞机号：{t_data["craft_no"]} | 机位：{t_data["stand"]}',
                            f'当前时间：{t_data["milestones"].get("current_time")}',
                            f'诊断结论：{t_data["conclusion"]["summary"]}'
                        ])
                        self.trace.start('model', '模型辅助解析', '全部条件已由 ACDM 节点规则确定')
                        self.trace.finish('model', '过站保障时序已由本体规则确定，无需模型改写 SQL', state='skipped')
                    plan = {
                        'subject': subj,
                        'intent': 'turnaround',
                        'disp': f'航班过站保障 · {t_data["flight_pair"]}',
                        'flight_no': t_data.get('out_flight') or t_data.get('in_flight') or fno,
                        'query_identifier': fno,
                        'flight_date': flight_date,
                        'flight_id': t_data.get('flight_id'),
                        'focus_tables': ['std_flight_process'] if t_data.get('process_row') else [],
                        'conditions': ([{'col': t_data.get('id_column') or 'id', 'op': '=', 'value': t_data['flight_id']}]
                                       if t_data.get('flight_id') is not None else
                                       [{'col': id_col, 'op': 'notnull', 'value': None}]),
                        'turnaround_data': t_data
                    }
                    return self.compiler.validate(plan), '本体规则·过站保障'

        if previous_plan:
            from app.agent.follow_up import FollowUpNormalizer
            normalizer = FollowUpNormalizer()
            follow_up = normalizer.apply(question, previous_plan, self.catalog, self)
            if follow_up:
                if self.trace:
                    self.trace.start('follow_up', '追问上下文继承', '基于上一轮查询计划继承主体与条件，仅差量置换当前槽位')
                    details = ['继承主体：' + follow_up.get('disp', follow_up['subject']) + ' · ' + follow_up['subject'],
                               '查询方式：' + {'detail':'信息明细', 'count':'记录计数', 'sum':'金额合计', 'metric':'指标统计'}.get(follow_up.get('intent', 'detail'), '信息明细'),
                               '最终条件：' + json.dumps(follow_up.get('conditions', []), ensure_ascii=False)]
                    self.trace.finish('follow_up', '已根据多轮追问上下文生成完整计划', details)
                return self.compiler.validate(follow_up), '多轮追问·槽位继承'
        if self._is_ops_catalog(self.catalog):
            has_named_entity = False
            try:
                if callable(getattr(self, 'names', None)):
                    has_named_entity = bool(self.names(question))
                elif isinstance(getattr(self, 'names', None), dict):
                    has_named_entity = any(
                        isinstance(n, str) and len(n) >= 2 and n in question
                        for names in self.names.values()
                        if isinstance(names, (set, list, tuple))
                        for n in names
                    )
            except Exception:
                has_named_entity = False
            if not has_named_entity and hasattr(self.catalog, 'has_ontology_match') and not self.catalog.has_ontology_match(question, previous_plan=previous_plan):
                if self.trace:
                    self.trace.start('ontology', '匹配业务本体', '检索当前数据库表注释、业务对象与管理别名')
                    self.trace.finish('ontology', '未匹配到业务本体对照', ['问题不在当前授权业务本体范围内'])
                return {
                    'outcome': 'unsupported',
                    'disp': '未匹配业务本体',
                    'subject': '',
                    'intent': 'notice',
                    'conditions': [],
                    'answer': '当前输入问题未匹配到业务本体对照，回复回复不了。请说明具体业务对象、指标或管理规则后提问。',
                }, '本体规则·无匹配'
        local, local_error = None, None
        if self.trace:
            self.trace.start('ontology', '匹配业务本体', '检索当前数据库表注释、业务对象与管理别名')
        try:
            local = self.rules(question)
        except ValueError as exc:
            local_error = str(exc)
        if self.trace:
            candidates = self.catalog.retrieve(question)
            self.trace.finish('ontology', '本体检索完成',
                              [self.catalog.entities[t]['name'] + ' · ' + t for t in candidates])
            self.trace.start('intent', '解析查询条件', '解析企业身份、姓名或姓氏、时间与统计方式')
        if local_error and not local_error.startswith('尚未匹配到业务对象'):
            raise ValueError(local_error)
        if self.trace:
            details = []
            if local:
                details = ['主体：' + local['disp'] + ' · ' + local['subject'],
                           '查询方式：' + {'detail':'信息明细', 'count':'记录计数', 'sum':'金额合计', 'metric':'指标统计'}.get(local['intent'], '指标统计')]
                for c in local['conditions']:
                    table = c.get('table') or local['subject']
                    attr = self.catalog.attribute(table, c['col'])
                    description = ('以“%s”开头' % c['value'] if c['op'] == 'prefix' else
                                   '包含“%s”' % c['value'] if c['op'] == 'contains' else
                                   '%s %s' % (c['op'], c['value']))
                    details.append(attr['label'] + '：' + description)
            self.trace.finish('intent', '本体规则解析完成' if local else '需要模型辅助解析', details)
        if local and (local.get('flight_sequence') or self.complete_identity_query(question, local) or (local.get('metric') and local.get('intent') == 'metric')):
            if self.trace:
                self.trace.start('model', '模型辅助解析', '检查是否需要模型补充查询计划')
                label = '全部条件已由本体规则确定，无需模型改写 SQL' if not local.get('metric') else '指标统计已由本体规则确定，无需模型改写 SQL'
                self.trace.finish('model', label, state='skipped')
            tag = '本体规则·运行指标' if local.get('metric') else ('本体规则·姓氏筛选' if self.surname(question) else '本体规则·精确身份查询')
            return self.compiler.validate(local), tag
        if self.chat:
            extra = [table for table, _ in self.names(question)]
            context = self.catalog.context(question, extra)
            if not context and local:
                context = self.catalog.context(question, [local['subject']])
            if context:
                if self.trace:
                    self.trace.start('model', '模型辅助解析', '仅向模型提供相关本体，生成受约束的结构化查询计划')
                prompt = (
                    '将问题转成结构化查询计划，只输出 JSON。仅使用提供的本体表、字段、关系和指标。'
                    '不能遗漏问题的筛选条件；不能选择不相关主体。缺少映射或存在歧义时填写 unresolved。'
                    'conditions 中 table 可指定相关表；币种和业务时间必须遵从本体，创建时间不能代替业务时间。'
                    'intent=detail|count|sum；aggregate_target 必须填本体中的表名，绝不能填 id 或字段名；metric 使用定义的指标键。'
                    '区分筛选锚点与分析粒度：subject 保留已确定身份/企业筛选的锚点；'
                    '统计关联对象时用 aggregate_target，查询关联明细时用 focus_tables，不能因粒度不同而丢弃锚点条件。'
                    '问任职、付款节点等具体关系时填写 focus_tables。数值和日期必须来自问题。'
                    '不得默认增加问题未要求的审批状态、任职状态或其他筛选。'
                    '按月/年/日分组时 time_bucket={field:业务时间字段,grain:month|year|day}。'
                    'subject 必须是相关本体 JSON 的表名键，不可填 Employee 等概念名或中文名。'
                    'op 仅允许 =、!=、>、>=、<、<=、prefix、contains、in、null、notnull。'
                    '姓氏用姓名字段 prefix，value 只填姓氏，例如夏，不带 %；不输出 like。'
                    '返回 {subject,intent,conditions:[{table,col,op,value}],focus_tables,aggregate_target,metric,group_by,time_bucket,unresolved}。'
                    '当前日期：' + date.today().isoformat() +
                    ('\n当前是本地数据副本，覆盖范围：' + json.dumps(self.catalog.overrides['coverage'],ensure_ascii=False) +
                     '。覆盖范围外或跨出范围的统计不能当作全库结果；需要全量历史数据时填写 unresolved。' if self.catalog.overrides.get('coverage') else '') +
                    '\n相关本体：' + json.dumps(context, ensure_ascii=False))
                try:
                    content = self.chat(prompt, question, max_tokens=1200, timeout=40)
                    match = re.search(r'\{.*\}', content, re.S)
                    parsed = json.loads(match.group()) if match else None
                except Exception:
                    parsed = None
                if isinstance(parsed, dict) and parsed:
                    if not isinstance(parsed.get('subject'), str) or parsed['subject'] not in context:
                        if local and self.complete_identity_query(question, local):
                            if self.trace:
                                self.trace.finish('model', '模型主体无效；完整本体规则计划通过校验，采用规则计划')
                            return self.compiler.validate(local), '本体规则·主体纠偏'
                        raise ValueError('模型选择的主体不在本题相关本体内，请明确业务对象')
                    parsed['disp'] = self.catalog.entities[parsed['subject']]['name']
                    parsed.setdefault('intent', 'detail')
                    parsed.setdefault('conditions', [])
                    parsed.setdefault('focus_tables', [])
                    for key in ('conditions','focus_tables','group_by'):
                        if parsed.get(key) is None:
                            parsed[key]=[]
                    if not isinstance(parsed['conditions'], list) or any(not isinstance(c, dict) for c in parsed['conditions']):
                        raise ValueError('模型返回的筛选条件格式无效')
                    if not isinstance(parsed['focus_tables'], list):
                        raise ValueError('模型返回的关联目标格式无效')
                    self.normalize_conditions(parsed, question)
                    # A rule anchor and an explicitly requested relation target
                    # are different roles, not necessarily conflicting subjects.
                    if local and parsed['subject'] != local['subject']:
                        if self.complete_identity_query(question, local):
                            if self.trace:
                                self.trace.finish('model', '模型主体与确定身份不符；采用完整的本体规则计划')
                            return self.compiler.validate(local), '本体规则·主体纠偏'
                        parsed=reconcile_anchor(self.catalog,local,parsed)
                        if self.trace:
                            self.trace.start('plan_roles', '协调筛选锚点与分析粒度',
                                             '核验明确请求的关联目标；不改变条件所属表或扩大查询范围')
                            self.trace.finish('plan_roles', '关联目标已规范为锚点下的查询计划',
                                ['筛选锚点：'+local['subject'],
                                 '分析目标：'+parsed['plan_roles']['analysis_target']])
                    # Exact identity and explicit period constraints are deterministic.
                    if local and parsed['subject'] == local['subject']:
                        company_field=self.catalog.entities[local['subject']]['bindings'].get('company')
                        company_code=self.company(question)
                        if company_code and company_field:
                            lookup={**self.enterprise_names,**self.catalog.overrides.get('enterprise_aliases',{})}
                            aliases={alias for alias,code in lookup.items() if code==company_code and alias in question}
                            # Do not additionally compare a resolved abbreviation with a
                            # full company-name/snapshot field; the canonical key is sufficient.
                            parsed['conditions']=[c for c in parsed['conditions'] if not (
                                (c.get('table') or parsed['subject'])==parsed['subject'] and
                                c.get('col')!=company_field and isinstance(c.get('value'),str) and
                                c['value'] in aliases)]
                        # Explicit local intent and target fix the grain, including plain
                        # counts whose target is the root rather than its id field.
                        if local['intent'] in ('sum','count'):
                            parsed['intent']=local['intent']
                            parsed['aggregate_target']=local['aggregate_target']
                        elif parsed['intent']=='detail':
                            parsed['aggregate_target']=local['subject']
                        for condition in local['conditions']:
                            table = condition.get('table', local['subject'])
                            parsed['conditions'] = [c for c in parsed['conditions']
                                if (c.get('table') or parsed['subject'], c.get('col')) != (table, condition['col'])]
                            parsed['conditions'].append(condition)
                        parsed['focus_tables'] = list(dict.fromkeys(parsed['focus_tables'] + local['focus_tables']))
                        if local['metric']:
                            parsed['metric'] = local['metric']
                        if local['focus_tables'] and local['intent'] in ('sum','count'):
                            parsed['aggregate_target'] = local['aggregate_target']
                        if local.get('time_bucket'):
                            parsed['time_bucket'] = local['time_bucket']
                            parsed['group_by'] = [field for field in parsed.get('group_by', [])
                                                  if field != local['time_bucket']['field']]
                    resolved=local['conditions'] if local else []
                    for condition in parsed['conditions']:
                        if condition in resolved:
                            continue
                        value=condition.get('value')
                        if condition.get('op') in ('null','notnull'):
                            if not re.search(r'空|缺失|填写|有数据',question):
                                raise ValueError('模型添加了问题未要求的空值筛选，请明确筛选条件')
                            continue
                        values=value if isinstance(value,list) else [value]
                        attr=self.catalog.attribute(condition.get('table') or parsed['subject'],condition.get('col'))
                        enums=attr.get('enums',{})
                        if any(str(v) not in question and not (
                            str(v) in enums and str(enums[str(v)]) in question) for v in values):
                            raise ValueError('模型添加的筛选值不在问题或已解析身份中，请明确筛选条件')
                    validated = self.compiler.validate(parsed)
                    if local and self.surname(question) and not self.complete_identity_query(question, validated, allow_extra=True):
                        raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
                    if self.trace:
                        self.trace.finish('model', '结构化计划通过本体、字段与筛选值校验')
                    return validated, 'LLM·本体校验'
                if self.trace:
                    self.trace.finish('model', '模型未返回可用计划；检查本体规则能否安全接管', state='skipped')
                if local and self.surname(question) and not self.complete_identity_query(question, local):
                    raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
        elif self.trace:
            self.trace.start('model', '模型辅助解析', '检查模型配置')
            self.trace.finish('model', '模型未启用，使用本体规则', state='skipped')
        if local_error:
            raise ValueError(local_error)
        if local and self.surname(question) and not self.complete_identity_query(question, local):
            raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
        return self.compiler.validate(local), '本体规则'
