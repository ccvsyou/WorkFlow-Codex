from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings


@dataclass
class ProcResult:
    ok: bool
    code: int
    stdout: str
    stderr: str
    command: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": self.command,
        }


class GitError(RuntimeError):
    pass


SENSITIVE_ENV_KEYS = {
    "API_TOKEN",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_OPENAI_API_KEY",
}


def sanitized_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in SENSITIVE_ENV_KEYS
        and not key.endswith(("_TOKEN", "_SECRET", "_PASSWORD"))
        and "API_KEY" not in key
    }


def run_process(args: list[str], cwd: Path, timeout: int = 120, env: dict[str, str] | None = None) -> ProcResult:
    command = " ".join(shlex.quote(part) for part in args)
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env,
        )
        return ProcResult(
            ok=proc.returncode == 0,
            code=proc.returncode,
            stdout=proc.stdout[-20000:],
            stderr=proc.stderr[-20000:],
            command=command,
        )
    except FileNotFoundError as exc:
        return ProcResult(False, 127, "", str(exc), command)
    except subprocess.TimeoutExpired as exc:
        return ProcResult(False, 124, exc.stdout or "", f"执行超时（{timeout} 秒）", command)
    except OSError as exc:
        return ProcResult(False, 1, "", str(exc), command)


def safe_repo_path(settings: Settings, repo_path: str) -> Path:
    path = Path(repo_path).expanduser().resolve()
    root = settings.repos_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GitError(f"仓库必须位于仓库根目录下（{root}）") from exc
    if not path.exists() or not path.is_dir():
        raise GitError(f"仓库路径不存在：{path}")
    check = run_process(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], path, 20)
    if not check.ok or check.stdout.strip() != "true":
        raise GitError(f"不是有效的 Git 工作区：{path}")
    return path


def git_head(repo: Path) -> str:
    result = run_process(["git", "-C", str(repo), "rev-parse", "HEAD"], repo, 20)
    if not result.ok:
        raise GitError("仓库没有 HEAD 提交，请先创建初始提交")
    return result.stdout.strip()


def is_sensitive_repo_file(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name in {"id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json"}:
        return True
    return name.endswith((".pem", ".key", ".p12", ".pfx", ".jks"))


def list_repo_files(repo: Path, max_files: int = 400) -> list[str]:
    result = run_process(
        ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"],
        repo,
        30,
    )
    if not result.ok:
        raise GitError(result.stderr or "无法列出仓库文件")
    files = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(".git/") or "/.git/" in line:
            continue
        if is_sensitive_repo_file(line):
            continue
        if line.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".lock")):
            continue
        files.append(line)
        if len(files) >= max_files:
            break
    return files


def read_text(path: Path, limit: int = 80000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_repo_context(
    settings: Settings,
    repo_path: str,
    allowed_paths: list[str],
    query: str,
    max_files: int | None = None,
) -> dict[str, Any]:
    repo = safe_repo_path(settings, repo_path)
    git_head(repo)
    files = list_repo_files(repo, max_files or settings.max_repo_files)
    allowed = [item.strip() for item in allowed_paths if item and item.strip()] or ["."]
    filtered = []
    for item in files:
        if any(item == p or item.startswith(p.rstrip("/") + "/") or p == "." for p in allowed):
            filtered.append(item)

    keywords = [word.lower() for word in query.replace("/", " ").replace("_", " ").split() if len(word) >= 2]
    priority_names = {
        "readme.md",
        "readme",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "cargo.toml",
        "go.mod",
        "makefile",
        "dockerfile",
        "docker-compose.yml",
        "agents.md",
        "architecture.md",
    }

    def score(name: str) -> tuple[int, int]:
        lower = name.lower()
        base = Path(lower).name
        value = 0
        if base in priority_names:
            value += 100
        if name.count("/") <= 2:
            value += 10
        value += sum(3 for keyword in keywords if keyword in lower)
        return (-value, len(name))

    selected = sorted(filtered, key=score)[:60]
    chunks: list[str] = []
    total = 0
    for name in selected:
        path = repo / name
        text = read_text(path)
        if not text.strip():
            continue
        chunk = f"\n--- FILE: {name} ---\n{text}\n"
        if total + len(chunk) > settings.max_context_chars:
            remaining = max(0, settings.max_context_chars - total)
            if remaining > 500:
                chunks.append(chunk[:remaining] + "\n...[context truncated]...\n")
            break
        chunks.append(chunk)
        total += len(chunk)

    log = run_process(["git", "-C", str(repo), "log", "--oneline", "-12"], repo, 20)
    status = run_process(["git", "-C", str(repo), "status", "--short"], repo, 20)
    context = (
        f"Repository: {repo}\n"
        f"Files: {len(filtered)}\n"
        f"Allowed paths: {allowed}\n"
        f"Recent commits:\n{log.stdout or '(none)'}\n"
        f"Working tree status:\n{status.stdout or '(clean)'}\n"
        + "".join(chunks)
    )
    return {
        "repo_path": str(repo),
        "file_count": len(filtered),
        "files": filtered[:300],
        "context": context,
        "git_head": git_head(repo),
    }


def _normalize_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def path_is_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = _normalize_repo_path(path)
    for allowed in allowed_paths or ["."]:
        normalized_allowed = _normalize_repo_path(allowed)
        if normalized_allowed in {"", "."}:
            return True
        if normalized == normalized_allowed or normalized.startswith(normalized_allowed.rstrip("/") + "/"):
            return True
    return False


def validate_changed_files(
    changed_files: list[str],
    allowed_paths: list[str],
    planned_files: list[str] | None = None,
) -> str | None:
    outside_allowed = [path for path in changed_files if not path_is_allowed(path, allowed_paths)]
    if outside_allowed:
        return "补丁触碰了允许路径之外的文件：" + "、".join(outside_allowed)
    planned = {_normalize_repo_path(path) for path in (planned_files or []) if path.strip()}
    if planned:
        outside_plan = [path for path in changed_files if _normalize_repo_path(path) not in planned]
        if outside_plan:
            return "补丁触碰了技术方案未列出的文件：" + "、".join(outside_plan)
    return None


def staged_files(worktree: Path) -> list[str]:
    result = run_process(["git", "-C", str(worktree), "diff", "--cached", "--name-only"], worktree, 60)
    if not result.ok:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def create_worktree(settings: Settings, repo_path: str, run_id: str) -> tuple[Path, str]:
    repo = safe_repo_path(settings, repo_path)
    git_head(repo)
    branch = f"flowcodex/run-{run_id}"
    destination = (settings.worktrees_dir / f"run-{run_id}").resolve()
    destination.relative_to(settings.worktrees_dir.resolve())
    if destination.exists():
        run_process(["git", "-C", str(repo), "worktree", "remove", "--force", str(destination)], repo, 60)
    if destination.exists():
        shutil.rmtree(destination)
    run_process(["git", "-C", str(repo), "worktree", "prune"], repo, 20)
    result = run_process(
        ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(destination), "HEAD"],
        repo,
        60,
    )
    if not result.ok:
        # A retried run can reuse an existing branch; reset the branch into a fresh worktree.
        result = run_process(
            ["git", "-C", str(repo), "worktree", "add", "-B", branch, str(destination), "HEAD"],
            repo,
            60,
        )
    if not result.ok:
        raise GitError(result.stderr or result.stdout or "无法创建 Git 隔离工作区")
    return destination, branch


def apply_patch(worktree: Path, patch: str) -> ProcResult:
    if not patch.strip():
        return ProcResult(False, 1, "", "补丁为空")
    patch_file = worktree / ".flowcodex.patch"
    try:
        patch_file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
        check = run_process(["git", "-C", str(worktree), "apply", "--check", str(patch_file)], worktree, 60)
        if not check.ok:
            return check
        return run_process(["git", "-C", str(worktree), "apply", "--index", str(patch_file)], worktree, 60)
    finally:
        try:
            patch_file.unlink(missing_ok=True)
        except OSError:
            pass


def staged_diff(worktree: Path) -> str:
    result = run_process(["git", "-C", str(worktree), "diff", "--cached", "--binary"], worktree, 60)
    return result.stdout if result.ok else ""


def commit_all(worktree: Path, message: str) -> ProcResult:
    return run_process(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "user.name=FlowCodex",
            "-c",
            "user.email=flowcodex@local",
            "commit",
            "-m",
            message,
        ],
        worktree,
        60,
    )


def run_command(command: str, cwd: Path, timeout: int) -> ProcResult:
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return ProcResult(False, 2, "", f"命令格式无效：{exc}", command)
    if not args:
        return ProcResult(False, 2, "", "命令为空", command)
    return run_process(args, cwd, timeout, env=sanitized_env())


def write_artifact(settings: Settings, run_id: str, filename: str, content: str) -> Path:
    directory = settings.artifact_dir / f"run-{run_id}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def safe_join_under(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path

