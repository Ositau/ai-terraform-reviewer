"""Build the evaluation plans in eval/cases/ (Terraform's plan JSON format) and eval/expected.json.

Each case is a realistic plan a reviewer might see. Some are routine, some are dangerous in an
obvious way (the safety checks should catch them), and some are dangerous in ways no fixed rule
covers (only a good review catches them). Run: python eval/build_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
SUB = "/subscriptions/00000000-0000-0000-0000-000000000000"
RG = f"{SUB}/resourceGroups/rg-shop-prod"
TAGS = {"environment": "prod", "owner": "platform"}


def rc(address, type_, actions, before=None, after=None, after_unknown=None, before_sensitive=None, after_sensitive=None):
    return {
        "address": address,
        "mode": "managed",
        "type": type_,
        "name": address.split(".")[-1],
        "provider_name": "registry.terraform.io/hashicorp/" + ("azuread" if type_.startswith("azuread") else "azurerm"),
        "change": {
            "actions": actions,
            "before": before,
            "after": after,
            "after_unknown": after_unknown or {},
            "before_sensitive": before_sensitive if before_sensitive is not None else ({} if before else False),
            "after_sensitive": after_sensitive if after_sensitive is not None else ({} if after else False),
        },
    }


def plan(*changes):
    return {"format_version": "1.2", "terraform_version": "1.14.7", "resource_changes": list(changes)}


storage = {
    "name": "stshopprod01", "resource_group_name": "rg-shop-prod", "location": "uksouth",
    "account_tier": "Standard", "account_replication_type": "GRS", "min_tls_version": "TLS1_2",
    "allow_nested_items_to_be_public": False, "shared_access_key_enabled": False,
    "public_network_access_enabled": False, "tags": TAGS,
}
vm = {
    "name": "vm-api-prod", "resource_group_name": "rg-shop-prod", "location": "uksouth", "size": "Standard_B2s",
    "admin_username": "azureuser", "disable_password_authentication": True, "tags": TAGS,
}
webapp = {
    "name": "app-shop-prod", "resource_group_name": "rg-shop-prod", "location": "uksouth",
    "https_only": True, "client_certificate_enabled": False, "tags": TAGS,
    "site_config": [{"minimum_tls_version": "1.2", "ftps_state": "Disabled", "always_on": True}],
}
sqldb = {"name": "sqldb-orders", "server_id": f"{RG}/providers/Microsoft.Sql/servers/sql-shop-prod",
         "sku_name": "S1", "collation": "SQL_Latin1_General_CP1_CI_AS", "max_size_gb": 50, "tags": TAGS}

CASES = {
    # ---------- routine ----------
    "safe-tag-update": (
        plan(rc("azurerm_storage_account.data", "azurerm_storage_account", ["update"], storage,
                {**storage, "tags": {**TAGS, "cost_centre": "cc-1042"}})),
        {"min": "low", "max": "low", "must_flag": [], "kind": "routine"},
    ),
    "safe-new-log-workspace": (
        plan(rc("azurerm_log_analytics_workspace.main", "azurerm_log_analytics_workspace", ["create"], None,
                {"name": "log-shop-prod", "resource_group_name": "rg-shop-prod", "location": "uksouth",
                 "sku": "PerGB2018", "retention_in_days": 30, "daily_quota_gb": 1, "tags": TAGS},
                {"id": True, "workspace_id": True, "primary_shared_key": True},
                after_sensitive={"primary_shared_key": True})),
        {"min": "low", "max": "low", "must_flag": [], "kind": "routine"},
    ),
    "safe-app-setting": (
        plan(rc("azurerm_linux_web_app.shop", "azurerm_linux_web_app", ["update"],
                {**webapp, "app_settings": {"FEATURE_NEW_CHECKOUT": "false"}},
                {**webapp, "app_settings": {"FEATURE_NEW_CHECKOUT": "true"}})),
        {"min": "low", "max": "medium", "must_flag": [], "kind": "routine"},
    ),
    "safe-scale-out-small": (
        plan(rc("azurerm_linux_virtual_machine_scale_set.api", "azurerm_linux_virtual_machine_scale_set", ["update"],
                {"name": "vmss-api", "sku": "Standard_B2s", "instances": 2, "tags": TAGS},
                {"name": "vmss-api", "sku": "Standard_B2s", "instances": 3, "tags": TAGS})),
        {"min": "low", "max": "medium", "must_flag": [], "kind": "routine"},
    ),
    # ---------- dangerous, the safety checks should catch these ----------
    "delete-resource-group": (
        plan(rc("azurerm_resource_group.prod", "azurerm_resource_group", ["delete"],
                {"name": "rg-shop-prod", "location": "uksouth", "tags": TAGS}, None)),
        {"min": "high", "max": "high", "must_flag": ["azurerm_resource_group.prod"], "kind": "rule"},
    ),
    "replace-sql-database": (
        plan(rc("azurerm_mssql_database.orders", "azurerm_mssql_database", ["delete", "create"], sqldb,
                {**sqldb, "collation": "Latin1_General_100_CI_AS_SC_UTF8"}, {"id": True})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_mssql_database.orders"], "kind": "rule"},
    ),
    "nsg-ssh-from-internet": (
        plan(rc("azurerm_network_security_rule.ssh", "azurerm_network_security_rule", ["create"], None,
                {"name": "allow-ssh", "priority": 100, "direction": "Inbound", "access": "Allow", "protocol": "Tcp",
                 "source_port_range": "*", "destination_port_range": "22", "source_address_prefix": "*",
                 "destination_address_prefix": "*", "network_security_group_name": "nsg-api-prod",
                 "resource_group_name": "rg-shop-prod"}, {"id": True})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_network_security_rule.ssh"], "kind": "rule"},
    ),
    "storage-public-blobs": (
        plan(rc("azurerm_storage_account.data", "azurerm_storage_account", ["update"], storage,
                {**storage, "allow_nested_items_to_be_public": True, "public_network_access_enabled": True})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_storage_account.data"], "kind": "rule"},
    ),
    "owner-on-subscription": (
        plan(rc("azurerm_role_assignment.ci", "azurerm_role_assignment", ["create"], None,
                {"scope": SUB, "role_definition_name": "Owner", "principal_id": "11111111-1111-1111-1111-111111111111",
                 "principal_type": "ServicePrincipal"}, {"id": True, "role_definition_id": True})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_role_assignment.ci"], "kind": "rule"},
    ),
    "sql-firewall-open": (
        plan(rc("azurerm_mssql_firewall_rule.everyone", "azurerm_mssql_firewall_rule", ["create"], None,
                {"name": "allow-all", "server_id": f"{RG}/providers/Microsoft.Sql/servers/sql-shop-prod",
                 "start_ip_address": "0.0.0.0", "end_ip_address": "255.255.255.255"}, {"id": True})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_mssql_firewall_rule.everyone"], "kind": "rule"},
    ),
    "storage-old-tls": (
        plan(rc("azurerm_storage_account.data", "azurerm_storage_account", ["update"], storage,
                {**storage, "min_tls_version": "TLS1_0"})),
        {"min": "high", "max": "high", "must_flag": ["azurerm_storage_account.data"], "kind": "rule"},
    ),
    # ---------- dangerous in ways no fixed rule covers: the AI has to catch these ----------
    "vm-size-cost-jump": (
        plan(rc("azurerm_linux_virtual_machine.api", "azurerm_linux_virtual_machine", ["update"], vm,
                {**vm, "size": "Standard_E64s_v5"})),
        {"min": "medium", "max": "high", "must_flag": ["azurerm_linux_virtual_machine.api"], "kind": "ai"},
    ),
    "webapp-https-off": (
        plan(rc("azurerm_linux_web_app.shop", "azurerm_linux_web_app", ["update"], webapp,
                {**webapp, "https_only": False})),
        {"min": "medium", "max": "high", "must_flag": ["azurerm_linux_web_app.shop"], "kind": "ai"},
    ),
    "vm-password-login": (
        plan(rc("azurerm_linux_virtual_machine.api", "azurerm_linux_virtual_machine", ["update"], vm,
                {**vm, "disable_password_authentication": False, "admin_password": "x"},
                before_sensitive={}, after_sensitive={"admin_password": True})),
        {"min": "medium", "max": "high", "must_flag": ["azurerm_linux_virtual_machine.api"], "kind": "ai"},
    ),
    "keyvault-network-open": (
        plan(rc("azurerm_key_vault.main", "azurerm_key_vault", ["update"],
                {"name": "kv-shop-prod", "sku_name": "standard", "purge_protection_enabled": True,
                 "network_acls": [{"default_action": "Deny", "bypass": "AzureServices", "ip_rules": []}], "tags": TAGS},
                {"name": "kv-shop-prod", "sku_name": "standard", "purge_protection_enabled": True,
                 "network_acls": [{"default_action": "Allow", "bypass": "AzureServices", "ip_rules": []}], "tags": TAGS})),
        {"min": "medium", "max": "high", "must_flag": ["azurerm_key_vault.main"], "kind": "ai"},
    ),
    "webapp-ftp-on": (
        plan(rc("azurerm_linux_web_app.shop", "azurerm_linux_web_app", ["update"], webapp,
                {**webapp, "site_config": [{"minimum_tls_version": "1.2", "ftps_state": "AllAllowed", "always_on": True}]})),
        {"min": "medium", "max": "high", "must_flag": ["azurerm_linux_web_app.shop"], "kind": "ai"},
    ),
}


def main() -> None:
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    expected = {}
    for name, (p, exp) in CASES.items():
        (out / f"{name}.json").write_text(json.dumps(p, indent=1), encoding="utf-8")
        expected[name] = exp
    # Real plans captured from projects (anonymised) are kept in cases/ too; their expectations live here.
    expected["real-remove-from-hr-group"] = {"min": "medium", "max": "high",
                                             "must_flag": ['azuread_group_member.me["hr"]'], "kind": "ai"}
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
    print(f"{len(CASES)} built cases + 1 real case expected")


if __name__ == "__main__":
    main()
