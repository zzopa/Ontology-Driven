"""Offline question plans use the active ontology's aliases and field bindings."""
from datetime import date, datetime, timedelta, timezone
import re


def parse_question(question, catalog):
    text = re.sub(r'[\s？?，,。]', '', question)
    text = re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})日',
                  lambda m: f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}', text)
    definitions = catalog.overrides['offline_queries']
    matches = [(len(alias), alias, spec) for spec in definitions for alias in spec['aliases'] if alias in text]
    if not matches:
        raise ValueError('模型外发已禁用。请指定本体对象，例如：昨天计划航班量、昨天执行航班量、昨天出港航班明细、查询航空公司。')
    _, alias, query = max(matches, key=lambda item:item[0])
    text = text.replace(alias, '', 1)
    subject = query['subject']
    entity = catalog.entities[subject]
    plan = {'subject': subject, 'disp': entity['name'], 'intent': query['intent'],
            'conditions': [], 'focus_tables': [], 'group_by': list(query.get('group_by', []))}
    period = re.search(r'今天|昨天|前天|最近7天|\d{4}-\d{2}-\d{2}', text)
    if period:
        value = period.group()
        text = text[:period.start()] + text[period.end():]
        today = datetime.now(timezone(timedelta(hours=8))).date()
        if value == '最近7天':
            start, end = today - timedelta(days=6), today + timedelta(days=1)
        else:
            start = today - timedelta(days={'今天':0,'昨天':1,'前天':2}[value]) if value in {'今天','昨天','前天'} else date.fromisoformat(value)
            end = start + timedelta(days=1)
        coverage = catalog.overrides.get('coverage')
        if coverage and (str(start) < coverage['start'] or str(end) > coverage['end_exclusive']):
            raise ValueError('日期超出本地副本范围：' + coverage['start'] + ' 至 ' + coverage['end_exclusive'] + '（结束日期不含）')
        field = entity['bindings'].get('time')
        if not field:
            raise ValueError('此对象没有业务日期绑定')
        plan['conditions'] += [{'col':field,'op':'>=','value':str(start)}, {'col':field,'op':'<','value':str(end)}]
    elif entity['bindings'].get('time'):
        raise ValueError('请指定业务日期；副本仅含最近7天的数据')
    if '出港' in text and '进港' in text:
        raise ValueError('查询进出港总量时请使用计划航班量或执行航班量，不同时指定两个方向')
    for word, value in [('出港','D'),('进港','A')]:
        if word in text:
            field = entity['bindings'].get('direction')
            if not field:
                raise ValueError('此对象没有进出港方向绑定')
            enums = entity.get('attributes', {}).get(field, {}).get('enums', {})
            value = next((code for code, label in enums.items() if label == word), value)
            plan['conditions'].append({'col':field,'op':'=','value':value})
            text = text.replace(word, '', 1)
    flight = re.search(r'(?<![A-Z0-9])[A-Z0-9]{2}\d{3,4}(?!\d)', text)
    if flight:
        field = entity['bindings'].get('code')
        if not field:
            raise ValueError('此对象没有航班号绑定')
        plan['conditions'].append({'col':field,'op':'=','value':flight.group()})
        text = text[:flight.start()] + text[flight.end():]
    if '按天' in text or '每天' in text:
        if plan['intent'] != 'count' or not entity['bindings'].get('time'):
            raise ValueError('此查询不支持按天分组')
        plan['time_bucket'] = {'field':entity['bindings']['time'],'grain':'day'}
        text = text.replace('按天','').replace('每天','')
    if '关联实际航班' in text:
        targets = catalog.relation_targets('实际航班', subject)
        if len(targets) != 1 or plan['intent'] != 'detail':
            raise ValueError('本体无法唯一确定实际航班关系')
        plan['focus_tables'] = targets
        text = text.replace('关联实际航班','')
    for token in ['帮我','查一下','查询','查看','统计','列出','请问','请','是多少','有多少','多少','共有','总共','的','和','航班','明细','信息','情况','数量','条','记录']:
        text = text.replace(token, '')
    if text:
        raise ValueError('离线本体尚不能完整解析这些条件：' + text + '；不会忽略条件执行查询')
    return plan
