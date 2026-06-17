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
resource "azurerm_resource_group" "uc2" {
  name     = var.resource_group_name
  location = var.location
}

# ── Storage Account (required by HDInsight) ────────────────────────────────────
resource "azurerm_storage_account" "uc2" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.uc2.name
  location                 = azurerm_resource_group.uc2.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "hdinsight" {
  name                  = "aria-uc2-hdinsight"
  storage_account_name  = azurerm_storage_account.uc2.name
  container_access_type = "private"
}

# ── Log Analytics Workspace ────────────────────────────────────────────────────
# Receives Syslog entries from the HDInsight cluster via Diagnostic Settings.
# AzureLogConnector queries this workspace using AZURE_LOG_WORKSPACE_ID secret.
resource "azurerm_log_analytics_workspace" "aria" {
  name                = "aria-logs-workspace"
  location            = azurerm_resource_group.uc2.location
  resource_group_name = azurerm_resource_group.uc2.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

# Grant the operator/runner identity Log Analytics Reader so AzureLogConnector
# can query from a local development machine (az login credential).
resource "azurerm_role_assignment" "log_reader" {
  scope                = azurerm_log_analytics_workspace.aria.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = var.aria_runner_object_id
}

# ── HDInsight Spark Cluster ────────────────────────────────────────────────────
# Named aria-uc2-cluster to match the GCP Dataproc cluster name in
# the KB and CMDB. AzureLogConnector queries logs by hostname/computer name
# which will match the cluster's head node hostname.
resource "azurerm_hdinsight_spark_cluster" "uc2" {
  name                = "aria-uc2-cluster"
  resource_group_name = azurerm_resource_group.uc2.name
  location            = azurerm_resource_group.uc2.location
  cluster_version     = "5.0"
  tier                = "Standard"

  component_version {
    spark = "3.3"
  }

  gateway {
    username = "ariagw"
    password = var.hdinsight_gateway_password
  }

  storage_account {
    storage_container_id = azurerm_storage_container.hdinsight.id
    storage_account_key  = azurerm_storage_account.uc2.primary_access_key
    is_default           = true
  }

  roles {
    head_node {
      vm_size  = "Standard_D3_V2"    # 4 vCPU, 14 GB RAM
      username = "aria"
      ssh_keys = [var.aria_ssh_public_key]
    }

    worker_node {
      vm_size               = "Standard_D3_V2"
      username              = "aria"
      ssh_keys              = [var.aria_ssh_public_key]
      target_instance_count = 2
    }

    zookeeper_node {
      vm_size  = "Standard_A2_V2"    # minimum ZK size
      username = "aria"
      ssh_keys = [var.aria_ssh_public_key]
    }
  }

  # Auto-delete after 1 hour idle — cost safety net (mirrors GCP Dataproc lifecycle_config)
  # Note: HDInsight does not have native idle-delete; destroy via terraform after smoke test.

  depends_on = [
    azurerm_storage_container.hdinsight,
    azurerm_log_analytics_workspace.aria,
  ]
}

# ── Diagnostic Settings — route HDInsight Syslog → Log Analytics ──────────────
# This is what feeds the Syslog table that AzureLogConnector queries.
resource "azurerm_monitor_diagnostic_setting" "hdinsight_logs" {
  name                       = "aria-uc2-diag"
  target_resource_id         = azurerm_hdinsight_spark_cluster.uc2.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.aria.id

  enabled_log {
    category = "GatewayAuditLogs"
  }

  metric {
    category = "AllMetrics"
    enabled  = false
  }
}
