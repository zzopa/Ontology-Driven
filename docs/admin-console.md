# 管理后台使用说明

入口：http://127.0.0.1:8088/admin。Next.js 静态页面由 FastAPI 同源提供，和问数页使用同一登录会话。

## 账号与权限

使用原填报系统 hbairport01 的现有账号和密码登录，无需注册或初始化本地管理员。用户、部门、角色、菜单、权限点及授权都读取原表；账号停用和角色变更在后续请求中重新校验。本服务不复制用户及密码，不提供额外的用户管理或修改密码入口。账号和授权继续在 `D:/project/gzwtb` 原项目维护。

- 原系统启用的 `admin` 角色：模型配置、系统设置、审计及本体/词条修改、结构刷新、关系验证。
- 其他启用账号：按已有数据范围与模块权限问数、查看词条、管理自己的会话。
- 所有账号的问答快照只允许本人读取，权限变化后旧结果隐藏并提示重新提问。
- 未登录不能问数、分页或读取会话，没有访客查询开关。

数据范围及业务权限口径见 [hbairport01 数据权限说明](hbairport01-data-permissions.md)。

## 模型与业务规则

在“模型配置”添加、检测或切换 OpenAI 兼容接口。API Key 加密保存，接口只返回是否配置密钥；编辑留空保持旧值，明确清除才删除。非本机接口要求 HTTPS，接口检测不会回显提供商响应中的凭据。切换模型对新问题生效，在途问题使用提交时配置。

“本体论词条”维护企业简称、对象别名、字段中文解释及统计指标，直接修改现有 `business_ontology.json`。关系、JSON 属性及版本回滚在“业务本体”维护。所有更新校验版本并备份，拒绝覆盖他人已修改的规则。

## 存储与迁移

`data/admin/management.sqlite3` 只存本服务的模型、会话、令牌摘要、保留策略和审计；不保存第二套用户。`secret.key` 是模型密钥、问题及答案快照的加密密钥，备份和恢复必须和管理库配套。

原本存在本地用户表时，启动先通过 SQLite backup 保存 `management.before-unified-users-*.sqlite3`，然后移除旧 `users`、`credentials` 表与访客设置。其他配置和记录保留。旧 Cookie 失效；旧本地会话归档保留，不自动按用户名绑定到现有业务用户，避免错误归属和未授权快照泄露。

`/api/auth/*` 为统一登录接口；`/api/business-auth/*` 是兼容别名，使用同一业务会话 Cookie。账号管理、注册及本地改密码接口已移除。`/api/conversations/*` 与 `/api/business-conversations/*` 同样共用身份和本人范围。

数据库连接、网段、端口和嵌入白名单仍按 `config.json` / `config.local.json` 加载；保留天数在后台修改。保存保留天数不会自动删除记录，清理操作按确认后保存的规则执行，跳过在途会话。

## 会话行为

问数开始创建记录；成功保存当时允许显示的结果、证据及执行过程，失败保存失败状态。服务重启将未完成记录标为中断。支持本人会话分页、搜索、重命名、归档、导出及删除。历史答案不自动作为新问题上下文；历史快照不持久保存明细令牌，需要重新查询获取实时分页。

Cookie 使用 HttpOnly、SameSite=Lax，HTTPS 下启用 Secure。写接口校验同源；API 客户端需 `X-Hbask-Request: 1` 并保存统一登录 Cookie。嵌入白名单不等于账号授权，跨站 iframe 仍受第三方 Cookie 限制。

## 验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
cd frontend
npm test
npm run typecheck
npm run build
```

`python -m scripts.demo.admin_preview` 在临时目录运行隔离预览，监听 127.0.0.1:9089，也使用原业务账号登录，不创建测试账号或写入业务库。
