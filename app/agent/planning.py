"""Separate the filter anchor from the queried grain using catalog topology.

No enterprise, concept or physical table name is special-cased. A connected
table alone is insufficient: the relationship must be explicitly requested by
the rule plan and have a valid unambiguous path in the authorized catalog.

双路由意图分发：
  1. 复杂指标聚合/统计分析类意图 → SemanticEngine 编译确定性 SQL
  2. 拓扑追踪/明细关联类意图 → 保留现有本体图谱路径规划与 Graph-to-SQL
"""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
from datetime import date
from typing import Any, Callable, Literal

from app.semantic_engine import SemanticEngine


def reconcile_anchor(catalog, local, proposed):
    anchor, target = local['subject'], proposed['subject']
    if anchor == target:
        return deepcopy(proposed)
    if target not in local.get('focus_tables', []):
        raise ValueError('模型与本体规则选择的主体不一致，且没有明确请求的关联目标；请明确业务对象')
    if proposed.get('intent') in ('count', 'sum') and len(set(local.get('focus_tables', []))) > 1:
        raise ValueError('问题涉及多个关联对象的统计，需拆分查询并分别确定统计粒度，不能仅返回其中一个')
    path = catalog.path(anchor, target)
    if not path:
        raise ValueError('缺少可验证的筛选锚点与查询目标路径')
    result = deepcopy(proposed)
    # Predicates without a table were interpreted relative to the model's
    # original subject. Preserve that ownership; never silently rebind fields.
    for condition in result.get('conditions', []):
        condition['table'] = condition.get('table') or target
    result['subject'] = anchor
    result['disp'] = catalog.entities[anchor]['name']
    result['focus_tables'] = list(dict.fromkeys([target] + result.get('focus_tables', [])))
    if result.get('intent', 'detail') in ('count', 'sum'):
        if not result.get('aggregate_target'):
            result['aggregate_target'] = target
    else:
        result['aggregate_target'] = anchor
    analysis_target=result['aggregate_target'] if result.get('intent') in ('count','sum') else target
    result['plan_roles'] = {'filter_anchor': anchor, 'analysis_target': analysis_target,
                            'model_subject': target,
                            'strategy': 'configured_relation_rebase'}
    return result


# ---------------------------------------------------------------------------
# 双路由意图分发机制
# ---------------------------------------------------------------------------

_METRIC_INTENT_RE = re.compile(
    r'按.{0,4}(月|月份|季度|季|年|年度|日|天|周|企业|部门|类别|分类|阶段|类型|币种|方式)'
    r'.{0,6}(统计|汇总|合计|聚合|分析|分布|趋势|对比|占比|占比情况)'
    r'|(总金额|总额|合计|总览|统计|汇总|聚合|平均|均值|最大值|最小值|同环比|同比|环比'
    r'|履约率|完成率|占比|分布|趋势|对比|分组|按月|按季|按年|按企业|按部门|按类别)'
    r'|各.{0,4}(企业|部门|类别|分类|阶段|类型|币种|方式).{0,6}(的)?(总|平均|数量|金额|合同|记录|笔数)')

_GRAPH_INTENT_RE = re.compile(
    r'明细|详情|详细|档案|关联|涉及|路径|拓扑|查找|查询|列出|哪些|属于|任职|履历'
    r'|经历|资质|证书|参会|议题|相对方|付款节点|交货|结算|发票|收据|流水明细'
    r'|名叫|姓名|姓氏|编号|合同号|会议号|账号')


IntentType = Literal['metric_aggregation', 'graph_entity_qa']


def classify_intent(question: str) -> IntentType:
    """基于关键词模式判定问题属于指标聚合还是拓扑明细类意图。

    当问题同时包含两类信号时，明细追溯关键词优先（明细类问题更具体，
    错路由代价更高）；仅在纯统计聚合场景下路由至语义层。
    """
    text = question.strip()
    if not text:
        raise ValueError('问题不能为空')
    has_metric = bool(_METRIC_INTENT_RE.search(text))
    has_graph = bool(_GRAPH_INTENT_RE.search(text))
    if has_graph:
        return 'graph_entity_qa'
    if has_metric:
        return 'metric_aggregation'
    return 'graph_entity_qa'


_SPEC_PROMPT = (
    '从问题中抽取指标查询规范，只输出 JSON，不要输出其他内容。'
    '基于下方提供的 Cube 定义，选择正确的 cube、measures、dimensions。'
    'filters 中 member 必须是定义中的维度键；op 仅允许 =、!=、>、>=、<、<=、like、in、is_null、is_not_null。'
    'time_dimension 包含 name 和 granularity（day/month/quarter/year）。'
    'order_by 中 direction 为 asc 或 desc。'
    '不要遗漏问题中的筛选条件；不要添加问题未要求的条件。'
    '无法确定指标或维度时，设 unresolved 为问题描述字符串并返回空 measures。'
    '返回格式：{"cube":"...","measures":[],"dimensions":[],'
    '"time_dimension":{"name":"","granularity":""},"filters":[],"order_by":[],"limit":null,"unresolved":""}'
    '\n当前日期：{today}\n可用 Cube 定义：{cubes}')


def extract_semantic_spec(question: str, engine: SemanticEngine,
                          chat: Callable[..., str] | None = None) -> dict[str, Any]:
    """使用大模型从自然语言中抽取结构化查询规范（JSON Spec）。

    大模型仅负责语义理解与字段映射，不生成 SQL；
    最终 SQL 由 SemanticEngine.compile 确定性编译。
    """
    cubes_doc: dict[str, Any] = {}
    for cube_name, cube in engine.cubes.items():
        cubes_doc[cube_name] = {
            'label': cube.get('label', cube_name),
            'measures': {k: {'label': v.get('label', k), 'unit': v.get('unit', '')}
                         for k, v in cube.get('measures', {}).items()},
            'dimensions': {k: v.get('label', k) for k, v in cube.get('dimensions', {}).items()},
            'time_dimensions': {
                k: {'label': v.get('label', k), 'granularities': v.get('granularities', [])}
                for k, v in cube.get('time_dimensions', {}).items()},
        }
    prompt = _SPEC_PROMPT.format(today=date.today().isoformat(),
                                 cubes=json.dumps(cubes_doc, ensure_ascii=False))
    if chat is None:
        raise ValueError('指标意图路由需要大模型支持，请配置 LLM 或改用明细查询')
    content = chat(prompt, question, max_tokens=1200, timeout=40)
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        raise ValueError('模型未返回有效的指标查询规范')
    spec = json.loads(match.group())
    if not isinstance(spec, dict):
        raise ValueError('模型返回的指标查询规范格式无效')
    if spec.get('unresolved'):
        raise ValueError('无法确定指标口径：' + str(spec['unresolved']))
    spec.setdefault('cube')
    spec.setdefault('measures', [])
    spec.setdefault('dimensions', [])
    spec.setdefault('filters', [])
    spec.setdefault('order_by', [])
    spec.setdefault('time_dimension')
    spec.setdefault('limit')
    return spec


def dispatch_semantic(question: str, engine: SemanticEngine,
                      chat: Callable[..., str] | None = None) -> dict[str, Any]:
    """完整双路由入口：判定意图 → 抽取 Spec → 编译 SQL。

    返回 dict 包含：
      intent:  'metric_aggregation' | 'graph_entity_qa'
      sql:     编译后的 SQL（仅 metric_aggregation 时存在）
      spec:    抽取的查询规范（仅 metric_aggregation 时存在）
    graph_entity_qa 意图返回 intent 标记，由调用方继续走本体图谱路径。
    """
    intent = classify_intent(question)
    if intent == 'graph_entity_qa':
        return {'intent': 'graph_entity_qa'}
    spec = extract_semantic_spec(question, engine, chat)
    sql = engine.compile(spec)
    return {'intent': 'metric_aggregation', 'spec': spec, 'sql': sql}


# ---------------------------------------------------------------------------
# 多轮对话上下文继承与指代消解
# ---------------------------------------------------------------------------

@dataclass
class ConversationContext:
    """多轮对话上下文摘要：保存上一轮的核心查询要素供指代消解。"""
    question: str
    subject: str = ''
    intent: str = ''
    conditions: list = field(default_factory=list)
    time_bucket: dict | None = None
    aggregate_target: str | None = None
    metric: str | None = None
    focus_tables: list = field(default_factory=list)
    group_by: list = field(default_factory=list)
    # 指代消解标记，供调用方判断追问模式
    time_overwrite: bool = False
    new_time_period: str | None = None
    condition_append: bool = False


# 时间切片覆写追问模式：如"那今年呢"、"换成去年看看"、"改成按季度"
_TIME_OVERWRITE_RE = re.compile(
    r'今年|去年|前年|上月|本月|上季|本季|本年|去岁'
    r'|那.{0,6}(呢|怎么样|如何)'
    r'|换.{0,6}看'
    r'|改成'
)

# 条件追加深入追问模式：如"把其中的超期合同列出来"、"再加上金额大于"
_CONDITION_APPEND_RE = re.compile(
    r'其中|把.{0,10}的|再.{0,4}筛选|加上|排除'
)

# 从追问中提取新的时间周期
_TIME_PERIOD_RE = re.compile(
    r'(今年|去年|前年|本年|上月|本月|上季|本季|本季度|上季度|去岁)'
)

# 时间维度字段名特征，用于区分时间条件与非时间条件
_TIME_FIELD_RE = re.compile(
    r'(date|time|year|month|day|period|created|updated|signing|_at$|签定|签订|生效|到期|交付)', re.I
)


def _is_time_condition(cond: dict) -> bool:
    """判断筛选条件是否作用于时间维度字段。"""
    return bool(_TIME_FIELD_RE.search(str(cond.get('col', ''))))


def resolve_coreference(question: str, prev: ConversationContext | None) -> ConversationContext | None:
    """检测追问模式，继承上一轮上下文并进行指代消解。

    支持两种追问模式：
    1. 时间切片覆写（如"那今年呢"）：继承 subject/intent/非时间条件，仅覆写时间维度
    2. 条件追加深入（如"把其中的超期合同列出来"）：继承 subject/intent/已有条件，拼接新增过滤条件

    若不匹配追问模式则返回 None。
    """
    if prev is None:
        return None
    text = question.strip()
    if not text:
        return None

    # 模式一：时间切片覆写
    if _TIME_OVERWRITE_RE.search(text):
        non_time_conditions = [
            c for c in prev.conditions
            if not _is_time_condition(c)
        ]
        ctx = ConversationContext(
            question=text,
            subject=prev.subject,
            intent=prev.intent,
            conditions=non_time_conditions,
            time_bucket=prev.time_bucket,
            aggregate_target=prev.aggregate_target,
            metric=prev.metric,
            focus_tables=list(prev.focus_tables),
            group_by=list(prev.group_by),
        )
        ctx.time_overwrite = True
        match = _TIME_PERIOD_RE.search(text)
        ctx.new_time_period = match.group(1) if match else text
        return ctx

    # 模式二：条件追加深入
    if _CONDITION_APPEND_RE.search(text):
        ctx = ConversationContext(
            question=text,
            subject=prev.subject,
            intent=prev.intent,
            conditions=list(prev.conditions),
            time_bucket=prev.time_bucket,
            aggregate_target=prev.aggregate_target,
            metric=prev.metric,
            focus_tables=list(prev.focus_tables),
            group_by=list(prev.group_by),
        )
        ctx.condition_append = True
        return ctx

    return None


def build_correction_prompt(question: str, failed_sql: str, error: str, schema_context: str) -> str:
    """构造 SQL 自愈修正 Prompt。

    将用户原始意图、执行失败的 SQL、具体报错日志及关联表 Schema 构造为修正 Prompt，
    由 Agent 重新调整/编译生成修正 SQL。
    """
    return (
        '你是 SQL 修正专家，请根据以下信息修正 SQL。\n'
        '规则：\n'
        '1. 仅输出一条修正后的 SELECT 语句，不要输出任何其他文字或解释。\n'
        '2. 禁止使用 DML（INSERT/UPDATE/DELETE）或 DDL 语句。\n'
        '3. 确保列名与表名与下方 Schema 上下文完全一致。\n'
        '4. 根据报错信息定位并修复问题。\n\n'
        f'用户原始问题：\n{question}\n\n'
        f'失败的 SQL：\n{failed_sql}\n\n'
        f'报错信息：\n{error}\n\n'
        f'相关表 Schema 上下文：\n{schema_context}\n\n'
        '请输出修正后的 SQL：'
    )
