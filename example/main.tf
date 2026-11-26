# A tiny Azure setup to try the reviewer on. It is only ever planned, never applied,
# so it costs nothing. Open a pull request that changes it and watch the review.

terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
  }
}

provider "azurerm" {
  features {}
  # The review identity is read-only, so don't let Terraform try to register providers.
  resource_provider_registrations = "none"
}

variable "location" {
  type    = string
  default = "uksouth"
}

resource "azurerm_resource_group" "demo" {
  name     = "rg-reviewer-example"
  location = var.location
  tags     = { purpose = "ai-terraform-reviewer example" }
}

resource "azurerm_storage_account" "data" {
  name                            = "streviewerexample01"
  resource_group_name             = azurerm_resource_group.demo.name
  location                        = azurerm_resource_group.demo.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
}

resource "azurerm_network_security_group" "web" {
  name                = "nsg-reviewer-example"
  location            = azurerm_resource_group.demo.location
  resource_group_name = azurerm_resource_group.demo.name
}

resource "azurerm_network_security_rule" "https" {
  name                        = "allow-https"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "AzureFrontDoor.Backend"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.demo.name
  network_security_group_name = azurerm_network_security_group.web.name
}
