from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db
from ..schemas import KnowledgeCandidate
from ..utils import dumps, loads, new_id, now_iso


def _tokens(text: str) -> set[str]:
    lower = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_./-]{2,}", lower))
    chinese = re.findall(r"[\u4e00-\u9fff]+", lower)
    for block in chinese:
        if len(block) == 1:
            words.add(block)
        else:
            words.update(block[index : index + 2] for index in range(len(block) - 1))
    return words


def serialize(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = loads(item.pop("tags_json", "[]"), [])
    return item


def get(knowledge_id: str) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,))
    return serialize(row) if row else None


def list_knowledge(
    project_id: str | None = None,
    status: str | None = None,
    include_expired: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if not include_expired:
        clauses.append("expires_at > ?")
        params.append(now_iso())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.query_all(f"SELECT * FROM knowledge{where} ORDER BY updated_at DESC LIMIT ?", (*params, limit))
    return [serialize(row) for row in rows]


def create_knowledge(
    project_id: str,
    kind: str,
    title: str,
    content: str,
    tags: Iterable[str] = (),
    confidence: float = 0.7,
    ttl_days: int = 90,
    status: str = "accepted",
    source_run_id: str = "",
    knowledge_id: str | None = None,
) -> dict[str, Any]:
    kid = knowledge_id or new_id("kb_")
    now = now_iso()
    expires = (datetime.now(UTC) + timedelta(days=max(1, ttl_days))).replace(microsecond=0).isoformat()
    db.execute(
        """
        INSERT INTO knowledge
        (id, project_id, kind, title, content, tags_json, confidence, ttl_days, expires_at,
         status, source_run_id, created_at, updated_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
        """,
        (
            kid,
            project_id,
            kind,
            title[:200],
            content,
            dumps(list(tags)),
            max(0.0, min(1.0, confidence)),
            max(1, ttl_days),
            expires,
            status,
            source_run_id,
            now,
            now,
        ),
    )
    return get(kid) or {}


def inject(project_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Deterministic pre-injection: score by token overlap, confidence, and recency."""
    rows = db.query_all(
        """
        SELECT * FROM knowledge
        WHERE project_id = ? AND status = 'accepted' AND expires_at > ?
        ORDER BY updated_at DESC LIMIT 200
        """,
        (project_id, now_iso()),
    )
    query_tokens = _tokens(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = serialize(row)
        haystack = " ".join([item["title"], item["content"], " ".join(item["tags"])])
        item_tokens = _tokens(haystack)
        overlap = len(query_tokens & item_tokens)
        union = max(1, len(query_tokens | item_tokens))
        relevance = overlap / union
        score = relevance * 0.7 + float(item["confidence"]) * 0.25 + 0.05
        if overlap or float(item["confidence"]) >= 0.8:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = [item for _, item in scored[:limit]]
    now = now_iso()
    for item in selected:
        db.execute("UPDATE knowledge SET last_used_at = ? WHERE id = ?", (now, item["id"]))
    return selected


def add_feedback(knowledge_id: str, reason: str, run_id: str = "") -> dict[str, Any] | None:
    item = get(knowledge_id)
    if not item:
        return None
    db.execute(
        "INSERT INTO knowledge_feedback (knowledge_id, run_id, reason, created_at) VALUES (?, ?, ?, ?)",
        (knowledge_id, run_id, reason[:2000], now_iso()),
    )
    confidence = max(0.0, float(item["confidence"]) - 0.15)
    status = item["status"]
    if confidence < 0.4:
        status = "pending_review"
    db.execute(
        "UPDATE knowledge SET confidence = ?, status = ?, updated_at = ? WHERE id = ?",
        (confidence, status, now_iso(), knowledge_id),
    )
    return get(knowledge_id)


def review(knowledge_id: str, decision: str, note: str = "") -> dict[str, Any] | None:
    """Human review for pending knowledge. Approve promotes it, reject removes it from injection."""
    item = get(knowledge_id)
    if not item:
        return None
    if decision == "approve":
        status = "accepted"
        confidence = max(0.75, float(item["confidence"]))
    else:
        status = "rejected"
        confidence = float(item["confidence"])
    now = now_iso()
    db.execute(
        "UPDATE knowledge SET status = ?, confidence = ?, updated_at = ? WHERE id = ?",
        (status, confidence, now, knowledge_id),
    )
    db.execute(
        "INSERT INTO knowledge_reviews (knowledge_id, decision, note, created_at) VALUES (?, ?, ?, ?)",
        (knowledge_id, decision, note[:2000], now),
    )
    return get(knowledge_id)


def store_distilled(
    project_id: str,
    run_id: str,
    candidates: Iterable[KnowledgeCandidate | dict[str, Any]],
    default_ttl_days: int = 90,
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = raw if isinstance(raw, KnowledgeCandidate) else KnowledgeCandidate.model_validate(raw)
        status = "accepted" if candidate.confidence >= 0.75 else "pending_review"
        ttl = candidate.ttl_days or default_ttl_days
        item = create_knowledge(
            project_id=project_id,
            kind=candidate.kind,
            title=candidate.title,
            content=candidate.content,
            tags=candidate.tags,
            confidence=candidate.confidence,
            ttl_days=ttl,
            status=status,
            source_run_id=run_id,
        )
        stored.append(item)
    return stored


def expire_stale() -> int:
    now = now_iso()
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE knowledge SET status = 'pending_review', updated_at = ? WHERE status = 'accepted' AND expires_at <= ?",
            (now, now),
        )
        return int(cur.rowcount)
