from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str = "") -> str:
    value = secrets.token_hex(8)
    return f"{prefix}{value}" if prefix else value


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def clip(value: str | None, limit: int = 20000) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n...[truncated]..."


def slugify(value: str, limit: int = 48) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.strip(), flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return (text[:limit] or "run").strip("-_")


def parse_json_response(text: str) -> dict[str, Any]:
    """Parse a strict or fenced JSON object returned by an LLM."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(cleaned[start : end + 1])
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    raise ValueError("LLM response did not contain a JSON object")
