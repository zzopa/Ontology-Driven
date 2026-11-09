# ACDM 本体数据源切换

原 test-ai 页面仍为 http://127.0.0.1:8088/ ，本体管理仍为 /ontology。
双击根目录 start-acdm.cmd 切换 ACDM 并重启；start-hbairport.cmd 切回原 hbairport01 并重启。
也可修改 config.json 的 data_source 为 acdm 或 hbairport 后运行原 restart.cmd。
环境变量 HBASK_DATA_SOURCE 优先于配置，设置了它时需同步清除或修改。

原数据库不迁移、不清空、不写入。本机另建 acdm_test_* PostgreSQL 数据库，
包含已复制的 12 张原始表及两张按原系统口径去重的计划/执行航班本地分析表。
计划按 PLAN_TIME，执行按进港落地/出港起飞时间，执行排除取消，均排除 A/N 备降。
数据范围为 2026-10-04（含）至 2026-10-11（不含），今天的数据仅截至复制时刻。
本地分析表进入原 Catalog、Compiler、关系寻路、证据与分页流程。

data/acdm/profile.json 保存测试库名、schema 及独立文件目录，不保存密码。
两套本体、图谱、计划缓存、管理员配置和会话目录分别保存；原文件仍在原位置。
身份验证仍只读访问原 hbairport01 的账号/角色，ACDM 问数仅开放给原系统管理员。
ACDM 服务只监听本机，所有业务查询沿用只读事务、超时、参数绑定与 EXPLAIN。
运行时不连接 Oracle。原有本体自由问答现使用 DeepSeek-V3.1，接口为用户指定的
http://172.26.197.154:3000/v1/chat/completions 。该模型从原管理配置复制并激活，
原模型配置和密钥保留，ACDM 目录中的密钥继续加密保存。
按用户明确授权，仅向指定内网服务发送问题、相关本体及查询证据；其他外部模型地址被服务端拦截。

可自由提问，以下仅为示例：

- 昨天计划航班量是多少？
- 昨天执行航班量是多少？
- 昨天出港航班明细
- 2026年10月9日 MU2503 航班信息
- 最近7天按天计划航班量
- 查询航空公司 / 航班代理 / 机位

模型通过相关本体生成受校验的计划，无法确认的字段或口径会要求澄清，不忽略条件扩大查询。
51 项原系统指标定义已保存为参考，尚不代表所有复杂指标都已配置完毕。

copy-acdm.cmd 从原 Oracle 只读复制最近7天到 SQLite 中间副本。
重新导入 PostgreSQL 时运行 `.venv\Scripts\python.exe -m scripts.db.acdm_postgres`，
每次创建新库，完整导入且核对行数后才更新 profile.json；随后重启 ACDM。
不自动删除旧副本或旧测试库，失败导入产生的独立新库也保留供检查。

验证：`python -m unittest discover -s tests -p test_acdm_ontology.py`；
设置 HBASK_DATA_SOURCE=acdm 后运行 `python -m scripts.demo.acdm_smoke` 验证完整本体流程。
