from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .gitops import GitError, run_process

DEMO_REPO_NAME = "flowcodex-demo"
DEMO_REPO_TITLE = "FlowCodex 示例项目"
DEMO_REQUIREMENT_TITLE = "体验：完善示例项目 README"
DEMO_REQUIREMENT_INPUT = (
    "在 README.md 末尾增加一行说明：这是 FlowCodex 的端到端交付示例。"
    "不要修改其他文件。"
)


def ensure_demo_repo(settings: Settings) -> Path:
    root = settings.repos_root.resolve()
    repo = (root / DEMO_REPO_NAME).resolve()
    repo.relative_to(root)
    repo.mkdir(parents=True, exist_ok=True)

    if not (repo / ".git").exists():
        initialized = run_process(["git", "init", "-b", "main"], repo, 30)
        if not initialized.ok:
            raise GitError(initialized.stderr or initialized.stdout or "无法初始化示例仓库")

    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# FlowCodex Demo\n\n这是 FlowCodex 的端到端交付示例仓库。\n",
            encoding="utf-8",
        )

    run_process(["git", "-C", str(repo), "add", "."], repo, 30)
    status = run_process(["git", "-C", str(repo), "status", "--porcelain"], repo, 20)
    if status.stdout.strip():
        committed = run_process(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=FlowCodex",
                "-c",
                "user.email=flowcodex@local",
                "commit",
                "-m",
                "Initialize FlowCodex demo",
            ],
            repo,
            60,
        )
        if not committed.ok:
            raise GitError(committed.stderr or committed.stdout or "无法创建示例仓库初始提交")

    head = run_process(["git", "-C", str(repo), "rev-parse", "HEAD"], repo, 20)
    if not head.ok:
        raise GitError("示例仓库没有可用提交")
    return repo
