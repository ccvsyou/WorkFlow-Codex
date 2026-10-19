from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .utils import parse_json_response

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mock = settings.is_mock
        self.provider = "mock" if self.mock else settings.llm_provider
        self.model = "mock" if self.mock else settings.llm_model

    async def complete_json(self, system: str, user: str, schema_name: str) -> LLMResult:
        if self.mock:
            data = self._mock_response(schema_name, user)
            return LLMResult(data=data, provider="mock", model="mock", tokens_in=0, tokens_out=0)

        if not self.settings.llm_api_key:
            raise LLMError("使用非模拟模式时必须配置 LLM_API_KEY")

        base = self.settings.llm_base_url.rstrip("/")
        url = base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.llm_temperature,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout) as client:
            response = await self._post(client, url, headers, payload)
            if response.status_code == 400:
                # Some OpenAI-compatible providers don't implement response_format.
                payload.pop("response_format", None)
                response = await self._post(client, url, headers, payload)
            if response.status_code >= 400:
                raise LLMError(f"模型服务返回 HTTP {response.status_code}：{response.text[:800]}")
            body = response.json()

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("模型响应缺少消息内容") from exc
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        try:
            data = parse_json_response(content)
        except ValueError as exc:
            raise LLMError(f"模型返回了非 JSON 内容：{content[:800]}") from exc

        usage = body.get("usage") or {}
        return LLMResult(
            data=data,
            provider=self.settings.llm_provider,
            model=body.get("model") or self.settings.llm_model,
            tokens_in=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        try:
            return await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"模型请求失败：{exc}") from exc

    def _mock_response(self, schema_name: str, user: str) -> dict[str, Any]:
        if schema_name == "clarify":
            # The prompt always carries a "用户回答" object. A non-empty object resumes the interview.
            answered = not bool(re.search(r'"用户回答"\s*:\s*\{\s*\}', user))
            if not answered:
                return {
                    "understanding": "我理解你想在现有项目中完成一项可验证的功能改动，但还需要确认目标用户与验收标准。",
                    "questions": [
                        {
                            "id": "target_users",
                            "question": "这项改动主要服务哪类用户或角色？",
                            "why": "目标用户决定交互和权限边界。",
                            "required": True,
                        },
                        {
                            "id": "acceptance",
                            "question": "用什么可观察的结果判定它已经完成？",
                            "why": "验收标准会固化为测试与交付检查点。",
                            "required": True,
                        },
                    ],
                    "ready_to_plan": False,
                    "spec": {
                        "goal": "完成需求描述中的功能改动，并通过仓库验证命令。",
                        "target_users": "",
                        "scope": ["按技术方案完成最小可用垂直切片", "补充关键路径验证"],
                        "out_of_scope": ["大规模架构重构", "跨仓库联动"],
                        "business_rules": [],
                        "acceptance_criteria": [],
                        "constraints": ["只改方案列出的文件", "不引入密钥或敏感信息"],
                    },
                    "assumptions": ["当前仓库具备可运行的本地验证命令。"],
                }
            return {
                "understanding": "需求目标、目标用户和验收标准已经明确，可以进入可行性评估。",
                "questions": [],
                "ready_to_plan": True,
                "spec": {
                    "goal": "完成需求描述中的功能改动，并通过仓库验证命令。",
                    "target_users": "需求提出者指定的用户或角色",
                    "scope": ["按垂直切片完成最小功能", "补充关键路径测试或验证说明"],
                    "out_of_scope": ["大规模架构重构", "跨仓库联动", "资金/支付/核心安全链路"],
                    "business_rules": ["改动范围必须与方案一致"],
                    "acceptance_criteria": ["补丁可被 Git 应用", "仓库验证命令通过", "无关键审查问题"],
                    "constraints": ["简单优先", "只碰方案列出的文件", "不写入敏感信息"],
                },
                "assumptions": ["用户在回答中确认的验收标准优先于模型推断。"],
            }
        if schema_name == "feasibility":
            return {
                "clarity": 86,
                "repo_match": 82,
                "scope_control": 80,
                "technical_simplicity": 84,
                "dependency_readiness": 78,
                "risk_control": 82,
                "delivery_shape": 86,
                "total": 83,
                "reasons": {
                    "clarity": "目标用户与验收标准已明确。",
                    "repo_match": "仓库为单仓本地项目，适合隔离工作区。",
                    "scope_control": "按垂直切片拆解，改动范围可控。",
                    "technical_simplicity": "模拟模式使用最小补丁验证流水线。",
                    "dependency_readiness": "不新增外部中间件。",
                    "risk_control": "不涉及资金、支付或核心安全链路。",
                    "delivery_shape": "输出分支、补丁与验证结果。",
                },
                "blockers": [],
            }
        if schema_name == "plan":
            return {
                "summary": "以最小垂直切片完成需求：先确认落点，再生成可验证补丁，最后用仓库命令验证。",
                "steps": [
                    {
                        "id": "s1",
                        "title": "建立可验证的最小改动落点",
                        "depends_on": [],
                        "files": ["docs/flowcodex-run.md"],
                        "changes": ["新增运行说明文件，作为模拟模式的隔离验证产物。"],
                        "acceptance": ["文件可被 git apply 创建。"],
                        "verification": "git apply --check 通过",
                    },
                    {
                        "id": "s2",
                        "title": "执行仓库验证",
                        "depends_on": ["s1"],
                        "files": [],
                        "changes": ["在隔离工作区中运行项目验证命令。"],
                        "acceptance": ["验证命令退出码为 0。"],
                        "verification": "项目验证命令",
                    },
                    {
                        "id": "s3",
                        "title": "审查与交付",
                        "depends_on": ["s1", "s2"],
                        "files": [],
                        "changes": ["审查补丁、记录残余风险并输出交付摘要。"],
                        "acceptance": ["无关键问题，补丁与验证结果可下载。"],
                        "verification": "审查通过且验证通过",
                    },
                ],
                "risks": ["模拟模式不会生成真实业务代码，只用于验证流水线。"],
                "assumptions": ["项目仓库已初始化并至少有一个提交。"],
                "unresolved_questions": [],
                "self_challenge": [
                    {
                        "severity": "suggestion",
                        "title": "模拟结果不代表真实编码能力",
                        "detail": "接入真实模型后应使用同一流水线验证补丁。",
                    }
                ],
                "checkpoints": ["s1 后补丁可应用", "s2 后验证命令通过", "s3 后交付摘要完整"],
            }
        if schema_name == "code":
            patch = (
                "diff --git a/docs/flowcodex-run.md b/docs/flowcodex-run.md\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/docs/flowcodex-run.md\n"
                "@@ -0,0 +1,4 @@\n"
                "+# FlowCodex 运行记录\n"
                "+\n"
                "+此文件由 FlowCodex 模拟模式在隔离工作区中生成。\n"
                "+生产环境请切换真实模型并审查实际业务补丁。\n"
            )
            return {
                "patch": patch,
                "summary": "新增一个隔离验证用的运行说明文件；未触碰业务代码。",
                "files_touched": ["docs/flowcodex-run.md"],
                "discovered_not_touched": [],
                "test_notes": ["模拟模式依赖项目配置的验证命令进行验证。"],
            }
        if schema_name == "review":
            return {
                "verdict": "pass",
                "summary": "补丁为新增文档，无关键问题；可以进入验证与交付。",
                "issues": [
                    {
                        "severity": "suggestion",
                        "title": "模拟产物仅用于流水线验证",
                        "file": "docs/flowcodex-run.md",
                        "detail": "生产环境应切换到真实模型并审查实际业务补丁。",
                        "security_rule": "",
                    }
                ],
                "security_checks": {
                    "auth_from_session": True,
                    "auth_middleware": True,
                    "parameterized_sql": True,
                    "no_hardcoded_secrets": True,
                    "input_validation": True,
                    "resource_ownership": True,
                },
                "residual_risks": ["模拟模式不验证真实业务逻辑。"],
            }
        if schema_name == "distill":
            return {
                "summary": "本次运行验证了隔离工作区、补丁审查与验证命令的闭环。",
                "candidates": [
                    {
                        "kind": "lesson",
                        "title": "先用隔离工作区验证补丁，再决定是否提交",
                        "content": "编码智能体的产物应先在 Git 隔离工作区中应用并运行验证命令；通过审查和验证后再提交，避免污染主工作区。",
                        "tags": ["Git", "隔离工作区", "安全", "交付"],
                        "confidence": 0.86,
                        "ttl_days": 180,
                    }
                ],
            }
        raise LLMError(f"未知的模拟响应类型：{schema_name}")
