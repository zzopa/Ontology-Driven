# 知航 · ACDM 智能问询（hbairport 智能问数）

基于 FastAPI 的业务本体驱动智能问数与民航 A-CDM 运营问询平台，前端采用 Next.js App Router + TypeScript + Tailwind CSS + Lucide + Framer Motion，编译为静态资源后由 FastAPI 同源托管于 `8088` 端口。服务默认读取本地 PostgreSQL 数据库（当前 `config.json` 默认数据源为 `ops_ask` 运营标准库，可随时切换为 `hbairport` 综合业务库或 `acdm` 本地副本）。

平台围绕“授权业务目录 + 受控查询编译 + 可追溯来源事实”构建：查询主体与规则来自当前库元数据及版本化本体配置，不依赖外部图数据库或向量库，业务数据查询全程在只读事务中以参数化 SQL 执行，绝不由模型直接生成并执行任意 SQL。

> 专题文档索引：
> - 复合问数、模拟机场对标、PDF与站内订阅、短期预测：[ACDM 分析能力](docs/acdm-analysis.md)
> - 运营库统一总控与澄清协议：[运营库统一问答](docs/ops-query-coordinator.md)
> - `ops_ask` 运营库与同库 ACDM 边界：[ops_ask 数据源](docs/ops-ask-data-source.md)
> - ACDM 本地副本切换说明：[ACDM 数据源切换](docs/acdm-local-test.md)
> - 通用规划、证据分析与内容编排：[通用问数智能体方案](docs/general-query-agent.md)
> - 账号、模型、会话与审计后台：[管理后台说明](docs/admin-console.md)
> - `hbairport01` 行列与指标权限：[数据权限说明](docs/hbairport01-data-permissions.md)
> - 工作台交互与宿主弹窗嵌入：[前端与嵌入说明](docs/frontend-and-embedding.md)
> - 实施状态与历史验收：[实施说明](docs/ontology-optimization-plan.md) · [验收报告](docs/verification/verification-2026-10-03.md) · [完整文档索引](docs/README.md)

---

## 核心架构与查询链路

```text
用户提问（支持自然语言 / 辅助表达 / 快捷问询 / 澄清选项确认）
  │
  ▼
1. 启动/刷新采集元数据 → 编译版本化业务本体 Catalog（不搬运全库业务数据）
  │
  ▼
2. 问数规划与总控路由（按数据源分支）
   ├─ ops_ask（默认开启 coordinator_enabled）：
   │    授权目录检索 → Coordinator 槽位提取与多轮追问 → 关键歧义澄清（outcome）
   │    → 专题报告 / 过站保障 / 常规结构化计划（最多 1 次目录选择 + 1 次详细规划 + 1 次纠偏）
   └─ hbairport / acdm：
        FollowUpNormalizer 多轮追问继承 → ACDM 本地规则 / 姓氏与精确身份快速路径
        → Planner 模型规划 + reconcile_anchor 锚点粒度协调（版本化计划缓存）
  │
  ▼
3. 校验与边界保护
   ├─ Compiler 白名单校验（主体/字段/关系路径/指标可用性）
   ├─ 业务数据覆盖边界检查（local_coverage / DataCoverageService）
   └─ EXPLAIN (FORMAT JSON) 预检 + 参数绑定 + 只读事务 + 查询超时
  │
  ▼
4. 实时执行与结果组装
   ├─ 常规明细/计数/合计/已发布指标（全量统计 + 主/关联分页令牌）
   ├─ 过站保障诊断（std_flight_core/process 或同库 acdm.tb_dyn_flight）
   └─ A-CDM 校准专题报告（通用报告 / 地服简报 / 保障节点与登机桥看板）
  │
  ▼
5. 事实证据、自适应可视化与流式输出（NDJSON）
   └─ 来源事实包（脱敏 + 全字段保留）→ 确定性摘要先发（ops_ask 不由模型扩写事实）
      → 通用模式无损分批证据分析与校验（evidence_transport + prompts）→ 内容编排（compose_document）
      → 自适应图表元数据（visual_meta）→ 完整结果与执行轨迹落库加密
```

### 三套数据源模式

| 数据源 (`HBASK_DATA_SOURCE`) | 默认数据库 | 适用业务与访问权限 | 规划与回答机制 | 模型调用限制 |
| --- | --- | --- | --- | --- |
| **`ops_ask`**（当前默认） | `ops_ask` | 民航标准航班、同库 `acdm.tb_dyn_flight` 保障、货邮行生产量、登机桥与保障节点（当前 `ontology.json` 显式开放 26 个业务对象）；**仅限 `hbairport01` 的 `admin` 角色访问** | 启用 `Coordinator` 统一总控；停用计划缓存；业务事实正文由实时结果确定性生成，支持 `outcome` 澄清状态与结构化选项 | 禁止外发公网模型，仅允许本机或 `permitted_origins` 内网模型 |
| **`hbairport`** | `hbairport01` | 员工、合同、资金、会议、资产、采购等综合国资业务（`data/business_permissions.json` 映射 55 张表）；强制读取 `hbairport01` 部门/企业树范围（`DEPT_ONLY` / `DEPT_TREE` / `SELF_ONLY` / `ALL`）与指标白名单 | 启用 `Planner` + `FollowUpNormalizer` + `reconcile_anchor`；支持计划缓存；启用无损分批证据分析与 `compose_document` 内容编排 | 支持外部及管理后台配置模型 |
| **`acdm`** | `acdm_test_*` | 从 Oracle 只读抽样导入本地的 ACDM 测试库与 SQLite 中间副本；**仅限 `hbairport01` 的 `admin` 角色访问** | `app/acdm_ontology.py` 离线本体规则解析 + 本机/内网模型自由问答兜底 | 禁止外发公网模型，仅允许本机或 `permitted_origins` 内网模型 |

> 说明：三种模式下身份认证均只读连接 `hbairport01` 用户表（`app/business_access.py`），本服务不复制用户表或密码；未登录不允许问数、翻页或读取会话。

---

## 核心功能点

### 1. 运营总控、澄清协议与多轮追问
- **统一总控（`app/agent/coordinator.py`）**：面向 `ops_ask` 26 个显式开放业务对象，自动识别北京时间日期与相对窗口（今天/昨天/前天/近 N 天/本月）、计划与实际执飞口径、进出港方向、航班号与机号、航司参考字典以及首末班排序（`最早/首班` vs `最晚/末班`）。
- **五态 `outcome` 业务结果协议**：`POST /api/ask` 与 `/api/ask/stream` 在 `ok=true` 时返回明确业务结果状态：
  - `answered`：已确认口径并返回事实或可问范围说明。
  - `clarification_needed`：存在口径或对象缺失（如未说明计划/实际、未指定日期、保障诊断缺少单日或唯一航班号），附 `clarification.question` 与可点击 `options`。用户点击选项或补充文字后携带 `conversation_id`、`clarification_of`、`choice_id` 提交，由服务端会话恢复原上下文与锚定时间继续规划。
  - `unsupported`：请求的能力或指标尚未发布（如状态为 `PLANNED`/`UNAVAILABLE` 的指标、缺少口径限制的生产量合计）。
  - `no_data`：当前本地数据无匹配记录（明确提示不代表现实业务量为零）。
  - `out_of_coverage`：查询日期落在该对象本地已观测时间边界之外。
- **多轮追问上下文继承**：
  - `ops_ask` 总控支持简短追问继承（如“出港呢？”“明细”“多少条”），保留上一轮确认主体与筛选并差量更新。
  - 通用 `Planner` 集成 `FollowUpNormalizer`（`app/agent/follow_up.py`），支持企业、人名、姓氏、时间段、进出港及意图切换的槽位继承，并在 `execution_trace` 中记录 `follow_up` 步骤。

### 2. A-CDM 校准专题报告
- 由 `app/acdm_reports.py` 实现，支持自然语言匹配、工作台「A-CDM 速查」快捷芯片以及专用只读 API（`/api/acdm/reports/*`）：
  - **A-CDM 通用报告（`general`）**：按日/周/月/自定义区间输出 8 大板块核心指标（计划/执飞/取消、起飞/放行/始发/落地正常率、近机位靠桥率、旅客与货邮吞吐量、同比/环比、机坪与关舱/撤轮挡过程指标等），主从结构附关联明细与 KPI 看板。
  - **地服公司运行简报（`ground_briefing`）**：覆盖当日与次日 00:00–05:00 收尾态势、截止 22:00 全场 vs 地服（`agency=210` & `attribute=2403`）正常性双口径对照、次日开局与早始发（识别 `IS_REMOTE='Y'` 外场保障）、当日取消及 5 分钟内国内放行延误清单。
  - **保障节点质量与资源周转看板（`safeguard_board`）**：统计 `std_flight_safeguard_node` 各保障节点实测录入排行及占执飞比、`std_flight_air_bridge` 登机桥使用与桥载电/气次数、以及 `T2`/`T3` 航站楼分流情况，并显式标注能力边界。

### 3. 过站保障甘特图与航班生命周期鱼骨图
- **过站保障诊断（`app/turnaround.py`、`app/turnaround_standard.py`）**：
  - 按航班号或机号 + 明确业务日（`YYYY-MM-DD` / 今天 / 昨天 / 前天）查询标准表（`std_flight_core` + `std_flight_process`）或同库 `acdm.tb_dyn_flight` 保障记录；不将不同业务日的节点拼凑为同一过站过程。
  - 前端 `TurnaroundGantt`（`frontend/components/turnaround-gantt.tsx`）与 `TurnaroundFishbone`（`frontend/components/turnaround-fishbone.tsx`）支持**时序甘特图 / 鱼骨图双视图切换**、AIBT/SOBT/TOBT/TSAT 核心时隙条、关键节点链路、“全部作业 / 缺结束记录”过滤，以及关联不同日期保障记录的一键切换查询。
- **航班里程碑鱼骨卡片（`app/agent/fishbone.py`、`FlightMilestones`）**：
  - 航班明细查询自动从主表及 `std_flight_process` 提取出港/进港全生命周期里程碑节点（值机、机坪保障、登机、关舱门、撤桥、TOBT、申请推出、AOBT、ATOT / 前站起飞、ELDT、ALDT、AIBT、靠桥、开舱门、首件行李等），展示计划、预计、目标与实际时刻及延误状态，跨日时刻保留日期，未记录不冒充未发生。

### 4. 自适应可视化引擎（`visual_meta`）
- `app/agent/visual.py` 根据查询意图与返回行结构自动生成可视化规格，流式阶段通过 `visual_meta` 事件提前下发，前端 `ResultView` 自动渲染：
  - `stats`：单行计数、指标或金额统计 KPI 大数字卡。
  - `donut`：2–6 个分组占比的环形分布图与图例。
  - `bar`：超过 6 组的 Top-10 排名横向柱状图。
  - `trend`：含小时/高峰小时/日/周/月或 `operation_plan_day`/`operation_exec_day` 的时间序列柱状趋势图。
  - `turnaround_gantt` / `fishbone`：航班保障甘特图与节点鱼骨图。

### 5. 民航运营日与覆盖范围评估
- **运营日口径（`app/agent/operational_day.py`）**：提供午夜 00:00 至日切时刻归属上一业务日的窗口划分（`get_operational_date`、`get_operational_window`、`get_operational_hour`、`build_operational_conditions`）；`ops_ask` 可在管理后台“问数设置”中配置启用状态、日切小时（如 `05:00 至次日 04:59`）与时区。
- **数据覆盖检查（`app/coverage.py`、`local_coverage`）**：在执行前核对查询时间窗口与本地观测区间，避免把未同步日期的数据空缺误判为业务量为零；明确声明“最早/最晚观测日期不证明中间每一天已同步完整”。

### 6. 全量查询、无损证据传递、执行过程与答案编排
- **全量统计 + 独立分页**：`COUNT`/`SUM`/分组统计覆盖全部满足条件记录；明细主表预览 5 条、默认取数上限为主表 50 条 / 每个关联表 200 条，使用带权限版本号与会话身份绑定的短期分页令牌（`/api/records`、`/api/related`）翻页查看全部允许字段与嵌套 JSON。
- **可追溯执行过程（`app/execution.py`）**：NDJSON `step` 事件实时推进，展示真实步骤、耗时、SQL、绑定参数、命中数量与传递覆盖；不捏造或输出模型内部思维链，所有成功与失败轨迹均加密保存在会话管理库中。
- **无损证据与校验编排（`app/evidence.py`、`app/evidence_transport.py`、`app/agent/response.py`）**：
  - 敏感字段及嵌套 JSON 中的证件号码、联系电话等自动剔除；保留 `NULL`、空字符串、`0`、`false` 等原始值。
  - 超出 `query.evidence_budget`（默认 `45000` 字符）时自动无损分批、超长记录编号分片并发请求（`evidence_workers` 1–4），发送前逐批还原核对。
  - 证据阶段支持按 `query.evidence_enable_thinking=false` 对硅基流动可切换系列关闭思考模式。
  - 校验通过的批次随到随发 `answer_delta`；最后由 `compose_document` 生成 `evidence-answer-v1` 结构化分组文档（查询事实、主要发现、分析解释、证据边界、核对建议），不允许编排模型新增数字、改分类或遗漏引用；失败自动回退确定性事实摘要。

### 7. Oracle-to-PostgreSQL 纯 Python 数据同步（`ops_ask`）
- 位于 `app/sync/`（`oracle_source.py`、`pg_sink.py`、`runner.py`、`transforms.py`）与 `app/admin/data_sync.py`，直接从 Oracle FIMS / ACDM / FORMFILL 清洗写入 `ops_ask` PostgreSQL（问数查询时不直连 Oracle）：
  - 支持任务类型：`full_sync`（近 30 天 + 未来 7 天全量导入并清理超期历史）、`master_data`（航司/机场/代理/不正常原因/飞机/机位/登机口/登机桥/保障节点/跑道 10 类 `dim_*` 维表全量）、`flights_fims`、`flights_acdm`、`codeshares`（重建 `std_flight_link`）、`production`（货邮行生产量）、`calc_total`（动态汇总旅客）、`safeguards`（登机桥与 49 类保障节点）、`flights_scheduled`（FIMS+ACDM 水位增量）。
  - 支持管理员在后台探测 Oracle 源库连通性与表边界、同步前执行 `pg_dump` 压缩备份（保存至 `artifacts/backups/`）、按执行日区间补数或按水位增量、任务中止与对账。

### 8. 管理后台、本体治理与“不准反馈”闭环
- **管理后台（`/admin`，`frontend/components/admin-console.tsx`）** 与问数工作台共用 `hbairport01` 登录会话，按功能划分为两大分组：
  - **本体与问数引擎**（除「本体论词条」可供登录用户查看外，其余需 `admin` 角色）：
    1. **本体管理**：能力总览、业务概念（`concepts`）、同义词（`synonyms`）、属性、问答场景（`scenarios`）、词条映射、一键编译发布至运行时本体（`publish_catalog_to_ontology`）及版本历史。
    2. **数据模型**：浏览业务对象语义建模与底层物理表/字段结构。
    3. **指标管理**：维护已发布/规划中指标（`metrics`）、分析维度（`dimensions`）与预置筛选器（`filters`）。
    4. **规则管理**：管理延误判定、派生字段口径及版本化规则表达式。
    5. **能力注册**：配置已接入能力域、阶段状态与超范围兜底话术。
    6. **查询规划**：查看规划模板并执行 **Dry-Run 模拟演练**（支持选择运营日模式，实时预览命中同义词/概念/场景、生成计划、编译 SQL、覆盖评估与运营时间窗）。
    7. **问数设置**：配置本场机场锚点（`home_airport`）、民航运营日日切（`operational_day`）与会话/审计保留天数。
    8. **不准反馈**：管理问数页提交的「反馈不准」工单（待处理/跟进中/已修复/不处理），支持一键将反馈问法中的词条**晋升为本体同义词**。
    9. **本体论词条**：非管理员角色浏览企业简称、对象别名、字段解释及统计指标。
  - **平台运维与同步**：
    10. **后台概览**：运行统计、目录规模、在用模型与数据状态。
    11. **数据同步**（`admin`）：Oracle 状态探测、PG 水位快照、手动同步任务触发/中止与同步前备份。
    12. **模型配置**（`admin`）：添加、检测、切换 OpenAI 兼容模型接口（支持本机/内网 HTTP 与 HTTPS），API Key 使用 `Vault` 加密保存。
    13. **会话管理**：按登录本人隔离会话，支持分页检索、重命名、归档、导出与删除（权限变更后隐藏旧授权结果并提示重问）。
    14. **操作审计**（`admin`）：记录模型、本体、词条、同步、设置、清理等审计流水。
- **独立本体工作台（`/ontology`，`OntologyManager`）**：面向基础本体配置，提供对象绑定与字段中文解释/单位/指标表单维护、JSON 高级编辑、乐观锁版本冲突拦截、历史版本回滚、数据库元数据刷新（含 `POST /api/admin/schema/sync-diff` 结构差异比对）及受控只读关系抽样检查（报告保存至 `data/runtime/relation_validation.json`）。
- **工作台交互与宿主嵌入（`/`、`/embed`）**：
  - 支持 `Ctrl/⌘+Enter` 提交、6 项可编辑辅助查询表单、8 套预设主题与自定义前背景色（`ThemeSettings`，持久化于浏览器）、长文本/JSON 单元格弹窗复制。
  - `/embed` 去除侧栏，支持 `server.embed_parent_origins` 配置 CSP `frame-ancestors` 白名单与安全的 `hbask:close` `postMessage` 关闭联动。

---

## 目录结构

```text
app/
  config.py                 配置加载（config.json + config.local.json + data/<source>/profile.json + 环境变量）
  metadata.py               PostgreSQL 表/字段注释、约束、视图元数据采集与指纹快照
  metadata_diff.py          实时库结构与本地 metrics_schema / field_graph / business_ontology 差异比对
  ontology.py               通用业务本体 Catalog 编译、相关检索、路径消歧与能力目录输出
  management.py             本体 JSON 校验、历史归档、乐观锁版本控制与回滚
  planner.py                规则规划、企业简称/姓名/姓氏识别、指标槽位与模型辅助规划
  semantic_engine.py        声明式指标 Cube 语义层 SQL 编译器（配合 data/metrics_schema.json）
  query.py                  查询计划白名单校验、参数化 SELECT、指标/维度聚合与分页 SQL 编译
  coverage.py               查询前时间边界覆盖评估（DataCoverageService）
  execution.py              可公开执行步骤轨迹记录（ExecutionTrace）
  evidence.py               来源事实构建、敏感字段/JSON 脱敏与数值/单位/引用核验
  evidence_transport.py     字段/来源去重、无损并发分批传递与完整性核验
  validation.py             有时间预算的只读关系外键/逻辑连接抽样验证
  business_access.py        hbairport01 统一身份认证、bcrypt 校验与企业/部门/指标数据范围解析
  ops_governance.py         ops_ask 治理目录（概念/同义词/场景/指标/维度/规则/能力/ Dry-Run / 反馈晋升）
  acdm.py                   ACDM SQLite 中间副本只读执行器
  acdm_ontology.py          ACDM 本地本体规则解析器
  acdm_reports.py           A-CDM 校准通用报告、地服运行简报与保障节点/登机桥看板
  turnaround.py             ACDM 航班过站保障查询与甘特图载荷构建
  turnaround_standard.py    ops_ask 标准模型与同库 ACDM 过站保障读取
  llm.py                    OpenAI 兼容接口客户端（上下文隔离、内网白名单、禁用重定向防凭证泄露）
  core.py                   主服务编排（initialize / do_ask / 分页令牌 / local_coverage）
  main.py                   FastAPI 生命周期、LAN 访问控制、CSP 头与核心/报告 HTTP 接口
  agent/
    coordinator.py          ops_ask 统一总控（目录路由、澄清状态机、追问、残余条件检验）
    planning.py             筛选锚点与分析粒度协调（reconcile_anchor）及双路由意图分发
    analysis.py             受控多指标分析、同环比、多步连续追问与对标执行器
    cross_day.py            计划/取消/实际执行跨日口径对账与窗口边界跨日航班明细
    forecast.py             确定性滚动回测短期预测基线
    coordinator_lexicon.py  运营总控词表、码表、首末班与纯展示字段正则规则
    follow_up.py            多轮追问槽位继承（FollowUpNormalizer）
    operational_day.py      民航运营日（04:00/可配置日切）时间窗与运营时计算
    fishbone.py             A-CDM 航班进出港全生命周期鱼骨节点提取
    visual.py               自适应图表元数据构建（stats / donut / bar / trend / detail_table / fishbone / turnaround_gantt）
    prompts.py              通用证据分析提示词与分批推理策略
    response.py             多批结论汇总与内容编排（compose_document）
  admin/
    api.py                  认证、本人会话、模型、同步、治理、设置、反馈与审计路由
    auth.py                 同源校验（Origin / X-Hbask-Request）、Cookie 与角色校验
    data_sync.py            Oracle 同步任务调度、状态对账、源库探测与 pg_dump 备份
    glossary.py             本体词条 CRUD 与校验
    security.py             Fernet 加密（Vault）与令牌摘要
    service.py              AdminStore 单例管理
    store.py                SQLite 管理库（模型、会话、消息、审计、sync_runs、ask_feedback）
  sync/
    oracle_source.py        Oracle Thin 客户端流式抽取与探针
    pg_sink.py              PostgreSQL 标准表/视图写入、水位持久化与历史清理
    runner.py               全量/基础资料/航班/共享/生产量/汇总旅客/保障节点同步执行器
    transforms.py           Oracle 到 PG 标准模型字段清洗与时区转换

frontend/
  app/                      Next.js App Router 页面（/、/embed、/ontology、/admin）与全局样式
  components/               Workspace、ResultView、ExecutionProcess、OntologyManager、AdminConsole、
                            TurnaroundGantt、TurnaroundFishbone、FlightMilestones、ThemeSettings 等组件
    admin/                  14 个管理子模块面板（ontology-admin、data-model、metrics-admin、rules-admin、
                            capabilities-admin、planner-admin、ask-settings、feedback-admin、data-sync 等）
  lib/                      API/NDJSON 客户端、辅助组句、执行状态归并、答案文档、甘特/鱼骨布局计算与类型
  public/brand/             品牌 Logo 与图标资源
  tests/                    Node 原生 TS 逻辑测试（tests/*.test.ts）
  out/                      npm run build 静态构建产物（由 FastAPI 8088 同源提供）

data/
  business_ontology.json    hbairport 数据源人工语义、本体关系与指标定义
  business_permissions.json hbairport01 55 张业务表权限与企业归属映射
  metrics_schema.json       SemanticEngine 声明式指标 Cube 配置
  field_graph.json          字段图谱配置（用于 schema 差异比对）
  ask_demo_memory.json      hbairport 按本体版本隔离的解析计划缓存（不缓存业务答案）
  ops_ask/                  ops_ask 独立 profile.json、ontology.json、ontology_history/、runtime/、admin/
  acdm/                     acdm 独立 profile.json、SQLite 副本与运行目录
  runtime/                  hbairport 自动采集的 schema、graph_full、ontology_compiled 与关系检查报告
  admin/                    hbairport 管理库（management.sqlite3）与加密密钥（secret.key）

scripts/
  paths.py                  离线脚本公共路径
  ops_ask/seed_acdm_handbook.py  A-CDM 手册校准指标/维度/规则/概念/同义词初始化脚本
  service/                  data_source.py 数据源切换、restart.ps1 服务重启检查、acdm_model.py
    launchers/              辅助/历史数据源启停脚本（start.ps1、start_ops.bat/ps1、restart-lan.cmd、
                            start-hbairport.cmd、start-acdm.cmd、copy-acdm.cmd）
  db/                       ops_ask_execution.sql 视图脚本、acdm_snapshot/acdm_postgres 导入、历史图谱脚本
  demo/                     coordinator_evaluate、evaluate、evidence_delivery_regression、smoke_api、
                            acdm_smoke、admin_preview 等验证与演示脚本

tests/                      Python 单元测试、PostgreSQL TEMP 表集成测试与重启脚本 PowerShell 测试
docs/                       方案设计、数据源说明、权限说明（目录索引见 docs/README.md）
  verification/             历史阶段验收报告归档
ask_web.py                  Uvicorn 启动入口
start-ops-ask.cmd           核心入口：切换 ops_ask（LAN 0.0.0.0）并重启服务
restart.cmd                 核心入口：安全重启当前服务
start-report-worker.cmd     核心入口：启动 PDF 报表后台工作进程
config.json                 默认运行配置（非密钥）
config.local.json           本机数据库/Oracle 密码与模型 Key（不提交 Git）
config.local.example.json   本机密钥配置模板
requirements.txt            Python 依赖（fastapi、uvicorn、pg8000、oracledb、cryptography、bcrypt）
```

> 注：根目录旧的 `data/hbairport_schema.json`、`web/` 等文件属于历史演示归档，并非当前在线服务入口。

---

## 安装、启动与数据源切换

### 1. 首次安装

确认 PostgreSQL 已运行且目标数据库可连接（Node.js 需 **20.9+** 仅用于构建前端，生产运行无需 Node 服务）：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.local.example.json config.local.json
# 编辑 config.local.json，填写本机数据库密码（及可选模型 API Key / Oracle 同步凭据）
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -X utf8 ask_web.py
```

> 已存在 `config.local.json` 时不要重复覆盖密钥；也可使用 `.\scripts\service\launchers\start.ps1` 检查依赖产物后启动。

### 2. 日常重启与数据源切换

- **常用根目录入口**：
  - `start-ops-ask.cmd`：切换至 `ops_ask` 运营库（当前默认，LAN `0.0.0.0`）并调用 `restart.cmd`
  - `restart.cmd`：按当前配置安全重启服务
  - `start-report-worker.cmd`：启动 PDF 报告中心后台渲染进程
- **辅助与历史数据源脚本**（收拢于 `scripts/service/launchers/`）：
  - `start-hbairport.cmd`：切换至 `hbairport` 综合业务库并重启
  - `start-acdm.cmd`：切换至 `acdm` 本地副本并重启；`copy-acdm.cmd` 用于导出近 7 天 SQLite 中间副本
  - `start_ops.bat` / `start_ops.ps1`：前台直接运行 `ops_ask` 查看控制台日志
  - `start.ps1` / `restart-lan.cmd`：依赖检查直启与 LAN 重启快捷脚本
- **安全重启脚本 `restart.cmd`**：
  - 自动读取当前配置及环境变量（默认端口 `8088`），先校验数据库端口连通性、`frontend/out` 构建产物与端口监听进程归属（仅识别本项目的 `.venv` 启动 `ask_web.py` 进程，端口属其他项目时拒绝强杀），随后重启后台进程、等待 `/api/status` 与页面就绪并打开浏览器。
  - 仅执行检查不重启：
    ```powershell
    & ([scriptblock]::Create([IO.File]::ReadAllText("$PWD\scripts\service\restart.ps1", [Text.Encoding]::UTF8))) -ProjectRoot "$PWD" -CheckOnly
    ```
  - 日志输出至 `artifacts/logs/restart-*.stdout.log` 与 `restart-*.stderr.log`；修改前端代码后须先在 `frontend/` 重新执行 `npm run build`。

### 3. 访问入口

| 页面 | 默认地址 | 用途 |
| --- | --- | --- |
| 智能问数工作台 | `http://127.0.0.1:8088/` | 自然语言问询、辅助表单、A-CDM 速查、甘特/鱼骨图、明细分页、反馈提交 |
| 宿主弹窗嵌入 | `http://127.0.0.1:8088/embed` | 无侧栏紧凑视图，配合 `embed_parent_origins` 嵌入第三方业务系统 iframe |
| 业务本体工作台 | `http://127.0.0.1:8088/ontology` | 对象绑定、字段注释/单位、指标维护、JSON 编辑、版本回滚与关系抽样检查 |
| 综合管理后台 | `http://127.0.0.1:8088/admin` | 14 个管理与治理模块（本体管理、数据模型、指标/规则/能力、规划模拟、数据同步、模型、审计等） |

---

## 配置、局域网与环境变量

### 配置文件合并优先级

`app/config.py` 按以下顺序加载并深度合并配置（环境变量优先级最高）：

1. `config.json`（默认配置：`server.port=8088`、`server.lan_cidr="192.168.3.0/24"`、`data_source="ops_ask"`、`query.page_size=20`、`query.graph_main_limit=50`、`query.graph_related_limit=200`、`query.evidence_budget=45000`、`query.evidence_workers=3`、`query.answer_composition_enabled=true` 等）
2. `config.local.json`（本机密钥与本地覆盖，不得提交或暴露给前端）
3. `data/<data_source>/profile.json`（当 `data_source` 为 `ops_ask` 或 `acdm` 时加载对应目录与策略覆盖；例如 `data/ops_ask/profile.json` 设置 `server.host="127.0.0.1"`、`database.database="ops_ask"`、`database.startup_params.TimeZone="Asia/Shanghai"`、`metadata.schemas=["public","ops_ask_views","acdm"]`、`metadata.views` 显式视图清单、`query.coordinator_enabled=true`、`query.max_evidence_batches=1`、`llm.permitted_origins=["http://172.26.197.154:3000"]`）
4. **环境变量覆盖**：
   - 数据源与库连接：`HBASK_DATA_SOURCE`、`HBASK_DB_PASSWORD`
   - 服务与网段：`HBASK_HOST`、`HBASK_PORT`、`HBASK_LAN_CIDR`（兼容 `ASK_WEB_HOST`、`ASK_WEB_LAN_CIDR`）
   - 管理与加密：`HBASK_ADMIN_DIR`、`HBASK_ADMIN_SECRET_KEY`
   - 模型与证据：`HBASK_LLM_API_KEY`、`HBASK_MAX_EVIDENCE_BATCHES`
   - Oracle 同步与备份：`HBASK_ORACLE_HOST`、`HBASK_ORACLE_PORT`、`HBASK_ORACLE_SERVICE`、`HBASK_ORACLE_USER`、`HBASK_ORACLE_PASSWORD`、`HBASK_SYNC_AIRPORT_CODE`、`HBASK_PG_BACKUP_CONTAINER`

> **安全注意**：`data/*/admin/secret.key` 是模型密钥与加密会话的加密密钥，须与同目录下的 `management.sqlite3` 配套备份；若数据库已存在但缺失 `secret.key`（且未设 `HBASK_ADMIN_SECRET_KEY`），服务会拒绝启动。非 `127.0.0.1`/局域网 CIDR 外的来源将被中间件直接返回 `403`。

---

## HTTP 接口概览

非浏览器客户端调用写接口或问数接口时，须保存统一登录 Cookie 并携带同源头 `X-Hbask-Request: 1`；明细令牌与登录身份及会话绑定。

| 类别 | 方法与路径 | 说明 |
| --- | --- | --- |
| **问答与明细** | `POST /api/ask` | 同步问答（支持 `conversation_id`、`clarification_of`、`choice_id`） |
| | `POST /api/ask/stream` | NDJSON 流式输出：`progress`、`step`、`answer_delta`、`visual_meta`、`answer_error`、`result`、`error` |
| | `POST /api/records` | 使用 `drill_token` + `page` 分页读取主表或统计参与记录 |
| | `POST /api/related` | 使用 `related_token` + `table` + `page` 分页读取关联明细 |
| | `POST /api/ask/feedback` | 提交问答「不准反馈」进入后台治理队列 |
| **状态与本体** | `GET /api/status` | 当前数据源、库名、目录规模、结构差异、覆盖范围与耗时统计 |
| | `GET /api/ui-config` | 允许嵌入的宿主来源白名单 `embed_parent_origins` |
| | `GET /api/ontology` · `PUT /api/ontology` | 读取原始规则、编译本体、`query_capabilities` / `pending_business_objects`；`admin` 保存校验 |
| | `GET /api/ontology/history` · `POST /api/ontology/rollback` | 本体版本历史与授权回滚 |
| | `POST /api/metadata/refresh` · `POST /api/ontology/validate-relations` | `admin` 触发元数据重新采集 / 有预算只读关系抽样检查 |
| **A-CDM 专题** | `GET /api/acdm/reports/general` | `admin` 角色：A-CDM 通用报告（支持 `start_date`、`end_date`、`period_type`） |
| | `GET /api/acdm/reports/ground-briefing` | `admin` 角色：地服公司运行简报（支持 `target_date`） |
| | `GET /api/acdm/reports/safeguard-board` | `admin` 角色：保障节点录入排行与登机桥周转看板（支持 `start_date`、`end_date`） |
| **认证与会话** | `/api/auth/{status,login,logout}` | `hbairport01` 统一登录认证（兼容 `/api/business-auth/*`） |
| | `/api/conversations/*` | 本人会话检索、详情、重命名、归档、导出与删除（兼容 `/api/business-conversations/*`） |
| **管理后台** | `GET /api/admin/overview` · `GET /api/admin/audit` | 后台概览与操作审计日志 |
| | `/api/admin/models*` · `/api/admin/settings` · `/api/admin/retention/purge` | 模型配置/检测/激活/删除、保留策略与过期清理 |
| | `/api/admin/glossary*` · `POST /api/admin/schema/sync-diff` | 词条维护；实时库结构与本地配置自动差异比对 |
| | `/api/admin/sync/{overview,runs,run,backup,cancel,probe-source}` | Oracle-PG 数据同步编排、探针与备份 |
| | `/api/admin/ontology-catalog*` · `/api/admin/ontology/{concepts,synonyms,attributes,scenarios}*` | `ops_ask` 本体目录能力、概念/同义词/属性/场景维护与一键发布 |
| | `/api/admin/model/{overview,objects/{code},physical}` | 业务对象建模与底层物理模型浏览 |
| | `/api/admin/{metrics,dimensions,filters,rules,capabilities}*` | 指标、维度、筛选器、规则与能力注册 CRUD |
| | `/api/admin/planner/{templates,dry-run}` | 规划模板维护与问题 Dry-Run 模拟推演 |
| | `/api/admin/ask-settings/{home-airport,operational-day}` | 本场机场锚点与运营日日切配置 |
| | `GET /api/admin/feedback` · `PATCH /api/admin/feedback/{id}` · `POST /api/admin/feedback/{id}/promote-synonym` | 「不准反馈」处置及一键晋升为本体同义词 |

---

## 测试、验收与脚本

### 1. 自动化单元测试与集成测试

```powershell
# Python 单元测试（无数据库连接时自动跳过实库 TEMP 表集成测试）
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 启用连接私有 TEMP 表集成测试（事务结束自动回滚清理，不写入业务数据）
$env:HBASK_INTEGRATION = '1'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 前端逻辑测试、TypeScript 类型检查与静态构建
cd frontend
npm test
npm run typecheck
npm run build
cd ..
```

### 2. 数据源验收与治理脚本

```powershell
# ops_ask 统一总控固定集（375 题）真实模型只读对照评估
$env:HBASK_DATA_SOURCE = 'ops_ask'
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.coordinator_evaluate --live-model --workers 3 --output artifacts/evaluations/ops-coordinator-runtime-final.json

# ops_ask 同步初始化 A-CDM 手册校准指标/维度/规则/概念/同义词
.\.venv\Scripts\python.exe -X utf8 -m scripts.ops_ask.seed_acdm_handbook

# hbairport 实库只读规则基线对照
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.evaluate

# 证据无损分批传递回归（默认模型桩；追加 --live-model 使用当前启用模型）
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.evidence_delivery_regression

# 已启动 8088 服务时执行 API 与分页联调
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.smoke_api

# ACDM 本地副本本体流程冒烟验证
$env:HBASK_DATA_SOURCE = 'acdm'
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.acdm_smoke
```

> 若从外部引入或首次部署 `ops_ask` 视图，请先执行 `scripts/db/ops_ask_execution.sql` 创建 `ops_ask_views.flight_execution`。`scripts/db/` 下部分历史图谱/演示脚本默认指向早期演示库，执行前请先核对其目标库配置。
