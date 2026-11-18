"""Small, backtested seasonal baselines; LLMs never produce forecast numbers."""
from copy import deepcopy
from datetime import date, timedelta
import math
import re
from statistics import mean
from time import perf_counter

from app.query import Compiler, execute, json_safe

METHODS = ('上周同日', '近两周同星期均值')
DATE_VALUE = r'\d{4}-\d{1,2}-\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]'
CUTOFF = re.compile(rf'(?:(?:只|仅)\s*)?(?:(?:使用|采用|用|基于|以|训练)\s*)?'
                    rf'(?:截至|截止(?:到|至)?)\s*({DATE_VALUE})(?:\s*为止)?(?:\s*的)?(?:\s*历史(?:数据|记录)?)?')
LEAD_DAYS = re.compile(r'(?:未来|接下来|今后)\s*(\d+)\s*天')
FORECAST_DETAILS = re.compile(r'(?:并|同时)?\s*(?:说明|给出|列出|注明|展示)\s*'
                              r'(?:回测误差|预测区间|经验区间|回测MAE|MAE)(?:\s*(?:和|与|及|、)\s*'
                              r'(?:回测误差|预测区间|经验区间|回测MAE|MAE))*', re.I)


def make_plan(coordinator, question, anchor, day_mode):
    from app.agent.analysis import DATE_TOKEN, normalize_dates, matched_metrics
    def day(value, reference):
        return date(*map(int, normalize_dates(value, reference).split('-')))

    cutoff = CUTOFF.search(question)
    history_end = anchor.date()
    text = question
    if cutoff:
        history_end = day(cutoff[1], anchor) + timedelta(days=1)
        if history_end > anchor.date():
            raise ValueError('请使用已结束的业务日作为历史数据截止日')
        text = text[:cutoff.start()] + ' ' + text[cutoff.end():]
    matches = matched_metrics(text, coordinator.catalog)
    if len({m[2] for m in matches}) > 1:
        raise ValueError('本轮预测请选择一个指标；多个指标请分别预测')
    for a, b, metric_key, _ in reversed(matches):
        text = text[:a] + coordinator.catalog.metrics[metric_key]['name'] + text[b:]
    match = LEAD_DAYS.search(text)
    horizon = int(match[1]) if match else 1
    lead = 0 if cutoff else 1
    if '后天' in text:
        lead = 2
    elif '明天' in text:
        lead = 1
    elif '今天' in text:
        lead = 0
    start = history_end + timedelta(days=lead)
    dates = list(re.finditer(DATE_VALUE, text))
    if dates:
        start = day(dates[0][0], history_end)
        if len(dates) > 1:
            if len(dates) != 2 or not re.fullmatch(r'\s*(?:至|到|~|～|—|–|-)\s*', text[dates[0].end():dates[1].start()]):
                raise ValueError('请指定一个连续的预测日期区间')
            end = day(dates[1][0], start)
            if len(re.findall(r'\d+', dates[1][0])) == 2 and end.month < start.month:
                end = end.replace(year=start.year + 1)
            span = (end - start).days + 1
            if match and horizon != span:
                raise ValueError('预测天数与指定日期区间不一致')
            horizon = span
        text = text[:dates[0].start()] + ' ' + text[dates[-1].end():]
    if not 1 <= horizon <= 7:
        raise ValueError('请指定按日期先后排列的1至7天预测区间')
    if start < history_end or start + timedelta(days=horizon - 1) > history_end + timedelta(days=7):
        if cutoff:
            raise ValueError('预测日期须晚于历史截止日，且最迟为截止日后第8天')
        raise ValueError('请选择今天至未来7天内的预测日期')
    base_text = FORECAST_DETAILS.sub(' ', text)
    base_text = re.sub(r'预测|预估|明天|后天|未来|接下来|今后', ' ', LEAD_DAYS.sub(' ', base_text))
    base_text = re.sub(DATE_TOKEN, ' ', base_text)
    base_text = re.sub(r'趋势|变化|情况|结果', '', base_text)
    begin = history_end - timedelta(days=56)
    base_question = f'{begin}至{history_end - timedelta(days=1)} {base_text}'
    base = coordinator.plan(base_question, day_mode=day_mode, _single=True)
    if base.get('outcome'):
        raise ValueError(base.get('answer', '请指定要预测的已发布指标'))
    metric = coordinator.catalog.metrics.get(base.get('metric'), {})
    if base.get('intent') != 'metric' or metric.get('availability') != 'AVAILABLE':
        raise ValueError('预测仅支持已发布的聚合指标，请明确计划量、执行量或具体指标')
    unit = metric.get('unit')
    if unit not in ('%', '分钟', '架次', '人次', '千克', 'kg', '班', '次', '吨', '人'):
        raise ValueError('此指标尚未发布短期预测口径')
    if base.get('group_by') or base.get('limit'):
        raise ValueError('本轮预测请选择一个总体指标及筛选条件，不附加排名或分组')
    if base['subject'] == 'airport_benchmark_daily':
        raise ValueError('机场对标模拟数据不参与本场真实指标预测')
    return {'subject': base['subject'], 'intent': 'forecast', 'base_plan': base,
            'base_question': base_question, 'target_start': str(start), 'horizon': horizon,
            'history_end': str(history_end), 'history_cutoff_explicit': cutoff is not None,
            'conditions': [], 'focus_tables': [], 'group_by': [],
            'query_context': {'question': question, 'anchor': anchor.isoformat(), 'slots': {}}}


def estimate(history, target, method):
    values = [v for day, v in sorted(history.items(), reverse=True) if day.weekday() == target.weekday()][:2]
    if len(values) < 2:
        raise ValueError('同星期历史样本不足')
    return values[0] if method == METHODS[0] else mean(values)


def predict_series(series, targets, unit):
    ordered = sorted(series)
    if len(ordered) < 28:
        raise ValueError('预测至少需要28个连续、有效的历史业务日')
    last = ordered[-1]
    required = [last - timedelta(days=i) for i in range(28)]
    if any(day not in series or not math.isfinite(series[day]) for day in required):
        raise ValueError('最近28个业务日存在缺日或无有效数值，不以零补齐预测历史')
    if any(series[day] < 0 or unit == '%' and series[day] > 100 for day in required):
        raise ValueError('历史指标超出有效数值范围，请先核对指标数据与口径')
    points = []
    for target in targets:
        distance = (target - last).days
        if not 1 <= distance <= 8:
            raise ValueError('历史数据距预测日期过远，请先更新本地数据')
        errors = {}
        for method in METHODS:
            residuals = []
            for offset in range(7):
                check = last - timedelta(days=offset)
                origin = check - timedelta(days=distance)
                train = {d: v for d, v in series.items() if d <= origin}
                residuals.append(abs(estimate(train, check, method) - series[check]))
            errors[method] = residuals
        method = min(METHODS, key=lambda m: mean(errors[m]))
        prediction = estimate(series, target, method)
        spread = sorted(errors[method])[math.ceil(.9 * len(errors[method])) - 1]
        def bound(value):
            value = max(0, min(100, value)) if unit == '%' else max(0, value)
            return round(value) if unit in ('架次', '人次', '班', '次', '人') else round(value, 2)
        points.append({'date': str(target), 'value': bound(prediction), 'lower': bound(prediction - spread),
                       'upper': bound(prediction + spread), 'method': method, 'mae': round(mean(errors[method]), 4),
                       'backtest_origins': 7, 'distance_days': distance})
    return points


def run_forecast(conn, question, plan, catalog, trace, access=None):
    from app.agent.analysis import blank
    from app import core
    started = perf_counter()
    base = deepcopy(plan['base_plan'])
    metric = catalog.metrics[base['metric']]
    base['time_bucket'] = {'field': (base.get('operational_window') or {}).get('field') or metric['date_field'], 'grain': 'day'}
    compiler = Compiler(catalog, access)
    compiler.validate(base)
    statement = compiler.aggregate(base)
    trace.start('forecast_history', '读取预测历史', '按已发布指标口径读取历史日序列，保留全部原始筛选条件',
                [statement.sql, str(statement.params)])
    rows = json_safe(execute(conn, statement))
    if any(r.get('__period') and r['__period'] >= plan['history_end'] for r in rows):
        message = '历史查询返回了截止日之后的数据，请核对日期口径。'
        trace.finish('forecast_history', message, state='failed')
        return core.business_response(question, {'outcome': 'no_data', 'answer': message,
                                                 'plan': plan}, catalog, trace, perf_counter() - started)
    series = {date.fromisoformat(r['__period']): float(r[metric.get('value_alias') or base['metric']]) for r in rows
              if r.get('__period') and r.get(metric.get('value_alias') or base['metric']) is not None and r.get('n', 0) > 0}
    trace.finish('forecast_history', f'读取到{len(series)}个有效观测日；观测连续不证明同步完整')
    start = date.fromisoformat(plan['target_start'])
    targets = [start + timedelta(days=i) for i in range(plan['horizon'])]
    try:
        points = predict_series(series, targets, metric['unit'])
    except ValueError as exc:
        return core.business_response(question, {'outcome': 'no_data', 'answer': str(exc), 'plan': plan}, catalog, trace, perf_counter() - started)
    trace.start('forecast_backtest', '滚动回测与预测', '每个预测步长使用7个历史滚动起点；训练严格截止于该起点')
    result = blank(question, plan, catalog)
    unit, name = metric['unit'], metric['name']
    warning = '试验性预测：仅使用本地观测记录，未证明源端同步完整；短历史和小样本经验误差范围不代表置信保证。'
    lines = [f"{p['date']} {name}预测 {p['value']} {unit}，经验范围 {p['lower']}～{p['upper']} {unit}；回测MAE {p['mae']} {unit}。" for p in points]
    facts = [{'id': f'H{i + 1}', 'table': base['subject'], 'name': name, 'kind': 'aggregate',
              'business_time': r['__period'], 'properties': {
                  '__period': {'label': '业务日', 'value': r['__period']},
                  base['metric']: {'label': name, 'value': r.get(metric.get('value_alias') or base['metric']), 'unit': unit}},
              'source': {'database': core.settings.db['database'], 'table': base['subject'], 'queried_at': str(date.today()), 'scope': '本地观测日汇总'}} for i, r in enumerate(rows)]
    if base['metric'] == 'planned_flight_count' and not plan.get('history_cutoff_explicit'):
        future = deepcopy(base)
        old = base['query_context']['slots']['period']
        new = [str(start), str(targets[-1] + timedelta(days=1))]
        for condition in future['conditions']:
            value = condition.get('value')
            if isinstance(value, str):
                for previous, replacement in zip(old, new):
                    if value.startswith(previous) and condition.get('op') in ('>=', '<'):
                        condition['value'] = replacement + value[len(previous):]
                        break
        known = json_safe(execute(conn, compiler.aggregate(future)))
        planned = {r['__period']: r.get(metric.get('value_alias') or base['metric']) for r in known if r.get('n', 0)}
        for point in points:
            value = planned.get(point['date'])
            point['planned_value'] = float(value) if value is not None else None
        warning += '已有未来计划单列展示，不属于统计预测。'
        lines.extend(f"{point['date']} 已同步计划 {point['planned_value']:g} 架次（计划值，不是预测值）。" for point in points if point.get('planned_value') is not None)
    methodology = ('回测误差使用 MAE（平均绝对误差）。每个预测步长使用 7 个滚动起点，按回测 MAE 选择方法。'
                   '经验区间以该方法回测绝对误差的 90 分位数为半宽。')
    history_note = f'实际训练数据：{min(series)}至{max(series)}，共 {len(series)} 个有效观测日。'
    result.update(answer='\n'.join([history_note, *lines, methodology, warning]),
        forecast={'metric': name, 'unit': unit, 'as_of': str(max(series)),
                  'training_start': str(min(series)), 'training_end': str(max(series)),
                  'quality': 'experimental', 'points': points, 'methodology': methodology,
                  'history': [{'date': str(d), 'value': v} for d, v in sorted(series.items())], 'warning': warning},
        main_rows=[{'日期': p['date'], '预测值': p['value'], '下界': p['lower'], '上界': p['upper'],
                    '方法': p['method'], '回测MAE': p['mae'],
                    **({'已有计划值': p['planned_value']} if 'planned_value' in p else {})} for p in points], main_total=len(points),
        metric={'name': name + '（预测）', 'unit': unit, 'description': warning, 'target': base['subject']},
        sql=statement.sql, sql_params=json_safe(statement.params),
        claims=[{'text': line, 'fact_ids': [f['id'] for f in facts]} for line in (history_note, *lines, methodology)]
            + [{'text': warning, 'fact_ids': [], 'kind': 'limitations'}],
        evidence={'facts': facts, 'queried_at': str(date.today()), 'coverage': []},
        field_meta={plan['subject']: {k: {'label': k} for k in ('日期', '预测值', '下界', '上界', '方法', '回测MAE')}},
        elapsed=round(perf_counter() - started, 3))
    trace.finish('forecast_backtest', '预测与回测完成；真实计划值与预测值分别解释')
    result['execution_trace'] = trace.snapshot()
    return result
