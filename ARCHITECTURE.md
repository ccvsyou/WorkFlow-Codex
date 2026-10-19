# FlowCodex Architecture

## 总览

```text
Browser
  │
  ▼
FastAPI /api  ──────────────  SQLite (WAL)
  │                              │
  │                              ├─ projects
  │                              ├─ requirements
  │                              ├─ runs
  │                              ├─ run_events
  │                              └─ knowledge
  │
  ├─ Worker (single process, DB-backed queue)
  │    │
  │    ├─ Clarify Agent
  │    ├─ Feasibility Agent
  │    ├─ Plan Agent
  │    ├─ Code Agent
  │    ├─ Review Agent
  │    └─ Distill Agent
  │
  └─ Git operations
       ├─ repo context
       ├─ isolated worktree
       ├─ git apply --check / --index
       ├─ staged diff
       └─ commit
```

设计目标是把“可靠性”放在“并发吞吐”之前：单机、单 Worker、SQLite WAL，适合 2C2G 轻量服务器。需要横向扩展时，可把 Worker 拆成独立进程，并把 SQLite 换成 Postgres。

## 状态机

运行状态：

- `queued`：等待 Worker。
- `running`：Worker 已接管。
- `awaiting_input`：需求访谈等待人工回答。
- `awaiting_plan_approval`：方案等待人工批准。
- `blocked`：可行性、审查或验证拦截。
- `completed`：交付完成。
- `failed`：未捕获异常或模型输出不可用。
- `cancelled`：人工取消。

阶段顺序：

```text
intake → clarify → feasibility → plan → code → review → verify → deliver → distill
```

暂停点：

- `clarify` 产出必答问题 → `awaiting_input`。
- `plan` 产出方案 → `awaiting_plan_approval`。
- 可行性低于阈值 → `blocked`，可人工放行。
- 审查存在 critical → `blocked`，可返工。
- 验证失败 → `blocked`，可返工。

## 数据流

### 1. 需求提交

`POST /api/requirements` 创建 requirement 和 run。run 初始状态 `queued`，阶段 `intake`。

### 2. 需求访谈

Worker 调用 Clarify Agent，输入：

- 需求原文；
- 项目元数据；
- 前置注入知识；
- 上一轮访谈与用户回答。

输出 `ClarifyResult`：理解复述、问题、Spec、假设。问题未清时暂停。

### 3. 可行性

`build_repo_context` 读取：

- `git ls-files` 文件列表；
- README、构建文件、配置、入口文件；
- 最近 commit 和工作区状态；
- 与需求关键词最相关的源码片段。

Feasibility Agent 输出七维分数和 blockers。总分按固定权重计算。

### 4. 技术方案

Plan Agent 使用 Spec、可行性、仓库上下文、知识。输出依赖排序步骤、风险、自审、待确认问题。人工批准后进入编码。

### 5. 编码

Code Agent 输出 unified diff。系统：

1. `git worktree add -b flowcodex/run-<id> <dest> HEAD`
2. 写临时 patch 文件
3. `git apply --check`
4. `git apply --index`
5. `git diff --cached --binary`

worktree 位于 `DATA_DIR/workspaces/run-<id>`，主工作区不变。

### 6. 审查与验证

Review Agent 对 staged diff 做两档审查和六项安全硬检查。通过后在 worktree 内执行 `verify_command`。

### 7. 交付

- 补丁写入 `DATA_DIR/artifacts/run-<id>/change.patch`。
- 项目 `auto_apply` 为 true 时提交隔离分支。
- 项目 `auto_publish` 为 true 时执行 `publish_command`。
- 导出 Obsidian 笔记（如果配置了 vault）。

### 8. 知识蒸馏

Distill Agent 从 Spec、方案、审查、验证中提取跨需求可复用结论。高置信度自动入库，低置信度进入待复核。

## 数据库

### projects

项目元数据、仓库路径、验证/发布命令、允许路径、自动提交/发布开关。

### requirements

原始需求与结构化 Spec。`spec_json` 是所有下游 Agent 的单一事实源。

### runs

运行状态、阶段、各阶段 JSON 结果、补丁、分支、worktree、token 计数、错误。

### run_events

不可变事件流，用于管理端时间线和审计。

### knowledge

知识资产：kind、title、content、tags、confidence、ttl_days、expires_at、status、source_run_id。

### knowledge_feedback

人工反馈记录与降级依据。

## 失败处理

- **LLM 非 JSON**：抛出错误，运行 `failed`，事件记录原始错误。
- **补丁不可应用**：保存补丁和应用错误，运行 `blocked`。
- **验证失败**：保存 stdout/stderr，运行 `blocked`。
- **知识蒸馏失败**：交付保持 `completed`，记录 warning。
- **Obsidian 导出失败**：交付保持 `completed`，记录 warning。
- **Worker 重启**：启动时把遗留 `running` 运行重新置为 `queued`。
- **重复人工操作**：状态不符时 API 返回 409。

## 安全设计

- 仓库路径必须位于 `REPOS_ROOT`。
- 使用 `Path.resolve()` + `relative_to()` 防路径逃逸。
- 命令使用 `subprocess.run(list, shell=False)`，不使用 shell 拼接。
- 补丁只应用到独立 worktree。
- 敏感信息不写入日志；LLM 提示词明确禁止生成密钥。
- 管理端 Token 使用常量时间比较。
- Caddy 添加基础安全头。
- Docker 容器使用非 root 用户（UID 10001），`no-new-privileges`。

### 已知限制

- `verify_command` / `publish_command` 本质上是应用用户可执行的任意命令，不是安全沙箱。
- 单用户 Token 不提供 RBAC、审计导出、租户隔离。
- 只支持单仓库闭环。
- SQLite 不适合多 Worker 高并发。

## 扩展方向

1. GitHub/GitLab PR 连接器。
2. Postgres + Redis/Celery 多 Worker。
3. 容器化命令沙箱（资源、网络、文件系统限制）。
4. 组织级知识库与审批流。
5. 浏览器端 diff 审查与逐行评论。
6. 成本预算与模型路由。
