"""Data coverage boundary assessment before execution.

Checks whether temporal filters fall within the current database's watermark,
preventing users from misinterpreting unsynchronized dates as true "zero results".
"""
from datetime import date
import re
from typing import Any, Dict, Optional, Tuple


class DataCoverageService:
    """Detects temporal boundaries and validates query ranges against database coverage."""

    def __init__(self, catalog: Any):
        self.catalog = catalog

    def assess_plan(self, plan: Dict[str, Any], coverage_override: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Evaluates whether the plan's time conditions fall within known data coverage."""
        coverage = coverage_override or self.catalog.overrides.get('coverage')
        if not coverage or not isinstance(coverage, dict):
            return {'status': 'AVAILABLE', 'message': None}

        start_date = coverage.get('start') or coverage.get('start_date')
        end_date = coverage.get('end') or coverage.get('end_date')
        if not start_date or not end_date:
            return {'status': 'AVAILABLE', 'message': None}

        subject = plan.get('subject')
        time_table = plan.get('aggregate_target') or subject
        spec = self.catalog.entities.get(time_table, {})
        time_field = spec.get('bindings', {}).get('time')
        if not time_field:
            return {'status': 'AVAILABLE', 'message': None}

        # Check conditions on time_field
        for cond in plan.get('conditions', []):
            table = cond.get('table') or subject
            col = cond.get('col')
            if col != time_field or table != time_table:
                continue

            op = cond.get('op')
            val = str(cond.get('value', ''))
            # Pattern matching for dates like YYYY, YYYY-MM, YYYY-MM-DD
            if op in ('=', 'prefix', '<', '<=', '>', '>='):
                # Check if val is later than end_date
                if val > end_date and op in ('=', 'prefix'):
                    return {
                        'status': 'OUT_OF_COVERAGE',
                        'coverage_start': start_date,
                        'coverage_end': end_date,
                        'queried_value': val,
                        'message': f'当前数据仅覆盖 {start_date} 至 {end_date}，所查询的日期（{val}）超出覆盖范围，暂未包含该时段记录。'
                    }
                if val < start_date and op in ('=', 'prefix'):
                    return {
                        'status': 'OUT_OF_COVERAGE',
                        'coverage_start': start_date,
                        'coverage_end': end_date,
                        'queried_value': val,
                        'message': f'当前数据仅覆盖 {start_date} 至 {end_date}，所查询的日期（{val}）早于当前最早记录时间。'
                    }

        return {'status': 'AVAILABLE', 'coverage_start': start_date, 'coverage_end': end_date, 'message': None}
