from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from .. import db, prompts
from ..config import Settings
from ..llm import LLMClient, LLMResult
from ..schemas import (
    ClarifyResult,
    CodeResult,
    DistillResult,
    FeasibilityResult,
    PlanResult,
    ReviewIssue,
    ReviewResult,
)
from ..serializers import project_out, requirement_out, run_out
from ..utils import clip, dumps, loads, now_iso
from . import gitops, knowledge, obsidian


logger = logging.getLogger(__name__)

STAGES = ["intake", "clarify", "feasibility", "plan", "code", "review", "verify", "deliver", "distill"]
STAGE_LABELS = {
    "intake": "接收",
    "clarify": "需求访谈",
    "feasibility": "可行性",
    "plan": "技术方案",
    "code": "编码",
    "review": "代码审查",
    "verify": "验证",
    "deliver": "交付",
    "distill": "知识蒸馏",
}
TERMINAL = {"completed", "blocked", "failed", "cancelled"}

FEASIBILITY_WEIGHTS = {
    "clarity": 0.20,
    "repo_match": 0.20,
    "scope_control": 0.15,
    "technical_simplicity": 0.15,
    "dependency_readiness": 0.10,
    "risk_control": 0.10,
    "delivery_shape": 0.10,
}


def weighted_feasibility_total(feasibility: FeasibilityResult) -> int:
    return round(
        sum(getattr(feasibility, key) * weight for key, weight in FEASIBILITY_WEIGHTS.items())
    )


def normalize_review(review: ReviewResult) -> ReviewResult:
    security_failed = [key for key, value in review.security_checks.items() if value is False]
    if security_failed:
        review.issues.append(
            ReviewIssue(
                severity="critical",
                title="安全硬检查未通过",
                detail="以下安全项未通过：" + "、".join(security_failed),
                security_rule="、".join(security_failed),
            )
        )
    has_critical = any(issue.severity == "critical" for issue in review.issues)
    review.verdict = "changes_requested" if has_critical else "pass"
    return review


def clarification_ready(clarify: ClarifyResult) -> bool:
    return not any(question.required for question in clarify.questions)


def validate_plan(plan: PlanResult) -> str | None:
    ids = [step.id.strip() for step in plan.steps]
    if any(not step_id for step_id in ids):
        return "技术方案存在空步骤 ID"
    if len(ids) != len(set(ids)):
        return "技术方案步骤 ID 重复"
    known = set(ids)
    for step in plan.steps:
        if step.id in step.depends_on:
            return f"步骤 {step.id} 不能依赖自身"
        missing = [dependency for dependency in step.depends_on if dependency not in known]
        if missing:
            return f"步骤 {step.id} 依赖了不存在的步骤：{', '.join(missing)}"
        if len(set(step.files)) > 5:
            return f"步骤 {step.id} 预计触碰超过 5 个文件，请拆分"
    graph = {step.id: [dependency for dependency in step.depends_on if dependency in known] for step in plan.steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in graph[node]:
            if visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    if any(visit(node) for node in graph):
        return "技术方案依赖存在环"
    return None


class WorkflowEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LLMClient(settings)

    async def process(self, run_id: str) -> None:
        now = now_iso()
        with db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runs
                SET status = 'running',
                    started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, run_id),
            )
            claimed = cur.rowcount
        if not claimed:
            return
        self._emit(run_id, "run", "intake", "后台执行器已接管运行")
        try:
            for _ in range(30):
                run = self._run(run_id)
                if not run or run["status"] != "running":
                    return
                stage = run["stage"]
                if stage == "intake":
                    self._update_run(run_id, stage="clarify")
                    self._emit(run_id, "stage", "clarify", "进入需求访谈")
                    continue
                if stage == "clarify":
                    if not await self._clarify(run_id):
                        return
                    continue
                if stage == "feasibility":
                    if not await self._feasibility(run_id):
                        return
                    continue
                if stage == "plan":
                    if not await self._plan(run_id):
                        return
                    continue
                if stage == "code":
                    if not await self._code(run_id):
                        return
                    continue
                if stage == "review":
                    if not await self._review(run_id):
                        return
                    continue
                if stage == "verify":
                    if not await self._verify(run_id):
                        return
                    continue
                if stage == "deliver":
                    if not await self._deliver(run_id):
                        return
                    continue
                if stage == "distill":
                    await self._distill(run_id)
                    return
                self._fail(run_id, stage, f"未知阶段: {stage}")
                return
            self._fail(run_id, "engine", "阶段推进超过安全上限，已停止以避免死循环")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Run %s failed", run_id)
            stage = (self._run(run_id) or {}).get("stage", "engine")
            self._fail(run_id, stage, str(exc))

    def recover_interrupted(self) -> None:
        rows = db.query_all("SELECT id, stage FROM runs WHERE status = 'running'")
        now = now_iso()
        for row in rows:
            db.execute(
                "UPDATE runs SET status = 'queued', error = ?, updated_at = ? WHERE id = ?",
                ("后台执行器重启，运行已重新排队。", now, row["id"]),
            )
            self._emit(row["id"], "warning", row["stage"], "检测到中断，已重新排队")

    # ---------- stage handlers ----------

    async def _clarify(self, run_id: str) -> bool:
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        query = prompts.context_query(spec, requirement["raw_input"], requirement["title"])
        injected = knowledge.inject(project["id"], query)
        previous = run.get("clarify", {})
        answers = run.get("answers", {})
        system, user = prompts.clarify_prompt(
            requirement["title"],
            requirement["raw_input"],
            project_out(project),
            self._knowledge_brief(injected),
            previous,
            answers,
        )
        result = await self.llm.complete_json(system, user, "clarify")
        self._track(run_id, result)
        if self._is_cancelled(run_id):
            return False
        clarify = ClarifyResult.model_validate(result.data)
        self._update_run(
            run_id,
            clarify_json=dumps(clarify.model_dump()),
            clarify_round=int(run["clarify_round"]) + 1,
        )
        ready = clarification_ready(clarify)
        if ready:
            db.execute(
                "UPDATE requirements SET spec_json = ?, status = 'in_progress', updated_at = ? WHERE id = ?",
                (dumps(clarify.spec.model_dump()), now_iso(), requirement["id"]),
            )
            self._update_run(run_id, stage="feasibility")
            self._emit(
                run_id,
                "stage",
                "feasibility",
                "需求访谈完成，进入可行性评估",
                {"spec": clarify.spec.model_dump()},
            )
            return True
        if int(run["clarify_round"]) >= 3:
            self._block(run_id, "clarify", "多轮访谈后仍存在未澄清的关键问题，请拆分需求或补充更明确的背景。")
            return False
        self._update_run(run_id, status="awaiting_input", stage="clarify")
        self._emit(
            run_id,
            "gate",
            "clarify",
            "需要人工补充需求信息",
            {"questions": [q.model_dump() for q in clarify.questions]},
        )
        return False

    async def _feasibility(self, run_id: str) -> bool:
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        query = prompts.context_query(spec, requirement["raw_input"], requirement["title"])
        injected = knowledge.inject(project["id"], query)
        try:
            repo_context = gitops.build_repo_context(
                self.settings,
                project["repo_path"],
                loads(project["allowed_paths_json"], ["."]),
                query,
            )
        except Exception as exc:
            self._block(run_id, "feasibility", f"无法读取仓库上下文：{exc}")
            return False
        self._update_run(
            run_id,
            context_json=dumps(
                {
                    "repo_path": repo_context["repo_path"],
                    "file_count": repo_context["file_count"],
                    "files": repo_context["files"][:80],
                    "git_head": repo_context["git_head"],
                    "context": repo_context["context"],
                    "context_chars": len(repo_context["context"]),
                    "knowledge_ids": [item["id"] for item in injected],
                }
            ),
        )
        system, user = prompts.feasibility_prompt(
            spec,
            requirement["title"],
            requirement["raw_input"],
            project_out(project),
            repo_context["context"],
        )
        result = await self.llm.complete_json(system, user, "feasibility")
        self._track(run_id, result)
        if self._is_cancelled(run_id):
            return False
        feasibility = FeasibilityResult.model_validate(result.data)
        feasibility.total = weighted_feasibility_total(feasibility)
        self._update_run(run_id, feasibility_json=dumps(feasibility.model_dump()))
        if feasibility.total < self.settings.feasibility_threshold or feasibility.blockers:
            self._update_run(run_id, status="blocked", stage="feasibility", error="可行性门禁未通过")
            db.execute("UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?", (now_iso(), requirement["id"]))
            self._emit(
                run_id,
                "gate",
                "feasibility",
                f"可行性门禁未通过：{feasibility.total}/100",
                {"feasibility": feasibility.model_dump()},
            )
            return False
        self._update_run(run_id, stage="plan")
        self._emit(run_id, "stage", "plan", f"可行性通过：{feasibility.total}/100，进入技术方案")
        return True

    async def _plan(self, run_id: str) -> bool:
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        feasibility = run.get("feasibility", {})
        context = self._repo_context(run)
        query = prompts.context_query(spec, requirement["raw_input"], requirement["title"])
        injected = knowledge.inject(project["id"], query)
        system, user = prompts.plan_prompt(
            spec,
            feasibility,
            project_out(project),
            context,
            self._knowledge_brief(injected),
            run.get("rework_note", ""),
        )
        result = await self.llm.complete_json(system, user, "plan")
        self._track(run_id, result)
        if self._is_cancelled(run_id):
            return False
        plan = PlanResult.model_validate(result.data)
        plan_error = validate_plan(plan)
        if plan_error:
            self._update_run(
                run_id,
                plan_json=dumps(plan.model_dump()),
                status="blocked",
                stage="plan",
                error=plan_error,
            )
            db.execute("UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?", (now_iso(), requirement["id"]))
            self._emit(run_id, "blocked", "plan", plan_error)
            return False
        if not plan.steps:
            self._update_run(
                run_id,
                plan_json=dumps(plan.model_dump()),
                status="blocked",
                stage="plan",
                error="技术方案没有可执行步骤",
            )
            db.execute("UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?", (now_iso(), requirement["id"]))
            self._emit(run_id, "blocked", "plan", "技术方案没有可执行步骤")
            return False
        self._update_run(
            run_id,
            plan_json=dumps(plan.model_dump()),
            status="awaiting_plan_approval",
            stage="plan",
        )
        self._emit(
            run_id,
            "gate",
            "plan",
            "技术方案已生成，等待人工确认",
            {"plan": plan.model_dump()},
        )
        return False

    async def _code(self, run_id: str) -> bool:
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        plan = run.get("plan", {})
        context = self._repo_context(run)
        query = prompts.context_query(spec, requirement["raw_input"], requirement["title"])
        injected = knowledge.inject(project["id"], query)
        system, user = prompts.code_prompt(
            spec,
            plan,
            project_out(project),
            context,
            self._knowledge_brief(injected),
            run.get("rework_note", ""),
        )
        result = await self.llm.complete_json(system, user, "code")
        self._track(run_id, result)
        if self._is_cancelled(run_id):
            return False
        code = CodeResult.model_validate(result.data)
        if not code.patch.strip():
            self._block(run_id, "code", code.summary or "模型未返回可应用的补丁")
            return False
        if len(code.patch) > self.settings.max_patch_chars:
            self._block(run_id, "code", "补丁超过大小限制")
            return False

        try:
            worktree, branch = gitops.create_worktree(self.settings, project["repo_path"], run_id)
            applied = gitops.apply_patch(worktree, code.patch)
        except Exception as exc:
            self._block(run_id, "code", f"无法创建隔离工作区：{exc}")
            return False
        code_payload = code.model_dump()
        code_payload["apply_ok"] = applied.ok
        code_payload["apply"] = applied.as_dict()
        if not applied.ok:
            code_payload["diff"] = ""
            self._update_run(
                run_id,
                code_json=dumps(code_payload),
                patch_text=code.patch,
                branch_name=branch,
                worktree_path=str(worktree),
            )
            self._block(run_id, "code", applied.stderr or applied.stdout or "补丁无法应用")
            return False
        changed_files = gitops.staged_files(worktree)
        if not changed_files:
            self._block(run_id, "code", "补丁未产生任何文件变更")
            return False
        planned_files = [
            path
            for step in plan.get("steps", [])
            for path in step.get("files", [])
        ]
        scope_error = gitops.validate_changed_files(
            changed_files,
            loads(project.get("allowed_paths_json"), ["."]),
            planned_files,
        )
        code_payload["changed_files"] = changed_files
        if scope_error:
            code_payload["scope_error"] = scope_error
            code_payload["diff"] = clip(gitops.staged_diff(worktree), 120000)
            self._update_run(
                run_id,
                code_json=dumps(code_payload),
                patch_text=code.patch,
                branch_name=branch,
                worktree_path=str(worktree),
            )
            self._block(run_id, "code", scope_error)
            return False
        code_payload["diff"] = clip(gitops.staged_diff(worktree), 120000)
        self._update_run(
            run_id,
            code_json=dumps(code_payload),
            patch_text=code.patch,
            branch_name=branch,
            worktree_path=str(worktree),
            stage="review",
            error="",
        )
        self._emit(run_id, "stage", "review", "补丁已应用到隔离工作区，进入代码审查")
        return True

    async def _review(self, run_id: str) -> bool:
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        plan = run.get("plan", {})
        code = run.get("code", {})
        system, user = prompts.review_prompt(
            spec,
            plan,
            code.get("diff") or run["patch_text"],
            project_out(project),
            run.get("verify", {}),
        )
        result = await self.llm.complete_json(system, user, "review")
        self._track(run_id, result)
        if self._is_cancelled(run_id):
            return False
        review = normalize_review(ReviewResult.model_validate(result.data))
        self._update_run(run_id, review_json=dumps(review.model_dump()))
        if review.verdict != "pass":
            self._update_run(run_id, status="blocked", stage="review", error="代码审查存在关键问题")
            db.execute("UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?", (now_iso(), requirement["id"]))
            self._emit(run_id, "gate", "review", "代码审查未通过，需要返工", {"review": review.model_dump()})
            return False
        self._update_run(run_id, stage="verify", error="")
        self._emit(run_id, "stage", "verify", "代码审查通过，进入验证")
        return True

    async def _verify(self, run_id: str) -> bool:
        run = self._run(run_id)
        project = self._project(run["project_id"])
        worktree = self._worktree(run)
        command = (project.get("verify_command") or "").strip()
        if not command:
            verify = {"ok": True, "skipped": True, "command": "", "stdout": "", "stderr": "未配置 verify_command，已跳过"}
        else:
            result = gitops.run_command(command, worktree, self.settings.verify_timeout)
            verify = result.as_dict()
        if self._is_cancelled(run_id):
            return False
        self._update_run(run_id, verify_json=dumps(verify))
        if not verify.get("ok"):
            self._block(run_id, "verify", f"验证命令失败：{verify.get('command','')}")
            return False
        self._update_run(run_id, stage="deliver")
        self._emit(run_id, "stage", "deliver", "验证通过，进入交付")
        return True

    async def _deliver(self, run_id: str) -> bool:
        if self._is_cancelled(run_id):
            return False
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        worktree = self._worktree(run)
        deliver: dict[str, Any] = {
            "branch": run["branch_name"],
            "worktree": str(worktree),
            "auto_apply": bool(project.get("auto_apply")),
        }
        patch_path = gitops.write_artifact(self.settings, run_id, "change.patch", run["patch_text"])
        deliver["patch_path"] = str(patch_path)
        if project.get("auto_apply"):
            commit = gitops.commit_all(worktree, f"FlowCodex: {requirement['title'][:100]}")
            deliver["commit_result"] = commit.as_dict()
            if not commit.ok:
                self._block(run_id, "deliver", commit.stderr or commit.stdout or "提交失败")
                return False
            head = gitops.run_process(["git", "-C", str(worktree), "rev-parse", "HEAD"], worktree, 20)
            deliver["commit"] = head.stdout.strip() if head.ok else ""
        if project.get("auto_publish") and project.get("publish_command"):
            publish = gitops.run_command(project["publish_command"], worktree, self.settings.verify_timeout)
            deliver["publish"] = publish.as_dict()
            if not publish.ok:
                self._block(run_id, "deliver", "发布命令失败")
                return False
        self._update_run(run_id, deliver_json=dumps(deliver), stage="distill")
        self._emit(run_id, "stage", "distill", "交付产物已生成，进入知识蒸馏", deliver)
        return True

    async def _distill(self, run_id: str) -> None:
        if self._is_cancelled(run_id):
            return
        run = self._run(run_id)
        requirement = self._requirement(run["requirement_id"])
        project = self._project(run["project_id"])
        spec = loads(requirement["spec_json"], {})
        plan = run.get("plan", {})
        review = run.get("review", {})
        verify = run.get("verify", {})
        stored: list[dict[str, Any]] = []
        try:
            system, user = prompts.distill_prompt(
                spec,
                plan,
                review,
                verify,
                project_out(project),
            )
            result = await self.llm.complete_json(system, user, "distill")
            self._track(run_id, result)
            if self._is_cancelled(run_id):
                return
            distill = DistillResult.model_validate(result.data)
            stored = knowledge.store_distilled(
                project["id"],
                run_id,
                distill.candidates,
                self.settings.knowledge_ttl_days,
            )
            self._update_run(run_id, distill_json=dumps(distill.model_dump()))
        except Exception as exc:
            logger.warning("Distillation failed for %s: %s", run_id, exc)
            self._update_run(run_id, distill_json=dumps({"summary": f"知识蒸馏失败：{exc}", "candidates": []}))
            self._emit(run_id, "warning", "distill", f"知识蒸馏失败，但交付保留：{exc}")

        if self._is_cancelled(run_id):
            return
        now = now_iso()
        self._update_run(run_id, status="completed", stage="distill", finished_at=now, error="")
        db.execute("UPDATE requirements SET status = 'completed', updated_at = ? WHERE id = ?", (now, requirement["id"]))
        self._emit(run_id, "complete", "distill", "运行完成", {"knowledge_count": len(stored)})

        try:
            full = self._full_run(run_id)
            if full:
                path = obsidian.export_run(
                    self.settings,
                    full,
                    requirement_out(requirement),
                    project_out(project),
                    stored,
                )
                if path:
                    deliver = self._run(run_id).get("deliver", {})
                    deliver["obsidian_note"] = str(path)
                    self._update_run(run_id, deliver_json=dumps(deliver))
                    self._emit(run_id, "artifact", "distill", f"已导出 Obsidian 笔记：{path.name}")
        except Exception as exc:
            logger.warning("Obsidian export failed for %s: %s", run_id, exc)
            self._emit(run_id, "warning", "distill", f"Obsidian 导出失败：{exc}")

    # ---------- helpers ----------

    def _run(self, run_id: str) -> dict[str, Any] | None:
        row = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return run_out(row) if row else None

    def _requirement(self, requirement_id: str) -> dict[str, Any]:
        row = db.query_one("SELECT * FROM requirements WHERE id = ?", (requirement_id,))
        if not row:
            raise RuntimeError("Requirement not found")
        return dict(row)

    def _project(self, project_id: str) -> dict[str, Any]:
        row = db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise RuntimeError("Project not found")
        return dict(row)

    def _is_cancelled(self, run_id: str) -> bool:
        run = self._run(run_id)
        return bool(run and run.get("status") == "cancelled")

    def _worktree(self, run: dict[str, Any]) -> Path:
        raw = run.get("worktree_path", "")
        if not raw:
            raise RuntimeError("运行没有隔离工作区")
        path = Path(raw).resolve()
        if not path.exists():
            raise RuntimeError(f"隔离工作区已不存在：{path}")
        return path

    def _repo_context(self, run: dict[str, Any]) -> str:
        context = run.get("context", {})
        if context.get("context"):
            return context["context"]
        project = self._project(run["project_id"])
        requirement = self._requirement(run["requirement_id"])
        spec = loads(requirement["spec_json"], {})
        query = prompts.context_query(spec, requirement["raw_input"], requirement["title"])
        built = gitops.build_repo_context(
            self.settings,
            project["repo_path"],
            loads(project["allowed_paths_json"], ["."]),
            query,
        )
        context["context"] = built["context"]
        self._update_run(run["id"], context_json=dumps(context))
        return built["context"]

    def _knowledge_brief(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": item["id"],
                "kind": item["kind"],
                "title": item["title"],
                "content": clip(item["content"], 3000),
                "confidence": item["confidence"],
                "tags": item["tags"],
            }
            for item in items
        ]

    def _update_run(self, run_id: str, **fields: Any) -> None:
        db.update("runs", run_id, **fields)

    def _track(self, run_id: str, result: LLMResult) -> None:
        db.execute(
            """
            UPDATE runs
            SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?,
                llm_provider = ?, llm_model = ?, updated_at = ?
            WHERE id = ?
            """,
            (result.tokens_in, result.tokens_out, result.provider, result.model, now_iso(), run_id),
        )

    def _emit(self, run_id: str, event_type: str, stage: str, message: str, payload: Any | None = None) -> None:
        db.execute(
            "INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, event_type, stage, message, dumps(payload or {}), now_iso()),
        )

    def _block(self, run_id: str, stage: str, message: str) -> None:
        if self._is_cancelled(run_id):
            return
        self._update_run(run_id, status="blocked", stage=stage, error=message, finished_at=now_iso())
        run = self._run(run_id)
        if run:
            db.execute(
                "UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?",
                (now_iso(), run["requirement_id"]),
            )
        self._emit(run_id, "blocked", stage, message)

    def _fail(self, run_id: str, stage: str, message: str) -> None:
        if self._is_cancelled(run_id):
            return
        self._update_run(run_id, status="failed", stage=stage, error=message, finished_at=now_iso())
        self._emit(run_id, "error", stage, message)

    def _full_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._run(run_id)
        if not run:
            return None
        events = [dict(row) for row in db.query_all("SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))]
        run["events"] = events
        return run


class Worker:
    def __init__(self, settings: Settings, engine: WorkflowEngine):
        self.settings = settings
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not self.settings.worker_enabled or self._task:
            return
        self.engine.recover_interrupted()
        knowledge.expire_stale()
        self._task = asyncio.create_task(self._loop(), name="flowcodex-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        last_expiry = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_expiry > 3600:
                knowledge.expire_stale()
                last_expiry = time.monotonic()
            try:
                rows = db.query_all(
                    "SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
                )
                if rows:
                    await self.engine.process(rows[0]["id"])
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker_poll_interval)
            except asyncio.TimeoutError:
                pass
