# 知航 · ACDM 运营智能问询系统（ops-ask）Docker 离线部署与运维手册

本文档适用于 `ops-ask` 服务在无互联网连接的高安全生产服务器环境下的容器化打包、离线发布、日常更新（镜像替换与程序包更新）、监控排错与故障应急。

---

## 目录
1. [系统架构与部署规格](#1-系统架构与部署规格)
2. [部署资产与发版工具清单](#2-部署资产与发版工具清单)
3. [核心操作：日常替换“程序包”（增量代码更新，约 1.5 MB）](#3-核心操作日常替换程序包增量代码更新约-15-mb)
4. [核心操作：底层替换“环境镜像”（全量运行环境更新，约 1.4 GB）](#4-核心操作底层替换环境镜像全量运行环境更新约-14-gb)
5. [全新服务器离线初始化部署流程](#5-全新服务器离线初始化部署流程)
6. [配置文件说明与修改（config.local.json）](#6-配置文件说明与修改configlocaljson)
7. [常用运维管理命令速查](#7-常用运维管理命令速查)
8. [高频故障排查与应急手册 (FAQ)](#8-高频故障排查与应急手册-faq)

---

## 1. 系统架构与部署规格

### 1.1 节点环境要求
- **操作系统**：Linux (CentOS 7/8 / Kylin Linux Advanced Server V10 / RHEL)
- **服务器 IP**：`10.200.10.6`（或 `172.20.224.151`）
- **容器引擎**：Docker Engine 20.10+、docker-compose
- **磁盘分区规划**：
  > [!IMPORTANT]
  > **必须部署在大容量数据盘所在目录（如 `/data/ops-ask`）**。  
  > 生产服务器的根分区 `/` 仅剩少量空间，而 `/data` 挂载了大容量盘（可用 116G+）。Docker 运行时存储也位于 `/data` 分区。严禁将大镜像包解压至 `/root`、`/opt` 或 `/tmp`。

### 1.2 部署架构拓扑
FastAPI 采用单端口架构同源托管 Next.js 前端编译静态产物，服务外部统一暴露业务端口 **`5010`**。

```mermaid
flowchart TD
    subgraph Host["宿主机 (IP: 10.200.10.6 / 目录: /data/ops-ask)"]
        CFG["config.local.json<br/>(宿主机专有配置: 密码/LLM/网段)"]
        CODE["app/ + scripts/ + ask_web.py<br/>(后端业务代码 :ro)"]
        FE["frontend/out/<br/>(前端 Next.js 静态产物 :ro)"]
        DATA["data/<br/>(SQLite管理库/图谱/本体缓存 :rw)"]
        LOGS["artifacts/<br/>(运行日志/定时报告临时文件 :rw)"]

        subgraph DockerRun["Docker 容器集群 (镜像: ops-ask-runtime:1.0)"]
            WEB["ops-ask-web<br/>FastAPI Web 核心服务<br/>监听宿主机 0.0.0.0:5010"]
            WORKER["ops-ask-report-worker<br/>PDF 报告与定时任务后台"]
        end
    end

    subgraph ExtNet["现有外部服务集群"]
        PG[("PostgreSQL 15432<br/>172.20.224.151<br/>库: ops_ask")]
        ORA[("Oracle 1521<br/>172.26.78.133<br/>服务: datacache (WUH)")]
        LLM["内网 New API 大模型服务<br/>http://172.26.197.154:3000<br/>模型: DeepSeek-V3.1"]
    end

    CODE -->|挂载 :ro| WEB
    CODE -->|挂载 :ro| WORKER
    FE -->|挂载 :ro| WEB
    CFG -->|挂载 :ro| WEB
    CFG -->|挂载 :ro| WORKER
    DATA -->|挂载 :rw| WEB
    DATA -->|挂载 :rw| WORKER
    LOGS -->|挂载 :rw| WEB
    LOGS -->|挂载 :rw| WORKER

    WEB -->|业务查询与管理| PG
    WEB -->|智能对话问答| LLM
    WORKER -->|报表采集| PG
    WORKER -->|数据同步| ORA
```

### 1.3 宿主机挂载目录映射表

| 宿主机路径（位于 `/data/ops-ask`） | 容器内挂载路径 | 权限模式 | 作用说明 |
| :--- | :--- | :--- | :--- |
| `./ask_web.py` | `/app/ask_web.py` | `:ro` (只读) | 服务统一启动入口脚本 |
| `./app/` | `/app/app` | `:ro` (只读) | 后端业务逻辑、API 接口、图谱算法 |
| `./scripts/` | `/app/scripts` | `:ro` (只读) | 运维脚本与数据同步工具 |
| `./frontend/out/` | `/app/frontend/out` | `:ro` (只读) | Next.js 编译导出的纯静态 HTML/CSS/JS |
| `./config.json` | `/app/config.json` | `:ro` (只读) | 全局基线默认配置 |
| `./config.local.json` | `/app/config.local.json` | `:ro` (只读) | **服务器专有配置**（密码、LLM Key、网段，更新包不包含） |
| `./data/` | `/app/data` | `:rw` (读写) | 本地持久化：SQLite 管理库、问答记忆、本地本体 |
| `./artifacts/` | `/app/artifacts` | `:rw` (读写) | 服务日志输出、PDF 报告临时生成路径、备份数据 |

---

## 2. 部署资产与发版工具清单

### 2.1 打包与发版工具
- **`deploy/package-code-update.cmd`**：**日常增量打包专属脚本**。双击运行，跳过 Docker 镜像构建，只编译前端与打包代码，秒级生成约 1.5 MB 的 `ops-ask-code-update-1.0.tar.gz`。
- **`deploy/package-offline.cmd`**：**全量打包脚本**。构建 amd64 Docker 镜像、导出 tar 镜像包并压缩，生成约 650 MB 的全量离线大包。
- **`deploy/server-deploy.sh`**：服务器部署与健康检查脚本。

### 2.2 两种发版包的对比与选择

| 维度 | 增量程序包 (`ops-ask-code-update-*.tar.gz`) | 环境镜像包 (`ops-ask-runtime-*.tar`) |
| :--- | :--- | :--- |
| **文件大小** | 约 **1.5 MB**（秒传） | 约 **1.4 GB**（较大） |
| **适用场景** | 前端界面修改、接口修改、Prompt 调优、配置模板调整等（**99% 的日常更新**） | Python 新增了依赖包（改动了 `requirements.txt`）、更新了底层动态库、Chromium 版本变更 |
| **生成方式** | 开发机双击 `deploy/package-code-update.cmd` | 开发机执行 `deploy/package-offline.cmd` |
| **生效耗时** | 服务器解压并重启，约 **5~10 秒** | 上传镜像、`docker load` 并重建容器，约 **1~2 分钟** |
| **配置安全性** | 压缩包内**不含** `config.local.json`，覆盖时不丢配置 | 镜像不含任何业务数据与私密配置，纯运行沙箱 |

---

## 3. 核心操作：日常替换“程序包”（增量代码更新，约 1.5 MB）

这是日常业务功能迭代、Bug 修复、前端页面升级的标准操作。

### 步骤 3.1：在开发机一键生成程序包
在本地开发机项目根目录下：
1. 直接双击运行：
   ```cmd
   deploy\package-code-update.cmd
   ```
2. 等待 10~20 秒，脚本会自动执行 `npm run build` 重新编译前端，并将最新代码打包为：
   `output/ops-ask-code-update-1.0.tar.gz`（大小约 1.5 MB）。

### 步骤 3.2：上传程序包至服务器
通过 SFTP / SCP 将本地 `output/ops-ask-code-update-1.0.tar.gz` 上传至服务器的 `/data/ops-ask/` 目录下。

### 步骤 3.3：服务器解压并重启生效
登录服务器终端，依次执行以下命令：

```bash
cd /data/ops-ask

# 1. 解压覆盖代码与 compose 配置（不会影响现有的 config.local.json 和 data 目录）
tar -xzf ops-ask-code-update-1.0.tar.gz

# 2. 重新加载并重建容器（应用最新代码和 compose 挂载）
docker-compose up -d --force-recreate

# 3. 检查容器状态与端口
docker ps

# 4. 验证服务就绪状态（返回 "ready":true 即成功）
curl -s http://127.0.0.1:5010/api/status | grep '"ready":true'
```

> [!NOTE]
> 整个更新过程耗时约 5~10 秒，服务自动完成热替换。浏览器打开 `http://10.200.10.6:5010/`，按 `Ctrl + F5` 强制刷新浏览器缓存即可体验最新版本。

---

## 4. 核心操作：底层替换“环境镜像”（全量运行环境更新，约 1.4 GB）

当且仅当后端新增了 Python 第三方依赖（修改了 `requirements.txt`）或调整了 Dockerfile 系统底层组件（如 Chromium、字体库）时，才需要替换 Docker 镜像。

### 步骤 4.1：在开发机重新构建并导出镜像
1. 本地确保 Docker Desktop 处于运行状态。
2. 双击运行：
   ```cmd
   deploy\package-offline.cmd
   ```
3. 脚本会自动构建 `linux/amd64` 平台镜像并导出：
   `output/ops-ask-runtime-1.0.tar`（约 1.4 GB）。

### 步骤 4.2：上传镜像文件至服务器
将 `output/ops-ask-runtime-1.0.tar` 上传到服务器的 `/data/ops-ask/` 目录。

### 步骤 4.3：服务器导入新镜像并重建容器
在服务器终端执行：

```bash
cd /data/ops-ask

# 1. 导入新的 Docker 镜像
docker load -i ops-ask-runtime-1.0.tar

# 2. 查看镜像加载时间确认更新成功
docker images | grep ops-ask-runtime

# 3. 基于新镜像强制重建并启动全部容器
docker-compose up -d --force-recreate

# 4. 验证 Chromium PDF 渲染引擎是否正常（必须输出 Chromium OK）
docker exec ops-ask-report-worker python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print('Chromium OK'); b.close(); p.stop()"

# 5. 释放磁盘空间（导入成功后及时删除 1.4GB 临时 tar 包）
rm -f /data/ops-ask/ops-ask-runtime-1.0.tar

# 6. 清理替换下来的无标签历史旧镜像层
docker image prune -f
```

---

## 5. 全新服务器离线初始化部署流程

如果是新服务器全新开荒部署，请按以下步骤执行：

```bash
# 1. 创建部署主目录（必须在 /data 盘下）
mkdir -p /data/ops-ask
cd /data/ops-ask

# 2. 上传 ops-ask-runtime-1.0.tar 和 ops-ask-code-update-1.0.tar.gz（或全量包）到该目录

# 3. 加载镜像
docker load -i ops-ask-runtime-1.0.tar

# 4. 解压代码与配置
tar -xzf ops-ask-code-update-1.0.tar.gz

# 5. 准备 config.local.json
cp config.local.server.example.json config.local.json
vi config.local.json
# （填写真实数据库密码与大模型 Key）

# 6. 赋予执行权限并运行部署脚本
chmod +x server-deploy.sh
./server-deploy.sh
```

---

## 6. 配置文件说明与修改（config.local.json）

宿主机文件：`/data/ops-ask/config.local.json`  
该文件用于覆盖默认配置，存放敏感密码与内网专有网络配置。修改后只需执行 `docker-compose restart web` 即可生效。

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
    "model": "DeepSeek-V3.1",
    "api_key": "sk-Xsi2EmjMSo2vj2xzfGUiRpnhuUCn671Y1iwtslqR5h6shoAx",
    "permitted_origins": [
      "http://172.26.197.154:3000"
    ]
  }
}
```

> [!WARNING]
> 1. `server.port` 必须保持为 `5010`。
> 2. `server.lan_cidr` 务必设置为 `"0.0.0.0/0"`，以避免 Docker 网桥 NAT 转发将宿主机请求当做外部网段拦截（报 403）。
> 3. `llm.model` 针对内网 New API 必须严格为 **`DeepSeek-V3.1`**（大小写敏感，`S` 必须大写）。

---

## 7. 常用运维管理命令速查

> [!IMPORTANT]
> 服务器 Docker 20.10 环境未安装独立的 `compose` 插件，命令行**必须使用带中划线的 `docker-compose`**，避免误敲 `docker compose`。

### 7.1 服务状态与启停
```bash
cd /data/ops-ask

# 查看容器运行状态与端口映射
docker-compose ps

# 查看 Web 容器实时滚动日志
docker-compose logs -f --tail=100 web

# 查看 报表 Worker 容器实时滚动日志
docker-compose logs -f --tail=100 report-worker

# 重启全部服务（不重建容器）
docker-compose restart

# 重新加载配置并强制重建容器
docker-compose up -d --force-recreate

# 停止并删除容器（数据挂载在宿主机，不会丢失）
docker-compose down

# 清理孤儿残留容器
docker-compose up -d --remove-orphans
```

### 7.2 健康状态检查与连通性验证
```bash
# 1. 验证 Web 接口健康状态
curl -i http://127.0.0.1:5010/api/status

# 2. 检查 Worker 容器的 Playwright Chromium 无头浏览器
docker exec ops-ask-report-worker python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print('Chromium OK'); b.close(); p.stop()"

# 3. 检查内网大模型接口连通性
curl -i http://172.26.197.154:3000/v1/models \
  -H "Authorization: Bearer sk-Xsi2EmjMSo2vj2xzfGUiRpnhuUCn671Y1iwtslqR5h6shoAx"
```

### 7.3 磁盘与清理维护
```bash
# 1. 查看 /data 分区空间
df -h /data

# 2. 清理升级后遗留的安装包与镜像 tar
rm -f /data/ops-ask/*.tar /data/ops-ask/*.tar.gz

# 3. 清理 Docker 悬空历史镜像
docker image prune -f
```

---

## 8. 高频故障排查与应急手册 (FAQ)

### 故障 1：容器启动崩溃，日志报错 `IsADirectoryError: [Errno 21] Is a directory: '/app/config.local.json'`
- **故障根因**：首次执行 `docker-compose up` 时宿主机上尚不存在 `config.local.json` 文件，Docker 默认将不存在的挂载目标当做目录自动创建了一个空文件夹 `/data/ops-ask/config.local.json/`。Python 在读取文件时抛出 `IsADirectoryError`。
- **应急处理方案**：
  ```bash
  cd /data/ops-ask
  # 1. 停止容器
  docker-compose down
  # 2. 删除误创建的空目录
  rm -rf config.local.json
  # 3. 复制真实配置文件
  cp config.local.server.example.json config.local.json
  vi config.local.json  # 检查并填写真实配置
  # 4. 重新启动
  docker-compose up -d
  ```

---

### 故障 2：外部浏览器或 curl 访问报 `拒绝连接` 或 `5010` 端口无法连接
- **故障根因**：
  1. `docker-compose.yml` 端口映射与服务监听未对齐（例如映射了 `5010:8088`，但程序实际监听在 5010）；
  2. 容器尚未完全启动或处于 `Restarting` 崩溃重启状态。
- **应急处理方案**：
  1. 查看 `docker ps`，确认端口列显示为：`0.0.0.0:5010->5010/tcp`；
  2. 若不是，使用最新的 `ops-ask-code-update-1.0.tar.gz` 解压覆盖 `docker-compose.yml`；
  3. 执行 `docker-compose up -d --force-recreate`；
  4. 检查日志：`docker-compose logs --tail 50 web`。

---

### 故障 3：大模型提问报错 `HTTP 401: Unauthorized` 或 `HTTP 503 无可用渠道`
- **故障根因**：
  1. **报 401**：首次部署时管理员配置库 `data/ops_ask/admin/admin.db` 缓存了公网硅基流动的旧 Key。前端界面提问优先使用了数据库中的配置；
  2. **报 503**：New API 模型名称大小写敏感，填写成小写 `Deepseek-V3.1` 导致 New API 找不到映射渠道，正确应为 **`DeepSeek-V3.1`**（`S` 必须大写）。
- **应急处理方案**：
  1. 使用管理员账号登录 Web 管理后台（`http://10.200.10.6:5010/` -> 系统管理 -> 模型配置），将模型地址改为 `http://172.26.197.154:3000/v1/chat/completions`，模型名设为 `DeepSeek-V3.1`，填入有效 Token，保存并点击“测试连接”；
  2. 或在宿主机上通过命令行批量更新 SQLite 缓存配置：
     ```bash
     docker exec ops-ask-web python -c "
     import sqlite3
     conn = sqlite3.connect('/app/data/admin/admin.db')
     cur = conn.cursor()
     cur.execute(\"UPDATE llm_configs SET api_key='sk-Xsi2EmjMSo2vj2xzfGUiRpnhuUCn671Y1iwtslqR5h6shoAx', model='DeepSeek-V3.1', base_url='http://172.26.197.154:3000/v1/chat/completions' WHERE is_active=1\")
     conn.commit()
     print('LLM Config Updated Successfully')
     "
     ```

---

### 故障 4：点击“生成运行报告”失败，提示检查浏览器安装
- **故障根因**：当前运行的 Docker 镜像中未打包无头 Chromium 或缺失 `libatomic.so.1` 动态链接库。
- **应急处理方案**：
  参考 [4. 核心操作：底层替换“环境镜像”](#4-核心操作底层替换环境镜像全量运行环境更新约-14-gb)，上传已内置 Playwright Chromium 和 `libatomic1` 的镜像包 `ops-ask-runtime-1.0.tar`，执行 `docker load -i` 并重建容器。

---

### 故障 5：访问页面提示 `403 Forbidden`
- **故障根因**：`lan_cidr` 网段白名单未放行 Docker 虚拟网桥网段（`172.17.x.x`）或办公网段。
- **应急处理方案**：
  编辑 `/data/ops-ask/config.local.json`，将 `server.lan_cidr` 设为 `"0.0.0.0/0"`，然后执行 `docker-compose restart web`。
