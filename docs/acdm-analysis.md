# ACDM 复合分析、模拟机场对标、报告与预测

本次补齐默认 `ops_ask` 模式。继续使用当前同步窗口，不回填13个月历史；所有业务查询沿用本体、参数绑定和只读事务。`hbairport` 与旧 `acdm` 模式保持原入口行为。

## 模拟机场对标

`public.airport_benchmark_daily` 保存长沙黄花(CSX)、南京禄口(NKG)、郑州新郑(CGO)的模拟日数据。首批日期为2026-10-16至2026-11-15，共93行，数据集为 `airport-demo-20261116-v1`。固定种子生成，重复运行不会产生重复记录，写入范围由数据集标识限定。武汉使用原有本场指标查询，不向真实航班表写入模拟记录。

客座率、国内执行率和单机座位数分别由汇总后的旅客/座位数、国内执行/计划数、座位数/执行架次计算。回答、图表、快照及PDF保留模拟标识，不能据此给出实际机场排名。请求期间缺日时提示部分覆盖，不补零。

本场客座率仅使用有正数可用座位的同一批已执行航班，分子为样本旅客量；全量旅客吞吐量单独统计。2026-11-17修正了此前混用全量旅客与部分座位的公式，11月2至15日综合客座率由85.65%修正为85.27%，全量旅客吞吐量仍为960160人次。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.ops_ask.seed_airport_benchmarks
```

脚本创建新表并核对93条记录，随后通过现有治理服务登记指标、能力及本体版本，管理后台再次发布不会丢失这些指标。

## 问数示例与边界

- `2026-11-10计划量和执行量分别多少`
- `2026-11-10和2026-11-09执行航班量对比`
- `最近两周执行航班量环比`
- `11月1日至15日武汉、长沙、南京、郑州航班量和客座率对比`
- `预测未来7天实际执行航班量`

复合问题最多展开12项，每项仍经过原计划校验和编译。使用同一只读快照，数据库错误通过保存点隔离；成功分项保留，失败或超覆盖分项明确标记。同环比差值由程序计算，率差使用百分点，基期为零时不输出增长率。当前数据缺少上年同期，返回覆盖不足；同比算法使用独立测试夹具验证。

滚动期间比较使用完整历史日。本月等未完成期间使用截至上一完整日的窗口；当天尚未结束的同环比要求改选完整历史日。追问可使用“出港呢”“第二项明细”“去年同期呢”；多项结果后单说“明细”会先澄清。

预测比较“上周同日”和“近两周同星期均值”，每个步长使用7个历史滚动起点，以MAE选择方法。至少要求28个连续有效观测日；缺日、无有效数值或超范围时拒绝补零生成结果。区间是回测绝对误差的90分位经验范围，短历史和小样本不代表置信保证；未证明源端同步完整时明确标注试验性。未来已同步的计划值与统计预测分开显示。

## PDF与站内订阅

安装项目依赖及对应浏览器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

在工作台打开“报告中心”，生成通用报告、地服简报或保障看板，查看历史及下载PDF。问询结果也可导出当前快照。文件保留数据来源、覆盖边界和模拟提示；明细快照仅包含当前提供的预览记录，完整数据通过工作台分页查看。

运行后台进程：

```powershell
.\start-report-worker.cmd
# 或仅处理当前就绪任务后退出
.\.venv\Scripts\python.exe -X utf8 -m app.report_worker --once
```

后台不随每个Web进程重复启动。任务在管理库中持久化，通过原子领取和租约恢复处理；临时失败最多自动重试两次。界面显示后台是否在线。

订阅由用户在界面启用，仅发送站内通知，默认北京时间08:00。日报每天、周报周一、月报每月1日、季报季度首日、年报1月1日生成上一完整周期。暂停或取消后，待处理任务停止生成通知。当前窗口不足以覆盖完整月季年时，报告注明部分覆盖或提示无数据。

报告快照和PDF加密保存于当前管理目录，通过登录鉴权接口下载，不作为公共静态文件挂载。每次生成和发布前复核账号权限。报告、通知沿用管理库保留天数，默认90天。业务库同步留存策略不变。

## 接口与验证

2026-11-16修正了源航班合并规则：FIMS与ACDM以共享的Oracle `FLIGHT_ID`归并，同一日期、方向、航班号的不同运行分别保留。现有窗口中35组错误合并已备份并拆分，重读63条FIMS与70条ACDM源记录，重新关联生产、旅客汇总、登机桥、保障节点、共享航班及过站记录。备份加密保存在当前管理目录的`backups/flight-occurrences-*.json.enc`，修复整体通过核验后才提交。

`operated_flight_count`、`dep_flight_count`、`arr_flight_count`按`real_exec_day`（北京时间实际起飞/落地日期）统计，计划量按FIMS `PLAN_TIME`的北京时间自然日统计。宽表以FIMS登记航班为主表，关联ACDM实际起降信息；仅在ACDM存在的源记录保留在基础表中，不计入该宽表的运行指标。2026-11-16补正了此前仅按ACDM全量核对时遗漏的FIMS主表限制：2026-11-02至11-15应为7003架次（出港3499、进港3504），7004多计了ACDM独立记录4825537（ZZVPCYS，11月2日15:45进港）。2026-11-09仍为506架次（出港250、进港256）。旧会话与旧PDF是历史快照，需要重新查询或生成报告。

修复脚本默认只查看错误合并范围，显式执行时先验证加密备份，并在单个事务中重建受影响记录；重复运行不会重复插入航班：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.ops_ask.repair_flight_occurrences
.\.venv\Scripts\python.exe -X utf8 -m scripts.ops_ask.repair_flight_occurrences --apply
```

现有 `/api/ask` 与 `/api/ask/stream` 增加可选 `sections`、`comparisons`、`forecast`、`partial`、`is_simulated`；流式增加带子查询标识的 `query_result`，最后仍返回完整结果。单查询格式兼容。

报告接口为 `/api/reports`（任务、历史、详情、下载及重试）、`/api/report-subscriptions`（本人订阅）和 `/api/notifications`（本人通知）。写操作要求同源标识，所有访问要求当前运营管理员权限与所属账号匹配。分页令牌授权递归覆盖分项结果。

专项回归：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_analysis.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_benchmarks.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_reporting.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_reporting_api.py
```

通用业务的全量测试基线使用 `HBASK_DATA_SOURCE=hbairport`，运营专项自行加载 `ops_ask` 测试目录。前端运行 `npm test`、`npm run typecheck` 和 `npm run build`。实际PDF与浏览器检查的样张存于忽略版本控制的 `artifacts/analysis-qa/`。

数据来源、日期分组、失败回滚与独立Oracle核对方法见[数据链路与口径校验](acdm-pipeline-validation.md)。
