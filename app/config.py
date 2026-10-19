from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _path(name: str, default: str | None = None) -> Path | None:
    raw = os.getenv(name, default)
    if raw is None or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    app_name: str = "FlowCodex"
    env: str = "development"
    api_token: str = ""

    data_dir: Path = field(default_factory=lambda: Path("data").resolve())
    repos_root: Path = field(default_factory=lambda: Path("repos").resolve())
    artifacts_dir: Path | None = None

    llm_provider: str = "mock"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_timeout: int = 180

    feasibility_threshold: int = 60
    worker_enabled: bool = True
    worker_poll_interval: float = 1.0
    verify_timeout: int = 300
    auto_apply_patch: bool = False

    knowledge_ttl_days: int = 90
    obsidian_vault_path: Path | None = None
    obsidian_folder: str = "WorkFlow-Codex"

    max_repo_files: int = 400
    max_context_chars: int = 60000
    max_patch_chars: int = 120000

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flowcodex.db"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def artifact_dir(self) -> Path:
        return self.artifacts_dir or (self.data_dir / "artifacts")

    @property
    def is_mock(self) -> bool:
        return self.llm_provider.strip().lower() == "mock"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.repos_root, self.worktrees_dir, self.artifact_dir):
            path.mkdir(parents=True, exist_ok=True)
        if self.obsidian_vault_path:
            self.obsidian_vault_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LLM_PROVIDER", "mock").strip().lower()
        api_key = os.getenv("LLM_API_KEY", "").strip()
        obsidian = _path("OBSIDIAN_VAULT_PATH")
        data_dir = _path("DATA_DIR", "./data") or Path("./data").resolve()
        repos_root = _path("REPOS_ROOT", "./repos") or Path("./repos").resolve()
        artifacts = _path("ARTIFACTS_DIR")
        return cls(
            app_name=os.getenv("APP_NAME", "FlowCodex").strip() or "FlowCodex",
            env=os.getenv("APP_ENV", "development").strip() or "development",
            api_token=os.getenv("API_TOKEN", "").strip(),
            data_dir=data_dir,
            repos_root=repos_root,
            artifacts_dir=artifacts,
            llm_provider=provider,
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip(),
            llm_api_key=api_key,
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip() or "deepseek-chat",
            llm_temperature=_float("LLM_TEMPERATURE", 0.2),
            llm_timeout=_int("LLM_TIMEOUT", 180),
            feasibility_threshold=_int("FEASIBILITY_THRESHOLD", 60),
            worker_enabled=_bool("WORKER_ENABLED", True),
            worker_poll_interval=max(0.1, _float("WORKER_POLL_INTERVAL", 1.0)),
            verify_timeout=_int("VERIFY_TIMEOUT", 300),
            auto_apply_patch=_bool("AUTO_APPLY_PATCH", False),
            knowledge_ttl_days=_int("KNOWLEDGE_TTL_DAYS", 90),
            obsidian_vault_path=obsidian,
            obsidian_folder=os.getenv("OBSIDIAN_FOLDER", "WorkFlow-Codex").strip() or "WorkFlow-Codex",
            max_repo_files=_int("MAX_REPO_FILES", 400),
            max_context_chars=_int("MAX_CONTEXT_CHARS", 60000),
            max_patch_chars=_int("MAX_PATCH_CHARS", 120000),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
