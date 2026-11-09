# ops_ask 数据源

本项目读取本机 PostgreSQL `ops_ask` 的标准模型。数据由现有
`D:\project\airports-ops-ai\airport-ops-data-sync` 服务从 Oracle FIMS/ACDM 清洗同步。
问数不直接连接 Oracle。

“执飞/执行航班”使用 `ops_ask_views.flight_execution` 实时视图，按出港实际起飞、
进港实际落地确定执行时间，排除取消、逻辑删除和虚拟航班。它不要求两个时间同时存在，
也不以计划日期代替实际发生日。视图随标准表同步结果实时变化，不复制另一份业务数据。
首次部署执行 `scripts/db/ops_ask_execution.sql`；元数据仅采集 profile 明确登记的视图。
“昨天航班执飞情况”等完整口径通过现有本体规则解析；附加条件无法完整解析时仍需校验。
“执飞情况”直接按进出港分组统计全量记录，并保留分页明细入口；明确问“执飞明细”时读取明细。

双击根目录 `start-ops-ask.cmd` 可切换并重启。原来的 `start-acdm.cmd` 和
`start-hbairport.cmd` 继续可用。环境变量 `HBASK_DATA_SOURCE` 优先于配置。
完整页面与登录入口为 `http://127.0.0.1:8088/`。

`data/ops_ask/profile.json` 指定连接及独立本体、缓存、图谱、管理库目录。
登录仍读取 `hbairport01` 的账号和角色；运营数据仅允许原系统管理员访问。
密码来自现有本机配置，不写入数据源 profile。模型沿用现有明确允许的内网端点，
密钥单独加密保存，不复制登录凭据或会话。

本体复用 `meta_model_entity`、`meta_model_field`、`meta_model_relation` 的定义，
仅开放已配置的 `std_*`、`dim_*` 表。profile 中空字符串排除前缀匹配所有表，
由本体的 `queryable: true` 显式开放标准表，因此新系统表不会自动进入问数范围。
机位、登机口关联同时匹配机场码与资源码；其他卫星表沿用数据库外键。
共享关系存在两个航班外键，本体通过关系 `pairs` 明确选择所属航班，避免与关联航班混淆。

航班日期、计划时刻与实际起降时刻分别保留。生产量一行代表一个航段；
PG 连接按 `Asia/Shanghai` 解释时区时间，避免业务日期被 UTC 跨日转换。
`FLIGHT_REPEAT` 数量不能按行直接相加，本体暂不开放直接 SUM 指标。
始发、目的和本场机场指向同一维表的多个角色，无法消歧时现有引擎会拒绝关联，
应明确使用航班表上的对应机场码筛选，不猜测关系。

同步由独立模块的手动 API 执行：基础资料 `/api/sync/master-data`，
航班 `/api/sync/flights?source=oracle-fims` 和 `oracle-acdm`，
生产量 `/api/sync/production`。FIMS 航班成功后会刷新本批共享关系。
本次接入不启用定时任务。更新前备份保存在 `artifacts/backups/`。

2026-11-09 本次手动更新：基础资料全量刷新；FIMS/ACDM 拉取北京时间
2026-11-03 起的变更，生产量按 2026-11-03 至 2026-11-09 执行日补数。
本次不是历史全量补齐，旧快照与近期数据之间的日期缺口不能当作零业务量。
同步与查询验收记录在 `artifacts/evaluations/ops_ask-*-20261109.json`。
