"""Run every case in eval/cases through the safety checks and Azure OpenAI, and check the results.

A case passes when:
- the final risk is within the expected range (not lower than `min`, not higher than `max`), and
- every resource in `must_flag` appears in the concerns (from either the checks or the AI).

It also reports what the AI caught on its own, so you can see whether a new prompt or model is
better or worse. Exit code 1 if any case fails, so CI can stop a prompt or model change.

Run: python eval/run_eval.py            (needs AZURE_OPENAI_ENDPOINT and an Azure sign-in)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reviewer import ai, plan, rules  # noqa: E402
from reviewer.report import combine  # noqa: E402

HERE = Path(__file__).parent
L = rules.LEVELS


def main() -> int:
    expected = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
    rows, tokens, failed = [], 0, 0
    for name, exp in expected.items():
        changes = plan.load(HERE / "cases" / f"{name}.json")
        findings = rules.run(changes)
        t = time.perf_counter()
        review = ai.review(changes, findings)
        secs = time.perf_counter() - t
        result = combine(changes, findings, review)
        flagged = {f.address for f in findings} | {c.address for c in review.concerns}
        ai_flagged = {c.address for c in review.concerns}
        in_range = L.index(exp["min"]) <= L.index(result.risk) <= L.index(exp["max"])
        missing = [a for a in exp["must_flag"] if a not in flagged]
        ok = in_range and not missing
        ai_alone = L.index(review.risk) >= L.index(exp["min"]) and all(a in ai_flagged for a in exp["must_flag"])
        failed += not ok
        tokens += review.prompt_tokens + review.completion_tokens
        rows.append((name, exp["kind"], result.rule_risk, review.risk, result.risk, exp["min"] + "-" + exp["max"],
                     "yes" if ai_alone else "no", "PASS" if ok else "FAIL" + (f" (missing {missing})" if missing else ""), f"{secs:.1f}s"))

    head = ("case", "kind", "rules", "ai", "final", "expected", "ai alone ok", "result", "time")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*head))
    for r in rows:
        print(fmt.format(*r))
    ai_only = [r for r in rows if r[1] == "ai"]
    print()
    print(f"{len(rows) - failed}/{len(rows)} cases passed; {tokens} tokens in total.")
    print(f"Cases no fixed rule covers, caught by the AI: {sum(r[6] == 'yes' for r in ai_only)}/{len(ai_only)}.")
    print(f"Cases where the AI alone was enough: {sum(r[6] == 'yes' for r in rows if r[1] != 'routine')}/"
          f"{sum(1 for r in rows if r[1] != 'routine')}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
