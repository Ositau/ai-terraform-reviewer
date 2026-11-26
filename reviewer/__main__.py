"""Command line: python -m reviewer plan.json [--out review.md] [--json result.json] [--fail-on high] [--no-ai]

Exit codes: 0 = below the --fail-on level, 2 = at or above it (the pipeline stops for approval),
1 = the reviewer itself failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import ai as ai_mod
from . import plan, rules
from .report import combine, markdown

# gpt-4o-mini, Data Zone deployment, list price per million tokens, converted to GBP (approximate).
PRICE_IN_GBP = 0.165 * 0.79 / 1_000_000
PRICE_OUT_GBP = 0.66 * 0.79 / 1_000_000


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reviewer", description="Review a Terraform plan with safety checks and Azure OpenAI.")
    p.add_argument("plan", help="JSON from: terraform show -json tfplan > plan.json")
    p.add_argument("--out", help="write the pull request comment (Markdown) here")
    p.add_argument("--json", dest="json_out", help="write the full result as JSON here")
    p.add_argument("--fail-on", choices=["medium", "high", "never"], default="high")
    p.add_argument("--no-ai", action="store_true", help="safety checks only, no call to Azure OpenAI")
    a = p.parse_args(argv)

    changes = plan.load(a.plan)
    findings = rules.run(changes)
    review = None
    if changes and not a.no_ai:
        try:
            review = ai_mod.review(changes, findings)
        except Exception as e:  # the safety checks still stand if the AI is unavailable
            print(f"AI review unavailable, continuing with safety checks only: {e}", file=sys.stderr)

    result = combine(changes, findings, review)
    cost = None
    if review:
        cost = review.prompt_tokens * PRICE_IN_GBP + review.completion_tokens * PRICE_OUT_GBP
    md = markdown(result, cost)
    print(md)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(asdict(result), indent=1, default=str), encoding="utf-8")

    levels = {"medium": 1, "high": 2, "never": 99}
    return 2 if rules.LEVELS.index(result.risk) >= levels[a.fail_on] else 0


if __name__ == "__main__":
    sys.exit(main())
