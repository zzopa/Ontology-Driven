"""Operations coordinator code tables, lexicons, regex patterns, and pure-rule helpers.

本模块集中维护运营问数总控（Coordinator）使用的所有码表值、同义词表、展示字段白名单与文本匹配辅助方法。
需要新增或调整码表值时，可直接编辑对应码表分组，或通过 `register_*` 方法动态扩展，无需改动 `coordinator.py` 主流程。
"""
from datetime import date, timedelta, timezone
import json
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 1. 核心业务常量与系统表键码表
# ---------------------------------------------------------------------------

BEIJING = timezone(timedelta(hours=8))
ACTUAL = 'ops_ask_views.flight_execution'
CORE = 'std_flight_core'
VERSION = 'ops-coordinator-v1'

FLIGHT_COUNT_METRICS: Tuple[str, ...] = ('flight_count', 'flight_total_count')

# 标识类槽位到候选数据表列名的映射码表（按优先级排序）
IDENTIFIER_COLUMN_MAP: Dict[str, Tuple[str, ...]] = {
    'flight': ('flight_no', 'FLIGHT_NO'),
    'aircraft': ('aircraft_reg', 'reg_no', 'CRAFT_NO'),
}

# 各业务表的“取消状态”筛选字段与枚举值码表
CANCEL_STATUS_MAP: Dict[str, Tuple[str, str]] = {
    'mv_acdm_dyn_flight_detail': ('abnormal_flight_stat_cd', 'CAN'),
    CORE: ('flight_status', '取消'),
}

# 属性追问时为特定字段补充的口语别名码表
EXTRA_ATTRIBUTE_FOLLOWUP_ALIASES: Dict[str, Tuple[str, ...]] = {
    'dest_airport': ('目的地', '到达机场'),
}

# 字面筛选自然语言运算符 -> 受控查询计划 op 码表
LITERAL_FILTER_OPERATORS: Dict[str, str] = {
    '等于': '=',
    '为': '=',
    '是': '=',
    '=': '=',
    '不等于': '!=',
    '大于': '>',
    '小于': '<',
    '包含': 'contains',
}

# 运营日 / 自然日模式码表
DAY_MODE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    'OPERATIONAL': ('运营日',),
    'CALENDAR': ('自然日',),
}

# 相对日期偏移码表：(触发词组, 距今日天数偏移)
RELATIVE_DAY_OFFSETS: Tuple[Tuple[Tuple[str, ...], int], ...] = (
    (('大前天',), 3),
    (('前天',), 2),
    (('昨天', '昨日', '前一天'), 1),
    (('今天', '今日'), 0),
)

# 澄清日期时默认提供给用户的快捷选项顺序
DEFAULT_PERIOD_LABELS: Tuple[str, ...] = ('昨天', '今天', '前天')

# 多轮追问中相对基准日加减一天的触发词码表
FOLLOWUP_DAY_SHIFT_WORDS: Dict[str, Tuple[str, ...]] = {
    'prev': ('上一天', '前一天', '前1天'),
    'next': ('下一天', '后一天', '后1天', '次日', '第二天'),
}


# ---------------------------------------------------------------------------
# 2. 方向、口径、首末班排序码表
# ---------------------------------------------------------------------------

DIRECTION_CODE_TABLE: Dict[str, Dict[str, object]] = {
    'DEP': {
        'label': '出港',
        'option_label': '出港',
        'keywords': ('出港', '离港', '起飞', '飞走', '离地', '出发'),
    },
    'ARR': {
        'label': '进港',
        'option_label': '进港',
        'keywords': ('进港', '到港', '落地', '降落', '抵达'),
    },
    'BOTH': {
        'label': '进出港',
        'option_label': '进出港合并比较',
        'keywords': ('进出港', '不分方向', '全部方向'),
    },
}

BASIS_CODE_TABLE: Dict[str, Dict[str, object]] = {
    'actual': {
        'label': '实际执行',
        'option_label': '实际执行（按实际起飞/落地）',
        'keywords': ('实际', '执飞', '实飞', '执行', '已飞', '飞走', '离地', '落地', '已起飞', '已到达'),
        'fallback_keywords': ('起飞', '降落'),
        'sync_replace_tokens': ('实际执飞', '实际执行', '实际航班', '实际', '执飞', '执行'),
    },
    'planned': {
        'label': '计划航班',
        'option_label': '计划航班（按计划业务日）',
        'keywords': ('计划', '排班', '预定', '预计'),
        'fallback_keywords': (),
        'sync_replace_tokens': ('计划航班', '计划', '排班', '预定'),
    },
}

SEQUENCE_CODE_TABLE: Dict[str, Dict[str, object]] = {
    'desc': {
        'label': '最后一班',
        'keywords': ('最后', '最晚', '最迟', '末班'),
    },
    'asc': {
        'label': '最早一班',
        'keywords': ('最早', '第一班', '首班'),
    },
}


# ---------------------------------------------------------------------------
# 3. 指标补充词表、纯展示字段码表、残差忽略词表（按类别分组，方便随时追加）
# ---------------------------------------------------------------------------

METRIC_RESIDUAL_TERMS: Dict[str, Tuple[str, ...]] = {
    'dep_delay_count': ('延误', '晚点'),
    'arr_delay_count': ('延误', '晚点'),
    'cancel_count': ('取消',),
    'avg_air_bridge_minutes': ('是多少分钟', '多少分钟', '几分钟'),
    'air_bridge_usage_count': (
        '登机桥使用总次数', '登机桥使用总次', '登机桥保障总次数', '登机桥保障总次',
        '登机桥使用次数', '登机桥使用', '登机桥', '廊桥使用总次数', '廊桥使用总次', '廊桥使用', '廊桥',
    ),
    'remote_stand_flights': (
        '远机位外保障', '外保障航班', '远机位航班', '远机位架次', '远机位', '外保障',
    ),
    'operated_flight_count': (
        '实际执行架次', '实际执行', '实际执飞', '执行架次', '实飞架次', '执飞架次',
        '执行航班', '实飞航班', '执飞航班', '航班量', '实际航班量', '执行航班量',
    ),
    'avg_turnaround_service_minutes': (
        '过站保障平均时长', '保障平均时长', '过站保障时长', '地面保障时长', '保障时长',
        '在地面', '在地上', '地面', '地上', '地面存放', '地面停放',
        '存放多久', '停放多久', '停了多久', '停留多久', '在地上停', '在地面停',
        '存放', '停放', '停留', '多久',
    ),
}

# 纯展示/投影子句允许出现的字段名码表（用于识别如“每次运行分别显示源航班ID、计划落地和实际落地时间”）
DISPLAY_FIELD_GROUPS: Dict[str, List[str]] = {
    'identity': [
        '源航班ID', '源航班id', '源ID', '源id', '航班ID', '航班id',
        '记录ID', '主ID', 'ID', 'id', '唯一标识',
        '航班号', '航班编号', '主飞航班号', '主飞航班', '共享航班号', '共享航班',
        '机号', '飞机号', '注册号', '航空器注册号', '机型', '航司', '航空公司', '承运人',
    ],
    'schedule_times': [
        '计划落地时间', '实际落地时间', '预计落地时间',
        '计划起飞时间', '实际起飞时间', '预计起飞时间',
        '计划到达时间', '实际到达时间', '预计到达时间',
        '计划出发时间', '实际出发时间', '预计出发时间',
        '计划进港时间', '实际进港时间', '计划出港时间', '实际出港时间',
        '计划撤轮挡时间', '实际撤轮挡时间', '计划靠位时间', '实际靠位时间',
        '计划落地', '实际落地', '预计落地',
        '计划起飞', '实际起飞', '预计起飞',
        '计划到达', '实际到达', '预计到达',
        '计划出发', '实际出发', '实际执飞落地', '实际执飞起飞',
        '落地时间', '起飞时间', '到达时间', '出发时间', '执行时间', '发生时间',
        '计划时刻', '实际时刻', '预计时刻', '落地时刻', '起飞时刻', '时刻', '时间',
    ],
    'route_and_resource': [
        '航线', '起降机场', '始发站', '目的站', '始发机场', '目的机场',
        '出发机场', '到达机场', '起飞机场', '落地机场', '前站', '后站',
        '机位', '停机位', '登机口', '航站楼', '跑道', '机坪', '廊桥', '登机桥',
    ],
    'status_and_ops': [
        '航班状态', '状态', '进出港', '方向', '航班属性', '航班性质', '任务性质',
        '业务日', '业务日期', '计划日期', '运行日期', '执行日期', '日期',
        '延误原因', '延误时长', '放行延误', '起飞延误', '落地延误',
        '过站时长', '保障时长', '代理', '地服代理',
    ],
}

# 残差检查中可安全忽略的通用词表（按语义分组，方便随时增删）
RESIDUAL_VOCABULARY_GROUPS: Dict[str, List[str]] = {
    'actions': [
        '请帮我', '帮我', '查一下', '查一查', '看一下', '查看', '查询',
        '列出', '显示', '展示', '统计', '请问', '请', '查',
        '给出', '给出各', '给出并', '给出和', '提供',
    ],
    'dates': [
        '大前天', '昨天', '昨日', '前一天', '前天', '今天', '今日',
        '最近一周', '近一周', '本月', '今年', '本年', '上半年', '下半年', '年', '月', '日', '号', '多久',
    ],
    'basis': [
        '实际', '执行了', '执行', '执飞', '计划', '排班', '预定',
    ],
    'directions': [
        '进出港', '出港', '进港', '离港', '到港', '起飞', '降落', '落地',
        '飞走', '离地', '从本场', '本场', '飞往', '前往', '出港去',
    ],
    'sequence': [
        '最晚', '最迟', '最后', '最早', '第一班', '首班', '末班',
        '哪一班', '哪班', '哪一个', '哪个',
        '最长', '最短', '时间最长', '时长最长', '延误最长', '延误时间最长', '延误时长',
    ],
    'pronouns': [
        '这一班', '那一班', '这个航班', '那个航班', '这个', '那个',
        '这班', '那班', '这趟', '那趟', '这架', '那架',
    ],
    'quantities': [
        '一下', '有哪些', '是哪些', '有什么', '有多少', '多少', '几条', '几家', '几班', '几架',
        '记录数', '条数', '数量', '总数', '总量', '共有', '总共', '一共',
        '合计', '全部', '所有', '共', '数',
        '占比', '占比是多少', '比例', '比例是多少', '所占比例', '占',
        '分钟', '多少分钟', '几分钟', '人', '多少人', '人次', '人数',
    ],
    'views': [
        '详细信息', '详情', '动态', '运行情况', '执行情况', '明细', '列表', '清单',
        '记录', '信息', '数据', '保障时序图', '保障时序', '保障甘特图', '时序图',
        '时序', '甘特图', '甘特', '鱼骨图', '时间轴', '时间线', '走势图', '走势',
        '趋势图', '图', '情况如何', '情况', '状况', '如何', '怎么样', '怎样',
        '降序', '升序', '倒序', '正序', '降序展示', '升序展示', '按降序', '按升序',
    ],
    'dimensions_and_entities': [
        '架次', '班次', '人次', '人数', '分布', '排名', '排行', '趋势',
        '各个', '各', '按', '小时', '航司', '航班', '飞机', '客机', '货机',
        '航班机', '客机航班', '客运航班', '班机',
        '主分类', '大类', '分类', '按分类', '分类展示', '按天', '按日',
        '靠桥', '靠桥架次', '近机位',
    ],
    'modifiers': [
        '分别统计', '分别查询', '分别显示', '分别', '各自',
    ],
    'particles': [
        '从', '由', '至', '的', '是', '有', '了', '个', '中', '里',
        '一个', '一班', '一条', '班', '条', '家', '吗', '呢', '在', '于', '并',
    ],
}

# 多轮追问中可剥离的口语虚词码表
FOLLOWUP_FILLER_TOKENS: Tuple[str, ...] = (
    '那', '那么', '其中', '换成', '改成', '换查', '改查', '再查', '再看', '只看', '仅看',
    '这个', '那个', '这一班', '那一班', '这趟', '那趟', '这班', '那班', '这架', '那架', '一下',
    '看下', '看看', '查看', '查一下', '看一下', '看', '进出港', '出港', '进港', '离港', '到港',
    '全部方向', '不分方向', '大前天', '昨天', '昨日', '前一天', '上一天', '下一天', '后一天',
    '次日', '第二天', '前天', '今天', '今日',
    '实际执飞', '实际执行', '实际航班', '计划航班', '实际', '执飞', '执行', '计划', '起飞', '降落', '落地',
    '分别是多少', '分别是什么', '分别有几班', '分别有几条', '分别是', '分别',
    '具体是哪些', '具体有哪些', '具体是', '具体',
    '是什么', '是多少', '有哪几班', '有哪几条', '哪几班', '哪几条',
    '的呢', '的吗', '的呀', '的吧', '呢', '吗', '呀', '吧', '的', '了', '号', '日',
)

FOLLOWUP_ALLOW_FLIGHT_TOKENS: Tuple[str, ...] = ('航班', '合并比较', '按')

FOLLOWUP_SEQUENCE_TOKENS: Tuple[str, ...] = (
    '最后一班航班', '最后一班', '最晚一班航班', '最晚一班', '最迟一班', '末班航班', '末班',
    '最早一班航班', '最早一班', '第一班航班', '第一班', '首班航班', '首班',
    '最后', '最晚', '最迟', '最早', '是哪一班', '哪一班', '是哪班', '哪班',
    '哪一个', '是哪个', '哪个', '一班', '班', '航班',
)

FOLLOWUP_ACTION_PREFIXES: Tuple[str, ...] = (
    '换成', '改成', '换查', '改查', '再查', '再看', '只看', '仅看', '那', '那么', '其中',
)

FOLLOWUP_SHORT_PREFIXES: Tuple[str, ...] = (
    '换成', '改成', '换查', '改查', '那', '那么', '再查', '再看', '只看', '仅看',
)

VIEW_DETAIL_EXTRA_ALIASES: Tuple[str, ...] = ('取消', '延误', '晚点', '架次', '班次')


# ---------------------------------------------------------------------------
# 4. 通用正则模式常量
# ---------------------------------------------------------------------------

FOLLOW_UP = re.compile(
    r'^(?:(?:那|那么|其中)?(?:进港|出港|进出港|全部方向|不分方向|大前天|前天|前一天|昨天|昨日|今天|今日|上一天|下一天|后一天|次日|第二天|实际|实际执飞|实际执行|计划|计划航班|实际航班|(?:最后|最晚|最迟|最早|第一)一?班|末班|首班)(?:呢|的呢|吗|吧|呀)?'
    r'|(?:换成|改成|换查|改查|再查|再看|只看|仅看)[^？?]{1,20}'
    r'|(?:看下|看看|看)?(?:明细|详情|时序图?|甘特图?|时间轴|鱼骨图?|时序)|列出来|列出明细|有哪些|多少条|多少班|有几班|有几条|总数)[？?]?$'
)
_SEQUENCE_LAST_DISCOURSE_RE = re.compile(
    r'最后(?:问|追问|再|请|查一下|查看|查询|查|看下|看看|看|列出|列|统计|展示|显示|给出|给|把|帮|说|提|输出|汇总|总结|确认|核对)'
)
SEQUENCE = re.compile(
    r'最后(?!(?:问|追问|再|请|查|看|列|统|展|显|给|把|帮|说|提|输|汇|总|确|核))|最晚|最迟|末班|最早|第一班|首班'
)
VIEW_TURNAROUND_RE = re.compile(
    r'(?:地服|地面|过站)?保障(?:情况|进度|状态|诊断)?|过站诊断|甘特图?|时序图?|时间轴|时间线|鱼骨图?'
)
VIEW_DETAIL_RE = re.compile(
    r'航班动态|动态|详情|详细信息|明细|列出来|列出明细|列出|有哪些|清单|列表|分别是|具体'
)
GROUND_BRIEFING_NAME = r'地服(?:公司)?(?:运行|运营|值班|当班|保障)?简报'
REPORT_NOUNS_RE = (
    rf'A-?CDM|(?:运行)?通用(?:报告|报表|报)?|完整运行(?:报告|报表|报)|运行[日周月季年]报|{GROUND_BRIEFING_NAME}'
    r'|保障节点(?:与?保障)?质量|保障节点|保障质量|登机桥|单桥|录入|排行|资源周转|周转|看板|生成|报告|报表|资源|运行'
)
DATE_PAT = (
    r'(?:'
    r'\d{4}[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}[日号]?'
    r'|(?<!\d)\d{1,2}月\d{1,2}[日号]?'
    r'|(?<![0-9A-Za-z\-])\d{1,2}[-/\.]\d{1,2}[日号]'
    r'|(?<![0-9A-Za-z\-])\d{1,2}[-/\.]\d{1,2}(?![0-9A-Za-z\-]|:(?:\d)|\s*(?:架|班|条|分|米|座|元|个|位|人|点|%|折))'
    r')'
)

_CALENDAR_MONTH = r'本月(?:度)?|这个月'
_CALENDAR_YEAR = r'本年(?:度)?|今年'
_BARE_PERIOD_START = r'(?<![\d零〇一二两三四五六七八九十百千上前每第满过])(?<!超过)(?<!大于)(?<!小于)(?<!不足)(?<!为期)(?<!长达)'
_ROLLING_PERIOD_END = r'(?:以内|之内|以来|内)?(?!\s*[\d前后以之级])'
_ROLLING_MONTH = rf'(?:(?:最近|近|过去)\s*(?:一个月|1个月|一月|30\s*[天日])|{_BARE_PERIOD_START}(?:一个月|1个月)){_ROLLING_PERIOD_END}'
_ROLLING_YEAR = rf'(?:(?:最近|近|过去)\s*(?:一年|1年|365\s*[天日])|{_BARE_PERIOD_START}(?:一年|1年)){_ROLLING_PERIOD_END}'
MONTH_YEAR_PERIOD = (rf'(?:{_CALENDAR_MONTH}|{_CALENDAR_YEAR}|{_ROLLING_MONTH}|{_ROLLING_YEAR})'
                     r'(?:\s*(?:截至|截止(?:到|至)?|至|到)\s*(?:今天|今日))?')


def month_year_window(question, today):
    # 支持形如 "2026年10月"、"10月份"、"10月" 的特定自然月
    m_spec = re.search(r'(?:(20\d{2})[年\-\./])?(?<!\d)(1[0-2]|0?[1-9])月份?(?!\d)', question)
    if m_spec:
        yr = int(m_spec[1]) if m_spec[1] else today.year
        mo = int(m_spec[2])
        start = date(yr, mo, 1)
        end = date(yr + 1, 1, 1) if mo == 12 else date(yr, mo + 1, 1)
        return (start.isoformat(), end.isoformat()), 'month'
    match = re.search(MONTH_YEAR_PERIOD, question)
    if not match:
        return None
    term = match[0]
    if re.match(_CALENDAR_MONTH, term):
        start, kind = today.replace(day=1), 'month'
    elif re.match(_CALENDAR_YEAR, term):
        start, kind = today.replace(month=1, day=1), 'year'
    else:
        days = 30 if re.match(_ROLLING_MONTH, term) else 365
        start, kind = today - timedelta(days=days - 1), 'range'
    return (start.isoformat(), (today + timedelta(days=1)).isoformat()), kind


DATE_RANGE_STRIP_RE = re.compile(
    rf'{DATE_PAT}(?:\s*(?:到|至|-|~)\s*{DATE_PAT})?|(?:20\d{{2}}年)?(?:1[0-2]|0?[1-9])月份?|{MONTH_YEAR_PERIOD}|(?:最近|近)\d+天|本周|上周|这周|上一周|(?:最近|近|过去)\s*(?:一周|1周|7\s*[天日])'
)
FOLLOWUP_DATE_STRIP_RE = re.compile(
    rf'{DATE_RANGE_STRIP_RE.pattern}|最近一周|近一周'
)
DAY_MODE_STRIP_RE = re.compile(r'(?:按|采用|使用)?(?:生产)?(?:运营日|自然日)(?:口径)?')
IDENTIFIER_STRIP_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:[A-Z0-9]{2}\d{2,4}[A-Z]?|B-?[A-Z0-9]{4,5})(?![A-Za-z0-9])',
    re.I,
)
TURNAROUND_RESIDUAL_STRIP_RE = re.compile(
    r'地服|地面|过站|保障|作业|诊断|甘特图?|时序图?|时间轴|时间线|流程|过程|配餐|保洁|加油|装卸|登机'
    r'|看下|看看|查下|查查|查看|查询|看一下|查一下|调取|获取|显示|展示|给我|来个|分析|图|看'
    r'|情况|如何|怎么样|怎样|状况|状态|进度|节点|环节|已完成|完成了吗|完成了没|完成了|完成吗|完成情况|完成|好不好|和|与|及|吗|呢'
)
REPORT_RESIDUAL_STRIP_RE = re.compile(
    rf'{REPORT_NOUNS_RE}|生成|导出|制作|出份|出一份|一份|一个|一期|来份|来个|我想看|想看|我要|要|看下|看看|查下|查查|查看|查询|显示|调取|获取|打开|请|给我|帮我'
    r'|昨天|昨日|今天|今日|前天|大前天|最近一周|近一周|最近一个月|近一个月|近一月|本月|与|和|及|的|号|日|份|期|[\s，,。；;？?：:]',
    re.I,
)
_CLOCK_DIGIT = r'[零〇一二两三四五六七八九]'
_CLOCK_NUMBER = rf'\d{{1,2}}|{_CLOCK_DIGIT}?十{_CLOCK_DIGIT}?|{_CLOCK_DIGIT}{{1,2}}'
_BRIEFING_CLOCK_RE = re.compile(
    rf'(?<![A-Za-z0-9])(?:(?P<daypart>当晚|今晚|晚上|夜里|傍晚|下午|凌晨|早上|上午|晚|早)\s*)?'
    rf'(?P<hour>{_CLOCK_NUMBER})\s*(?::\s*(?P<minutes>\d{{2}})(?::(?P<seconds>\d{{2}}))?'
    rf'|[点时](?:\s*(?:(?P<minute_term>{_CLOCK_NUMBER})\s*分?|(?P<half>半)|整))?)(?![A-Za-z0-9])'
)
_BRIEFING_FIELDS_RE = re.compile(
    r'放行|起飞|正常率|准点率|分子|分母|底数|两套口径|双口径|执行量|执飞量|数量|总数|架次|班数|几[班架]|多少'
)
_BRIEFING_DISPLAY_TERMS_RE = re.compile(
    r'截至|截止(?:到|至)?|统计至|早于|为止|之前|以前|前|到|'
    r'本场全量|本场|全场|全机场|全量|地服代理|地服公司|地服|两套口径|双口径|口径|'
    r'放行(?:正常率|准点率|率)?|起飞(?:正常率|准点率)?|正常率|准点率|正常|准点|出港|离港|实际|执行量|执飞量|执行|执飞|实飞|飞了|'
    r'分子|分母|计算底数|底数|参与计算|怎么(?:算|计算)|如何(?:算|计算)|计算|各(?:有)?多少|多少|'
    r'总共|一共|总(?:数|量)|数量|几[班架]|架次|班数|班次|航班|[班架数总]|'
    r'写清楚|写出来|列出来|标出来|说清楚|给一下|写一下|解释一下|写上|做一下|出一下|做|'
    r'明确|列出|给出|说明|标明|标注|展示|对照|对比|分别|统计|麻烦|顺带|同时|以及|(?:各|两)(?:项|个)|把|也|都|并|各|有|是|了|[、/！!]'
)


def _clock_number(value: str) -> int:
    if value.isdecimal():
        return int(value)
    digits = '零一二三四五六七八九'
    value = value.replace('两', '二').replace('〇', '零')
    if '十' in value:
        tens, units = value.split('十', 1)
        return (digits.index(tens) if tens else 1) * 10 + (digits.index(units) if units else 0)
    return int(''.join(str(digits.index(digit)) for digit in value))


def ground_briefing_display_residual(text, cutoff):
    clocks = list(_BRIEFING_CLOCK_RE.finditer(text))
    values = []
    for clock in clocks:
        hour = _clock_number(clock['hour'])
        minute = 30 if clock['half'] else _clock_number(clock['minutes'] or clock['minute_term'] or '0')
        if clock['daypart'] in ('当晚', '今晚', '晚上', '夜里', '傍晚', '下午', '晚') and 1 <= hour <= 11:
            hour += 12
        if clock['daypart'] in ('凌晨', '早上', '上午', '早') and hour >= 12:
            return None
        values.append((hour, minute, int(clock['seconds'] or 0)))
    end = (cutoff.hour, cutoff.minute, cutoff.second)
    if len(clocks) == 2 and re.fullmatch(r'\s*(?:至|到|[-–—~～])\s*', text[clocks[0].end():clocks[1].start()]):
        if values != [(0, 0, 0), end]:
            return None
        text = re.sub(r'从\s*$', '', text[:clocks[0].start()]) + text[clocks[1].end():]
    elif any(clock != end for clock in values):
        return None
    elif clocks:
        text = _BRIEFING_CLOCK_RE.sub('', text)
    if clocks or _BRIEFING_FIELDS_RE.search(text):
        text = _BRIEFING_DISPLAY_TERMS_RE.sub('', text)
    return text

COUNT_QUESTION_RE = re.compile(
    r'多少|几条|几家|几班|几架|数一数|总数|条数|记录数|数量[?？。]*$|统计.*数量'
)

# 纯展示/列投影子句识别：必须作为从句出现（位于标点、连接词或“每次运行/每条记录”之后），不会误伤主查询句
DISPLAY_CLAUSE_RE = re.compile(
    r'(?:'
    r'[，,；;]\s*'
    r'|(?<=\S)\s*(?:并|同时|顺便|且|并且)\s*'
    r'|(?<=\S)\s*(?=每(?:次|条|班|架|趟)?(?:运行|执行|记录|航班|航段))'
    r')'
    r'(?:'
    r'(?:每(?:次|条|班|架|趟)?(?:运行|执行|记录|航班|航段)\s*)(?:并|同时|顺便|以及|且)?\s*(?:分别|各自|同时|逐一|一并)?\s*(?:显示|展示|列出|返回|输出|带上|附上|呈现|标明|给出|包含|包括)'
    r'|(?:并|同时|顺便|以及|且)?\s*(?:分别|各自|同时|逐一|一并)\s*(?:显示|展示|列出|返回|输出|带上|附上|呈现|标明|给出|包含|包括)'
    r'|(?:并|同时|顺便|以及|且)?\s*(?:显示|展示|返回|输出|带上|附上|呈现|标明|给出|包含|包括)'
    r')\s*'
    r'(?:各(?:项|个)?)?(?:字段|列|信息)?\s*[:：]?\s*'
    r'([^。！？?!；;]+)'
)

# 只要子句体内包含筛选运算符、比较词、数字、聚合/排序或状态筛选词，即不允许视作纯展示子句
DISPLAY_CLAUSE_FORBIDDEN_RE = re.compile(
    r'\d|!=|>=|<=|[=<>]|等于|不等于|大于|小于|超过|不足|以上|以下|至少|最多'
    r'|为空|不为空|非空|缺失|未填|不是|不含|排除|除去|除了|早于|晚于'
    r'|排序|升序|降序|从大到小|从小到大|按|统计|合计|汇总|平均|总计|求和'
    r'|占比|比例|正常率|靠桥率|放行率|起飞率|客座率|多少|几[班架条家]|总数'
    r'|排行|排名|趋势|分布|对比|同比|环比|取消|异常|正常'
)


# ---------------------------------------------------------------------------
# 5. 码表读取与动态扩展方法（外部统一调用入口）
# ---------------------------------------------------------------------------

def get_direction_label(code: str) -> str:
    """获取进出港方向编码对应的中文名称。"""
    entry = DIRECTION_CODE_TABLE.get(code)
    return str(entry['label']) if entry else code


def get_direction_options() -> List[Tuple[str, str]]:
    """获取方向澄清选项列表。"""
    return [(str(DIRECTION_CODE_TABLE[code]['option_label']), code) for code in ('DEP', 'ARR', 'BOTH')]


def get_basis_options(detailed: bool = True) -> List[Tuple[str, str]]:
    """获取计划/实际口径澄清选项列表。"""
    field = 'option_label' if detailed else 'label'
    return [(str(BASIS_CODE_TABLE[code][field]), code) for code in ('actual', 'planned')]


def get_basis_replace_pattern(basis: str) -> str:
    """获取某口径用于多轮改写替换的正则模式。"""
    tokens = BASIS_CODE_TABLE.get(basis, {}).get('sync_replace_tokens', ())
    return '|'.join(re.escape(str(tok)) for tok in tokens)


def get_day_mode_keywords() -> Dict[str, Tuple[str, ...]]:
    """获取运营日/自然日模式触发词码表。"""
    return DAY_MODE_KEYWORDS


def get_relative_day_offsets() -> Tuple[Tuple[Tuple[str, ...], int], ...]:
    """获取相对日期到天数偏移的码表。"""
    return RELATIVE_DAY_OFFSETS


def get_default_period_labels() -> Tuple[str, ...]:
    """获取日期澄清默认选项。"""
    return DEFAULT_PERIOD_LABELS


def get_followup_day_shift_patterns() -> Dict[str, str]:
    """获取追问单日加减触发正则。"""
    return {
        key: '|'.join(re.escape(word) for word in words)
        for key, words in FOLLOWUP_DAY_SHIFT_WORDS.items()
    }


def get_identifier_columns(role: str) -> Tuple[str, ...]:
    """获取航班号/机号对应的候选字段名码表。"""
    return IDENTIFIER_COLUMN_MAP.get(role, ())


def get_literal_filter_operators() -> Dict[str, str]:
    """获取字面筛选中文操作符到受控 op 的映射码表。"""
    return dict(LITERAL_FILTER_OPERATORS)


def get_cancel_status_filter(subject: str) -> Optional[Tuple[str, str]]:
    """获取指定业务表的取消状态字段与枚举值。"""
    return CANCEL_STATUS_MAP.get(subject)


def get_extra_attribute_aliases(field: str) -> Tuple[str, ...]:
    """获取字段在属性追问时的补充别名。"""
    return EXTRA_ATTRIBUTE_FOLLOWUP_ALIASES.get(field, ())


def get_metric_residual_terms(metric: str) -> Tuple[str, ...]:
    """获取特定指标对应的残差忽略补充词表。"""
    return METRIC_RESIDUAL_TERMS.get(metric, ())


def get_sequence_label(direction: str) -> str:
    """获取首末班排序方向对应的中文表达。"""
    entry = SEQUENCE_CODE_TABLE.get(direction)
    return str(entry['label']) if entry else direction


def get_residual_vocabulary() -> Tuple[str, ...]:
    """汇总全部残差忽略词表，按词长降序返回，确保长词优先剥离。"""
    merged = {
        term
        for terms in RESIDUAL_VOCABULARY_GROUPS.values()
        for term in terms
        if term
    }
    return tuple(sorted(merged, key=len, reverse=True))


def get_display_field_terms(catalog=None, subject: Optional[str] = None) -> Tuple[str, ...]:
    """汇总允许出现在纯展示/列投影子句中的字段名白名单。"""
    terms = {
        term
        for group_terms in DISPLAY_FIELD_GROUPS.values()
        for term in group_terms
        if term
    }
    if catalog is not None and hasattr(catalog, 'entities'):
        hidden = set(getattr(catalog, 'overrides', {}).get('sensitive_fields', []))
        target_tables: Iterable[str]
        if subject and subject in catalog.entities:
            target_tables = (subject, CORE, ACTUAL) if subject in (CORE, ACTUAL) else (subject,)
        else:
            target_tables = (CORE, ACTUAL)
        for table in target_tables:
            spec = catalog.entities.get(table)
            if not spec:
                continue
            for col, attr in spec.get('attributes', {}).items():
                if col in hidden:
                    continue
                for alias in (col, attr.get('label', ''), *(attr.get('aliases') or ())):
                    if alias and isinstance(alias, str) and len(alias.strip()) >= 2:
                        terms.add(alias.strip())
    return tuple(sorted(terms, key=len, reverse=True))


def get_followup_fillers(allow_flights_word: bool = False, has_sequence: bool = False) -> Tuple[str, ...]:
    """获取多轮追问剥离口语虚词列表（按词长降序）。"""
    tokens: List[str] = list(FOLLOWUP_FILLER_TOKENS)
    if allow_flights_word:
        tokens.extend(FOLLOWUP_ALLOW_FLIGHT_TOKENS)
    if has_sequence:
        tokens.extend(FOLLOWUP_SEQUENCE_TOKENS)
    return tuple(sorted(set(tokens), key=len, reverse=True))


def register_residual_terms(group: str, *terms: str) -> None:
    """动态向残差忽略词表的指定分组追加词条，便于业务扩展。"""
    bucket = RESIDUAL_VOCABULARY_GROUPS.setdefault(group, [])
    for term in terms:
        if term and term not in bucket:
            bucket.append(term)


def register_display_fields(group: str, *terms: str) -> None:
    """动态向展示字段白名单的指定分组追加字段名，便于业务扩展。"""
    bucket = DISPLAY_FIELD_GROUPS.setdefault(group, [])
    for term in terms:
        if term and term not in bucket:
            bucket.append(term)


def register_metric_residual_terms(metric: str, *terms: str) -> None:
    """动态为指定指标补充残差忽略词条。"""
    existing = list(METRIC_RESIDUAL_TERMS.get(metric, ()))
    for term in terms:
        if term and term not in existing:
            existing.append(term)
    METRIC_RESIDUAL_TERMS[metric] = tuple(existing)


# ---------------------------------------------------------------------------
# 6. 文本检测与纯展示子句剥离辅助方法
# ---------------------------------------------------------------------------

def is_count(question: str) -> bool:
    """判断问句是否表达计数意图。"""
    return bool(COUNT_QUESTION_RE.search(question))


def json_object(content: str) -> dict:
    """从模型文本输出中安全提取首个 JSON 对象。"""
    if not isinstance(content, str):
        raise ValueError('模型返回格式无效')
    value = json.JSONDecoder().raw_decode(content[content.find('{'):])[0] if '{' in content else None
    if not isinstance(value, dict):
        raise ValueError('模型需要返回 JSON 对象')
    return value


def detect_day_mode(text: str) -> Optional[str]:
    """识别运营日/自然日模式。"""
    for mode, keywords in DAY_MODE_KEYWORDS.items():
        if any(word in text for word in keywords):
            return mode
    return None


def detect_direction(text: str) -> Optional[str]:
    """根据方向码表识别进出港方向。"""
    both_pat = '|'.join(re.escape(str(w)) for w in DIRECTION_CODE_TABLE['BOTH']['keywords'])
    if re.search(both_pat, text):
        return 'BOTH'
    dep_pat = '|'.join(re.escape(str(w)) for w in DIRECTION_CODE_TABLE['DEP']['keywords'])
    arr_pat = '|'.join(re.escape(str(w)) for w in DIRECTION_CODE_TABLE['ARR']['keywords'])
    dep = bool(re.search(dep_pat, text))
    arr = bool(re.search(arr_pat, text))
    if dep != arr:
        return 'DEP' if dep else 'ARR'
    return None


def detect_basis(
    text: str,
    *,
    has_planned_metric: bool = False,
    has_operated_metric: bool = False,
    full_text: Optional[str] = None,
) -> Optional[str]:
    """根据口径码表识别计划/实际执飞口径。"""
    planned_pat = '|'.join(re.escape(str(w)) for w in BASIS_CODE_TABLE['planned']['keywords'])
    actual_pat = '|'.join(re.escape(str(w)) for w in BASIS_CODE_TABLE['actual']['keywords'])
    fallback_pat = '|'.join(re.escape(str(w)) for w in BASIS_CODE_TABLE['actual']['fallback_keywords'])

    planned = bool(re.search(planned_pat, text)) or has_planned_metric
    text_clean_for_actual = re.sub(r'执行计划|计划执飞|计划排班', '', text)
    actual = bool(re.search(actual_pat, text_clean_for_actual)) or has_operated_metric
    if not planned and fallback_pat and re.search(fallback_pat, text):
        actual = True
    if actual and not planned:
        return 'actual'
    if planned and not actual:
        return 'planned'
    source_for_cancel = full_text if full_text is not None else text
    if not actual and not planned and '取消' in source_for_cancel:
        return 'planned'
    return None


def detect_sequence(text: str) -> Optional[str]:
    """识别首班（asc）或末班（desc）排序需求。"""
    cleaned = _SEQUENCE_LAST_DISCOURSE_RE.sub('', text)
    if not SEQUENCE.search(cleaned):
        return None
    desc_pat = '|'.join(re.escape(str(w)) for w in SEQUENCE_CODE_TABLE['desc']['keywords'])
    return 'desc' if re.search(desc_pat, cleaned) else 'asc'


def _normalize_display_field_token(token: str) -> str:
    cleaned = token.strip(' \t\r\n、，,。；;：:（）()“”"\'')
    cleaned = re.sub(r'^(?:它的|其|的|各|各个|所有|全部|相应|对应)+', '', cleaned)
    cleaned = re.sub(r'(?:字段|列|信息|数据|值|情况)+$', '', cleaned)
    return cleaned.strip()


def strip_display_field_clauses(
    text: str,
    catalog=None,
    subject: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """识别并剥离句中的纯字段展示/投影从句（如“，每次运行分别显示源航班ID、计划落地和实际落地时间”）。

    仅当从句满足以下全部安全约束时才剥离，确保绝不会吞掉任何筛选、排序或聚合条件：
    1. 从句不含任何数字、比较符、筛选谓词或聚合/排序关键词；
    2. 按分隔符拆出的每个字段项均命中展示字段白名单或实体属性标签/别名；
    3. 剥离后主句仍保留有效查询内容。

    返回 `(剥离后的文本, 被剥离的子句原文列表)`。
    """
    if not text:
        return text, []
    matches = list(DISPLAY_CLAUSE_RE.finditer(text))
    if not matches:
        return text, []

    allowed_lower = {term.lower() for term in get_display_field_terms(catalog=catalog, subject=subject)}
    spans_to_remove: List[Tuple[int, int, str]] = []

    for match in matches:
        body = match.group(1).strip()
        if not body or DISPLAY_CLAUSE_FORBIDDEN_RE.search(body):
            continue
        raw_items = [item for item in re.split(r'[、，,]+|以及|和|与|及|\s+', body) if item and item.strip()]
        if not raw_items:
            continue
        valid = True
        for raw_item in raw_items:
            raw_clean = raw_item.strip(' \t\r\n、，,。；;：:（）()“”"\'')
            normalized = _normalize_display_field_token(raw_item)
            if not raw_clean and not normalized:
                continue
            if raw_clean.lower() not in allowed_lower and (not normalized or normalized.lower() not in allowed_lower):
                valid = False
                break
        if valid:
            spans_to_remove.append((match.start(), match.end(), match.group(0)))

    if not spans_to_remove:
        return text, []

    parts: List[str] = []
    removed: List[str] = []
    cursor = 0
    for start, end, raw_clause in spans_to_remove:
        if start < cursor:
            continue
        parts.append(text[cursor:start])
        removed.append(raw_clause)
        cursor = end
    parts.append(text[cursor:])
    stripped = ''.join(parts).strip()

    # 防御性保护：绝不把整句剥空或只剩孤立日期
    remaining_core = FOLLOWUP_DATE_STRIP_RE.sub('', stripped)
    if not re.sub(r'[\s，,。；;？?！!：:、]', '', remaining_core):
        return text, []
    return stripped, removed
