# FlowCodex

**把一条需求，推到可验证交付。**

FlowCodex 是一个自托管的工作流 Agent 系统：从需求访谈、可行性评估、技术方案、隔离编码、代码审查、验证、交付，到知识蒸馏，整条链路有明确状态、质量门禁和人工决策点。

它参考了腾讯技术工程文章《别只盯着 AI Coding，真正的变化发生在整个研发流程》中的端到端研发理念，但不是复刻文章里的内部 OK 平台。这里是一套独立、可运行、可部署到腾讯云轻量服务器的实现。

## 它解决什么问题

- **需求模糊**：先用访谈式澄清补目标用户、业务规则、验收标准，不带着错误理解进入开发。
- **方案不可执行**：方案按依赖图排序、按垂直切片拆分，单步控制文件范围，并做一次“找茬式”自审。
- **AI 改出范围外问题**：补丁只能改方案列出的文件，范围外问题记录在“发现但不碰”。
- **不敢让 AI 直接改仓库**：每个运行创建独立 git worktree，补丁先应用、再审查、再验证，最后才允许提交。
- **知识不沉淀**：每次交付后自动蒸馏可复用经验，支持 TTL、置信度和人工反馈降级。
- **过程不可见**：管理端展示阶段、事件、评分、方案、补丁、审查、验证和知识资产。

## 核心能力

1. **九阶段状态机**：intake → clarify → feasibility → plan → code → review → verify → deliver → distill。
2. **需求访谈门禁**：生成结构化 Spec，信息不足时暂停。
3. **七维可行性评分**：清晰度、仓库匹配、范围、技术简单度、依赖、风险、交付形态，默认 60 分通过。
4. **技术方案施工图**：依赖排序、垂直切片、检查点、自审、待确认问题。
5. **隔离 worktree 编码**：补丁 `git apply --check` + `git apply --index`，不直接污染主工作区。
6. **改善即批准审查**：只区分 critical / suggestion；六项安全硬检查命中即阻塞。
7. **验证命令**：在 worktree 内执行项目自定义命令，失败则拦截。
8. **交付产物**：补丁文件、隔离分支、可选提交、可选发布命令。
9. **知识飞轮**：前置强注入、交付后蒸馏、TTL 老化、反馈降级。
10. **Obsidian 导出**：把运行摘要、Spec、方案、审查和验证结果写入 Markdown 笔记。
11. **Mock 模式**：没有 API Key 也能跑通全流水线，便于本地验证和演示。
12. **全中文管理端**：工作台、项目页、运行详情、知识库、弹窗、状态与错误提示均为中文。

## 技术栈

- Python 3.11+ / FastAPI / Uvicorn
- SQLite（WAL，单机零依赖）
- 原生 HTML/CSS/JS 管理端，无前端构建步骤
- Git worktree 隔离
- OpenAI-compatible LLM API（默认 DeepSeek）
- Docker Compose + Caddy（腾讯云轻量服务器）

## 本地快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
# 编辑 .env：本地演示可保持 LLM_PROVIDER=mock；真实模型填写 LLM_API_KEY
```

准备一个仓库（放在 `repos/` 下）：

```powershell
mkdir repos\demo
git -C repos\demo init -b main
"# Demo" | Set-Content repos\demo\README.md
git -C repos\demo add .
git -C repos\demo -c user.name=Demo -c user.email=demo@local commit -m init
```

启动：

```powershell
$env:LLM_PROVIDER="mock"
$env:API_TOKEN=""
$env:DATA_DIR="./data"
$env:REPOS_ROOT="./repos"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，创建项目：

- 仓库路径：`demo` 或绝对路径 `.../repos/demo`
- 验证命令：`python -c "print('ok')"`
- 勾选“自动提交到隔离分支”（本地演示可选）

然后提交一条需求，按管理端提示完成访谈回答和方案批准。

也可以先启动服务，再运行演示脚本自动创建 demo 仓库、项目和一条需求：

```powershell
.\.venv\Scripts\python.exe scripts\seed_demo.py --api http://127.0.0.1:8000 --repos-root ./repos
```

## 真实模型配置

```env
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-chat
```

也支持任意 OpenAI-compatible 服务：把 `LLM_BASE_URL` 指向服务的 `/v1` 或根路径即可，代码会请求 `/chat/completions`。

## 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_TOKEN` | 空 | 设置后所有 `/api/*` 需要 Bearer Token |
| `DATA_DIR` | `./data` | SQLite、worktree、补丁产物 |
| `REPOS_ROOT` | `./repos` | 只允许操作此目录下的 git 仓库 |
| `LLM_PROVIDER` | `mock` | `mock` / `deepseek` / `openai-compatible` |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible 根地址 |
| `LLM_API_KEY` | 空 | 模型密钥 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `FEASIBILITY_THRESHOLD` | `60` | 可行性门禁 |
| `AUTO_APPLY_PATCH` | `false` | 全局默认；每个项目还有自己的 `auto_apply` |
| `VERIFY_TIMEOUT` | `300` | 验证命令超时秒数 |
| `OBSIDIAN_VAULT_PATH` | 空 | 挂载的 Obsidian vault 路径 |
| `OBSIDIAN_FOLDER` | `WorkFlow-Codex` | 导出笔记子目录 |

## 安全边界

- 仓库必须位于 `REPOS_ROOT` 内。
- 补丁只应用到独立 worktree；主工作区不被直接修改。
- 代码审查存在 critical 时流程阻塞。
- `verify_command` / `publish_command` 是项目管理员配置的命令，会在容器内以应用用户执行；不要把不可信仓库交给本系统。
- 管理端只适合单用户/小团队自托管。多租户、RBAC、审计留存和真正的命令沙箱不在 MVP 范围。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前测试覆盖：Token 鉴权、项目创建、需求访谈暂停/恢复、可行性、方案批准、补丁应用、审查、验证、交付、知识沉淀、补丁下载、Obsidian 导出。

## 腾讯云部署

见 [deploy/tencent-lighthouse.md](deploy/tencent-lighthouse.md)。最短路径：

```bash
sudo bash deploy/install-tencent.sh
```

## 文档

- [SPEC.md](SPEC.md) — 产品规格与验收标准
- [ARCHITECTURE.md](ARCHITECTURE.md) — 架构、状态机、数据模型、失败处理
- [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md) — 原文要点与本实现的映射
- [deploy/tencent-lighthouse.md](deploy/tencent-lighthouse.md) — 轻量服务器部署手册
- [docs/COMMERCIAL_READINESS.md](docs/COMMERCIAL_READINESS.md) — 商业化就绪评估、阻断项与 30/60/90 天路线图
