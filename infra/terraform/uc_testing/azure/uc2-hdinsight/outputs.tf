output "log_analytics_workspace_id" {
  description = "Log Analytics workspace GUID — store as AZURE_LOG_WORKSPACE_ID in Infisical for AzureLogConnector"
  value       = azurerm_log_analytics_workspace.aria.workspace_id
}

output "log_analytics_workspace_resource_id" {
  description = "Full ARM resource ID of the workspace — used by UC3 to share the same workspace"
  value       = azurerm_log_analytics_workspace.aria.id
}

output "cluster_name" {
  description = "HDInsight cluster name — use for ServiceNow CMDB CI and smoke test incident"
  value       = azurerm_hdinsight_spark_cluster.uc2.name
}

output "storage_account_name" {
  description = "Storage account backing the HDInsight cluster"
  value       = azurerm_storage_account.uc2.name
}

output "resource_group_name" {
  description = "Resource group — use for az CLI teardown"
  value       = azurerm_resource_group.uc2.name
}
