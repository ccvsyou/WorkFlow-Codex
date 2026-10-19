from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import Settings, get_settings
from .schemas import (
    AnswerPayload,
    FeasibilityOverride,
    KnowledgeCreate,
    KnowledgeFeedback,
    PlanApproval,
    ProjectCreate,
    ProjectUpdate,
    RequirementCreate,
    ReworkRequest,
)
from .serializers import event_out, project_out, requirement_out, run_out
from .services import gitops, knowledge, obsidian
from .services.engine import STAGE_LABELS, STAGES, TERMINAL, Worker, WorkflowEngine
from .utils import dumps, new_id, now_iso

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def require_auth(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.api_token:
        return
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        token = request.headers.get("X-API-Key", "").strip()
    if not token or not hmac.compare_digest(token, settings.api_token):
        raise HTTPException(status_code=401, detail="缺少或无效的访问令牌")


def _project_or_404(project_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail="项目不存在")
    return dict(row)


def _run_or_404(run_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run_out(row)


def _requirement_or_404(requirement_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM requirements WHERE id = ?", (requirement_id,))
    if not row:
        raise HTTPException(status_code=404, detail="需求不存在")
    return requirement_out(row)


def _resolve_repo_input(settings: Settings, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = settings.repos_root / path
    return gitops.safe_repo_path(settings, str(path))


def _full_run_payload(run_id: str) -> dict[str, Any]:
    run = _run_or_404(run_id)
    requirement = _requirement_or_404(run["requirement_id"])
    project = project_out(_project_or_404(run["project_id"]))
    events = [event_out(row) for row in db.query_all("SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))]
    related_knowledge = knowledge.list_knowledge(project_id=run["project_id"], include_expired=True, limit=50)
    related_knowledge = [item for item in related_knowledge if item.get("source_run_id") == run_id]
    return {
        "run": run,
        "requirement": requirement,
        "project": project,
        "events": events,
        "knowledge": related_knowledge,
        "stages": [{"id": stage, "label": STAGE_LABELS.get(stage, stage)} for stage in STAGES],
        "is_terminal": run["status"] in TERMINAL,
    }


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.ensure_dirs()
        db.configure(app_settings.db_path)
        db.init_db()
        engine = WorkflowEngine(app_settings)
        worker = Worker(app_settings, engine)
        app.state.settings = app_settings
        app.state.engine = engine
        app.state.worker = worker
        if start_worker:
            worker.start()
        logger.info("FlowCodex started: db=%s repos=%s mock=%s", app_settings.db_path, app_settings.repos_root, app_settings.is_mock)
        try:
            yield
        finally:
            await worker.stop()

    app = FastAPI(title=app_settings.app_name, version="0.1.0", lifespan=lifespan)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    auth = [Depends(require_auth)]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=500, detail="前端资源未构建")
        return FileResponse(index_path)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        return {"ok": True, "app": app_settings.app_name, "version": "0.1.0"}

    @app.get("/api/config", include_in_schema=False)
    def config() -> dict[str, Any]:
        return {
            "app_name": app_settings.app_name,
            "env": app_settings.env,
            "mock": app_settings.is_mock,
            "llm_provider": "mock" if app_settings.is_mock else app_settings.llm_provider,
            "llm_model": "mock" if app_settings.is_mock else app_settings.llm_model,
            "feasibility_threshold": app_settings.feasibility_threshold,
            "worker_enabled": app_settings.worker_enabled,
            "obsidian_enabled": bool(app_settings.obsidian_vault_path),
            "auth_required": bool(app_settings.api_token),
            "stages": [{"id": stage, "label": STAGE_LABELS.get(stage, stage)} for stage in STAGES],
            "version": "0.1.0",
        }

    # ---------- projects ----------

    @app.get("/api/projects", dependencies=auth)
    def list_projects() -> list[dict[str, Any]]:
        rows = db.query_all("SELECT * FROM projects ORDER BY created_at DESC")
        return [project_out(row) for row in rows]

    @app.post("/api/projects", dependencies=auth, status_code=201)
    def create_project(payload: ProjectCreate) -> dict[str, Any]:
        try:
            repo_path = _resolve_repo_input(app_settings, payload.repo_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        pid = new_id("prj_")
        now = now_iso()
        db.execute(
            """
            INSERT INTO projects
            (id, name, repo_path, default_branch, verify_command, publish_command,
             allowed_paths_json, auto_apply, auto_publish, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                payload.name.strip(),
                str(repo_path),
                payload.default_branch.strip() or "main",
                payload.verify_command.strip(),
                payload.publish_command.strip(),
                dumps(payload.allowed_paths or ["."]),
                int(payload.auto_apply if payload.auto_apply is not None else app_settings.auto_apply_patch),
                int(payload.auto_publish),
                now,
                now,
            ),
        )
        return project_out(_project_or_404(pid))

    @app.get("/api/projects/{project_id}", dependencies=auth)
    def get_project(project_id: str) -> dict[str, Any]:
        return project_out(_project_or_404(project_id))

    @app.patch("/api/projects/{project_id}", dependencies=auth)
    def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
        project = _project_or_404(project_id)
        fields: dict[str, Any] = {}
        data = payload.model_dump(exclude_unset=True)
        if "repo_path" in data and data["repo_path"]:
            try:
                fields["repo_path"] = str(_resolve_repo_input(app_settings, data["repo_path"]))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        for key in ("name", "default_branch", "verify_command", "publish_command"):
            if key in data and data[key] is not None:
                fields[key] = data[key].strip()
        if "allowed_paths" in data and data["allowed_paths"] is not None:
            fields["allowed_paths_json"] = dumps(data["allowed_paths"])
        if "auto_apply" in data and data["auto_apply"] is not None:
            fields["auto_apply"] = int(data["auto_apply"])
        if "auto_publish" in data and data["auto_publish"] is not None:
            fields["auto_publish"] = int(data["auto_publish"])
        if fields:
            db.update("projects", project_id, **fields)
        return project_out(_project_or_404(project["id"]))

    @app.get("/api/projects/{project_id}/requirements", dependencies=auth)
    def list_project_requirements(project_id: str) -> list[dict[str, Any]]:
        _project_or_404(project_id)
        rows = db.query_all("SELECT * FROM requirements WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        return [requirement_out(row) for row in rows]

    @app.get("/api/projects/{project_id}/knowledge", dependencies=auth)
    def list_project_knowledge(project_id: str, include_expired: bool = False) -> list[dict[str, Any]]:
        _project_or_404(project_id)
        return knowledge.list_knowledge(project_id=project_id, include_expired=include_expired)

    # ---------- requirements ----------

    @app.get("/api/requirements", dependencies=auth)
    def list_requirements(project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = db.query_all("SELECT * FROM requirements WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        else:
            rows = db.query_all("SELECT * FROM requirements ORDER BY created_at DESC LIMIT 200")
        return [requirement_out(row) for row in rows]

    @app.post("/api/requirements", dependencies=auth, status_code=201)
    def create_requirement(payload: RequirementCreate) -> dict[str, Any]:
        project = _project_or_404(payload.project_id)
        rid = new_id("req_")
        now = now_iso()
        db.execute(
            "INSERT INTO requirements (id, project_id, title, raw_input, spec_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, '{}', 'open', ?, ?)",
            (rid, project["id"], payload.title.strip(), payload.raw_input.strip(), now, now),
        )
        result: dict[str, Any] = {"requirement": _requirement_or_404(rid), "run": None}
        if payload.start_run:
            run_id = new_id("run_")
            db.execute(
                """
                INSERT INTO runs
                (id, requirement_id, project_id, status, stage, llm_provider, llm_model, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', 'intake', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rid,
                    project["id"],
                    "mock" if app_settings.is_mock else app_settings.llm_provider,
                    "mock" if app_settings.is_mock else app_settings.llm_model,
                    now,
                    now,
                ),
            )
            db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'created', 'intake', '需求已提交，等待 Worker', '{}', ?)", (run_id, now))
            result["run"] = _full_run_payload(run_id)
        return result

    @app.get("/api/requirements/{requirement_id}", dependencies=auth)
    def get_requirement(requirement_id: str) -> dict[str, Any]:
        return _requirement_or_404(requirement_id)

    # ---------- runs ----------

    @app.get("/api/runs", dependencies=auth)
    def list_runs(
        project_id: str | None = None,
        status: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = db.query_all(
            f"SELECT r.*, req.title AS requirement_title FROM runs r JOIN requirements req ON req.id = r.requirement_id{where.replace('project_id', 'r.project_id').replace('status', 'r.status')} ORDER BY r.created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [run_out(row) for row in rows]

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def get_run(run_id: str) -> dict[str, Any]:
        return _full_run_payload(run_id)

    @app.get("/api/runs/{run_id}/events", dependencies=auth)
    def get_run_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
        _run_or_404(run_id)
        rows = db.query_all(
            "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id ASC",
            (run_id, after),
        )
        return [event_out(row) for row in rows]

    @app.post("/api/runs/{run_id}/answers", dependencies=auth)
    def answer_clarify(run_id: str, payload: AnswerPayload) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] != "awaiting_input" or run["stage"] != "clarify":
            raise HTTPException(status_code=409, detail="当前运行不在需求访谈等待状态")
        clean = {str(key): str(value).strip() for key, value in payload.answers.items() if str(value).strip()}
        if not clean:
            raise HTTPException(status_code=400, detail="至少回答一个必要问题")
        db.execute(
            "UPDATE runs SET answers_json = ?, status = 'queued', error = '', updated_at = ? WHERE id = ?",
            (dumps(clean), now_iso(), run_id),
        )
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'human', 'clarify', '已提交需求补充回答', ?, ?)", (run_id, dumps(clean), now_iso()))
        return _full_run_payload(run_id)

    @app.post("/api/runs/{run_id}/approve-plan", dependencies=auth)
    def approve_plan(run_id: str, payload: PlanApproval) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] != "awaiting_plan_approval":
            raise HTTPException(status_code=409, detail="当前运行不在方案确认状态")
        now = now_iso()
        if payload.approved:
            db.execute(
                "UPDATE runs SET plan_approved = 1, status = 'queued', stage = 'code', error = '', updated_at = ? WHERE id = ?",
                (now, run_id),
            )
            db.execute("UPDATE requirements SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, run["requirement_id"]))
            message = "方案已批准，进入编码"
            event_type = "human"
        else:
            db.execute(
                "UPDATE runs SET plan_approved = 0, status = 'blocked', stage = 'plan', error = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                (payload.comment or "方案被人工拒绝", now, now, run_id),
            )
            db.execute("UPDATE requirements SET status = 'blocked', updated_at = ? WHERE id = ?", (now, run["requirement_id"]))
            message = payload.comment or "方案被人工拒绝"
            event_type = "blocked"
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, ?, 'plan', ?, ?, ?)", (run_id, event_type, message, dumps(payload.model_dump()), now))
        return _full_run_payload(run_id)

    @app.post("/api/runs/{run_id}/override-feasibility", dependencies=auth)
    def override_feasibility(run_id: str, payload: FeasibilityOverride) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] != "blocked" or run["stage"] != "feasibility":
            raise HTTPException(status_code=409, detail="当前运行不在可行性拦截状态")
        now = now_iso()
        db.execute(
            "UPDATE runs SET status = 'queued', stage = 'plan', error = '', finished_at = '', updated_at = ? WHERE id = ?",
            (now, run_id),
        )
        db.execute("UPDATE requirements SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, run["requirement_id"]))
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'human', 'feasibility', ?, ?, ?)", (run_id, "人工强制放行可行性门禁", dumps({"reason": payload.reason}), now))
        return _full_run_payload(run_id)

    @app.post("/api/runs/{run_id}/rework", dependencies=auth)
    def rework(run_id: str, payload: ReworkRequest) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["stage"] not in {"plan", "review", "verify", "code"}:
            raise HTTPException(status_code=409, detail="当前阶段不支持返工")
        now = now_iso()
        next_stage = "plan" if run["stage"] == "plan" else "code"
        next_message = "人工要求重新生成方案" if next_stage == "plan" else "人工要求返工"
        db.execute(
            "UPDATE runs SET status = 'queued', stage = ?, rework_note = ?, error = '', finished_at = '', updated_at = ? WHERE id = ?",
            (next_stage, payload.note, now, run_id),
        )
        db.execute("UPDATE requirements SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, run["requirement_id"]))
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'human', ?, ?, ?, ?)", (run_id, next_stage, next_message, dumps({"note": payload.note}), now))
        return _full_run_payload(run_id)

    @app.post("/api/runs/{run_id}/promote", dependencies=auth)
    def promote(run_id: str) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] != "completed":
            raise HTTPException(status_code=409, detail="只有已完成运行可以提交分支")
        deliver = run.get("deliver") or {}
        if deliver.get("commit"):
            return _full_run_payload(run_id)
        worktree_raw = run.get("worktree_path", "")
        if not worktree_raw:
            raise HTTPException(status_code=409, detail="没有可提交的隔离工作区")
        worktree = Path(worktree_raw).resolve()
        if not worktree.exists():
            raise HTTPException(status_code=409, detail="隔离工作区已不存在")
        requirement = _requirement_or_404(run["requirement_id"])
        commit = gitops.commit_all(worktree, f"FlowCodex: {requirement['title'][:100]}")
        if not commit.ok:
            raise HTTPException(status_code=500, detail=commit.stderr or commit.stdout or "提交失败")
        head = gitops.run_process(["git", "-C", str(worktree), "rev-parse", "HEAD"], worktree, 20)
        deliver["commit"] = head.stdout.strip() if head.ok else ""
        deliver["commit_result"] = commit.as_dict()
        db.update("runs", run_id, deliver_json=dumps(deliver))
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'human', 'deliver', '已提交隔离分支', ?, ?)", (run_id, dumps({"commit": deliver["commit"]}), now_iso()))
        return _full_run_payload(run_id)

    @app.post("/api/runs/{run_id}/export/obsidian", dependencies=auth)
    def export_obsidian(run_id: str) -> dict[str, Any]:
        run = _run_or_404(run_id)
        requirement = _requirement_or_404(run["requirement_id"])
        project = project_out(_project_or_404(run["project_id"]))
        related = [item for item in knowledge.list_knowledge(project_id=run["project_id"], include_expired=True, limit=100) if item.get("source_run_id") == run_id]
        payload = _full_run_payload(run_id)
        export_run = dict(payload["run"])
        export_run["events"] = payload["events"]
        try:
            path = obsidian.export_run(app_settings, export_run, requirement, project, related)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"导出 Obsidian 笔记失败：{exc}") from exc
        if not path:
            raise HTTPException(status_code=409, detail="未配置 Obsidian 知识库路径")
        deliver = run.get("deliver") or {}
        deliver["obsidian_note"] = str(path)
        db.update("runs", run_id, deliver_json=dumps(deliver))
        return {"ok": True, "path": str(path)}

    @app.post("/api/runs/{run_id}/cancel", dependencies=auth)
    def cancel_run(run_id: str) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] in TERMINAL:
            return _full_run_payload(run_id)
        now = now_iso()
        db.execute("UPDATE runs SET status = 'cancelled', error = '人工取消', updated_at = ?, finished_at = ? WHERE id = ?", (now, now, run_id))
        db.execute("UPDATE requirements SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, run["requirement_id"]))
        db.execute("INSERT INTO run_events (run_id, type, stage, message, payload_json, created_at) VALUES (?, 'human', ?, '运行已取消', '{}', ?)", (run_id, run["stage"], now))
        return _full_run_payload(run_id)

    @app.get("/api/runs/{run_id}/artifact", dependencies=auth, response_class=PlainTextResponse)
    def get_artifact(run_id: str) -> str:
        run = _run_or_404(run_id)
        if not run.get("patch_text"):
            raise HTTPException(status_code=404, detail="没有补丁产物")
        return run["patch_text"]

    # ---------- knowledge ----------

    @app.get("/api/knowledge", dependencies=auth)
    def list_all_knowledge(
        project_id: str | None = None,
        status: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        return knowledge.list_knowledge(project_id=project_id, status=status, include_expired=include_expired)

    @app.post("/api/knowledge", dependencies=auth, status_code=201)
    def create_knowledge_item(payload: KnowledgeCreate) -> dict[str, Any]:
        _project_or_404(payload.project_id)
        return knowledge.create_knowledge(
            project_id=payload.project_id,
            kind=payload.kind,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            confidence=payload.confidence,
            ttl_days=payload.ttl_days,
            status=payload.status,
        )

    @app.post("/api/knowledge/{knowledge_id}/feedback", dependencies=auth)
    def feedback(knowledge_id: str, payload: KnowledgeFeedback) -> dict[str, Any]:
        item = knowledge.add_feedback(knowledge_id, payload.reason, payload.run_id)
        if not item:
            raise HTTPException(status_code=404, detail="知识不存在")
        return item

    @app.post("/api/knowledge/expire", dependencies=auth)
    def expire_knowledge() -> dict[str, Any]:
        return {"expired": knowledge.expire_stale()}

    # ---------- dashboard ----------

    @app.get("/api/dashboard", dependencies=auth)
    def dashboard() -> dict[str, Any]:
        status_rows = db.query_all("SELECT status, COUNT(*) AS count FROM runs GROUP BY status")
        counts = {row["status"]: row["count"] for row in status_rows}
        recent = db.query_all(
            "SELECT r.*, req.title AS requirement_title FROM runs r JOIN requirements req ON req.id = r.requirement_id ORDER BY r.created_at DESC LIMIT 12"
        )
        knowledge_count = db.query_one("SELECT COUNT(*) AS count FROM knowledge WHERE status = 'accepted'")
        pending_knowledge = db.query_one("SELECT COUNT(*) AS count FROM knowledge WHERE status = 'pending_review'")
        return {
            "counts": {
                "active": counts.get("queued", 0) + counts.get("running", 0) + counts.get("awaiting_input", 0) + counts.get("awaiting_plan_approval", 0),
                "waiting": counts.get("awaiting_input", 0) + counts.get("awaiting_plan_approval", 0),
                "completed": counts.get("completed", 0),
                "blocked": counts.get("blocked", 0),
                "failed": counts.get("failed", 0),
                "total": sum(counts.values()),
            },
            "knowledge": {
                "accepted": knowledge_count["count"] if knowledge_count else 0,
                "pending_review": pending_knowledge["count"] if pending_knowledge else 0,
            },
            "recent_runs": [run_out(row) for row in recent],
        }

    return app


app = create_app()

