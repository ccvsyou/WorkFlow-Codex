#!/usr/bin/env bash
set -euo pipefail

# FlowCodex one-command bootstrap for Ubuntu 22.04/24.04 on Tencent Cloud Lighthouse.
# Run as root from the repository root: sudo bash deploy/install-tencent.sh

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 或 sudo 运行。"
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/flowcodex}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "[1/6] 安装 Docker..."
  apt-get update
  apt-get install -y ca-certificates curl git rsync
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "[1/6] Docker 已安装。"
  apt-get update
  apt-get install -y rsync
  if ! docker compose version >/dev/null 2>&1; then
    echo "检测到 Docker Compose 插件缺失，正在安装..."
    apt-get install -y docker-compose-plugin
  fi
fi

systemctl enable --now docker

echo "[2/6] 同步应用文件到 ${APP_DIR}..."
mkdir -p "${APP_DIR}"
if [[ "${SRC_DIR}" != "${APP_DIR}" ]]; then
  rsync -a --delete \
    --exclude '.git' --exclude '.env' --exclude '.venv' --exclude '.pytest_cache' --exclude '__pycache__' --exclude 'data' --exclude 'repos' --exclude 'vault' \
    "${SRC_DIR}/" "${APP_DIR}/"
else
  echo "已在应用目录，跳过同步。"
fi

echo "[3/6] 准备持久化目录..."
mkdir -p "${APP_DIR}/data" "${APP_DIR}/repos" "${APP_DIR}/vault"
chown -R 10001:10001 "${APP_DIR}/data" "${APP_DIR}/repos" "${APP_DIR}/vault"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "[4/6] 生成 .env（请稍后修改 API_TOKEN / LLM_API_KEY / SITE_ADDRESS）..."
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  TOKEN="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sed -i "s/replace-with-a-long-random-token/${TOKEN}/" "${APP_DIR}/.env"
else
  echo "[4/6] 保留已有 .env。"
fi

# Custom host directories from .env must also be writable by the container user.
cd "${APP_DIR}"
for env_key in DATA_HOST_DIR REPOS_HOST_DIR OBSIDIAN_HOST_DIR; do
  env_value="$(grep -E "^${env_key}=" .env | head -1 | cut -d= -f2- || true)"
  if [[ -n "${env_value}" ]]; then
    mkdir -p "${env_value}"
    chown -R 10001:10001 "${env_value}"
  fi
done

echo "[5/6] 构建并启动..."
cd "${APP_DIR}"
docker compose up -d --build

echo "[6/6] 完成。"
echo "查看状态: cd ${APP_DIR} && docker compose ps"
echo "查看日志: cd ${APP_DIR} && docker compose logs -f flowcodex"
echo "打开管理端: http://<服务器公网IP>/"
