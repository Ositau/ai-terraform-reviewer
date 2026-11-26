"""Read a Terraform plan (the JSON from `terraform show -json tfplan`) into a short list of changes.

Only what changes is kept: for an update, the attributes whose value differs; for a create or
delete, the attributes that matter for review. Values Terraform marks as sensitive are replaced
with "(sensitive)" so secrets never leave the pipeline, not even to the AI model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SENSITIVE = "(sensitive)"
UNKNOWN = "(known after apply)"
MAX_VALUE_CHARS = 300  # long values (policies, scripts) are cut so one resource can't flood the prompt


@dataclass
class Change:
    address: str
    type: str
    action: str  # create, update, delete, replace, read
    changed: dict[str, dict[str, Any]] = field(default_factory=dict)  # attr -> {"before": .., "after": ..}
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    def to_prompt(self) -> dict[str, Any]:
        out: dict[str, Any] = {"address": self.address, "type": self.type, "action": self.action}
        if self.action == "update":
            out["changed_attributes"] = self.changed
        elif self.action == "replace":
            out["changed_attributes"] = self.changed
        elif self.action == "create":
            out["new_values"] = self.after
        elif self.action == "delete":
            out["deleted_values"] = self.before
        return out


def _action(actions: list[str]) -> str:
    if actions in (["delete", "create"], ["create", "delete"]):
        return "replace"
    if actions == ["no-op"]:
        return "no-op"
    return actions[0]


def _clean(value: Any, sensitive: Any, unknown: Any = None) -> Any:
    """Replace sensitive values, mark unknown ones and shorten long ones, recursively."""
    if sensitive is True:
        return SENSITIVE
    if unknown is True:
        return UNKNOWN
    if isinstance(value, dict):
        s = sensitive if isinstance(sensitive, dict) else {}
        u = unknown if isinstance(unknown, dict) else {}
        out = {k: _clean(v, s.get(k), u.get(k)) for k, v in value.items()}
        # Terraform leaves values it can't know yet out of "after" and lists them in after_unknown instead.
        out.update({k: UNKNOWN for k, flag in u.items() if flag is True and k not in out})
        return out
    if isinstance(value, list):
        s = sensitive if isinstance(sensitive, list) else []
        u = unknown if isinstance(unknown, list) else []
        return [_clean(v, s[i] if i < len(s) else None, u[i] if i < len(u) else None) for i, v in enumerate(value)]
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + "…"
    return value


def _drop_empty(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {k: v for k, v in d.items() if v not in (None, [], {}, "")}


def parse(plan: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    for rc in plan.get("resource_changes", []):
        ch = rc.get("change", {})
        action = _action(ch.get("actions", ["no-op"]))
        if action in ("no-op", "read"):
            continue
        before = _clean(ch.get("before"), ch.get("before_sensitive"))
        after = _clean(ch.get("after"), ch.get("after_sensitive"), ch.get("after_unknown"))
        diff: dict[str, dict[str, Any]] = {}
        if action in ("update", "replace") and isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    diff[key] = {"before": before.get(key), "after": after.get(key)}
        changes.append(
            Change(
                address=rc["address"],
                type=rc.get("type", ""),
                action=action,
                changed=diff,
                before=_drop_empty(before) if isinstance(before, dict) else None,
                after=_drop_empty(after) if isinstance(after, dict) else None,
            )
        )
    return changes


def load(path: str | Path) -> list[Change]:
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))


def counts(changes: list[Change]) -> dict[str, int]:
    out = {"create": 0, "update": 0, "replace": 0, "delete": 0}
    for c in changes:
        out[c.action] = out.get(c.action, 0) + 1
    return out
