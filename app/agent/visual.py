"""Adaptive visualization metadata builder for business queries.

Generates structured chart and KPI specifications (visual_meta) for:
- stats: Big number KPI cards for totals, counts, averages
- donut: Proportional distribution rings for multi-category breakdowns
- bar: Ranked horizontal comparison bars for dimensions
- trend: Time-series sequences for monthly/quarterly/daily temporal groups
"""
from decimal import Decimal
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
    catalog: Any = None
) -> Dict[str, Any]:
    """Inspects query plan and results to build structured visual metadata."""
    if not main_rows or main_total == 0:
        return {'kind': 'none'}

    intent = plan.get('intent', 'detail')
    subject = plan.get('subject', '')
    target = plan.get('aggregate_target') or subject
    subject_name = catalog.entities[subject]['name'] if catalog and subject in catalog.entities else subject

    # Case 0: Flight Milestone Fishbone Diagram
    from app.agent.fishbone import is_flight_row, build_fishbone_meta
    if main_rows and any(is_flight_row(r) for r in main_rows[:3]):
        for r in main_rows:
            if is_flight_row(r):
                fishbone = build_fishbone_meta(r, plan=plan, total_flights=main_total)
                if fishbone.get('nodes'):
                    return fishbone

    # Case 1: Temporal Trend (e.g. __period in rows or time_bucket)
    if intent in ('count', 'sum') and len(main_rows) > 1 and any('__period' in r for r in main_rows):
        series = []
        unit = (metric or {}).get('unit', '笔' if intent == 'count' else '')
        for r in main_rows:
            period_label = str(r.get('__period') or '未标注')
            val = safe_num(r.get('total') if intent == 'sum' else r.get('n', 0))
            series.append({'label': period_label, 'value': val})
        return {
            'kind': 'trend',
            'title': f'{subject_name}趋势分布',
            'series': series,
            'unit': unit,
            'metric_label': (metric or {}).get('name', '数量' if intent == 'count' else '合计')
        }

    # Case 2: Multi-row Grouped Distribution (Donut / Bar)
    if intent in ('count', 'sum') and len(main_rows) > 1:
        # Determine group key
        sample = main_rows[0]
        group_keys = [k for k in sample.keys() if k not in ('total', 'mx', 'n', 'missing', 'invalid', '__period')]
        group_key = group_keys[0] if group_keys else None
        
        attr_label = '分组'
        if group_key and catalog and target in catalog.entities:
            attr = catalog.entities[target].get('attributes', {}).get(group_key, {})
            attr_label = attr.get('label') or group_key

        total_sum = sum(safe_num(r.get('total') if intent == 'sum' else r.get('n', 0)) for r in main_rows)
        unit = (metric or {}).get('unit', '笔' if intent == 'count' else '')

        # Build items
        items = []
        for i, r in enumerate(main_rows):
            val = safe_num(r.get('total') if intent == 'sum' else r.get('n', 0))
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
    if intent in ('count', 'sum') and len(main_rows) == 1:
        row = main_rows[0]
        stats = []
        if intent == 'count':
            stats.append({
                'label': f'{subject_name}总数',
                'value': str(row.get('n', main_total)),
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
            'title': f'{subject_name}统计概览',
            'stats': stats
        }

    return {'kind': 'none'}
