# 运营库统一问答

2026-11-11：运营问答改为“授权目录 → 总控识别 → 必要时澄清 → 一个受控查询计划 → 实时结果和来源事实”。适用数据源为 `ops_ask`，包含同库 ACDM 记录，不跨库拼接。

## 查询范围

当前 25 个业务对象都有身份、时间、粒度、字段及能力说明。目录继续采用显式开放策略，系统账号、会话和提示词等管理表不进入问数；新增标准业务对象列为待配置项。现有本体接口返回 `query_capabilities` 和 `pending_business_objects`。

明细、合法筛选、计数、明细排序/前 N、已发布指标和明确关系继续使用原 SQL 编译器。实际执飞使用实际执行视图及 `execution_time`；计划业务日、保障记录日分别解释。生产量指标必须有已发布、可验证的粒度限制，否则提示能力缺口，不由模型推算公式。

规则不能完整解释问题时，总控可从全目录重新选择业务对象，再提供所选对象和所需关系的结构。规划最多一次目录选择、一次详细规划和一次结构纠正。模型只能返回白名单计划字段；日期、身份、方向、航司等已识别条件由系统保留并校验。数字参数及日界线的等价写法会规范化，不能以其他字段或其他日期替代。

## 澄清及结果协议

`POST /api/ask` 和 `/api/ask/stream` 保持原入口。正常业务响应的 `ok` 为 `true`，流式仍以 `result` 事件结束。新增 `outcome`：

| outcome | 行为 |
| --- | --- |
| `answered` | 已确认口径并返回事实，或说明可问范围 |
| `clarification_needed` | 还需确认对象、日期或业务口径，附 `clarification.question/options` |
| `unsupported` | 当前没有可核验的查询能力或指标定义 |
| `no_data` | 当前本地数据没有匹配；不代表现实业务量为零 |
| `out_of_coverage` | 所查日期落在该对象本地已观测日期之外 |

参数错误、权限错误及数据库/模型等技术故障继续走原错误通道。成功查询保留原有 SQL、绑定参数、证据、图表和分页字段。历史快照不重写。

用户确认时，继续提交 `question`、`conversation_id`，并增加 `clarification_of`（待确认消息 ID）；点击选项时再提交 `choice_id`。服务器从本人会话重新读取原问题、已确认条件及候选，客户端不传 SQL 或计划。也支持在输入框补充文字；输入新的完整问题会重新规划。

相对日期固定到原提问时刻并使用北京时间。澄清、失败和不支持的结果不覆盖最近有效查询上下文；有效零结果查询仍可继续追问。目录版本改变后，旧澄清需要重新确认。

例如，“昨天最后一班航班是哪个”先确认计划/实际和比较方向；“昨天最晚实际起飞的出港航班”直接按实际起飞时刻查询。换成“昨天哪一班最晚从本场飞走”保持相同口径。

## 事实与数据边界

运营库计划缓存停用。业务事实正文直接从实时结果生成，模型不会自由扩写事实或原因。NULL 指标不会被改写为 0。已有明细、图表和执行记录保留。

覆盖范围按具体对象及时间字段读取，ACDM 与标准航班不共用窗口。最早/最晚观测日期不能证明中间每一天已同步完整；结果明确保留这个限制。保障诊断要求一个明确业务日，规划阶段不取数，不按固定航班号补造连班或替换其他日期。

## 配置与验证

`data/ops_ask/profile.json` 的 `query.coordinator_enabled` 控制新流程；通用默认值为 `false`，只在运营库 profile 中启用。回滚设为 `false` 后重启本项目服务。关闭开关会恢复旧规划路径，旧路径的已知限制也会恢复。

测试命令（PowerShell）：

```powershell
$env:HBASK_DATA_SOURCE='hbairport'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
cd frontend
npm test
npm run typecheck
npm run build
cd ..
```

真实验收使用现有加密模型配置，只读取当前授权内网模型；不输出密钥，不写业务数据。模型评估前加载已发布治理口径，参考 SQL 与生成 SQL 在只读事务中对照：

```powershell
$env:HBASK_DATA_SOURCE='ops_ask'
.\.venv\Scripts\python.exe -X utf8 -m scripts.demo.coordinator_evaluate --live-model --workers 3 --output artifacts/evaluations/ops-coordinator-runtime-final.json
```

固定题集含 375 条问题：25 个对象 × 明细/计数/筛选 × 5 种表达，其中 150 条标记为改写验收样本。评估不仅检查回答成功，还核对主体、意图、实际记录/统计值和事实引用。报告保留每题耗时、模型请求次数、拒绝及技术失败，不以重试后的成功掩盖失败。

启用门槛：全部基础用例通过，改写集正确率至少 95%，关键口径、权限和条件完整性回归通过，验收样本无错误事实。报告中的通过率只适用于该固定样本，不代表任意自然语言问题都能正确回答。

2026-11-11 验收记录：当前内网 DeepSeek-V3.1 与真实运营库只读对照通过 375/375（基础 225/225，改写 150/150），没有发现与独立参考 SQL 不一致的结果。报告为 `artifacts/evaluations/ops-coordinator-runtime-final.json`。此前 15 条登机桥用例因 `BRIDGE` 被识别成机号而失败；修复标识符解析并补回归后重新完整验收，原失败报告保留在 `artifacts/evaluations/ops-coordinator-before-identifier-fix.json`。

真实关键链路验证实际发生日、航司和方向计数，末班同义问法、两阶段澄清、新完整问题替换待澄清问题，以及全目录模型选表和后续分页，记录为 `artifacts/evaluations/ops-coordinator-key-flow-final.json`。后端共 357 项测试：338 项通过、19 项按环境条件跳过；前端 39 项通过，类型检查与生产构建通过。浏览器工具连接超时，未完成实际页面点击验收。
