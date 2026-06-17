output "master_external_ip" {
  description = "Public IP of cdp-master-01 — use for SSH access and ServiceNow CMDB"
  value       = azurerm_public_ip.master.ip_address
}

output "node_internal_ips" {
  description = "Map of node name to private IP — populate ServiceNow CMDB member CI ip_address fields"
  value = {
    for name, _ in local.nodes :
    name => azurerm_network_interface.nodes[name].private_ip_address
  }
}

output "resource_group_name" {
  description = "Resource group containing all UC1 resources — use for az CLI commands and cleanup"
  value       = azurerm_resource_group.uc1.name
}
