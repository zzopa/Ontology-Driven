# hbairport 智能问数

基于 FastAPI 的本体驱动问数服务，前端使用 Next.js App Router + Tailwind CSS + Lucide + Framer Motion，默认读取本地 PostgreSQL `hbairport01`。查询主体来自当前库结构，不写死为员工或合同；资金、会议、资产、采购等对象使用同一个查询引擎。

完整链路：启动采集元数据 → 编译本地业务本体 → 问题生成结构化计划 → 协调筛选锚点与统计粒度 → 校验并编译参数化 SQL → 实时查询 → 来源事实 → 证据分析与结论校验 → 内容编排 → 流式正文与分组回答。

2026-10-08 新增通用规划/证据回答层，保留原有准确摘要、明细和权限校验，不为特定企业或问题定制答案。全模块职责、当前完成范围与后续步骤见 [通用问数智能体方案](docs/general-query-agent.md)。

实施状态与业务边界见 [实施说明](docs/ontology-optimization-plan.md)，实测结果见 [验收报告](docs/verification-2026-10-03.md)。

## 目录规则

```text
app/
  config.py         配置加载
  metadata.py       表/字段注释、复合约束、版本及快照
  ontology.py       通用业务本体编译、相关本体检索、关系路径消歧
  planner.py        问题解析、企业简称/人员身份/业务时间约束
  execution.py      可公开的实际执行步骤、状态、耗时与失败记录
  query.py          计划校验、参数化 SELECT、全量统计及分页
  evidence.py       事实与关系、来源、单位、覆盖范围和结论检查
  evidence_transport.py   字段/来源去重、无损分批传递与完整性还原校验
  validation.py     有时间预算的只读关系抽样
  management.py     本体配置校验、历史版本、并发冲突及回滚
  llm.py            模型调用适配
  agent/            通用锚点/粒度协调、证据分析策略、完整结论内容编排
  core.py           服务编排
  main.py           FastAPI 生命周期与 HTTP 接口
  admin/            身份认证、模型/账号配置、会话、词条和审计
frontend/
  app/              问答、紧凑嵌入、本体管理、管理后台及深色设计样式
  components/       业务组件、分页明细、证据、图谱及弹层
    admin/          按模块组织的模型、人员、会话、词条、设置、审计表单
  lib/              API、NDJSON、类型及辅助表单组句
  tests/            前端逻辑测试
  out/              Next.js 静态构建产物，由 FastAPI 8088 同源提供
web/                旧版 HTML 归档，非在线入口
data/
  business_ontology.json    人工语义、本体关系与指标（长期保留）
  ontology_history/        管理页保存/回滚产生的历史版本
  ask_demo_memory.json     按版本隔离的解析计划缓存，不缓存业务答案
  runtime/                 自动生成的当前结构、上一份结构、编译本体和检查报告
  admin/                   独立管理库与加密密钥（不提交，需配套备份）
scripts/
  paths.py                 离线脚本共用路径
  db/{schema,graph,probes,renderers}/   数据库离线脚本，根目录不放 db_*.py
  demo/                    问答演示与验收脚本
  service/                 Windows 服务重启与启动检查
artifacts/
  graphs/                  图谱展示产物
  renderers/               演示页面产物
  evaluations/             验收结果
  logs/                    后台运行日志
tests/                     单元与可选 TEMP 表集成测试
docs/                      实施方案、报告
ask_web.py                 启动入口
start.ps1                  Windows 快捷启动
restart.cmd                双击重启（检查、后台启动、日志与自动打开页面）
config.json                非密钥配置
config.local.json          本机密钥，不提交
config.local.example.json  本机配置模板
requirements.txt           依赖
```

旧的 `data/hbairport_schema.json` 等离线文件仅供历史演示，不是在线服务的启动输入。

## 安装与启动

确认 PostgreSQL 已运行，数据库可连接。在项目根目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.local.example.json config.local.json
# 填写本机数据库密码与模型 API Key
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -X utf8 ask_web.py
```

已存在 `config.local.json` 时不要执行复制命令覆盖密钥。也可运行 `start.ps1`。

日常重启直接双击根目录的 **`restart.cmd`**。脚本读取实际配置与当前环境变量（默认端口 8088），先检查数据库端口、前端产物和监听进程归属，再停止本项目旧服务、后台启动并等待 `/api/status` 与页面就绪，成功后自动打开浏览器。正在执行的问答会中断；不会删除会话、配置或数据库数据，不会重建前端。修改前端后请先执行 `npm run build`。

日志保存在 `artifacts/logs/restart-*.stdout.log`、`restart-*.stderr.log`，双击窗口不会自动关闭，失败会提示原因。端口属于其他项目或无法核实归属时不会强杀；支持现有项目虚拟环境执行 `ask_web.py` 的启动方式，不接受任意 Python/Uvicorn 启动命令。遇到进程访问权限不足可右键“以管理员身份运行”。

仅检查、不重启（PowerShell 终端执行）：

```powershell
& ([scriptblock]::Create([IO.File]::ReadAllText("$PWD\scripts\service\restart.ps1", [Text.Encoding]::UTF8))) -ProjectRoot "$PWD" -CheckOnly
```

问答：[http://127.0.0.1:8088/](http://127.0.0.1:8088/)；本体管理：[http://127.0.0.1:8088/ontology](http://127.0.0.1:8088/ontology)；弹窗嵌入：[http://127.0.0.1:8088/embed](http://127.0.0.1:8088/embed)。前端构建需要 Node.js 20.9+，生产不需要额外 Node 服务。前端开发、表单规则及安全嵌入见 [前端与嵌入说明](docs/frontend-and-embedding.md)。

服务启动时从配置的 schema 重新采集表注释、字段注释、类型、默认值、主键、唯一约束和完整复合外键。轻量刷新不调用模型，不复制全库业务数据。结构、库标识和人工规则共同决定本体版本；更新后旧的明细令牌失效。数据库不可连接时启动失败，不使用旧快照伪装刷新成功。

## 配置与局域网

- `config.json`：监听地址/端口、允许访问网段、数据库连接、模型地址/名称、目录、schema、排除项、分页大小、SQL 超时、关系深度、证据预算及抽样检查预算。
- `config.local.json`：数据库密码与模型 API Key；不得复制到浏览器或提交。
- 环境变量：`HBASK_DB_PASSWORD`、`HBASK_LLM_API_KEY`、`HBASK_HOST`、`HBASK_PORT`、`HBASK_LAN_CIDR`。兼容旧 `ASK_WEB_HOST`、`ASK_WEB_LAN_CIDR`。

监听默认 `0.0.0.0:8088`，允许本机和配置网段。当前配置为 `192.168.31.0/24`，电脑地址以实际网卡为准；变更网络时同步修改 CIDR。Windows 防火墙是否允许该网络访问需要单独确认，不直接放开公网。

管理后台：[http://127.0.0.1:8088/admin](http://127.0.0.1:8088/admin)。直接使用 hbairport01 现有账号登录，问数与后台共用账号、角色和会话，不维护第二套用户。后台支持模型配置/检测/切换、持久化会话、企业简称/字段/指标词条、系统策略与操作审计，详情见 [管理后台说明](docs/admin-console.md)。

本体写操作和全局配置沿用原系统 admin 角色。问数强制读取现有部门/企业数据范围及模块查看权限；未登录不能问数。账号、密码和角色继续在原填报项目维护。详见 [数据权限说明](docs/hbairport01-data-permissions.md)。

## 本体维护与业务扩展

管理页可查看中文属性、原始注释、单位、类型、JSON 映射及关系来源；对象定义和字段解释可用表单维护，复杂关系/指标仍用 JSON 高级编辑。修改先写入草稿，保存前校验，保存后即时生效。每次保存留下历史版本，旧 revision 保存会被拒绝；回滚同样保留回滚前版本，不改业务库注释。

`business_ontology.json` 主要对象：

- `entities`：业务名称、`aliases`、记录粒度、身份字段、`bindings`、字段解释和 `metrics`。
- `bindings`：`name`、`company`、`code`、`time` 对应真实字段。查询业务月份不能默认改用创建时间。
- `json_attributes`：JSON 所在列、路径、中文标签、是否结构化。兼容 JSON 列与存有合法 JSON 的文本列。
- `enterprise_aliases`：例如“信科”“信科公司” → 规范企业代码，解析器不会再追加“企业全称等于简称”的冲突条件。
- `logical_links` / `relationship_hints`：完整连接两端；复合连接用 `pairs` 数组。候选/停用/失效边不参与查询；历史配置边为兼容仍启用，但不能称为已审核。
- `relations`：业务关系名称、目标别名、角色路径 `via`；多个有效路径未消歧时明确报错。
- `sensitive_fields`：不对前端和模型暴露的字段；嵌套 JSON 中证件号码/联系电话也会过滤。

新增表会在重启/刷新后自动进入允许范围内的目录。表注释、字段注释支持基础检索；补充自然语言别名、身份/业务时间、关系和指标后即可增强新业务，不必添加按表名分支的 Python 逻辑。注释不足时不会凭同名字段自动启用 JOIN，也不会猜测指标单位或交易类型枚举。

当前资金指标按币种与交易类型分组，合同金额不解释成已付款，余额快照不逐笔累加。薪酬金额为文本字段，其中存在非数字原值；检测到这种值会拒绝合计，必须先确认原系统的解密/标准化方式，不能由问数模型猜测。

## 查询与回答范围

支持“夏姓员工”“姓夏的员工”“欧阳姓员工”等姓氏前缀筛选，使用当前本体的人员姓名/企业绑定，不写死人员物理表。企业简称和姓氏条件同时保留；全部条件确定的简单姓氏或单一精确姓名问题（如“查找张浩的情况”）不调用模型解析。多姓名、额外部门、性别、时间或关联业务条件不走此快速路径，不能为了提速丢掉筛选。模型的字面 `LIKE '夏%'` 可规范为 `prefix: 夏`，不接受多重通配符或凭空筛选。额外条件未完整解析时会要求澄清，不能退化为全公司查询。

问答区始终保留可折叠“查询执行过程”：显示当前步骤、耗时、实际筛选、SQL/绑定参数、命中数量及来源核验。通过 NDJSON `step` 事件实时更新，同一阶段的开始/完成只占一项；成功和失败过程都加密保存在管理库中，历史会话可展开。展示的是可核验执行记录，不是模型内部思维链或原始模型推理；旧会话无步骤数据时明确说明，不事后补造。

COUNT/SUM 覆盖所有满足条件的记录；主表预览 5 条、内部取数上限和关联分页不限制关联查询的主记录范围。明细默认读取最多 50 条主记录、每个关联表最多 200 条，超过部分明确标为未读取，可通过明细分页查看；不能把已读取部分解释为数据库全量。多条一对多关系分别取数，相关筛选使用 EXISTS，关联目标去重，避免笛卡尔乘积污染合计。

已读取且脱敏后的证据不再因模型预算静默丢失：字段解释、来源和关系说明共享定义，保留每个原始值（含 NULL、空字符串、0、false）；超过 `query.evidence_budget`（单批 JSON 字符数，默认 45000）时完整分批，默认 `query.evidence_workers=3` 并发（1–4）。发送前把各批还原，与查询原始事实和关系逐项比较。超长记录用完整编号分片传递，不把截断内容当作完整值；单个分片不能支撑整条记录结论。分批会增加模型调用次数与耗时；单次模型请求默认 `query.evidence_timeout_seconds=120` 秒，临时网络/限流错误最多按 `query.evidence_retries=1` 重试一次，鉴权错误和无依据结论不重试。等待期间每 10 秒更新实际执行状态。

每批 `coverage.provided` 按本批真实完整记录重算，`query_provided` 表示数据库已读取数，跨批端点身份引用不计为完整记录。执行过程显示批次、字符数、请求/核验状态和最终传递数量；接口的 `model_evidence_delivery` 同时记录传递完整性。请求失败不会阻断其余批次，也不会伪称全部已传递。命中数量和已读取的主记录身份名单由系统确定性生成，模型仅补充核验通过的结论。

明细及数据库 COUNT/SUM 结果均可进入通用证据分析；发现、分析解释、证据不足和核对建议分别展示，解释和建议不等于数据库已证实事实。原摘要另存为 `query_summary`，最终分组在 `answer_document`，兼容旧 `answer` 与会话。编排模型只重排已核验结论，不能新增正文/金额或遗漏引用；没有附加分析不调用。配置 `query.answer_composition_enabled=true`、`answer_composition_budget=18000` 字符、`answer_composition_timeout_seconds=20` 秒；预算超出或请求/校验失败直接保留完整确定性分组，不截断、不重试。首段不等编排，但编排可能增加总耗时。

证据摘要阶段 `query.evidence_enable_thinking=false` 默认使用非思考模式，仅在硅基流动 DeepSeek-V4 / Qwen3 可切换系列上添加对应参数（排除 Thinking-only 名称）；不切换管理员选中的模型，不改变 SQL 计划调用，也不对其他提供商发送未知参数。接口参数依据 [硅基流动说明](https://docs.siliconflow.cn/docs/api/chat-completions-post)。

返回全部允许展示的字段，包括空值与嵌套内容；点击单元格可看完整内容。明细通过短期令牌翻页，不接受前端自带 SQL。

模型只生成计划和带事实编号的结论，不直接执行任意模型 SQL。SQL 值绑定参数，标识符来自目录，执行使用只读事务、超时和不带 ANALYZE 的 EXPLAIN 预检。同名人员需要进一步定位。

答案附来源表/记录键、业务时间、查询时间、单位和覆盖范围；检查引用、数字与金额单位，失败时返回确定性事实摘要。该检查不是完整语义证明，仍可能需要人工审核。统计优先确定性表达；多分组显示摘要和完整分组表。

正文与步骤同时流式输出：查询证据整理后、任何模型证据请求开始前先发送确定性命中摘要；各模型批次完成核验后立即追加结论，不等待最慢批次。正文按实际追加顺序去重，最终回答保持相同顺序。仍发送每 10 秒的真实等待状态，不使用假打字延迟，不发送未经检查的模型 token。每段发送前检查业务权限版本，权限变更后不继续泄露新正文。前端独立展示实时正文卡，停止等待或连接失败仍保留已收到部分并明确标注“未完整完成”；成功后由最终结果替换，不重复展示。该方式改善首段等待，不承诺外部模型总耗时降低。

## 接口

| 方法与路径 | 用途 |
| --- | --- |
| POST /api/ask | 普通问答 |
| POST /api/ask/stream | NDJSON：progress、answer_delta、result、error |
| POST /api/records | token、page：主表或统计参与记录 |
| POST /api/related | token、table、page：关联明细 |
| GET /api/status | 当前库、目录规模、结构差异、启动/查询耗时 |
| GET /api/ontology | 原始规则及编译本体、revision、编辑权限 |
| PUT /api/ontology | document、revision：原系统 admin 角色保存 |
| GET /api/ontology/history | 历史版本 |
| POST /api/ontology/rollback | revision、expected：授权回滚 |
| POST /api/metadata/refresh | 授权重新采集 |
| POST /api/ontology/validate-relations | 授权受控抽样检查 |
| /api/auth/* | 初始化、登录、退出与修改密码 |
| /api/admin/{models,users,glossary,settings,audit,overview} | 按角色保护的管理接口 |
| /api/conversations/* | 使用统一业务身份管理本人会话 |

问数 POST 支持可选 `conversation_id`，返回会话/记录 ID。非浏览器调用需 `X-Hbask-Request: 1` 并保存 Cookie；即时明细令牌与该身份绑定。API 客户端需使用现有业务账号登录。

关联抽样每条最多 100 个非空键；单查询默认 2 秒，总预算 30 秒。报告显示类型差异、多目标、超时/未检查；抽样通过不等于全量唯一，更不能证明业务含义。报告在 `data/runtime/relation_validation.json`。

## 验收

```powershell
# 单元测试：无数据库时跳过实库 TEMP 表集成测试
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 可选集成测试：只创建连接私有 TEMP 表，事务结束自动清理
$env:HBASK_INTEGRATION='1'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 实库只读规则基线，参考结果与生成 SQL 对照
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.evaluate

# 回放现有李姓员工查询快照，逐值检查实际模型请求载荷（默认模型桩，不调用外部模型）
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.evidence_delivery_regression
# 使用当前启用模型做真实传递测试（会产生模型调用费用，不重新执行业务 SQL）
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.evidence_delivery_regression --live-model

# 已启动 8088 时：真实模型调用、API 与分页联调
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.smoke_api
```

基线包含预期拒绝/澄清，不把拒绝异常数据算成查询失败。实库数据会变化，测试不冻结全库数字。模型联调有外部调用费用和延迟，单元测试默认不调用模型。

## 离线演示

从项目根目录以模块运行，例如：

```powershell
.\.venv\Scripts\python.exe -m scripts.db.schema.db_schema_extract
.\.venv\Scripts\python.exe -m scripts.db.graph.db_build_graph
.\.venv\Scripts\python.exe -m scripts.demo.graph2sql "查询合同 GL-XZ-2026001 的所有信息"
```

历史脚本部分默认 `hbairport`，不代表在线服务的 `hbairport01`；可能覆盖旧图谱演示产物，执行前核对用途。
