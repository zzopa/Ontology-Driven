# 问数工作台与弹窗嵌入

## 技术与目录

前端使用 Next.js App Router、TypeScript、Tailwind CSS、Lucide React 和 Framer Motion。生产执行静态导出，HTML/CSS/JS 由现有 FastAPI 在 8088 同源提供，业务 API、数据库、本体和模型配置仍在 Python 后端。生产运行不需要额外 Node 服务。

```text
frontend/
  app/                 页面、布局、全局设计样式
    page.tsx           完整工作台
    embed/page.tsx     紧凑弹窗工作台
    ontology/page.tsx  本体管理
  components/          查询、结果、管理及共享交互组件
  lib/                 API、NDJSON、类型及表单组句
  tests/               前端逻辑测试
  out/                 npm run build 生成，不手工修改
  package-lock.json    锁定依赖；重新安装使用 npm ci
web/                   旧版页面归档，非在线入口
```

深色 Zinc 基底、Emerald 高亮、细边框和克制光晕；以响应式 Bento 网格分隔查询、连接与流程。结果分为回答、完整字段、事实依据、关联图谱和 SQL，避免所有文字堆叠在首页。

构建、真实查询、分页与响应式实测结果见 [UI 验收报告](verification-2026-10-03-frontend.md)。

## 构建与运行

```powershell
cd D:\project\test-ai\frontend
npm ci
npm run test
npm run typecheck
npm run build
cd ..
.\.venv\Scripts\python.exe -X utf8 ask_web.py
```

首次安装没有锁文件时使用 `npm install`。不覆盖 `config.local.json`。修改前端后重新 build；FastAPI 源码/配置改变需要重启服务。所有统计数字来自 `/api/status` 或查询返回，不使用静态演示业务数字。

`npm run dev` 在 3000 提供前端热更新，开发时问答 API 转发至 8088。管理写操作的跨站 Origin 检查保持不变；完整管理联调请构建后使用 8088 同源页面，不为开发方便放宽写权限。

## 功能与交互

- 自然语言提问、Ctrl/⌘+Enter、六类快捷问题、可编辑辅助查询表单。
- 辅助表单把业务、企业、月份、关键词和查询方式写入可见问题，不在后台附加隐藏 SQL 条件。实际筛选可在解析条件中核验。
- 实时阶段进度、耗时、校验后分段输出、断线错误和重新查询。停止等待只停止浏览器等待，不声称取消已开始的后台 SQL。
- 命中数量点击打开完整分页明细；关联表同样完整分页。所有允许字段包括空值均可查看，长文本/JSON 点击打开完整内容并复制。
- 卡片/表格切换、来源事实、业务时间/查询时间、证据覆盖、实际参数化 SQL 和独立绑定参数。
- 表级图谱支持节点聚焦；不把配置结构边说成已经命中的记录关系。
- 会话历史只保留当前页面内最近 8 个成功结果，不存 localStorage，也不自动发给宿主系统。
- 本体管理增加对象名称/别名/业务绑定、字段名称/解释/单位表单。先写入 JSON 草稿，保存仍走原有后端校验、版本冲突与历史备份。未编辑的规则保留。
- 本体修改需要管理员/本体维护员登录，授权后可从本机或允许的 LAN 操作；未保存草稿提示，回滚需确认。JSON 高级编辑、结构刷新、关系抽样、历史回滚保留。
- 所有可点击项具备过渡和焦点状态；弹层支持 Escape、焦点约束、关闭后回到触发位置。系统“减少动态效果”偏好会降低动画。

## 嵌入宿主弹窗

专用入口：`http://服务电脑:8088/embed`。移除侧栏和大面积说明，保留流式查询、结果标签页和明细；页面在弹窗内部滚动，明细弹层不会覆盖宿主 iframe 外部。

跨站嵌入前，在 `config.json` 或本机覆盖配置中明确允许宿主来源。例如：

```json
{
  "server": {
    "embed_parent_origins": ["http://192.168.31.20:9000"]
  }
}
```

填协议、主机和端口，不含路径，不使用 `*`。重启 FastAPI 后，CSP `frame-ancestors` 仅允许本站及这些宿主嵌入。默认空列表仅允许同源；局域网访问控制仍生效。

```html
<iframe
  src="http://服务电脑:8088/embed?parentOrigin=http%3A%2F%2F192.168.31.20%3A9000"
  title="智能问数"
  style="width:100%;height:min(780px,85vh);border:0;border-radius:14px"
></iframe>
```

推荐宿主弹窗宽 1000–1200px、高 75–85vh，小屏自动单列。不要在 URL 放密码、API Key 或人员敏感信息。

关闭按钮只向已配置且匹配 `parentOrigin` 的宿主发送 `{type: 'hbask:close'}`，不附带业务结果。宿主必须检查来源与窗口：

```js
const frame = document.querySelector('iframe[title="智能问数"]');
window.addEventListener('message', (event) => {
  if (event.origin !== 'http://服务电脑:8088') return;
  if (event.source !== frame.contentWindow) return;
  if (event.data?.type === 'hbask:close') closeYourDialog();
});
```

HTTPS 宿主页不能嵌入 HTTP 服务；正式接入建议同域反向代理或为问数服务配置 HTTPS。iframe 不是账号认证；现已新增 [管理后台和账号权限](admin-console.md)，但没有宿主 SSO 或业务行列权限。跨站 iframe 的第三方 Cookie 可能被浏览器阻止，优先使用同域代理，不能仅通过放宽嵌入白名单解决登录和历史会话问题。
