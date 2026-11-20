#!/usr/bin/env bash
set -euo pipefail

# 在离线服务器的 /data/ops-ask 目录下执行：
#   chmod +x /data/ops-ask/server-deploy.sh
#   /data/ops-ask/server-deploy.sh

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="ops-ask-runtime:1.0"
IMAGE_TAR="${APP_DIR}/ops-ask-runtime-1.0.tar"
PORT="5010"

cd "${APP_DIR}"

echo "=========================================="
echo " ops-ask 离线部署脚本"
echo " 工作目录: ${APP_DIR}"
echo "=========================================="

# 1. 检查目录与磁盘空间
if [[ "${APP_DIR}" != /data/* ]]; then
  echo "[警告] 当前目录不在 /data 分区下 (${APP_DIR})，建议迁移至 /data/ops-ask"
fi
df -h "${APP_DIR}"

# 2. 准备可写挂载目录
mkdir -p \
  "${APP_DIR}/data/ops_ask/admin" \
  "${APP_DIR}/data/ops_ask/operations" \
  "${APP_DIR}/data/ops_ask/runtime" \
  "${APP_DIR}/data/ops_ask/ontology_history" \
  "${APP_DIR}/artifacts/logs" \
  "${APP_DIR}/artifacts/backups"

# 3. 首次部署检查 config.local.json（不覆盖服务器已有配置）
if [[ ! -f "${APP_DIR}/config.local.json" ]]; then
  if [[ -f "${APP_DIR}/config.local.server.example.json" ]]; then
    cp "${APP_DIR}/config.local.server.example.json" "${APP_DIR}/config.local.json"
    echo ""
    echo "[中止] 已生成模板文件: ${APP_DIR}/config.local.json"
    echo "       请先编辑该文件，填入现有数据库连接地址、密码、LLM 配置和 lan_cidr，"
    echo "       修改完成后重新执行本脚本。"
    exit 1
  else
    echo "[错误] 缺少 ${APP_DIR}/config.local.json"
    exit 1
  fi
fi

# 4. 检查前端产物
if [[ ! -f "${APP_DIR}/frontend/out/index.html" ]]; then
  echo "[错误] 缺少前端构建产物: ${APP_DIR}/frontend/out/index.html"
  exit 1
fi

# 5. 导入离线镜像（若当前目录存在 tar 包）
if [[ -f "${IMAGE_TAR}" ]]; then
  echo "[1/3] 导入离线镜像 ${IMAGE_TAR} ..."
  docker load -i "${IMAGE_TAR}"
  echo "      导入完成（确认服务稳定后可手动删除 ${IMAGE_TAR} 释放磁盘空间）"
else
  if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "[错误] 本地未找到镜像 ${IMAGE_NAME}，且目录下缺少 ${IMAGE_TAR}"
    exit 1
  fi
  echo "[1/3] 复用已存在的镜像 ${IMAGE_NAME}"
fi

# 6. 启动或重建容器（自动兼容 docker compose v2 / docker-compose v1 / 纯 docker run）
echo "[2/3] 启动容器 ..."
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --force-recreate
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose up -d --force-recreate
else
  echo "      未检测到 docker compose，使用原生 docker run 启动 ..."
  docker rm -f ops-ask-web ops-ask-report-worker >/dev/null 2>&1 || true
  docker run -d \
    --name ops-ask-web \
    --restart unless-stopped \
    -w /app \
    -p "${PORT}:${PORT}" \
    --add-host=host.docker.internal:host-gateway \
    -e TZ=Asia/Shanghai \
    -e HBASK_DATA_SOURCE=ops_ask \
    -e HBASK_HOST=0.0.0.0 \
    -e HBASK_PORT="${PORT}" \
    -v "${APP_DIR}/ask_web.py:/app/ask_web.py:ro" \
    -v "${APP_DIR}/app:/app/app:ro" \
    -v "${APP_DIR}/scripts:/app/scripts:ro" \
    -v "${APP_DIR}/frontend/out:/app/frontend/out:ro" \
    -v "${APP_DIR}/config.json:/app/config.json:ro" \
    -v "${APP_DIR}/config.local.json:/app/config.local.json:ro" \
    -v "${APP_DIR}/data:/app/data" \
    -v "${APP_DIR}/artifacts:/app/artifacts" \
    "${IMAGE_NAME}"
  docker run -d \
    --name ops-ask-report-worker \
    --restart unless-stopped \
    -w /app \
    --add-host=host.docker.internal:host-gateway \
    -e TZ=Asia/Shanghai \
    -e HBASK_DATA_SOURCE=ops_ask \
    -v "${APP_DIR}/app:/app/app:ro" \
    -v "${APP_DIR}/scripts:/app/scripts:ro" \
    -v "${APP_DIR}/frontend/out:/app/frontend/out:ro" \
    -v "${APP_DIR}/config.json:/app/config.json:ro" \
    -v "${APP_DIR}/config.local.json:/app/config.local.json:ro" \
    -v "${APP_DIR}/data:/app/data" \
    -v "${APP_DIR}/artifacts:/app/artifacts" \
    "${IMAGE_NAME}" \
    python -X utf8 -m app.report_worker
fi

# 7. 健康检查
echo "[3/3] 等待服务就绪 (http://127.0.0.1:${PORT}/api/status) ..."
for i in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/status" 2>/dev/null | grep -q '"ready":true\|"ready": true'; then
    echo ""
    echo "[成功] ops-ask 已就绪！访问地址: http://<服务器IP>:${PORT}/"
    exit 0
  fi
  sleep 2
done

echo ""
echo "[警告] 等待超时，请查看容器日志排查数据库连接或配置："
docker logs --tail 60 ops-ask-web
exit 1
