"""Ask Azure OpenAI to review the plan.

Sign-in uses Microsoft Entra ID (DefaultAzureCredential): in a pipeline that's the workload
identity of the service connection or GitHub OIDC, on a laptop it's `az login`. No API keys.
The model must answer in a fixed JSON shape (structured output), so the result can be checked
and acted on by code, not just read.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .plan import Change
from .rules import Finding

SYSTEM = """You review Terraform plans for Azure before they are applied.
You are given the planned changes (secrets already removed) and the findings of fixed safety checks.

Your job:
1. Explain in plain English what will change, one short line per resource. No jargon where a plain word works.
2. Judge the overall risk:
   - high: data loss, an outage, exposing something to the internet, weakening security or identity, or a large cost increase
   - medium: something a reviewer should look at before approving (behaviour change, moderate cost, access change)
   - low: routine changes such as tags, new resources with sensible settings, small scaling
3. List specific concerns. Each must name the resource address and say why it matters. Include concerns the safety checks
   did not mention, such as cost jumps from larger SKUs or VM sizes, HTTPS or authentication being turned off, or
   settings that only make sense in a test environment. Don't repeat a concern that adds nothing.
4. Ask the author up to three short questions a careful reviewer would ask. Use an empty list if none are needed.

Only judge what is in the plan. Don't invent resources or values. Values shown as "(known after apply)" are not yet known."""

SCHEMA: dict[str, Any] = {
    "name": "plan_review",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["risk", "summary", "changes", "concerns", "questions"],
        "properties": {
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary": {"type": "string", "description": "Two or three sentences for the pull request reviewer."},
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["address", "explanation"],
                    "properties": {"address": {"type": "string"}, "explanation": {"type": "string"}},
                },
            },
            "concerns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["address", "severity", "reason"],
                    "properties": {
                        "address": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "reason": {"type": "string"},
                    },
                },
            },
            "questions": {"type": "array", "items": {"type": "string"}},
        },
    },
}


@dataclass
class AiReview:
    risk: str
    summary: str
    changes: list[dict[str, str]] = field(default_factory=list)
    concerns: list[Finding] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


def _client():
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        azure_ad_token_provider=token,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


def build_prompt(changes: list[Change], findings: list[Finding]) -> str:
    return json.dumps(
        {
            "planned_changes": [c.to_prompt() for c in changes],
            "safety_check_findings": [{"address": f.address, "severity": f.severity, "reason": f.reason} for f in findings],
        },
        indent=1,
        default=str,
    )


def review(changes: list[Change], findings: list[Finding], client=None) -> AiReview:
    client = client or _client()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=deployment,
        temperature=0,
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(changes, findings)}],
    )
    data = json.loads(resp.choices[0].message.content)
    usage = getattr(resp, "usage", None)
    return AiReview(
        risk=data["risk"],
        summary=data["summary"],
        changes=data["changes"],
        concerns=[Finding(c["address"], c["severity"], c["reason"], source="ai") for c in data["concerns"]],
        questions=data["questions"][:3],
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        model=getattr(resp, "model", deployment) or deployment,
    )
