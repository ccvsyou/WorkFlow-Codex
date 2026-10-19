# 部署到腾讯云轻量应用服务器

本手册以 **Ubuntu 24.04 LTS / 2 核 2G / 40GB SSD** 为例。1 核 2G 也能跑，但首次构建和同时运行代码仓库时建议 2 核。

## 1. 购买与基础配置

1. 在腾讯云轻量应用服务器控制台创建实例，镜像选择 **Ubuntu 24.04 LTS**。
2. 防火墙放行：
   - `22/tcp`（SSH）
   - `80/tcp`（HTTP）
   - `443/tcp`（HTTPS，使用域名时）
3. **不要**在防火墙放行 8000；应用只通过 Caddy 反向代理访问。
4. 建议绑定域名。中国大陆地域的域名需要完成备案；没有域名可以先用 `http://公网IP/`。

## 2. 上传代码

在本地项目目录：

```powershell
# 将整个目录上传到服务器，或在服务器上 git clone 你的仓库
scp -r .\WorkFlow-Codex root@<服务器IP>:/opt/flowcodex-src
```

如果代码已经在 Git 仓库中，也可以：

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone <你的仓库地址> flowcodex-src
```

## 3. 一键安装

```bash
cd /opt/flowcodex-src
sudo bash deploy/install-tencent.sh
```

脚本会：

- 安装 Docker Engine 与 Compose 插件；
- 同步文件到 `/opt/flowcodex`；
- 创建 `data/ repos/ vault/` 并设置容器用户权限；
- 从 `.env.example` 生成 `.env`，自动生成随机 `API_TOKEN`；
- 构建并启动 `flowcodex` 与 `caddy`。

## 4. 配置环境变量

```bash
cd /opt/flowcodex
sudo nano .env
```

至少修改：

```env
API_TOKEN=上一步生成的随机值，可换成你自己的长随机串

LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-你的DeepSeekKey
LLM_MODEL=deepseek-chat

# 只有 IP：
SITE_ADDRESS=:80
# 有域名并已解析：
# SITE_ADDRESS=flow.example.com
```

保存后：

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs -f flowcodex
```

## 5. 准备代码仓库

FlowCodex 只允许操作 `REPOS_ROOT=/repos` 下的 git 仓库。

```bash
cd /opt/flowcodex/repos
sudo git clone <你的仓库地址> my-app
sudo chown -R 10001:10001 /opt/flowcodex/repos/my-app
```

确保仓库至少有一个 commit：

```bash
sudo -u '#10001' git -C /opt/flowcodex/repos/my-app log --oneline -1
```

如果仓库属于其他用户、权限不足，容器内无法创建 worktree。至少保证 UID 10001 对仓库目录和 `.git` 有读写权限。

## 6. 首次登录

打开：

- 只有 IP：`http://<服务器公网IP>/`
- 有域名：`https://flow.example.com/`

点击左下角 **API Token**，填入 `.env` 中的 `API_TOKEN`。

创建项目：

- 项目名称：`my-app`
- 仓库路径：`my-app`（相对路径会解析到 `/repos/my-app`）
- 默认分支：`main`
- 验证命令：例如 `pytest -q`、`npm test`、`go test ./...`
- 允许路径：`.`（或 `src, tests`）
- 自动提交：建议先用关闭状态，确认流水线稳定后再开启

提交一条需求，按页面提示完成需求补充和方案批准。

## 7. 域名与 HTTPS

1. 在 DNS 添加 A 记录：`flow.example.com -> 服务器公网 IP`。
2. 修改 `.env`：

```env
SITE_ADDRESS=flow.example.com
```

3. 重启：

```bash
cd /opt/flowcodex
sudo docker compose up -d
```

Caddy 会自动申请 Let's Encrypt 证书。确保 80/443 已放行，且域名已备案（中国大陆地域）。

## 8. Obsidian 导出

如果 Obsidian vault 在服务器上：

```bash
# 例如用 Syncthing/rsync 把 vault 同步到服务器
sudo mkdir -p /opt/flowcodex/vault
sudo chown -R 10001:10001 /opt/flowcodex/vault
```

`.env` 中保持：

```env
OBSIDIAN_VAULT_PATH=/vault
OBSIDIAN_FOLDER=WorkFlow-Codex
```

完成一次运行后，页面上的“导出 Obsidian”会写入：

```text
/opt/flowcodex/vault/WorkFlow-Codex/YYYY-MM-DD-需求标题-runid.md
```

## 9. 备份

```bash
cd /opt/flowcodex
sudo bash deploy/backup.sh
```

备份包含 `data/ repos/ vault/ .env`。建议每天用 cron：

```bash
sudo crontab -e
```

```cron
0 3 * * * cd /opt/flowcodex && bash deploy/backup.sh >> /var/log/flowcodex-backup.log 2>&1
```

## 10. 升级

```bash
cd /opt/flowcodex-src
sudo git pull
sudo bash deploy/install-tencent.sh
```

`install-tencent.sh` 使用 `rsync --delete` 同步代码，但会排除 `data/`、`repos/`、`vault/`、`.env`，不会删除持久化数据。

## 11. 常见问题

### 页面 502

```bash
cd /opt/flowcodex
sudo docker compose ps
sudo docker compose logs -f flowcodex
```

常见原因：`.env` 中 LLM 配置错误、容器启动失败、数据目录权限错误。

### 项目创建失败：Repository must live under REPOS_ROOT

仓库必须放在 `/opt/flowcodex/repos` 下，或者填入相对于 `/repos` 的路径。

### worktree 创建失败

检查：

```bash
sudo -u '#10001' git -C /opt/flowcodex/repos/my-app status
sudo -u '#10001' git -C /opt/flowcodex/repos/my-app worktree list
```

### 验证命令失败

验证命令在隔离 worktree 中执行，工作目录不是主仓库。确保命令不依赖主仓库的绝对路径、未提交文件或交互式终端。

### 想重置演示数据

**会删除运行记录和 worktree**，执行前先备份：

```bash
cd /opt/flowcodex
sudo docker compose down
sudo rm -rf data/*
sudo docker compose up -d
```

## 12. 安全建议

- 不要把 8000 端口暴露到公网。
- 使用长随机 `API_TOKEN`，不要使用默认值。
- 只接入你信任的仓库；`verify_command` / `publish_command` 不是安全沙箱。
- 不要把资金、支付、核心安全链路接入自动交付。
- 定期备份 `data/`。
- 使用 SSH 密钥登录，关闭密码登录。
