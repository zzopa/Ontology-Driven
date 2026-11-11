"""Adaptive visualization metadata builder for business queries.

Generates structured chart and KPI specifications (visual_meta) for:
- stats: Big number KPI cards for totals, counts, averages
- donut: Proportional distribution rings for multi-category breakdowns
- bar: Ranked horizontal comparison bars for dimensions
- trend: Time-series sequences for monthly/quarterly/daily temporal groups
"""
from decimal import Decimal
import json
from typing import Any, Dict, List, Optional


PALETTE = [
    '#2563eb',  # Blue
    '#10b981',  # Emerald
    '#f59e0b',  # Amber
    '#8b5cf6',  # Violet
    '#ec4899',  # Pink
    '#06b6d4',  # Cyan
    '#f97316',  # Orange
    '#64748b',  # Slate
]


def safe_num(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(Decimal(str(val).replace(',', '')))
    except Exception:
        return 0.0


def build_visual_meta(
    plan: Dict[str, Any],
    main_rows: List[Dict[str, Any]],
    main_total: int,
    metric: Optional[Dict[str, Any]] = None,
    catalog: Any = None,
    related: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Inspects query plan and results to build structured visual metadata."""
    if not main_rows or main_total == 0:
        return {'kind': 'none'}

    intent = plan.get('intent', 'detail')
    subject = plan.get('subject', '')
    target = plan.get('aggregate_target') or subject
    subject_name = catalog.entities[subject]['name'] if catalog and subject in catalog.entities else subject

    # Case -1: ACDM Turnaround Gantt
    if plan.get('intent') == 'turnaround' or plan.get('turnaround_data'):
        turnaround_data = plan.get('turnaround_data')
        if not turnaround_data:
            from app.turnaround import get_turnaround_data
            fno = plan.get('query_identifier') or plan.get('flight_no')
            turnaround_data = get_turnaround_data(fno, plan.get('flight_date')) if fno else None
        if turnaround_data:
            return {'kind': 'turnaround_gantt', **{key: value for key, value in turnaround_data.items()
                    if key not in ('source_row', 'process_row')}}

    # Case 0: Flight Milestone Fishbone Diagram
    from app.agent.fishbone import is_flight_row, build_fishbone_meta
    if intent == 'detail' and main_rows and any(is_flight_row(r) for r in main_rows[:3]):
        for r in main_rows:
            if is_flight_row(r):
                node_row = r
                if subject.split('.')[-1] == 'std_flight_core' and r.get('id') is not None:
                    for item in related or []:
                        if item.get('table', '').split('.')[-1] != 'std_flight_process':
                            continue
                        for process in item.get('rows', []):
                            if str(process.get('flight_id')) != str(r['id']):
                                continue
                            ext = process.get('ext_json') or {}
                            if isinstance(ext, str):
                                try:
                                    ext = json.loads(ext)
                                except ValueError:
                                    ext = {}
                            node_row = {**(ext if isinstance(ext, dict) else {}), **r, **process}
                fishbone = build_fishbone_meta(node_row, plan=plan, total_flights=main_total)
                if fishbone.get('nodes'):
                    return fishbone

    # Case 1: Temporal Trend (e.g. __period in rows, hour, or date)
    val_alias = (metric or {}).get('value_alias') or (metric or {}).get('field') or ('total' if intent in ('sum', 'metric') else 'n')
    temporal_keys = ('__period', 'hour', 'hour_of_day', 'peak_hour', 'flight_date', 'exec_date', 'month', 'week', 'operation_plan_day', 'operation_exec_day')
    is_temporal = any(k in r for r in main_rows for k in temporal_keys) and not any(k in r for r in main_rows for k in ('airline_code', 'aircraft_type', 'agency', 'attribute', 'flight_task_cd', 'terminal', 'craft_site_cd', 'stand_code', 'gate_cd', 'gate_code', 'stand_type', 'delay_reason', 'abnormal_reason', 'origin_airport', 'dest_airport'))
    if intent in ('count', 'sum', 'metric') and len(main_rows) > 1 and is_temporal:
        series = []
        unit = (metric or {}).get('unit', '架次' if intent in ('count', 'metric') else '')
        for r in main_rows:
            if r.get('__period') is not None:
                period_label = str(r['__period'])
            elif r.get('peak_hour') is not None:
                pk_str = str(r['peak_hour'])
                period_label = pk_str[11:16] if len(pk_str) >= 16 and ('T' in pk_str or ' ' in pk_str) else pk_str
            elif r.get('hour') is not None:
                period_label = f"{int(r['hour']):02d}时"
            elif r.get('hour_of_day') is not None:
                period_label = f"{int(r['hour_of_day']):02d}时"
            else:
                first_t = next((r.get(k) for k in ('flight_date', 'exec_date', 'month', 'week', 'operation_plan_day', 'operation_exec_day') if r.get(k) is not None), None)
                period_label = str(first_t)[:10] if first_t is not None else '未标注'
            val = safe_num(r.get(val_alias, r.get('total', r.get('n', 0))))
            series.append({'label': period_label, 'value': val})
        return {
            'kind': 'trend',
            'title': f"{(metric or {}).get('name') or subject_name}分布趋势",
            'series': series,
            'unit': unit,
            'metric_label': (metric or {}).get('name', '架次' if intent == 'count' else '统计值')
        }

    # Case 2: Multi-row Grouped Distribution (Donut / Bar)
    if intent in ('count', 'sum', 'metric') and len(main_rows) > 1:
        # Determine group key
        sample = main_rows[0]
        group_keys = [k for k in sample.keys() if k not in ('total', 'mx', 'n', 'missing', 'invalid', '__period', val_alias)]
        group_key = group_keys[0] if group_keys else None
        
        attr_label = '分组'
        if group_key and hasattr(catalog, 'dimensions') and group_key in catalog.dimensions:
            attr_label = catalog.dimensions[group_key].get('name') or group_key
        elif group_key and catalog and target in catalog.entities:
            attr = catalog.entities[target].get('attributes', {}).get(group_key, {})
            attr_label = attr.get('label') or group_key

        total_sum = sum(safe_num(r.get(val_alias, r.get('total', r.get('n', 0)))) for r in main_rows)
        unit = (metric or {}).get('unit', '架次' if intent in ('count', 'metric') else '')

        # Build items
        items = []
        for i, r in enumerate(main_rows):
            val = safe_num(r.get(val_alias, r.get('total', r.get('n', 0))))
            pct = round((val / total_sum * 100), 1) if total_sum > 0 else 0.0
            label_val = str(r.get(group_key) or '未标注') if group_key else f'项目 {i+1}'
            items.append({
                'label': label_val,
                'value': val,
                'percent': pct,
                'color': PALETTE[i % len(PALETTE)],
                'count': int(r.get('n', 0))
            })

        # Sort descending by value
        items.sort(key=lambda x: -x['value'])

        if 2 <= len(items) <= 6:
            return {
                'kind': 'donut',
                'title': f'按{attr_label}占比分布',
                'slices': items,
                'total': round(total_sum, 2),
                'unit': unit,
                'group_label': attr_label
            }
        else:
            return {
                'kind': 'bar',
                'title': f'按{attr_label}排名统计',
                'bars': items[:10],
                'unit': unit,
                'group_label': attr_label
            }

    # Case 3: Single Aggregate KPI Stats
    if intent in ('count', 'sum', 'metric') and len(main_rows) == 1:
        row = main_rows[0]
        stats = []
        if intent == 'count':
            stats.append({
                'label': f'{subject_name}总数',
                'value': str(row.get('n', main_total)),
                'unit': '条'
            })
        elif intent == 'metric':
            unit = (metric or {}).get('unit', '')
            metric_name = (metric or {}).get('name', '统计指标')
            raw_val = row.get(val_alias)
            if raw_val is None:
                for cand in ((metric or {}).get('code'), 'total', 'n'):
                    if cand and row.get(cand) is not None:
                        raw_val = row[cand]
                        break
            stats.append({
                'label': metric_name,
                'value': str(raw_val if raw_val is not None else '—'),
                'unit': unit
            })
            if row.get('n') is not None:
                stats.append({
                    'label': '统计样本数',
                    'value': str(row['n']),
                    'unit': '条'
                })
        else:
            tot = row.get('total')
            unit = (metric or {}).get('unit', '元')
            metric_name = (metric or {}).get('name', '总金额')
            stats.append({
                'label': metric_name,
                'value': str(tot),
                'unit': unit
            })
            stats.append({
                'label': '参与记录数',
                'value': str(row.get('n', main_total)),
                'unit': '笔'
            })
            if row.get('mx') is not None:
                stats.append({
                    'label': '单笔最大',
                    'value': str(row.get('mx')),
                    'unit': unit
                })
        return {
            'kind': 'stats',
            'title': f'{(metric or {}).get("name") or subject_name}统计概览',
            'stats': stats
        }

    return {'kind': 'none'}
