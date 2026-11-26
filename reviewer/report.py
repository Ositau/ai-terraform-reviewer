"""Combine the safety checks and the AI review into one result and a pull request comment."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ai import AiReview
from .plan import Change, counts
from .rules import Finding, worst

ICON = {"high": "**HIGH**", "medium": "**MEDIUM**", "low": "LOW"}
MARKER = "<!-- ai-terraform-reviewer -->"  # lets the pipeline find and update its own comment


@dataclass
class Result:
    risk: str
    rule_risk: str
    ai_risk: str | None
    changes: list[Change]
    findings: list[Finding] = field(default_factory=list)
    ai: AiReview | None = None


def combine(changes: list[Change], findings: list[Finding], ai: AiReview | None) -> Result:
    rule_risk = worst(*(f.severity for f in findings)) if findings else "low"
    ai_risk = ai.risk if ai else None
    # The rules set a floor: the AI can raise the risk, never lower it.
    risk = worst(rule_risk, ai_risk) if ai_risk else rule_risk
    if not changes:
        risk = "low"
    return Result(risk, rule_risk, ai_risk, changes, findings, ai)


def _concerns(r: Result) -> list[Finding]:
    """Rule findings first, then AI concerns that add something new."""
    out = list(r.findings)
    seen = {(f.address, f.severity) for f in r.findings}
    for c in r.ai.concerns if r.ai else []:
        if (c.address, c.severity) not in seen or c.severity == "high":
            out.append(c)
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda f: order[f.severity])


def markdown(r: Result, cost_gbp: float | None = None) -> str:
    n = counts(r.changes)
    lines = [MARKER, f"## Terraform plan review: {ICON[r.risk]} risk", ""]
    lines.append(f"**{n['create']} to create, {n['update']} to update, {n['replace']} to replace, {n['delete']} to delete.**")
    lines.append("")
    if not r.changes:
        lines.append("No changes. Nothing to review.")
        return "\n".join(lines)
    if r.ai:
        lines += [r.ai.summary, ""]
    concerns = _concerns(r)
    if concerns:
        lines += ["### Concerns", "", "| Severity | Resource | Why |", "|---|---|---|"]
        for f in concerns:
            who = "safety check" if f.source == "rule" else "AI review"
            lines.append(f"| {ICON[f.severity]} | `{f.address}` | {f.reason} _({who})_ |")
        lines.append("")
    if r.ai and r.ai.changes:
        lines += ["<details><summary>What changes, resource by resource</summary>", ""]
        lines += [f"- `{c['address']}`: {c['explanation']}" for c in r.ai.changes]
        lines += ["", "</details>", ""]
    if r.ai and r.ai.questions:
        lines += ["### Questions for the author", ""] + [f"- {q}" for q in r.ai.questions] + [""]
    if r.risk == "high":
        lines.append("> **This change needs explicit approval before it is applied.**")
        lines.append("")
    foot = "Safety checks always run; the AI can raise the risk but never lower it."
    if r.ai:
        foot += f" Model: {r.ai.model}, {r.ai.prompt_tokens + r.ai.completion_tokens} tokens"
        foot += f" (about £{cost_gbp:.4f})." if cost_gbp is not None else "."
    else:
        foot += " The AI review was switched off or unavailable for this run, so only the safety checks are shown."
    lines.append(f"<sub>{foot}</sub>")
    return "\n".join(lines)
