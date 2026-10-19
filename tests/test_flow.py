from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


AUTH = {"Authorization": "Bearer test-token"}


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    repo = root / "demo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# Demo\n\nA tiny repository for FlowCodex tests.\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init")
    return root


@pytest.fixture()
def client(tmp_path: Path, repo_root: Path):
    settings = Settings(
        app_name="FlowCodex Test",
        env="test",
        api_token="test-token",
        data_dir=tmp_path / "data",
        repos_root=repo_root,
        llm_provider="mock",
        llm_api_key="",
        worker_enabled=True,
        worker_poll_interval=0.05,
        auto_apply_patch=True,
        obsidian_vault_path=tmp_path / "vault",
        obsidian_folder="WorkFlow-Codex",
    )
    settings.ensure_dirs()
    app = create_app(settings=settings, start_worker=True)
    with TestClient(app) as test_client:
        yield test_client


def wait_for(client: TestClient, run_id: str, status: str, timeout: float = 12.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        last = response.json()
        if last["run"]["status"] == status:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Run {run_id} did not reach {status}; last={last}")


def test_health_and_auth(client: TestClient):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers=AUTH).status_code == 200


def test_end_to_end_mock_pipeline(client: TestClient, repo_root: Path):
    project_response = client.post(
        "/api/projects",
        headers=AUTH,
        json={
            "name": "Demo Project",
            "repo_path": str(repo_root / "demo"),
            "default_branch": "main",
            "verify_command": "python -c \"print('ok')\"",
            "allowed_paths": ["."],
            "auto_apply": True,
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()

    requirement_response = client.post(
        "/api/requirements",
        headers=AUTH,
        json={
            "project_id": project["id"],
            "title": "Add a run marker",
            "raw_input": "Need a small change that can be verified by the pipeline.",
            "start_run": True,
        },
    )
    assert requirement_response.status_code == 201, requirement_response.text
    created = requirement_response.json()
    run_id = created["run"]["run"]["id"]

    waiting = wait_for(client, run_id, "awaiting_input")
    questions = waiting["run"]["clarify"]["questions"]
    assert questions
    answers = {question["id"]: f"answer for {question['id']}" for question in questions}

    answer_response = client.post(
        f"/api/runs/{run_id}/answers",
        headers=AUTH,
        json={"answers": answers},
    )
    assert answer_response.status_code == 200, answer_response.text

    plan_wait = wait_for(client, run_id, "awaiting_plan_approval")
    assert plan_wait["run"]["plan"]["steps"]

    approve_response = client.post(
        f"/api/runs/{run_id}/approve-plan",
        headers=AUTH,
        json={"approved": True},
    )
    assert approve_response.status_code == 200, approve_response.text

    completed = wait_for(client, run_id, "completed")
    run = completed["run"]
    assert run["feasibility"]["total"] >= 60
    assert run["code"]["apply_ok"] is True
    assert run["review"]["verdict"] == "pass"
    assert run["verify"]["ok"] is True
    assert run["deliver"]["commit"]
    assert completed["knowledge"]
    assert "diff --git" in run["patch_text"]

    artifact = client.get(f"/api/runs/{run_id}/artifact", headers=AUTH)
    assert artifact.status_code == 200
    assert "docs/flowcodex-run.md" in artifact.text

    obsidian = client.post(f"/api/runs/{run_id}/export/obsidian", headers=AUTH)
    assert obsidian.status_code == 200, obsidian.text
    note_path = Path(obsidian.json()["path"])
    assert note_path.exists()
    note = note_path.read_text(encoding="utf-8")
    assert "## 事件时间线" in note
    assert "运行完成" in note

    knowledge = client.get(f"/api/knowledge?project_id={project['id']}", headers=AUTH)
    assert knowledge.status_code == 200
    assert knowledge.json()

    runs = client.get(f"/api/runs?project_id={project['id']}", headers=AUTH)
    assert runs.status_code == 200
    assert runs.json()[0]["requirement_title"] == "Add a run marker"

    dashboard = client.get("/api/dashboard", headers=AUTH)
    assert dashboard.status_code == 200
    assert dashboard.json()["counts"]["completed"] >= 1
