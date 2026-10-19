from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _non_blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValueError("不能为空")
    return text


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    repo_path: str = Field(min_length=1)
    default_branch: str = "main"
    verify_command: str = ""
    publish_command: str = ""
    allowed_paths: list[str] = Field(default_factory=lambda: ["."])
    auto_apply: bool | None = None
    auto_publish: bool = False

    _strip_non_blank = field_validator("name", "repo_path")(_non_blank)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    repo_path: str | None = None
    default_branch: str | None = None
    verify_command: str | None = None
    publish_command: str | None = None
    allowed_paths: list[str] | None = None
    auto_apply: bool | None = None
    auto_publish: bool | None = None

    _strip_non_blank = field_validator("name", "repo_path")(_non_blank)


class RequirementCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=160)
    raw_input: str = Field(min_length=1, max_length=30000)
    start_run: bool = True

    _strip_non_blank = field_validator("title", "raw_input")(_non_blank)


class AnswerPayload(BaseModel):
    answers: dict[str, str]


class FeasibilityOverride(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    _strip_non_blank = field_validator("reason")(_non_blank)


class PlanApproval(BaseModel):
    approved: bool = True
    comment: str = Field(default="", max_length=2000)


class PatchApproval(BaseModel):
    apply: bool = True
    comment: str = Field(default="", max_length=2000)


class ReworkRequest(BaseModel):
    note: str = Field(min_length=1, max_length=4000)

    _strip_non_blank = field_validator("note")(_non_blank)


class KnowledgeCreate(BaseModel):
    project_id: str
    kind: str = "lesson"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0, le=1)
    ttl_days: int = Field(default=90, ge=1, le=3650)
    status: Literal["accepted", "pending_review"] = "accepted"

    _strip_non_blank = field_validator("title", "content")(_non_blank)


class KnowledgeFeedback(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    run_id: str = ""

    _strip_non_blank = field_validator("reason")(_non_blank)


class ClarifyQuestion(BaseModel):
    id: str
    question: str
    why: str = ""
    required: bool = True


class SpecDoc(BaseModel):
    goal: str = ""
    target_users: str = ""
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class ClarifyResult(BaseModel):
    understanding: str
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    ready_to_plan: bool = False
    spec: SpecDoc = Field(default_factory=SpecDoc)
    assumptions: list[str] = Field(default_factory=list)


class FeasibilityResult(BaseModel):
    clarity: int = Field(default=0, ge=0, le=100)
    repo_match: int = Field(default=0, ge=0, le=100)
    scope_control: int = Field(default=0, ge=0, le=100)
    technical_simplicity: int = Field(default=0, ge=0, le=100)
    dependency_readiness: int = Field(default=0, ge=0, le=100)
    risk_control: int = Field(default=0, ge=0, le=100)
    delivery_shape: int = Field(default=0, ge=0, le=100)
    total: int = Field(default=0, ge=0, le=100)
    reasons: dict[str, str] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: str
    title: str
    depends_on: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    verification: str = ""

    _strip_id = field_validator("id")(_non_blank)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _strip_dependencies(cls, value):
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]


class PlanFinding(BaseModel):
    severity: Literal["critical", "suggestion"] = "suggestion"
    title: str
    detail: str


class PlanResult(BaseModel):
    summary: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    self_challenge: list[PlanFinding] = Field(default_factory=list)
    checkpoints: list[str] = Field(default_factory=list)


class CodeResult(BaseModel):
    patch: str = ""
    summary: str = ""
    files_touched: list[str] = Field(default_factory=list)
    discovered_not_touched: list[str] = Field(default_factory=list)
    test_notes: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    severity: Literal["critical", "suggestion"] = "suggestion"
    title: str
    file: str = ""
    detail: str = ""
    security_rule: str = ""


class ReviewResult(BaseModel):
    verdict: Literal["pass", "changes_requested"] = "pass"
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)
    security_checks: dict[str, bool] = Field(default_factory=dict)
    residual_risks: list[str] = Field(default_factory=list)


class KnowledgeCandidate(BaseModel):
    kind: str = "lesson"
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0, le=1)
    ttl_days: int = 90


class DistillResult(BaseModel):
    summary: str = ""
    candidates: list[KnowledgeCandidate] = Field(default_factory=list)


class RunView(BaseModel):
    id: str
    requirement_id: str
    project_id: str
    status: str
    stage: str
    created_at: str
    updated_at: str
    data: dict[str, Any] = Field(default_factory=dict)
