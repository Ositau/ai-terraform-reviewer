"""Fixed safety checks that run before the AI.

The AI explains a plan and spots things no rule anticipates, but it can miss something obvious.
These checks guarantee the dangerous basics are always flagged: the final risk is never lower
than what the rules found, whatever the AI says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .plan import Change

LEVELS = ["low", "medium", "high"]


def worst(*levels: str) -> str:
    return max(levels, key=LEVELS.index) if levels else "low"


@dataclass
class Finding:
    address: str
    severity: str  # low, medium, high
    reason: str
    source: str = "rule"


# Resources that hold data or identity: deleting or replacing them loses something you can't get back.
STATEFUL = {
    "azurerm_resource_group",
    "azurerm_storage_account",
    "azurerm_storage_container",
    "azurerm_key_vault",
    "azurerm_key_vault_secret",
    "azurerm_mssql_server",
    "azurerm_mssql_database",
    "azurerm_postgresql_flexible_server",
    "azurerm_mysql_flexible_server",
    "azurerm_cosmosdb_account",
    "azurerm_managed_disk",
    "azurerm_log_analytics_workspace",
    "azurerm_container_registry",
    "azurerm_cognitive_account",
    "azurerm_search_service",
    "azurerm_user_assigned_identity",
    "azuread_application",
    "azuread_group",
}

OPEN_SOURCES = {"*", "0.0.0.0/0", "0.0.0.0", "internet", "any"}
ADMIN_PORTS = {"22", "3389", "*", "1433", "3306", "5432"}
BROAD_ROLES = {"owner", "user access administrator", "contributor", "role based access control administrator"}


def _after(c: Change, key: str, default: Any = None) -> Any:
    return (c.after or {}).get(key, default)


def _became(c: Change, key: str, value: Any) -> bool:
    """True if this change sets `key` to `value` (on create, or when it changes to it)."""
    if c.action == "create":
        return _after(c, key) == value
    d = c.changed.get(key)
    return bool(d) and d.get("after") == value


def check_delete(c: Change) -> list[Finding]:
    if c.action not in ("delete", "replace"):
        return []
    verb = "deleted" if c.action == "delete" else "destroyed and recreated"
    if c.type in STATEFUL:
        return [Finding(c.address, "high", f"This resource holds data or identity and will be {verb}. Anything in it is lost.")]
    return [Finding(c.address, "medium", f"This resource will be {verb}.")]


def check_nsg(c: Change) -> list[Finding]:
    if c.type != "azurerm_network_security_rule" or c.action == "delete":
        return []
    if str(_after(c, "direction", "")).lower() != "inbound" or str(_after(c, "access", "")).lower() != "allow":
        return []
    src = {str(_after(c, "source_address_prefix", "")).lower()} | {str(s).lower() for s in _after(c, "source_address_prefixes", []) or []}
    ports = {str(_after(c, "destination_port_range", ""))} | {str(p) for p in _after(c, "destination_port_ranges", []) or []}
    if src & OPEN_SOURCES and ports & ADMIN_PORTS:
        return [Finding(c.address, "high", f"Opens port(s) {', '.join(sorted(p for p in ports if p))} to the whole internet.")]
    if src & OPEN_SOURCES:
        return [Finding(c.address, "medium", "Allows inbound traffic from the whole internet.")]
    return []


def check_storage(c: Change) -> list[Finding]:
    if c.type != "azurerm_storage_account" or c.action == "delete":
        return []
    out = []
    if _became(c, "allow_nested_items_to_be_public", True):
        out.append(Finding(c.address, "high", "Lets containers in this storage account be made public."))
    if _became(c, "shared_access_key_enabled", True):
        out.append(Finding(c.address, "medium", "Turns on storage account keys (shared key access)."))
    tls = _after(c, "min_tls_version")
    if tls in ("TLS1_0", "TLS1_1") and (c.action == "create" or "min_tls_version" in c.changed):
        out.append(Finding(c.address, "high", f"Allows old, insecure TLS ({tls})."))
    return out


def check_public_access(c: Change) -> list[Finding]:
    if c.action == "delete" or not c.type.startswith("azurerm_"):
        return []
    if _became(c, "public_network_access_enabled", True) and c.type != "azurerm_storage_account":
        return [Finding(c.address, "medium", "Turns on public network access.")]
    return []


def check_sql_firewall(c: Change) -> list[Finding]:
    if not c.type.endswith("firewall_rule") or c.action == "delete":
        return []
    if _after(c, "start_ip_address") == "0.0.0.0" and _after(c, "end_ip_address") == "255.255.255.255":
        return [Finding(c.address, "high", "Database firewall rule allows every IP address on the internet.")]
    return []


def check_role(c: Change) -> list[Finding]:
    if c.type != "azurerm_role_assignment" or c.action == "delete":
        return []
    role = str(_after(c, "role_definition_name", "")).lower()
    scope = str(_after(c, "scope", ""))
    if role in BROAD_ROLES:
        at_sub = scope.count("/") <= 2  # "/subscriptions/<id>"
        sev = "high" if at_sub or role in ("owner", "user access administrator") else "medium"
        where = "the whole subscription" if at_sub else "this scope"
        return [Finding(c.address, sev, f"Gives the broad role '{_after(c, 'role_definition_name')}' on {where}.")]
    return []


def check_local_auth(c: Change) -> list[Finding]:
    if c.action == "delete":
        return []
    for key in ("local_auth_enabled", "local_authentication_enabled", "admin_enabled"):
        if _became(c, key, True) and c.action != "create":
            return [Finding(c.address, "medium", f"Switches on key/password-based access ({key}).")]
    return []


RULES: list[Callable[[Change], list[Finding]]] = [
    check_delete,
    check_nsg,
    check_storage,
    check_public_access,
    check_sql_firewall,
    check_role,
    check_local_auth,
]


def run(changes: list[Change]) -> list[Finding]:
    return [f for c in changes for rule in RULES for f in rule(c)]
