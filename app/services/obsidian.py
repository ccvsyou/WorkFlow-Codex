from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..utils import slugify


def _yaml(value: Any) -> str:
    text = "" if value is None else str(value)
    return json.dumps(text.replace("\n", " "), ensure_ascii=False)


def _bullets(value: Any) -> str:
    if not value:
        return "- 无"
    if isinstance(value, str):
        return f"- {value}"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return f"- {value}"


def export_run(
    settings: Settings,
    run: dict[str, Any],
    requirement: dict[str, Any],
    project: dict[str, Any],
    knowledge_items: list[dict[str, Any]] | None = None,
) -> Path | None:
    if not settings.obsidian_vault_path:
        return None
    vault = settings.obsidian_vault_path.resolve()
    folder = (vault / settings.obsidian_folder).resolve()
    folder.relative_to(vault)
    folder.mkdir(parents=True, exist_ok=True)

    title = requirement.get("title") or "未命名需求"
    date = (run.get("created_at") or "")[:10] or "run"
    filename = f"{date}-{slugify(title)}-{run['id'][:8]}.md"
    path = (folder / filename).resolve()
    path.relative_to(folder)

    clarity = run.get("clarify") or {}
    feasibility = run.get("feasibility") or {}
    plan = run.get("plan") or {}
    review = run.get("review") or {}
    verify = run.get("verify") or {}
    deliver = run.get("deliver") or {}
    spec = (requirement.get("spec") or clarity.get("spec") or {})
    steps = plan.get("steps") or []
    issues = review.get("issues") or []
    knowledge = knowledge_items or []

    step_md = []
    for index, step in enumerate(steps, 1):
        step_md.append(
            f"### {index}. {step.get('title','')}\n"
            f"- id: `{step.get('id','')}`\n"
            f"- 依赖: {', '.join(step.get('depends_on') or []) or '无'}\n"
            f"- 文件: {', '.join(step.get('files') or []) or '无'}\n"
            f"- 验收: {'; '.join(step.get('acceptance') or []) or '无'}\n"
            f"- 验证: {step.get('verification','') or '无'}"
        )

    issue_md = []
    for issue in issues:
        issue_md.append(
            f"- **[{issue.get('severity','suggestion')}] {issue.get('title','')}** "
            f"`{issue.get('file','')}`\n  {issue.get('detail','')}"
        )

    content = f"""---
type: flowcodex-run
run_id: {run['id']}
project: {_yaml(project.get('name', ''))}
status: {_yaml(run.get('status', ''))}
stage: {_yaml(run.get('stage', ''))}
created: {_yaml(run.get('created_at', ''))}
tags:
  - flowcodex
  - agent-workflow
---

# {title}

> {requirement.get('raw_input','')}

## 运行概览

| 字段 | 值 |
|---|---|
| 项目 | {project.get('name','')} |
| 状态 | {run.get('status','')} |
| 阶段 | {run.get('stage','')} |
| 分支 | {run.get('branch_name','') or '未创建'} |
| 可行性 | {feasibility.get('total','')}/100 |
| 审查结论 | {review.get('verdict','') or '未审查'} |
| 验证 | {'通过' if verify.get('ok') else '未通过/未执行'} |

## 结构化 Spec

### 目标

{spec.get('goal','') or '未生成'}

### 目标用户

{spec.get('target_users','') or '未生成'}

### 范围

{_bullets(spec.get('scope'))}

### 不做

{_bullets(spec.get('out_of_scope'))}

### 业务规则

{_bullets(spec.get('business_rules'))}

### 验收标准

{_bullets(spec.get('acceptance_criteria'))}

## 需求访谈

**理解确认：** {clarity.get('understanding','')}

**假设：**

{_bullets(clarity.get('assumptions'))}

## 可行性

{_bullets([f"{k}: {v}" for k, v in (feasibility.get('reasons') or {}).items()])}

**总分：** {feasibility.get('total','')}/100

## 技术方案

{plan.get('summary','') or '未生成'}

{chr(10).join(step_md) or '- 无'}

### 方案自审

{_bullets([f"[{item.get('severity','suggestion')}] {item.get('title','')}: {item.get('detail','')}" for item in (plan.get('self_challenge') or [])])}

## 代码审查

{review.get('summary','') or '未审查'}

{chr(10).join(issue_md) or '- 无问题'}

## 验证

```text
{verify.get('stdout','') or verify.get('stderr','') or '未执行'}
```

## 交付

- 分支：`{run.get('branch_name','') or '未创建'}`
- Worktree：`{run.get('worktree_path','') or '未创建'}`
- 补丁：{deliver.get('patch_path','') or '未生成'}
- 提交：{deliver.get('commit','') or '未提交'}

## 知识沉淀

{_bullets([f"[{item.get('kind','')}] {item.get('title','')}（置信度 {item.get('confidence','')}）" for item in knowledge])}

## 事件时间线

{chr(10).join(f"- {event.get('created_at','')} · {event.get('stage','')} · {event.get('message','')}" for event in (run.get('events') or []))}
"""
    path.write_text(content, encoding="utf-8")
    return path
