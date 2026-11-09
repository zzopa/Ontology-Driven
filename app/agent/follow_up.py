"""Follow-up question normalization and slot inheritance across multi-turn queries.

Allows conversational queries like "出港呢？", "中天智航呢？", "那去年呢？", "有哪些合同？"
to inherit previous turn's subject, time range, filters, and intent with minimal delta changes.
"""
from copy import deepcopy
import re
from typing import Any, Dict, Optional


class FollowUpNormalizer:
    """Normalizes elliptical or conversational follow-up questions using prior plan context."""

    FOLLOW_UP_PATTERNS = [
        re.compile(r'.{1,20}(?:呢|吗|呀|吧)[？?]?$'),
        re.compile(r'^(?:那|那么|如果是|换成|换作|改成|查一下|再看|看看)\s*.{1,20}$'),
        re.compile(r'^(?:有哪些|有什么|有哪些记录|明细|详细信息|详情|具体情况|列出来|看下明细)[？?]?$'),
        re.compile(r'^(?:共有多少|一共有多少|总共有多少|一共有几条|共有几条|有多少人|有几个人|有多少份|总数是多少)[？?]?$'),
        re.compile(r'^(?:出港|进港)(?:呢|的呢|航班)?$'),
    ]

    def is_follow_up(self, question: str, previous_plan: Optional[Dict[str, Any]]) -> bool:
        """Determines whether a question is an elliptical follow-up that requires prior context."""
        if not previous_plan or not isinstance(previous_plan, dict) or not previous_plan.get('subject'):
            return False
        text = question.strip()
        if not text or len(text) > 40:
            return False
        # If it explicitly matches one of the follow-up patterns
        for pattern in self.FOLLOW_UP_PATTERNS:
            if pattern.search(text):
                return True
        # Very short queries (< 10 chars) without explicit subject entity
        if len(text) <= 8 and ('呢' in text or '那' in text or '有' in text):
            return True
        return False

    def apply(self, question: str, previous_plan: Dict[str, Any], catalog: Any, planner: Any) -> Optional[Dict[str, Any]]:
        """Applies slot inheritance and delta replacement to produce a new plan."""
        if not self.is_follow_up(question, previous_plan):
            return None

        plan = deepcopy(previous_plan)
        subject = plan.get('subject')
        if not subject or subject not in catalog.entities:
            return None

        spec = catalog.entities[subject]
        bindings = spec.get('bindings', {})
        modified = False

        # 1. Delta check: Intent swap
        old_intent = plan.get('intent', 'detail')
        if re.search(r'有哪些|明细|详细|详情|列出|具体|情况', question):
            if old_intent != 'detail':
                plan['intent'] = 'detail'
                modified = True
        elif re.search(r'总额|合计|总金额|金额是多少|总计', question):
            if old_intent != 'sum':
                plan['intent'] = 'sum'
                modified = True
        elif re.search(r'多少|几条|几人|几份|总数|数量|人数|笔数', question):
            if old_intent != 'count':
                plan['intent'] = 'count'
                modified = True

        # 2. Delta check: Enterprise replacement
        new_company = planner.company(question)
        company_field = bindings.get('company')
        if new_company and company_field:
            conditions = [c for c in plan.get('conditions', [])
                          if c.get('col') != company_field and c.get('col') != 'enterprise_name']
            conditions.append({'col': company_field, 'op': '=', 'value': new_company})
            plan['conditions'] = conditions
            modified = True

        # 3. Delta check: Person name replacement
        names = planner.names(question)
        name_field = bindings.get('name')
        if names and name_field:
            matched_name = names[0][1]
            conditions = [c for c in plan.get('conditions', []) if c.get('col') != name_field]
            conditions.append({'table': subject, 'col': name_field, 'op': '=', 'value': matched_name})
            plan['conditions'] = conditions
            modified = True

        # 4. Delta check: Surname replacement
        surname = planner.surname(question)
        if surname and name_field:
            conditions = [c for c in plan.get('conditions', []) if c.get('col') != name_field]
            conditions.append({'col': name_field, 'op': 'prefix', 'value': surname})
            plan['conditions'] = conditions
            modified = True

        # 5. Delta check: Time period replacement
        new_period = planner.period(question)
        time_table = plan.get('aggregate_target') or subject
        time_field = catalog.entities[time_table]['bindings'].get('time')
        if new_period and time_field:
            conditions = [c for c in plan.get('conditions', []) if c.get('col') != time_field]
            conditions.append({'table': time_table, 'col': time_field, 'op': 'prefix', 'value': new_period})
            plan['conditions'] = conditions
            modified = True

        # 6. Delta check: Direction swap (for flight data like std_flight_core)
        if 'in_out' in spec.get('attributes', {}):
            if re.search(r'出港|离港|起飞', question):
                conditions = [c for c in plan.get('conditions', []) if c.get('col') != 'in_out']
                conditions.append({'col': 'in_out', 'op': '=', 'value': 'D'})
                plan['conditions'] = conditions
                modified = True
            elif re.search(r'进港|到港|到达', question):
                conditions = [c for c in plan.get('conditions', []) if c.get('col') != 'in_out']
                conditions.append({'col': 'in_out', 'op': '=', 'value': 'A'})
                plan['conditions'] = conditions
                modified = True

        if modified:
            plan['follow_up'] = True
            return plan

        return None
