from __future__ import annotations

from typing import Any

from .utils import loads


RUN_JSON_FIELDS = {
    "clarify": "clarify_json",
    "answers": "answers_json",
    "feasibility": "feasibility_json",
    "context": "context_json",
    "plan": "plan_json",
    "code": "code_json",
    "review": "review_json",
    "verify": "verify_json",
    "deliver": "deliver_json",
    "distill": "distill_json",
}


def project_out(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["allowed_paths"] = loads(item.pop("allowed_paths_json", "[]"), ["."])
    item["auto_apply"] = bool(item.get("auto_apply"))
    item["auto_publish"] = bool(item.get("auto_publish"))
    return item


def requirement_out(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["spec"] = loads(item.pop("spec_json", "{}"), {})
    return item


def run_out(row: Any) -> dict[str, Any]:
    item = dict(row)
    for public_name, column in RUN_JSON_FIELDS.items():
        item[public_name] = loads(item.pop(column, "{}"), {})
    item["plan_approved"] = bool(item.get("plan_approved"))
    return item


def event_out(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = loads(item.pop("payload_json", "{}"), {})
    return item
