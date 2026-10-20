from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

AUTH = {"Authorization": "Bearer test-token"}


def make_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        app_name="FlowCodex Onboarding Test",
        env="test",
        api_token="test-token",
        data_dir=tmp_path / "data",
        repos_root=tmp_path / "repos",
        llm_provider="mock",
        llm_api_key="",
        worker_enabled=False,
        worker_poll_interval=0.05,
        auto_apply_patch=True,
        obsidian_vault_path=tmp_path / "vault",
        obsidian_folder="WorkFlow-Codex",
    )
    settings.ensure_dirs()
    return settings


def test_demo_onboarding_creates_git_repo_project_and_run(tmp_path: Path):
    app = create_app(settings=make_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        response = client.post("/api/onboarding/demo", headers=AUTH, json={"start_run": True})
        assert response.status_code == 201, response.text
        data = response.json()

        project = data["project"]
        repo_path = Path(project["repo_path"])
        assert repo_path.exists()
        assert (repo_path / ".git").exists()
        assert (repo_path / "README.md").exists()
        assert data["requirement"]["title"] == "体验：完善示例项目 README"
        assert data["run"]["run"]["status"] == "queued"

        second = client.post("/api/onboarding/demo", headers=AUTH, json={"start_run": True})
        assert second.status_code == 201, second.text
        second_data = second.json()
        assert second_data["project"]["id"] == project["id"]
        assert second_data["run"]["run"]["id"] != data["run"]["run"]["id"]


def test_demo_onboarding_requires_auth(tmp_path: Path):
    app = create_app(settings=make_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        response = client.post("/api/onboarding/demo", json={"start_run": True})
        assert response.status_code == 401


def test_demo_onboarding_reaches_completed_with_mock_worker(tmp_path: Path):
    settings = Settings(
        app_name="FlowCodex Onboarding E2E",
        env="test",
        api_token="test-token",
        data_dir=tmp_path / "data",
        repos_root=tmp_path / "repos",
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

    def wait_for(client: TestClient, run_id: str, status: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
            if last["run"]["status"] == status:
                return last
            time.sleep(0.1)
        raise AssertionError(f"run {run_id} did not reach {status}; last={last.get('run', {}).get('status')}")

    with TestClient(app) as client:
        created = client.post("/api/onboarding/demo", headers=AUTH, json={"start_run": True})
        assert created.status_code == 201, created.text
        run_id = created.json()["run"]["run"]["id"]

        waiting = wait_for(client, run_id, "awaiting_input")
        answers = {
            question["id"]: "按 README 追加一行说明，不修改其他文件。"
            for question in waiting["run"]["clarify"]["questions"]
        }
        assert client.post(f"/api/runs/{run_id}/answers", headers=AUTH, json={"answers": answers}).status_code == 200

        wait_for(client, run_id, "awaiting_plan_approval")
        assert client.post(
            f"/api/runs/{run_id}/approve-plan", headers=AUTH, json={"approved": True}
        ).status_code == 200

        completed = wait_for(client, run_id, "completed")
        run = completed["run"]
        assert run["code"]["apply_ok"] is True
        assert run["review"]["verdict"] == "pass"
        assert run["verify"]["ok"] is True
        assert run["deliver"]["commit"]
        assert completed["knowledge"]
