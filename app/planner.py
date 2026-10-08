"""Question interpretation using only the current catalog and configured bindings."""
from datetime import date
from decimal import Decimal
import json
import re

from app.query import Compiler
from app.agent.planning import reconcile_anchor


class Planner:
    def __init__(self, catalog, name_index=None, enterprise_names=None, chat=None, trace=None):
        self.catalog = catalog
        self.compiler = Compiler(catalog)
        self.name_index = name_index or {}
        self.enterprise_names = enterprise_names or {}
        self.chat = chat
        self.trace = trace

    def surname(self, question):
        # Prefer explicit compound surnames; never treat the last character of
        # a company name as part of a two-character surname.
        compounds = ('欧阳', '司马', '上官', '诸葛', '东方', '皇甫', '尉迟', '公孙',
                     '慕容', '宇文', '司徒', '司空', '夏侯', '令狐', '长孙', '南宫',
                     '独孤', '闻人', '澹台', '公羊', '赫连', '宗政', '濮阳', '淳于',
                     '单于', '太叔', '申屠', '仲孙', '轩辕', '钟离', '闾丘', '鲜于',
                     '拓跋', '百里', '东郭', '西门', '公冶', '端木', '呼延')
        term = '(?:' + '|'.join(compounds) + r'|[\u4e00-\u9fff])'
        boundary = r'(?=的|员工|人员|职工|人|同事|情况|信息|女|男|[\s，,、。?？]|或|和|及|$)'
        patterns = [r'(' + term + r')姓' + boundary,
                    r'(?:姓氏(?:为|是|[:：])?|姓)\s*[“"\']?(' + term + r')[”"\']?' + boundary]
        matches = []
        for pattern in patterns:
            for match in re.finditer(pattern, question):
                # “夏姓女员工” means surname 夏 + gender 女, not also 姓女.
                if not any(match.start() < old.end() and old.start() < match.end() for old in matches):
                    matches.append(match)
        values = {match.group(1) for match in matches}
        if len(values) > 1:
            raise ValueError('检测到多个姓氏，请一次指定一个姓氏，或明确各姓氏之间的筛选关系')
        if matches and any(re.search(r'不|非|排除|除去', question[max(0, m.start()-4):m.start()]) for m in matches):
            raise ValueError('当前姓氏规则支持包含筛选；排除姓氏请明确排除条件，不能按包含条件查询')
        return next(iter(values)) if values else None

    def complete_identity_query(self, question, plan, allow_extra=False):
        """Conservative coverage check for fallback, not a guess at missing filters."""
        spec = self.catalog.entities[plan['subject']]
        if (not spec.get('name_lookup') or plan['intent'] not in ('detail', 'count')
                or plan.get('focus_tables') or plan.get('time_bucket')):
            return False
        name_field, company_field = spec['bindings'].get('name'), spec['bindings'].get('company')
        # Multiple names cannot be treated as a complete single-identity query
        # (the current predicate compiler combines conditions with AND).
        if sum(c['col'] == name_field for c in plan['conditions']) != 1:
            return False
        if any((c.get('table') or plan['subject']) != plan['subject'] or
               (not allow_extra and c['col'] not in (name_field, company_field)) for c in plan['conditions']):
            return False
        remaining = question
        surname = self.surname(question)
        if surname:
            patterns = [re.escape(surname) + r'姓',
                        r'(?:姓氏(?:为|是|[:：])?|姓)\s*[“"\']?' + re.escape(surname) + r'[”"\']?']
            for pattern in patterns:
                remaining = re.sub(pattern, '', remaining)
        tokens = list(spec['aliases'])
        tokens += [name for table, name in self.names(question) if table == plan['subject']]
        company = self.company(question)
        tokens += [alias for alias, code in {**self.enterprise_names,
                   **self.catalog.overrides.get('enterprise_aliases', {})}.items() if code == company]
        tokens += ['查找', '查询', '查看', '检索', '列出', '统计', '详细信息', '档案信息',
                   '员工信息', '人员信息', '信息', '详情', '明细', '档案', '情况', '记录',
                   '共有', '总共', '总数', '数量', '人数', '多少', '几名', '几人', '有',
                   '名', '条', '位', '个', '人', '的', '请', '帮我', '一下', '所有', '全部']
        if allow_extra:
            for condition in plan['conditions']:
                if condition['col'] in (name_field, company_field):
                    continue
                # Only extra predicates present in the validated plan can
                # account for extra question terms. Missing predicates cannot.
                attr = self.catalog.attribute(plan['subject'], condition['col'])
                tokens += [attr['label'], *attr.get('aliases', [])]
                values = condition['value'] if isinstance(condition.get('value'), list) else [condition.get('value')]
                for value in values:
                    text = str(value)
                    if text in question:
                        tokens.append(text)
                    translated = str(attr.get('enums', {}).get(text, ''))
                    if translated and translated in question:
                        tokens.append(translated)
            tokens += ['并且', '而且', '且', '并', '中', '是', '为', '等于', '属于']
        for token in sorted(set(tokens), key=len, reverse=True):
            if token:
                remaining = remaining.replace(token, '')
        return not re.sub(r'[\s，,。.!！?？：:;；“”"\'（）()]', '', remaining)

    def normalize_conditions(self, parsed, question):
        # A provider may express LIKE rather than the plan's prefix/contains
        # operators. Only normalize literal, explicitly requested text patterns.
        for condition in parsed['conditions']:
            op = condition.get('op')
            if not isinstance(op, str) or op.lower() != 'like':
                continue
            value = condition.get('value')
            if not isinstance(value, str) or '_' in value or '\\' in value:
                continue
            if value.endswith('%') and value.count('%') == 1:
                raw, op = value[:-1], 'prefix'
            elif value.startswith('%') and value.endswith('%') and value.count('%') == 2:
                raw, op = value[1:-1], 'contains'
            else:
                continue
            if raw and raw in question:
                condition.update(op=op, value=raw)

    def company(self, question):
        candidates = {**self.enterprise_names, **self.catalog.overrides.get('enterprise_aliases', {})}
        hits = [(len(alias), alias, code) for alias, code in candidates.items() if alias in question]
        return max(hits)[2] if hits else None

    def names(self, question):
        hits = [(len(name), table, name) for table, names in self.name_index.items()
                for name in names if len(name) >= 2 and name in question]
        if not hits:
            return []
        longest = max(item[0] for item in hits)
        return [(table, name) for size, table, name in hits if size == longest]

    def period(self, question):
        today = date.today()
        if '今年' in question:
            return str(today.year)
        if '去年' in question:
            return str(today.year - 1)
        if '上个月' in question or '上月' in question:
            year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            return '%04d-%02d' % (year, month)
        if '本月' in question or '这个月' in question:
            return '%04d-%02d' % (today.year, today.month)
        match = re.search(r'(20\d{2})[年/-](\d{1,2})月?', question)
        if match:
            month = int(match.group(2))
            if not 1 <= month <= 12:
                raise ValueError('月份无效')
            return '%s-%02d' % (match.group(1), month)
        match = re.search(r'(20\d{2})年', question)
        return match.group(1) if match else None

    def rules(self, question):
        catalog = self.catalog
        candidates = catalog.retrieve(question)
        names = self.names(question)
        surname = self.surname(question)
        if not surname and re.search(r'姓氏|姓(?!名)', question):
            raise ValueError('检测到姓氏表达但无法安全解析，请使用“姓夏的员工”或“夏姓员工”等明确格式')
        explicit = [table for table in candidates if any(alias.lower() in question.lower()
                    for alias in catalog.entities[table]['aliases'])]
        subject = explicit[0] if explicit else (names[0][0] if names else None)
        if not subject and surname:
            people = [table for table, spec in catalog.entities.items() if spec.get('name_lookup')]
            subject = people[0] if len(people) == 1 else None
        if not subject:
            roots = [root for root in catalog.entities if catalog.relation_targets(question, root)]
            if len(roots) > 1:
                preferred = [root for root in roots if any(
                    cfg.get('default_unscoped') and any(a in question for a in cfg.get('aliases', []))
                    for cfg in catalog.overrides.get('relations', {}).get(root, {}).values())]
                roots = preferred or roots
            if len(roots) == 1:
                subject = roots[0]
        company = self.company(question)
        if not subject and company:
            roots = [table for table, spec in catalog.entities.items() if spec.get('enterprise_lookup')]
            subject = roots[0] if len(roots) == 1 else None
        if not subject:
            raise ValueError('尚未匹配到业务对象，请说明资金、会议或其他具体业务；也可在本体管理中补充别名')
        spec = catalog.entities[subject]
        focus = catalog.relation_targets(question, subject)
        intent = ('sum' if re.search(r'总金额|总额|金额合计|工资合计|工资总额|实发合计|金额汇总|余额合计', question)
                  else 'count' if re.search(r'多少|几条|几份|几个|几名|几人|总数|数量|人数|笔数', question)
                  else 'detail')
        if intent == 'count' and '多少' in question and re.search(r'工资|薪酬|余额', question) and not re.search(r'条|人|记录|笔', question):
            intent = 'detail'
        if intent == 'sum' and any(attr.get('aggregation') == 'nonadditive' and
                any(alias in question for alias in attr.get('aliases', []))
                for attr in spec['attributes'].values()):
            raise ValueError('该字段是时点快照，不能逐笔相加；请明确账户和业务时点，或在本体中定义余额口径')
        target = focus[0] if intent in ('sum', 'count') and len(focus) == 1 else subject
        if intent in ('sum', 'count') and len(focus) > 1:
            intent = 'detail'
        metric = None
        if intent == 'sum':
            metrics = catalog.entities[target]['metrics']
            matches = [(len(alias), key) for key, cfg in metrics.items()
                       for alias in cfg.get('aliases', []) if alias in question]
            metric = max(matches)[1] if matches else None
        conditions = []
        bindings = spec['bindings']
        if surname:
            if not spec.get('name_lookup') or not bindings.get('name'):
                raise ValueError('姓氏筛选需要明确人员档案及姓名字段映射，请指定员工或人员业务对象')
            conditions.append({'col': bindings['name'], 'op': 'prefix', 'value': surname})
        for table, name in names:
            if table != subject:
                catalog.path(subject, table)
            field = catalog.entities[table]['bindings'].get('name')
            if field:
                conditions.append({'table': table, 'col': field, 'op': '=', 'value': name})
        if company:
            field = bindings.get('company')
            if not field:
                raise ValueError('当前业务对象缺少企业身份映射，请先配置 company 绑定')
            conditions.append({'col': field, 'op': '=', 'value': company})
        period = self.period(question)
        time_table = target if intent in ('sum', 'count') else subject
        time_field = catalog.entities[time_table]['bindings'].get('time')
        if period:
            if not time_field:
                raise ValueError('该业务尚未定义业务时间字段，不能用入库时间代替，请补充时间口径')
            conditions.append({'table': time_table, 'col': time_field, 'op': 'prefix', 'value': period})
        code = re.search(r'(?<!\w)([A-Za-z][A-Za-z0-9()（）/_-]{3,})(?!\w)', question)
        if code and any(ch.isdigit() for ch in code.group(1)):
            if not bindings.get('code'):
                raise ValueError('当前业务没有编号字段映射')
            conditions.append({'col': bindings['code'], 'op': '=', 'value': code.group(1)})
        title = re.search(r'(?:名称为|名称是|标题为|标题是|名为)[“"\s]*([^”"，。]+)', question)
        if title:
            if not bindings.get('name'):
                raise ValueError('当前业务没有名称字段映射')
            title_value=re.split(r'有多少|共有多少|的情况|的明细',title.group(1),maxsplit=1)[0].strip()
            conditions.append({'col': bindings['name'], 'op': 'contains', 'value': title_value})
        threshold = re.search(r'(大于|超过|高于|小于|低于|不足)\s*(\d+(?:\.\d+)?)\s*(亿元|万元|万|元)?', question)
        if threshold:
            metrics = spec['metrics']
            defaults = [cfg for cfg in metrics.values() if cfg.get('default')]
            if len(defaults) != 1:
                raise ValueError('金额比较需要明确指标字段和单位')
            cfg = defaults[0]
            scale = {'元': Decimal(1), '万': Decimal(10000), '万元': Decimal(10000), '亿元': Decimal(100000000)}
            value = Decimal(threshold.group(2))
            if threshold.group(3):
                if cfg.get('unit') not in scale:
                    raise ValueError('指标没有可转换的金额单位')
                value *= scale[threshold.group(3)] / scale[cfg['unit']]
            conditions.append({'col': cfg['field'], 'op': '>' if threshold.group(1) in ('大于','超过','高于') else '<', 'value': value})
        bucket = None
        by_time = re.search(r'按(月份|月|年度|年|日期|天)', question)
        if by_time:
            if not time_field:
                raise ValueError('当前业务缺少用于分组的业务时间字段')
            bucket = {'field': time_field, 'grain': 'month' if by_time.group(1) in ('月份','月')
                      else 'year' if by_time.group(1) in ('年度','年') else 'day'}
        return {'subject': subject, 'disp': spec['name'], 'intent': intent,
                'conditions': conditions, 'focus_tables': focus,
                'aggregate_target': target, 'metric': metric, 'group_by': [], 'time_bucket': bucket}

    def parse(self, question):
        local, local_error = None, None
        if self.trace:
            self.trace.start('ontology', '匹配业务本体', '检索当前数据库表注释、业务对象与管理别名')
        try:
            local = self.rules(question)
        except ValueError as exc:
            local_error = str(exc)
        if self.trace:
            candidates = self.catalog.retrieve(question)
            self.trace.finish('ontology', '本体检索完成',
                              [self.catalog.entities[t]['name'] + ' · ' + t for t in candidates])
            self.trace.start('intent', '解析查询条件', '解析企业身份、姓名或姓氏、时间与统计方式')
        if local_error and not local_error.startswith('尚未匹配到业务对象'):
            raise ValueError(local_error)
        if self.trace:
            details = []
            if local:
                details = ['主体：' + local['disp'] + ' · ' + local['subject'],
                           '查询方式：' + {'detail':'信息明细', 'count':'记录计数', 'sum':'金额合计'}[local['intent']]]
                for c in local['conditions']:
                    table = c.get('table') or local['subject']
                    attr = self.catalog.attribute(table, c['col'])
                    description = ('以“%s”开头' % c['value'] if c['op'] == 'prefix' else
                                   '包含“%s”' % c['value'] if c['op'] == 'contains' else
                                   '%s %s' % (c['op'], c['value']))
                    details.append(attr['label'] + '：' + description)
            self.trace.finish('intent', '本体规则解析完成' if local else '需要模型辅助解析', details)
        if local and self.complete_identity_query(question, local):
            if self.trace:
                self.trace.start('model', '模型辅助解析', '检查是否需要模型补充查询计划')
                self.trace.finish('model', '全部条件已由本体规则确定，无需模型改写 SQL', state='skipped')
            return self.compiler.validate(local), ('本体规则·姓氏筛选' if self.surname(question) else '本体规则·精确身份查询')
        if self.chat:
            extra = [table for table, _ in self.names(question)]
            context = self.catalog.context(question, extra)
            if not context and local:
                context = self.catalog.context(question, [local['subject']])
            if context:
                if self.trace:
                    self.trace.start('model', '模型辅助解析', '仅向模型提供相关本体，生成受约束的结构化查询计划')
                prompt = (
                    '将问题转成结构化查询计划，只输出 JSON。仅使用提供的本体表、字段、关系和指标。'
                    '不能遗漏问题的筛选条件；不能选择不相关主体。缺少映射或存在歧义时填写 unresolved。'
                    'conditions 中 table 可指定相关表；币种和业务时间必须遵从本体，创建时间不能代替业务时间。'
                    'intent=detail|count|sum；aggregate_target 必须填本体中的表名，绝不能填 id 或字段名；metric 使用定义的指标键。'
                    '区分筛选锚点与分析粒度：subject 保留已确定身份/企业筛选的锚点；'
                    '统计关联对象时用 aggregate_target，查询关联明细时用 focus_tables，不能因粒度不同而丢弃锚点条件。'
                    '问任职、付款节点等具体关系时填写 focus_tables。数值和日期必须来自问题。'
                    '不得默认增加问题未要求的审批状态、任职状态或其他筛选。'
                    '按月/年/日分组时 time_bucket={field:业务时间字段,grain:month|year|day}。'
                    'subject 必须是相关本体 JSON 的表名键，不可填 Employee 等概念名或中文名。'
                    'op 仅允许 =、!=、>、>=、<、<=、prefix、contains、in、null、notnull。'
                    '姓氏用姓名字段 prefix，value 只填姓氏，例如夏，不带 %；不输出 like。'
                    '返回 {subject,intent,conditions:[{table,col,op,value}],focus_tables,aggregate_target,metric,group_by,time_bucket,unresolved}。'
                    '当前日期：' + date.today().isoformat() + '\n相关本体：' + json.dumps(context, ensure_ascii=False))
                try:
                    content = self.chat(prompt, question, max_tokens=1200, timeout=40)
                    match = re.search(r'\{.*\}', content, re.S)
                    parsed = json.loads(match.group()) if match else None
                except Exception:
                    parsed = None
                if isinstance(parsed, dict) and parsed:
                    if not isinstance(parsed.get('subject'), str) or parsed['subject'] not in context:
                        if local and self.complete_identity_query(question, local):
                            if self.trace:
                                self.trace.finish('model', '模型主体无效；完整本体规则计划通过校验，采用规则计划')
                            return self.compiler.validate(local), '本体规则·主体纠偏'
                        raise ValueError('模型选择的主体不在本题相关本体内，请明确业务对象')
                    parsed['disp'] = self.catalog.entities[parsed['subject']]['name']
                    parsed.setdefault('intent', 'detail')
                    parsed.setdefault('conditions', [])
                    parsed.setdefault('focus_tables', [])
                    for key in ('conditions','focus_tables','group_by'):
                        if parsed.get(key) is None:
                            parsed[key]=[]
                    if not isinstance(parsed['conditions'], list) or any(not isinstance(c, dict) for c in parsed['conditions']):
                        raise ValueError('模型返回的筛选条件格式无效')
                    if not isinstance(parsed['focus_tables'], list):
                        raise ValueError('模型返回的关联目标格式无效')
                    self.normalize_conditions(parsed, question)
                    # A rule anchor and an explicitly requested relation target
                    # are different roles, not necessarily conflicting subjects.
                    if local and parsed['subject'] != local['subject']:
                        if self.complete_identity_query(question, local):
                            if self.trace:
                                self.trace.finish('model', '模型主体与确定身份不符；采用完整的本体规则计划')
                            return self.compiler.validate(local), '本体规则·主体纠偏'
                        parsed=reconcile_anchor(self.catalog,local,parsed)
                        if self.trace:
                            self.trace.start('plan_roles', '协调筛选锚点与分析粒度',
                                             '核验明确请求的关联目标；不改变条件所属表或扩大查询范围')
                            self.trace.finish('plan_roles', '关联目标已规范为锚点下的查询计划',
                                ['筛选锚点：'+local['subject'],
                                 '分析目标：'+parsed['plan_roles']['analysis_target']])
                    # Exact identity and explicit period constraints are deterministic.
                    if local and parsed['subject'] == local['subject']:
                        company_field=self.catalog.entities[local['subject']]['bindings'].get('company')
                        company_code=self.company(question)
                        if company_code and company_field:
                            lookup={**self.enterprise_names,**self.catalog.overrides.get('enterprise_aliases',{})}
                            aliases={alias for alias,code in lookup.items() if code==company_code and alias in question}
                            # Do not additionally compare a resolved abbreviation with a
                            # full company-name/snapshot field; the canonical key is sufficient.
                            parsed['conditions']=[c for c in parsed['conditions'] if not (
                                (c.get('table') or parsed['subject'])==parsed['subject'] and
                                c.get('col')!=company_field and isinstance(c.get('value'),str) and
                                c['value'] in aliases)]
                        # Explicit local intent and target fix the grain, including plain
                        # counts whose target is the root rather than its id field.
                        if local['intent'] in ('sum','count'):
                            parsed['intent']=local['intent']
                            parsed['aggregate_target']=local['aggregate_target']
                        elif parsed['intent']=='detail':
                            parsed['aggregate_target']=local['subject']
                        for condition in local['conditions']:
                            table = condition.get('table', local['subject'])
                            parsed['conditions'] = [c for c in parsed['conditions']
                                if (c.get('table') or parsed['subject'], c.get('col')) != (table, condition['col'])]
                            parsed['conditions'].append(condition)
                        parsed['focus_tables'] = list(dict.fromkeys(parsed['focus_tables'] + local['focus_tables']))
                        if local['metric']:
                            parsed['metric'] = local['metric']
                        if local['focus_tables'] and local['intent'] in ('sum','count'):
                            parsed['aggregate_target'] = local['aggregate_target']
                        if local.get('time_bucket'):
                            parsed['time_bucket'] = local['time_bucket']
                            parsed['group_by'] = [field for field in parsed.get('group_by', [])
                                                  if field != local['time_bucket']['field']]
                    resolved=local['conditions'] if local else []
                    for condition in parsed['conditions']:
                        if condition in resolved:
                            continue
                        value=condition.get('value')
                        if condition.get('op') in ('null','notnull'):
                            if not re.search(r'空|缺失|填写|有数据',question):
                                raise ValueError('模型添加了问题未要求的空值筛选，请明确筛选条件')
                            continue
                        values=value if isinstance(value,list) else [value]
                        attr=self.catalog.attribute(condition.get('table') or parsed['subject'],condition.get('col'))
                        enums=attr.get('enums',{})
                        if any(str(v) not in question and not (
                            str(v) in enums and str(enums[str(v)]) in question) for v in values):
                            raise ValueError('模型添加的筛选值不在问题或已解析身份中，请明确筛选条件')
                    validated = self.compiler.validate(parsed)
                    if local and self.surname(question) and not self.complete_identity_query(question, validated, allow_extra=True):
                        raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
                    if self.trace:
                        self.trace.finish('model', '结构化计划通过本体、字段与筛选值校验')
                    return validated, 'LLM·本体校验'
                if self.trace:
                    self.trace.finish('model', '模型未返回可用计划；检查本体规则能否安全接管', state='skipped')
                if local and self.surname(question) and not self.complete_identity_query(question, local):
                    raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
        elif self.trace:
            self.trace.start('model', '模型辅助解析', '检查模型配置')
            self.trace.finish('model', '模型未启用，使用本体规则', state='skipped')
        if local_error:
            raise ValueError(local_error)
        if local and self.surname(question) and not self.complete_identity_query(question, local):
            raise ValueError('姓氏已识别，但额外筛选条件未全部解析，不能扩大查询范围，请明确其余条件或重试')
        return self.compiler.validate(local), '本体规则'
