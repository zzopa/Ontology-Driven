"""Separate the filter anchor from the queried grain using catalog topology.

No enterprise, concept or physical table name is special-cased. A connected
table alone is insufficient: the relationship must be explicitly requested by
the rule plan and have a valid unambiguous path in the authorized catalog.

双路由意图分发：
  1. 复杂指标聚合/统计分析类意图 → SemanticEngine 编译确定性 SQL
  2. 拓扑追踪/明细关联类意图 → 保留现有本体图谱路径规划与 Graph-to-SQL
"""
from copy import deepcopy
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
