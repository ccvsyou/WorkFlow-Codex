# FlowCodex SPEC

## Summary

FlowCodex 是一个自托管、单租户的端到端研发工作流 Agent。用户提交一条需求后，系统按质量门依次完成需求访谈、可行性评估、技术方案、隔离编码、代码审查、验证、交付和知识蒸馏。人只在需求补充、方案批准、可行性放行、返工等判断节点介入。

本规格基于 [腾讯技术工程文章](https://zhuanlan.zhihu.com/p/2083953932536234138) 的原则独立实现，不复刻其内部 OK 平台的代码、界面或组织流程。

## Problem

现有 AI Coding 工具主要提升“写代码”速度，但需求理解、方案决策、跨模块影响、验证和知识沉淀仍然断裂。结果是：

- 需求模糊时，AI 快速产出错误实现；
- 代码能编译但缺少关键路径验证；
- AI 顺手修改范围外代码，引入噪声和回归；
- 每次对话从零开始，历史踩坑无法复用；
- 过程不可见，人无法判断该在何处介入。

## Goals

1. 把“需求 → 可验证交付”做成显式状态机，而不是一段长 Prompt。
2. 在任何执行前生成结构化 Spec，并允许人在信息不足时补充。
3. 用可行性门禁拦截明显不适合端到端的需求。
4. 用依赖排序和垂直切片让技术方案可执行。
5. 在隔离 worktree 中应用补丁，审查和验证通过后才允许提交。
6. 用 critical / suggestion 两档审查标准避免风格性返工。
7. 把每次交付中可复用的结论沉淀为带 TTL 和置信度的知识。
8. 提供可在腾讯云轻量服务器上运行的单机部署方案。

## Non-Goals

- 不做多租户 SaaS、组织级 RBAC、SSO。
- 不做跨仓库/跨服务联动。
- 不做资金、支付、核心安全链路的自动交付。
- 不做真正的容器级命令沙箱；验证/发布命令的执行安全由部署者负责。
- 不自动 push 到远程仓库或创建 GitHub PR（MVP 只生成隔离分支、补丁和可选提交）。
- 不追求 100% 需求自动完成率；可行性门禁的作用就是拦截不适合的需求。

## Users

- **个人开发者**：把自己的 git 仓库接入，快速完成小需求。
- **2-5 人小团队**：共享一台自托管实例，用结构化 Spec 和审查门禁降低返工。
- **知识工作者**：把 Obsidian 作为交付记录和知识资产入口。

## Functional Requirements

### FR-1 项目接入

- 创建项目时必须提供名称和仓库路径。
- 仓库必须在 `REPOS_ROOT` 内，且是包含至少一个 commit 的 git worktree。
- 项目可配置验证命令、发布命令、允许路径、是否自动提交。

### FR-2 需求提交

- 需求包含标题和原始描述。
- 提交后默认立即创建运行并进入 intake。
- 运行有唯一 ID，关联项目与需求。

### FR-3 需求访谈

- Agent 先复述理解，再提出最多 4 个问题。
- 必须补齐：做什么、为谁做、关键业务规则、验收标准。
- 信息不足时运行进入 `awaiting_input`，不继续执行。
- 用户回答后运行重新排队，生成结构化 Spec。

### FR-4 可行性评估

- 七维评分，权重固定：
  - clarity 20%
  - repo_match 20%
  - scope_control 15%
  - technical_simplicity 15%
  - dependency_readiness 10%
  - risk_control 10%
  - delivery_shape 10%
- 总分低于 `FEASIBILITY_THRESHOLD` 或存在 blockers 时进入 `blocked`。
- 人工可填写原因后强制放行。

### FR-5 技术方案

- 方案步骤按依赖图排序。
- 每个步骤包含：id、标题、依赖、文件、变更、验收、验证。
- 单步预计不超过 5 个文件；跨 2 个以上子系统必须拆分。
- 每 2-3 步有验证检查点。
- 方案包含风险、假设、待确认问题、自审结果。
- 运行进入 `awaiting_plan_approval`，人工批准后才编码。

### FR-6 编码与隔离

- 编码 Agent 只能修改方案列出的文件。
- 范围外发现记录在 `discovered_not_touched`。
- 补丁必须是 unified diff，先 `git apply --check`，再 `git apply --index`。
- 补丁应用在独立 worktree 和 `flowcodex/run-<id>` 分支。
- 补丁无法应用时运行阻塞。

### FR-7 代码审查

- 问题只分 `critical` 和 `suggestion`。
- 只有 critical 才允许 `changes_requested`。
- 六项安全硬检查：登录态 UID、鉴权中间件、参数化 SQL、无硬编码敏感信息、边界校验、资源归属。
- 任一安全项失败视为 critical。

### FR-8 验证

- 项目配置 `verify_command` 时在 worktree 内执行。
- 超时、非零退出码或命令无法解析时阻塞。
- 未配置验证命令时记录 `skipped`，不伪造通过结果。

### FR-9 交付

- 生成 `change.patch` 产物。
- `auto_apply=true` 时在 worktree 内提交。
- `auto_publish=true` 且配置 `publish_command` 时执行发布命令。
- 管理端支持下载补丁、提交隔离分支、导出 Obsidian 笔记。

### FR-10 知识飞轮

- 每次运行后尝试蒸馏知识候选。
- 候选包含 kind、title、content、tags、confidence、ttl_days。
- confidence >= 0.75 自动入库；否则进入 `pending_review`。
- 后续运行按 token overlap + confidence 前置注入知识。
- 过期知识自动进入 `pending_review`。
- 人工反馈会将置信度下调 0.15；低于 0.4 时进入 `pending_review`。

### FR-11 管理端

- 工作台展示进行中、待决策、已完成、知识资产数量。
- 项目页展示仓库、验证命令、需求列表和运行状态。
- 运行页展示阶段轨、Spec、访谈、评分、方案、补丁、审查、验证、知识、事件。
- 支持 Token 登录、自动轮询、下载补丁、导出 Obsidian、取消运行、返工。

## Acceptance Criteria

1. 无 API Key 时，`LLM_PROVIDER=mock` 能完整跑通一条需求到 `completed`。
2. 需求信息不足时运行停在 `awaiting_input`；提交回答后继续。
3. 技术方案未批准时不会进入编码。
4. 补丁在独立 worktree 中应用，主工作区保持干净。
5. 任一 critical 审查问题会阻塞运行。
6. 验证命令失败会阻塞运行。
7. 完成后存在补丁产物、审查结论、验证结果和至少一条知识候选。
8. 配置 Obsidian vault 后能导出 Markdown 笔记。
9. `API_TOKEN` 设置后，未带 Token 的 `/api/*` 请求返回 401。
10. `pytest -q` 全部通过。
11. `docker compose config` 可解析；Dockerfile 可构建（构建验证需在安装 Docker 的环境执行）。
12. 腾讯云轻量服务器可按部署文档从零启动。

## Edge Cases

- 仓库没有 commit：项目创建失败。
- 仓库不在 `REPOS_ROOT`：项目创建失败。
- 补丁为空或超过大小限制：运行阻塞。
- 补丁无法应用：保留补丁与错误，运行阻塞。
- LLM 返回非 JSON：运行失败并记录错误。
- Worker 重启时存在 `running` 运行：启动时重新排队。
- 知识蒸馏失败：交付仍完成，记录 warning。
- Obsidian 导出失败：交付仍完成，记录 warning。
- 验证命令超时：按失败处理。
- 同一运行重复批准：返回 409。

## Technical Design

- `app/services/engine.py`：阶段状态机与 Worker。
- `app/services/gitops.py`：仓库校验、上下文采集、worktree、补丁、命令执行。
- `app/services/knowledge.py`：知识注入、蒸馏入库、TTL、反馈降级。
- `app/services/obsidian.py`：运行笔记导出。
- `app/llm.py`：OpenAI-compatible 客户端与 Mock Provider。
- `app/prompts.py`：各阶段系统提示词与结构化输出契约。
- `app/main.py`：FastAPI API 与静态管理端。
- SQLite 表：projects、requirements、runs、run_events、knowledge、knowledge_feedback。

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | JSON 解析、评分计算、知识 token 匹配 | 待补 |
| Integration | API 鉴权、项目创建、澄清暂停/恢复、方案批准、补丁应用、审查、验证、交付、知识、Obsidian | 2 个测试函数，覆盖完整主链路 |
| E2E | 本地启动 + 浏览器操作 | 手动验证 |

## Rollback Plan

- 运行产生的问题不修改主工作区；删除 `data/workspaces/run-*` 即可丢弃 worktree。
- 已提交但未 push 的隔离分支可用 `git branch -D flowcodex/run-<id>` 删除。
- 数据库和产物在 `data/`；恢复时用备份覆盖。
- 代码升级失败可回退镜像或上一版本目录，保留 `data/ repos/ vault/`。

## Out of Scope

- 多仓库编排
- GitHub/GitLab PR 自动创建
- 远程 push
- 组织级权限
- 费用预算控制
- 前端可视化 diff 编辑器
- 自动合并到主干

## References

- [原文：别只盯着 AI Coding，真正的变化发生在整个研发流程](https://zhuanlan.zhihu.com/p/2083953932536234138)
- [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
