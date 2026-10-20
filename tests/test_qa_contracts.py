from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.llm import LLMResult
from app.main import create_app
from app.schemas import (
    ClarifyQuestion,
    ClarifyResult,
    FeasibilityResult,
    PlanResult,
    PlanStep,
    ReviewIssue,
    ReviewResult,
)
from app.services import gitops
from app.services.engine import (
    clarification_ready,
    normalize_review,
    validate_plan,
    weighted_feasibility_total,
)

AUTH = {"Authorization": "Bearer test-token"}


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def make_settings(
    tmp_path: Path,
    repo_root: Path,
    worker_enabled: bool = True,
    input_price: float = 0.0,
    output_price: float = 0.0,
) -> Settings:
    settings = Settings(
        app_name="FlowCodex QA",
        env="test",
        api_token="test-token",
        data_dir=tmp_path / "data",
        repos_root=repo_root,
        llm_provider="mock",
        llm_api_key="",
        llm_input_price_per_mtok=input_price,
        llm_output_price_per_mtok=output_price,
        worker_enabled=worker_enabled,
        worker_poll_interval=0.05,
        auto_apply_patch=True,
        obsidian_vault_path=tmp_path / "vault",
        obsidian_folder="WorkFlow-Codex",
    )
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    repo = root / "demo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init")
    return root


@pytest.fixture()
def client(tmp_path: Path, repo_root: Path):
    app = create_app(settings=make_settings(tmp_path, repo_root), start_worker=True)
    with TestClient(app) as test_client:
        yield test_client


def wait_for_status(client: TestClient, run_id: str, status: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
        if last["run"]["status"] == status:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Run {run_id} did not reach {status}: {last}")


def create_project(client: TestClient, repo_root: Path, name: str = "QA Project", auto_apply: bool = True) -> dict:
    response = client.post(
        "/api/projects",
        headers=AUTH,
        json={
            "name": name,
            "repo_path": str(repo_root / "demo"),
            "default_branch": "main",
            "verify_command": "python -c \"print('ok')\"",
            "allowed_paths": ["."],
            "auto_apply": auto_apply,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_weighted_feasibility_total_is_deterministic():
    result = FeasibilityResult(
        clarity=100,
        repo_match=50,
        scope_control=80,
        technical_simplicity=80,
        dependency_readiness=80,
        risk_control=80,
        delivery_shape=80,
        total=1,
    )
    assert weighted_feasibility_total(result) == 78


def test_required_clarification_questions_block_even_if_model_says_ready():
    blocked = ClarifyResult(
        understanding="还需要确认目标用户。",
        ready_to_plan=True,
        questions=[ClarifyQuestion(id="q1", question="目标用户是谁？", required=True)],
    )
    assert clarification_ready(blocked) is False

    optional_only = ClarifyResult(
        understanding="可选信息缺失但不影响执行。",
        ready_to_plan=False,
        questions=[ClarifyQuestion(id="q2", question="是否需要更多示例？", required=False)],
    )
    assert clarification_ready(optional_only) is True


def test_review_normalization_only_blocks_on_critical_or_security_failure():
    suggestions_only = ReviewResult(
        verdict="changes_requested",
        issues=[ReviewIssue(severity="suggestion", title="样式建议", detail="可选")],
        security_checks={"auth_middleware": True},
    )
    normalized = normalize_review(suggestions_only)
    assert normalized.verdict == "pass"
    assert all(issue.severity == "suggestion" for issue in normalized.issues)

    security_failure = ReviewResult(
        verdict="pass",
        issues=[],
        security_checks={"auth_middleware": False, "parameterized_sql": True},
    )
    normalized = normalize_review(security_failure)
    assert normalized.verdict == "changes_requested"
    assert any(issue.severity == "critical" for issue in normalized.issues)


def drive_to_completion(client: TestClient, repo_root: Path, auto_apply: bool = True) -> tuple[str, dict]:
    project = create_project(client, repo_root, auto_apply=auto_apply)
    created = client.post(
        "/api/requirements",
        headers=AUTH,
        json={
            "project_id": project["id"],
            "title": "已完成运行测试",
            "raw_input": "验证默认交付与人工提交流程。",
            "start_run": True,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["run"]["id"]
    waiting = wait_for_status(client, run_id, "awaiting_input")
    answers = {question["id"]: "由开发者验收，补丁需通过验证。" for question in waiting["run"]["clarify"]["questions"]}
    assert client.post(f"/api/runs/{run_id}/answers", headers=AUTH, json={"answers": answers}).status_code == 200
    wait_for_status(client, run_id, "awaiting_plan_approval")
    assert client.post(f"/api/runs/{run_id}/approve-plan", headers=AUTH, json={"approved": True}).status_code == 200
    return run_id, wait_for_status(client, run_id, "completed")


def test_plan_validation_checks_ids_dependencies_cycles_and_file_scope():
    valid = PlanResult(steps=[
        PlanStep(id="s1", title="一", files=["a.py"]),
        PlanStep(id="s2", title="二", depends_on=["s1"], files=["b.py"]),
    ])
    assert validate_plan(valid) is None

    duplicate = PlanResult(steps=[PlanStep(id="s1", title="一"), PlanStep(id="s1", title="二")])
    assert "重复" in validate_plan(duplicate)

    missing = PlanResult(steps=[PlanStep(id="s1", title="一", depends_on=["s9"])])
    assert "不存在" in validate_plan(missing)

    cyclic = PlanResult(steps=[
        PlanStep(id="s1", title="一", depends_on=["s2"]),
        PlanStep(id="s2", title="二", depends_on=["s1"]),
    ])
    assert "环" in validate_plan(cyclic)

    too_many_files = PlanResult(steps=[PlanStep(id="s1", title="一", files=["a", "b", "c", "d", "e", "f"])])
    assert "超过 5 个文件" in validate_plan(too_many_files)


def test_patch_scope_validation_rejects_outside_paths_and_unplanned_files():
    assert gitops.validate_changed_files(["docs/a.md"], ["."], []) is None
    assert "允许路径" in gitops.validate_changed_files(["src/app.py"], ["docs"], [])
    assert "技术方案未列出" in gitops.validate_changed_files(["docs/b.md"], ["."], ["docs/a.md"])
    assert gitops.path_is_allowed("src/app.py", ["src"]) is True
    assert gitops.path_is_allowed("other/app.py", ["src"]) is False
    assert gitops.path_is_allowed(".gitignore", ["."]) is True
    assert gitops.path_is_allowed(".github/workflows/ci.yml", [".github"]) is True
    assert gitops.path_is_allowed(".gitignore", ["src"]) is False


def test_verify_commands_do_not_inherit_api_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    monkeypatch.setenv("API_TOKEN", "super-secret-token")
    result = gitops.run_command(
        "python -c \"import os; print(os.getenv('LLM_API_KEY', 'missing')); print(os.getenv('API_TOKEN', 'missing'))\"",
        tmp_path,
        15,
    )
    assert result.ok, result.stderr
    assert result.stdout.splitlines() == ["missing", "missing"]


def test_blank_project_and_requirement_fields_are_rejected(client: TestClient, repo_root: Path):
    bad_project = client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "   ", "repo_path": str(repo_root / "demo")},
    )
    assert bad_project.status_code == 422, bad_project.text

    project = create_project(client, repo_root)
    bad_requirement = client.post(
        "/api/requirements",
        headers=AUTH,
        json={"project_id": project["id"], "title": "   ", "raw_input": "   ", "start_run": False},
    )
    assert bad_requirement.status_code == 422, bad_requirement.text


def test_sensitive_files_are_not_included_in_repo_context(repo_root: Path):
    repo = repo_root / "demo"
    (repo / ".env").write_text("LLM_API_KEY=should-not-leak\n", encoding="utf-8")
    (repo / "id_rsa").write_text("private-key\n", encoding="utf-8")
    run_git(repo, "add", "-f", ".env", "id_rsa")
    run_git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "add sensitive files")
    files = gitops.list_repo_files(repo)
    assert ".env" not in files
    assert "id_rsa" not in files


def test_repository_outside_repos_root_is_rejected(client: TestClient, tmp_path: Path):
    outside = tmp_path / "outside-repo"
    outside.mkdir()
    run_git(outside, "init", "-b", "main")
    (outside / "README.md").write_text("# Outside\n", encoding="utf-8")
    run_git(outside, "add", ".")
    run_git(outside, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init")

    response = client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "Outside", "repo_path": str(outside)},
    )
    assert response.status_code == 400, response.text
    assert "仓库" in response.json()["detail"]


def test_knowledge_feedback_lowers_confidence_and_marks_pending(client: TestClient, repo_root: Path):
    project = create_project(client, repo_root)
    created = client.post(
        "/api/knowledge",
        headers=AUTH,
        json={
            "project_id": project["id"],
            "kind": "lesson",
            "title": "反馈测试",
            "content": "用于验证人工反馈会降低置信度。",
            "tags": ["qa"],
            "confidence": 0.4,
            "ttl_days": 30,
            "status": "accepted",
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()

    feedback = client.post(
        f"/api/knowledge/{item['id']}/feedback",
        headers=AUTH,
        json={"reason": "与实际代码不一致", "run_id": ""},
    )
    assert feedback.status_code == 200, feedback.text
    updated = feedback.json()
    assert updated["confidence"] < 0.4
    assert updated["status"] == "pending_review"


def test_promote_commits_completed_isolated_branch(client: TestClient, repo_root: Path):
    run_id, completed = drive_to_completion(client, repo_root, auto_apply=False)
    assert not completed["run"]["deliver"].get("commit")
    promoted = client.post(f"/api/runs/{run_id}/promote", headers=AUTH)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["run"]["deliver"]["commit"]


def test_critical_review_blocks_and_rework_recovers(client: TestClient, repo_root: Path):
    engine = client.app.state.engine
    original = engine.llm.complete_json
    review_attempts = 0

    async def review_with_critical(system: str, user: str, schema_name: str):
        nonlocal review_attempts
        if schema_name == "review":
            review_attempts += 1
            if review_attempts == 1:
                return LLMResult(
                    data={
                        "verdict": "pass",
                        "summary": "发现关键 SQL 注入风险。",
                        "issues": [
                            {
                                "severity": "critical",
                                "title": "SQL 字符串拼接",
                                "file": "app.py",
                                "detail": "外部入参进入 SQL 拼接。",
                                "security_rule": "parameterized_sql",
                            }
                        ],
                        "security_checks": {
                            "auth_from_session": True,
                            "auth_middleware": True,
                            "parameterized_sql": True,
                            "no_hardcoded_secrets": True,
                            "input_validation": True,
                            "resource_ownership": True,
                        },
                        "residual_risks": [],
                    },
                    provider="qa-fake",
                    model="qa-fake",
                )
        return await original(system, user, schema_name)

    engine.llm.complete_json = review_with_critical
    project = create_project(client, repo_root)
    created = client.post(
        "/api/requirements",
        headers=AUTH,
        json={
            "project_id": project["id"],
            "title": "审查返工测试",
            "raw_input": "验证 critical 审查会阻塞，并可通过返工恢复。",
            "start_run": True,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["run"]["id"]

    waiting = wait_for_status(client, run_id, "awaiting_input")
    answers = {question["id"]: "由开发者验收，补丁需通过验证。" for question in waiting["run"]["clarify"]["questions"]}
    assert client.post(f"/api/runs/{run_id}/answers", headers=AUTH, json={"answers": answers}).status_code == 200
    wait_for_status(client, run_id, "awaiting_plan_approval")
    assert client.post(f"/api/runs/{run_id}/approve-plan", headers=AUTH, json={"approved": True}).status_code == 200

    blocked = wait_for_status(client, run_id, "blocked")
    assert blocked["run"]["stage"] == "review"
    assert any(issue["severity"] == "critical" for issue in blocked["run"]["review"]["issues"])

    rework = client.post(
        f"/api/runs/{run_id}/rework",
        headers=AUTH,
        json={"note": "请修复 SQL 注入风险后再提交。"},
    )
    assert rework.status_code == 200, rework.text
    completed = wait_for_status(client, run_id, "completed")
    assert completed["run"]["review"]["verdict"] == "pass"
    assert review_attempts >= 2


def test_cancel_while_clarification_is_running(client: TestClient, repo_root: Path):
    engine = client.app.state.engine
    original = engine.llm.complete_json

    async def slow_complete(system: str, user: str, schema_name: str):
        await asyncio.sleep(0.4)
        return await original(system, user, schema_name)

    engine.llm.complete_json = slow_complete
    project = create_project(client, repo_root)
    created = client.post(
        "/api/requirements",
        headers=AUTH,
        json={
            "project_id": project["id"],
            "title": "运行中取消测试",
            "raw_input": "验证运行中的需求访谈可以被取消。",
            "start_run": True,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["run"]["id"]

    for _ in range(30):
        current = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
        if current["run"]["status"] == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("run never entered running state")

    cancelled = client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)
    assert cancelled.status_code == 200, cancelled.text
    time.sleep(0.8)
    final = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert final["run"]["status"] == "cancelled", final


def test_queued_run_can_be_cancelled_without_worker(tmp_path: Path, repo_root: Path):
    app = create_app(settings=make_settings(tmp_path, repo_root, worker_enabled=False), start_worker=False)
    with TestClient(app) as test_client:
        project = create_project(test_client, repo_root)
        created = test_client.post(
            "/api/requirements",
            headers=AUTH,
            json={
                "project_id": project["id"],
                "title": "排队取消测试",
                "raw_input": "验证排队中的运行可以被取消。",
                "start_run": True,
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run"]["run"]["id"]

        cancelled = test_client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["run"]["status"] == "cancelled"
        assert cancelled.json()["requirement"]["status"] == "cancelled"


def test_project_settings_can_be_updated(client: TestClient, repo_root: Path):
    project = create_project(client, repo_root)
    response = client.patch(
        f"/api/projects/{project['id']}",
        headers=AUTH,
        json={
            "name": "Renamed Project",
            "default_branch": "develop",
            "verify_command": "pytest -q",
            "publish_command": "./scripts/deploy.sh",
            "allowed_paths": ["src", "tests"],
            "auto_apply": False,
            "auto_publish": True,
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "Renamed Project"
    assert updated["default_branch"] == "develop"
    assert updated["verify_command"] == "pytest -q"
    assert updated["publish_command"] == "./scripts/deploy.sh"
    assert updated["allowed_paths"] == ["src", "tests"]
    assert updated["auto_apply"] is False
    assert updated["auto_publish"] is True


def test_usage_and_cost_are_reported(tmp_path: Path, repo_root: Path):
    settings = make_settings(tmp_path, repo_root, input_price=2.0, output_price=8.0)
    app = create_app(settings=settings, start_worker=False)
    with TestClient(app) as costing_client:
        project = create_project(costing_client, repo_root)
        created = costing_client.post(
            "/api/requirements",
            headers=AUTH,
            json={
                "project_id": project["id"],
                "title": "成本统计",
                "raw_input": "验证 token 与成本统计。",
                "start_run": True,
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run"]["run"]["id"]
        db.execute(
            "UPDATE runs SET tokens_in = ?, tokens_out = ? WHERE id = ?",
            (1_000_000, 500_000, run_id),
        )

        run = costing_client.get(f"/api/runs/{run_id}", headers=AUTH).json()["run"]
        assert run["usage"]["tokens_in"] == 1_000_000
        assert run["usage"]["tokens_out"] == 500_000
        assert run["usage"]["total_tokens"] == 1_500_000
        assert run["usage"]["estimated_cost"] == 6.0
        assert run["usage"]["priced"] is True

        dashboard = costing_client.get("/api/dashboard", headers=AUTH).json()
        assert dashboard["usage"]["total_tokens"] == 1_500_000
        assert dashboard["usage"]["estimated_cost"] == 6.0
        assert dashboard["recent_runs"][0]["usage"]["estimated_cost"] == 6.0


def test_knowledge_review_approves_and_rejects(client: TestClient, repo_root: Path):
    project = create_project(client, repo_root)

    def create_pending(title: str) -> dict:
        response = client.post(
            "/api/knowledge",
            headers=AUTH,
            json={
                "project_id": project["id"],
                "kind": "lesson",
                "title": title,
                "content": "待人工审核的知识候选。",
                "tags": ["review"],
                "confidence": 0.5,
                "ttl_days": 30,
                "status": "pending_review",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    approved_item = create_pending("批准测试")
    approved = client.post(
        f"/api/knowledge/{approved_item['id']}/review",
        headers=AUTH,
        json={"decision": "approve", "note": "内容准确"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "accepted"
    assert approved.json()["confidence"] >= 0.75

    rejected_item = create_pending("驳回测试")
    rejected = client.post(
        f"/api/knowledge/{rejected_item['id']}/review",
        headers=AUTH,
        json={"decision": "reject", "note": "与当前架构不一致"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    rejected_list = client.get("/api/knowledge?status=rejected", headers=AUTH)
    assert rejected_list.status_code == 200
    assert [item["id"] for item in rejected_list.json()] == [rejected_item["id"]]
