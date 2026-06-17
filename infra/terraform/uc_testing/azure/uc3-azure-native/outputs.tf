output "eventhub_namespace_name" {
  description = "Event Hubs namespace name — use as ServiceNow CMDB CI name for UC3 smoke incident"
  value       = azurerm_eventhub_namespace.uc3.name
}

output "synapse_workspace_name" {
  description = "Synapse Analytics workspace name"
  value       = azurerm_synapse_workspace.uc3.name
}

output "resource_group_name" {
  description = "Resource group — use for az CLI teardown"
  value       = azurerm_resource_group.uc3.name
}
