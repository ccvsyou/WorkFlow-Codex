from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import httpx


def run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def ensure_demo_repo(repos_root: Path) -> Path:
    repo = repos_root / "demo"
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        run(["git", "init", "-b", "main"], repo)
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text("# Demo\n\nCreated by FlowCodex seed script.\n", encoding="utf-8")
    run(["git", "add", "."], repo)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    if status.stdout.strip():
        run(["git", "-c", "user.name=FlowCodex", "-c", "user.email=flowcodex@local", "commit", "-m", "seed demo"], repo)
    return repo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--repos-root", default="./repos")
    args = parser.parse_args()

    repo = ensure_demo_repo(Path(args.repos_root).resolve())
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(base_url=args.api.rstrip("/"), headers=headers, timeout=30) as client:
        project = client.post(
            "/api/projects",
            json={
                "name": "Demo Project",
                "repo_path": str(repo),
                "default_branch": "main",
                "verify_command": "python -c \"print('ok')\"",
                "allowed_paths": ["."],
                "auto_apply": True,
            },
        )
        project.raise_for_status()
        project_data = project.json()
        requirement = client.post(
            "/api/requirements",
            json={
                "project_id": project_data["id"],
                "title": "Add a FlowCodex run marker",
                "raw_input": "Add a small documentation marker that can be applied and verified by the pipeline.",
                "start_run": True,
            },
        )
        requirement.raise_for_status()
        data = requirement.json()
        print("项目：", project_data["id"])
        print("运行：", data["run"]["run"]["id"])
        print("打开管理端，完成需求访谈和方案批准。")


if __name__ == "__main__":
    main()
