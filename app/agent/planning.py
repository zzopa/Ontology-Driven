"""Separate the filter anchor from the queried grain using catalog topology.

No enterprise, concept or physical table name is special-cased. A connected
table alone is insufficient: the relationship must be explicitly requested by
the rule plan and have a valid unambiguous path in the authorized catalog.
"""
from copy import deepcopy


def reconcile_anchor(catalog, local, proposed):
    anchor, target = local['subject'], proposed['subject']
    if anchor == target:
        return deepcopy(proposed)
    if target not in local.get('focus_tables', []):
        raise ValueError('模型与本体规则选择的主体不一致，且没有明确请求的关联目标；请明确业务对象')
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
    result['plan_roles'] = {'filter_anchor': anchor, 'analysis_target': target,
                            'strategy': 'configured_relation_rebase'}
    return result
