terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  features {}
}

# ── Resource Group ─────────────────────────────────────────────────────────────
resource "azurerm_resource_group" "uc3" {
  name     = var.resource_group_name
  location = var.location
}

# ── Storage Account (→ GCS equivalent) ────────────────────────────────────────
resource "azurerm_storage_account" "uc3" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.uc3.name
  location                 = azurerm_resource_group.uc3.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

# GCS folder equivalents for simulated service types
resource "azurerm_storage_container" "log_buckets" {
  for_each             = toset(["gcp-pubsub-sim", "gcp-bigquery-sim", "gcp-dataflow-sim", "gcp-cloudrun-sim"])
  name                 = each.value
  storage_account_name = azurerm_storage_account.uc3.name
}

# ── Event Hubs (→ Pub/Sub equivalent) ─────────────────────────────────────────
resource "azurerm_eventhub_namespace" "uc3" {
  name                = "aria-uc3-events"
  location            = azurerm_resource_group.uc3.location
  resource_group_name = azurerm_resource_group.uc3.name
  sku                 = "Basic"
  capacity            = 1
}

resource "azurerm_eventhub" "uc3" {
  name                = "aria-uc3-topic"
  namespace_name      = azurerm_eventhub_namespace.uc3.name
  resource_group_name = azurerm_resource_group.uc3.name
  partition_count     = 2
  message_retention   = 1
}

# ── Synapse Analytics workspace (→ BigQuery equivalent) ───────────────────────
resource "azurerm_synapse_workspace" "uc3" {
  name                                 = "aria-uc3-synapse"
  resource_group_name                  = azurerm_resource_group.uc3.name
  location                             = azurerm_resource_group.uc3.location
  storage_data_lake_gen2_filesystem_id = azurerm_storage_data_lake_gen2_filesystem.uc3.id
  sql_administrator_login              = "ariaadmin"
  sql_administrator_login_password     = var.synapse_sql_password

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_storage_data_lake_gen2_filesystem" "uc3" {
  name               = "aria-uc3-datalake"
  storage_account_id = azurerm_storage_account.uc3.id
}

# ── Diagnostic Settings → shared Log Analytics workspace ─────────────────────
# Routes Event Hubs operational logs to the workspace created by UC2.
# No application-level log data is seeded — UC3 is designed to return empty
# log evidence, driving confidence_band=LOW in the classifier.
resource "azurerm_monitor_diagnostic_setting" "eventhub_logs" {
  name                       = "aria-uc3-eventhub-diag"
  target_resource_id         = azurerm_eventhub_namespace.uc3.id
  log_analytics_workspace_id = var.log_analytics_workspace_resource_id

  enabled_log {
    category = "OperationalLogs"
  }

  metric {
    category = "AllMetrics"
    enabled  = false
  }
}

resource "azurerm_monitor_diagnostic_setting" "synapse_logs" {
  name                       = "aria-uc3-synapse-diag"
  target_resource_id         = azurerm_synapse_workspace.uc3.id
  log_analytics_workspace_id = var.log_analytics_workspace_resource_id

  enabled_log {
    category = "SynapseRbacOperations"
  }

  metric {
    category = "AllMetrics"
    enabled  = false
  }
}
