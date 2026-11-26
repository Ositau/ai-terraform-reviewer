"""Unit tests: no Azure needed. The AI is replaced by a fake client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from reviewer import __main__ as cli
from reviewer import ai, plan, rules
from reviewer.report import MARKER, combine, markdown

CASES = Path(__file__).parent.parent / "eval" / "cases"
EXPECTED = json.loads((CASES.parent / "expected.json").read_text(encoding="utf-8"))


def fake_client(answer: dict, prompt_tokens: int = 900, completion_tokens: int = 200):
    """Stands in for AzureOpenAI and records the prompt it was sent."""
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        msg = SimpleNamespace(content=json.dumps(answer))
        usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage, model="gpt-4o-mini")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, sent


LOW = {"risk": "low", "summary": "Routine.", "changes": [], "concerns": [], "questions": []}


def test_every_case_has_an_expectation():
    assert sorted(p.stem for p in CASES.glob("*.json")) == sorted(EXPECTED)


def test_sensitive_values_never_reach_the_prompt():
    changes = plan.load(CASES / "vm-password-login.json")
    prompt = ai.build_prompt(changes, rules.run(changes))
    assert '"x"' not in prompt and "(sensitive)" in prompt


def test_unknown_values_are_marked():
    changes = plan.load(CASES / "nsg-ssh-from-internet.json")
    assert changes[0].after["id"] == "(known after apply)"


def test_update_lists_only_what_changed():
    changes = plan.load(CASES / "safe-tag-update.json")
    assert list(changes[0].changed) == ["tags"]


def test_replace_is_recognised():
    assert plan.load(CASES / "replace-sql-database.json")[0].action == "replace"


@pytest.mark.parametrize("name", [n for n, e in EXPECTED.items() if e["kind"] == "rule"])
def test_rules_alone_catch_the_obvious_dangers(name):
    changes = plan.load(CASES / f"{name}.json")
    found = rules.run(changes)
    assert rules.worst(*(f.severity for f in found)) == EXPECTED[name]["min"]
    assert {f.address for f in found} >= set(EXPECTED[name]["must_flag"])


@pytest.mark.parametrize("name", [n for n, e in EXPECTED.items() if e["kind"] == "routine"])
def test_rules_stay_quiet_on_routine_changes(name):
    assert rules.run(plan.load(CASES / f"{name}.json")) == []


def test_ai_cannot_lower_a_rule_finding():
    changes = plan.load(CASES / "delete-resource-group.json")
    findings = rules.run(changes)
    client, _ = fake_client(LOW)
    result = combine(changes, findings, ai.review(changes, findings, client=client))
    assert result.ai_risk == "low" and result.risk == "high"


def test_ai_can_raise_the_risk():
    changes = plan.load(CASES / "webapp-https-off.json")
    answer = {**LOW, "risk": "high", "concerns": [
        {"address": "azurerm_linux_web_app.shop", "severity": "high", "reason": "HTTPS is no longer enforced."}]}
    client, _ = fake_client(answer)
    result = combine(changes, [], ai.review(changes, [], client=client))
    assert result.risk == "high"
    assert "HTTPS is no longer enforced" in markdown(result)


def test_prompt_is_structured_output_at_temperature_zero():
    changes = plan.load(CASES / "safe-tag-update.json")
    client, sent = fake_client(LOW)
    ai.review(changes, [], client=client)
    assert sent["temperature"] == 0
    assert sent["response_format"]["json_schema"]["strict"] is True


def test_comment_has_marker_counts_and_approval_line():
    changes = plan.load(CASES / "delete-resource-group.json")
    md = markdown(combine(changes, rules.run(changes), None))
    assert md.startswith(MARKER)
    assert "0 to create, 0 to update, 0 to replace, 1 to delete" in md
    assert "needs explicit approval" in md
    assert "AI review was not available" in md


def test_empty_plan_is_low_risk(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"resource_changes": []}), encoding="utf-8")
    assert cli.main([str(p), "--no-ai"]) == 0


def test_exit_code_2_stops_the_pipeline_on_high_risk(tmp_path):
    out = tmp_path / "review.md"
    code = cli.main([str(CASES / "owner-on-subscription.json"), "--no-ai", "--out", str(out)])
    assert code == 2 and "HIGH" in out.read_text(encoding="utf-8")


def test_fail_on_never_only_reports(tmp_path):
    assert cli.main([str(CASES / "owner-on-subscription.json"), "--no-ai", "--fail-on", "never"]) == 0


def test_ai_failure_falls_back_to_rules(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(ai, "review", boom)
    out = tmp_path / "review.md"
    code = cli.main([str(CASES / "nsg-ssh-from-internet.json"), "--out", str(out)])
    assert code == 2 and "AI review was not available" in out.read_text(encoding="utf-8")
