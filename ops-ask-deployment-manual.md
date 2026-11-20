# 知航 · ACDM 运营智能问询系统（ops-ask）Docker 离线部署与运维手册

本文档适用于 `ops-ask` 服务在无互联网连接的高安全生产服务器环境下的容器化打包、离线发布、日常增量更新、监控排查与故障应急。

---

## 1. 系统架构与环境规格

### 1.1 节点环境要求
- **操作系统**：Linux (CentOS / Kylin Linux Advanced Server / RHEL)
- **容器引擎**：Docker Engine 20.10+ / Docker Compose (v1 或 v2 均已兼容)
- **磁盘分区规划**：
  > 务必部署在 `/data` 分区（如 `/data/ops-ask`）。
  > 生产服务器的根分区 `/` 容量有限，而 `/data` 通常挂载了大容量数据盘（如 500G+），且 Docker 运行时存储也位于 `/data/lib/docker`。严禁将大镜像与日志解压至 `/root`、`/opt` 或 `/tmp`。

### 1.2 部署架构拓扑
FastAPI 采用单端口架构同源托管 Next.js 静态产物，服务外部仅需暴露统一业务端口 `5010`。

```
                    ┌────────────────────────────────────────────────────────┐
                    │            外部客户端浏览器 (http://10.200.10.6:5010)  │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ 宿主机 (10.200.10.6)  - 根目录: /data/ops-ask                                              │
│                                                                                           │
│   ┌─────────────────────┐    ┌──────────────────────────────────┐   ┌─────────────────┐   │
│   │   config.local.json │    │  app/ + scripts/ + ask_web.py    │   │  frontend/out/  │   │
│   │   (本地专有连接配置)│    │  (后端 Python 代码与启动入口)    │   │  (Next.js 导出) │   │
│   └──────────┬──────────┘    └────────────────┬─────────────────┘   └────────┬────────┘   │
│              │                                │                              │            │
│              │        挂载映射 (:ro)          │              挂载映射 (:ro)  │            │
│              ├────────────────────────────────┼──────────────────────────────┤            │
│              ▼                                ▼                              ▼            │
│     ┌──────────────────────────────────────────────────────────────────────────────┐      │
│     │ Docker 容器: ops-ask-web (基于 ops-ask-runtime:1.0)                          │      │
│     │ - 暴露宿主机端口 5010                                                        │      │
│     │ - FastAPI 启动入口 ask_web.py 托管前端静态资源与核心问答 API                │      │
│     └──────────────────────────────┬───────────────────────────────────────────────┘      │
│                                    │                                                      │
│     ┌──────────────────────────────┴───────────────────────────────────────────────┐      │
│     │ Docker 容器: ops-ask-report-worker (基于 ops-ask-runtime:1.0)                │      │
│     │ - 执行任务: python -m app.report_worker (后台 PDF 报表与定时订阅处理)        │      │
│     │ - 内置组件: Playwright 1.65.2 + Chromium + libatomic1 + fonts-noto-cjk       │      │
│     └──────────────────────────────┬───────────────────────────────────────────────┘      │
│                                    │                                                      │
│              ┌─────────────────────┴────────────────────┐                                 │
│              ▼                                          ▼                                 │
│     ┌─────────────────────────────────┐        ┌───────────────────────────────────┐      │
│     │ data/ (SQLite管理库/图谱/本体)  │        │ artifacts/ (运行日志/报表产物)   │      │
│     └─────────────────────────────────┘        └───────────────────────────────────┘      │
└────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                     │ 外部网络连接 (直连现有数据库与大模型)
                                     ▼
        ┌────────────────────────────┬─────────────────────────────┐
        │                            │                             │
        ▼                            ▼                             ▼
┌────────────────────────┐  ┌────────────────────────┐  ┌──────────────────────────────────┐
│ PostgreSQL (业务/权限) │  │ Oracle (外部数据同步)  │  │ 内网大模型服务 (LLM)             │
│ 172.20.224.151:15432   │  │ 172.26.78.133:1521     │  │ http://172.26.197.154:3000       │
│ 数据库: ops_ask        │  │ 服务名: datacache (WUH)│  │ /v1/chat/completions             │
└────────────────────────┘  └────────────────────────┘  └──────────────────────────────────┘
```

### 1.3 宿主机挂载目录映射表

| 宿主机路径（位于 `/data/ops-ask`） | 容器内挂载路径 | 权限模式 | 作用说明 |
| :--- | :--- | :--- | :--- |
| `./ask_web.py` | `/app/ask_web.py` | `:ro` (只读) | 服务统一启动入口脚本 |
| `./app/` | `/app/app` | `:ro` (只读) | 后端业务代码（FastAPI/业务逻辑/算法） |
| `./scripts/` | `/app/scripts` | `:ro` (只读) | 运维脚本与数据同步工具 |
| `./frontend/out/` | `/app/frontend/out` | `:ro` (只读) | Next.js 生产导出的静态 HTML/CSS/JS |
| `./config.json` | `/app/config.json` | `:ro` (只读) | 全局默认基线配置 |
| `./config.local.json` | `/app/config.local.json` | `:ro` (只读) | 宿主机本地生产配置（数据库密码、LLM、网段） |
| `./data/` | `/app/data` | `:rw` (读写) | 本地持久化：SQLite 会话库、业务本体、图谱元数据 |
| `./artifacts/` | `/app/artifacts` | `:rw` (读写) | 服务运行日志、PDF 报表临时文件、备份数据 |

---

## 2. 部署文件资产清单

在项目开发机与服务器部署目录中，相关部署文件清单如下：

| 文件名称 | 存放位置 | 职能定义 |
| :--- | :--- | :--- |
| `Dockerfile` | 项目根目录 | 纯运行环境定义：Debian 13 + Python 3.13 + Chromium + 中文字体 + `libatomic1` |
| `.dockerignore` | 项目根目录 | 限制构建上下文仅包含 `requirements.txt`，确保秒级构建 |
| `docker-compose.yml` | 部署根目录 | 编排 `web` 与 `report-worker` 容器，固定 5010 端口与挂载卷 |
| `deploy/config.local.server.example.json` | 项目内模板 | 生产配置模板（预置 PG 15432、Oracle 1521、内网模型与 0.0.0.0/0） |
| `deploy/package-offline.cmd` | 项目开发机 | Windows 双击一键打包批处理入口（标准 CRLF） |
| `deploy/package-offline.ps1` | 项目开发机 | 自动化构建前端产物、构建 amd64 镜像、压缩成单包的执行核心 |
| `deploy/server-deploy.sh` | 服务器部署根目录 | Linux 离线部署脚本：自动识别 Docker Compose 版本、校验目录与自检健康 |

---

## 3. 全量首次部署流程

### 步骤 1：在开发机（有网环境）生成离线发布包

在项目根目录下打开命令行或直接双击运行：
```cmd
.\deploy\package-offline.cmd
```
执行完毕后，在 `output` 目录下生成：
- `output/ops-ask-offline-full-1.0.tar.gz`（约 108 MB 代码与前端全量包）
- `output/ops-ask-runtime-1.0.tar`（约 1.4 GB 完整环境镜像，已内置 Playwright Chromium）

### 步骤 2：上传发布包至服务器

通过 SCP 或 SFTP 工具将上述两个文件上传至服务器 `/data/ops-ask/`：
```bash
# 1. 登录服务器创建目录
mkdir -p /data/ops-ask
cd /data/ops-ask

# 2. 解压代码与配置包
tar -xzf ops-ask-offline-full-1.0.tar.gz

# 3. 赋予部署脚本执行权限
chmod +x server-deploy.sh
```

### 步骤 3：导入离线 Docker 镜像

```bash
cd /data/ops-ask
docker load -i ops-ask-runtime-1.0.tar

# 验证镜像导入成功
docker images | grep ops-ask-runtime
```
> 镜像导入成功后，即可执行 `rm -f ops-ask-runtime-1.0.tar` 释放 1.4 GB 的安装包临时磁盘空间。

### 步骤 4：配置生产数据库与模型连接（`config.local.json`）

首次执行 `./server-deploy.sh` 时，若不存在 `config.local.json`，脚本会自动根据模板创建并安全暂停。按实际生产环境编辑：
```bash
vi /data/ops-ask/config.local.json
```
标准配置内容对照：
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 5010,
    "lan_cidr": "0.0.0.0/0"
  },
  "database": {
    "host": "172.20.224.151",
    "port": 15432,
    "user": "hbairport",
    "password": "填写实际PostgreSQL密码",
    "database": "ops_ask"
  },
  "oracle": {
    "host": "172.26.78.133",
    "port": 1521,
    "service_name": "datacache",
    "user": "SEL_ZTZHSJZL",
    "password": "填写实际Oracle密码",
    "airport_code": "WUH",
    "acdm_schema": "ACDM",
    "acdm_table": "TB_DYN_FLIGHT",
    "fims_schema": "FIMS",
    "fims_table": "TB_DYN_FLIGHT",
    "fims_share_table": "TB_DYN_SHARE_FLIGHTS"
  },
  "llm": {
    "url": "http://172.26.197.154:3000/v1/chat/completions",
    "model": "填写内网实际加载的模型名",
    "api_key": "sk-jshrubzaqeyyampgzazucrgkpczjarhkgovmtuciknekorgx",
    "permitted_origins": [
      "http://172.26.197.154:3000"
    ]
  }
}
```

### 步骤 5：启动服务与就绪验收

```bash
cd /data/ops-ask
./server-deploy.sh
```

输出验证：
```text
[+] Running 2/2
 ✔ Container ops-ask-web            Started
 ✔ Container ops-ask-report-worker  Started
[3/3] 等待服务就绪 (http://127.0.0.1:5010/api/status) ...

[成功] ops-ask 已就绪！访问地址: http://<服务器IP>:5010/
```

在本地浏览器中打开 `http://10.200.10.6:5010/` 即可正常访问。

---

## 4. 日常维护与版本发布策略

### 4.1 场景 A：纯前端界面修改（零停机、无感更新）
FastAPI 每次直接读取硬盘上的 `frontend/out/index.html`：
1. 本地打包：
   ```powershell
   cd frontend
   npm run build
   ```
2. 通过 SFTP/Xftp 将本地 `frontend/out` 目录的所有内容覆盖上传至服务器 `/data/ops-ask/frontend/out/`。
3. **无需重启任何 Docker 容器**，直接在浏览器按 `Ctrl + F5` 强制刷新缓存即可。

### 4.2 场景 B：后端业务代码更新（秒级生效）
1. 本地生成轻量增量包（排除 1.4G 镜像，仅打包 Python 与前端产物，约 1.3 MB）：
   ```cmd
   .\deploy\package-offline.cmd -SkipImageBuild
   ```
2. 将 `output/ops-ask-code-update-1.0.tar.gz` 上传至 `/data/ops-ask/`。
3. 在服务器上解压并重启容器：
   ```bash
   cd /data/ops-ask
   tar -xzf ops-ask-code-update-1.0.tar.gz
   docker-compose restart web report-worker
   ```

### 4.3 场景 C：修改数据库密码或 LLM 地址
直接编辑宿主机上的配置文件：
```bash
vi /data/ops-ask/config.local.json
docker-compose restart web report-worker
```

---

## 5. 常用运维管理命令速查

> 提示：服务器环境为 Docker 20.10.x，命令需使用带中划线的 **`docker-compose`**，避免误用 `docker compose`。

### 5.1 服务启停与状态查看
```bash
cd /data/ops-ask

# 查看当前运行容器
docker-compose ps

# 查看 Web 核心日志（实时滚动追踪）
docker-compose logs -f --tail=100 web

# 查看 报告生成与订阅后台 日志
docker-compose logs -f --tail=100 report-worker

# 重启全部服务
docker-compose restart

# 平滑停止全部服务
docker-compose down

# 重新加载配置并后台启动
docker-compose up -d
```

### 5.2 宿主机直接健康自检
```bash
# 检测 Web 服务端口与图谱初始化状态
curl -s http://127.0.0.1:5010/api/status | grep '"ready":true'

# 测试容器内 Playwright Chromium 能否正常唤起
docker exec ops-ask-report-worker python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print('Chromium OK'); b.close(); p.stop()"
```

### 5.3 磁盘与空间例行维护
```bash
# 1. 检查 /data 剩余空间
df -h /data

# 2. 清理升级后残留的临时安装包
rm -f /data/ops-ask/*.tar /data/ops-ask/*.tar.gz

# 3. 清理 Docker 悬空历史镜像层（<none> 镜像）
docker image prune -f

# 4. 检查日志目录占用
du -sh /data/ops-ask/artifacts/logs
```

---

## 6. 典型故障排查应急手册 (FAQ)

### 故障 1：访问出现 `403 forbidden` 或 `curl` 返回 `forbidden`
- **现象**：浏览器或本地 curl 无法访问页面，返回状态码 403。
- **排查根因**：Docker 桥接网络转发时，宿主机流量被 NAT 转换为容器网关 IP（如 `172.17.0.1`），触发了 FastAPI 的白名单过滤。
- **处理方案**：
  检查 `/data/ops-ask/config.local.json`，确保 `lan_cidr` 放行包含 Docker 网关及办公网段：
  ```json
  "server": {
    "host": "0.0.0.0",
    "port": 5010,
    "lan_cidr": "0.0.0.0/0"
  }
  ```
  改完后执行 `docker-compose restart web`。

---

### 故障 2：点击生成 PDF 报告失败，提示“检查浏览器安装”
- **现象**：前台提示“报告生成失败，请检查浏览器安装、数据库连接及后台日志”；后台日志输出：
  `error while loading shared libraries: libatomic.so.1: cannot open shared object file` 或 `Executable doesn't exist at /root/.cache/ms-playwright/`。
- **排查根因**：当前容器镜像中未打入完整的 Chromium 无头浏览器二进制文件或缺失底层的 `libatomic1` 系统动态链接库。
- **处理方案**：
  必须使用带 Playwright 的全量镜像包更新：
  1. 将本地打包好的 `ops-ask-runtime-1.0.tar` 上传至 `/data/ops-ask/`。
  2. 导入镜像并重建 Worker：
     ```bash
     docker load -i ops-ask-runtime-1.0.tar
     docker-compose up -d --force-recreate report-worker
     ```

---

### 故障 3：执行命令报错 `unknown shorthand flag: 'd' in -d`
- **现象**：执行 `docker compose up -d` 报错退出。
- **排查根因**：当前服务器 Docker 版本为 20.10.x，未安装独立 `compose` 插件，不支持空格语法的 `docker compose`。
- **处理方案**：统一使用带连字符的命令：**`docker-compose up -d`**，或直接执行封装好的脚本 `./server-deploy.sh`。

---

### 故障 4：执行报错 `yaml: line 1: did not find expected key`
- **现象**：启动时报 YAML 语法解析异常。
- **排查根因**：使用 `vi` 编辑 `docker-compose.yml` 时误在首行混入了非法按键字符，或首行的 `services:` 未顶格写（带有多余空格）。
- **处理方案**：检查 `docker-compose.yml` 第 1 行，确保 `services:` 必须处于首行且完全顶格无空格。

---

### 故障 5：大模型提问超时或报连接异常
- **现象**：在前端提问时等待长达数十秒后报连接错误。
- **排查根因**：服务器处于纯离线网络，未在 `config.local.json` 中配置内网模型地址，系统默认调用了公网的 `https://api.siliconflow.cn`。
- **处理方案**：
  在 `config.local.json` 的 `"llm"` 配置块中，将 `"url"` 改为实际的内网模型中转地址（例如 `http://172.26.197.154:3000/v1/chat/completions`）。
