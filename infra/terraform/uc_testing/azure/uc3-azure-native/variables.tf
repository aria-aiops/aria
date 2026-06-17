variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — get from: az account show --query id"
}

variable "resource_group_name" {
  type        = string
  default     = "aria-uc3-rg"
  description = "Name of the Azure resource group for UC3"
}

variable "location" {
  type        = string
  default     = "West Europe"
  description = "Azure region for all UC3 resources"
}

variable "storage_account_name" {
  type        = string
  default     = "ariauc3native"
  description = "Storage account name (must be globally unique, 3-24 chars, lowercase alphanumeric)"
}

variable "log_analytics_workspace_resource_id" {
  type        = string
  description = "Full ARM resource ID of the Log Analytics workspace created in UC2. Get from: cd ../uc2-hdinsight && terraform output log_analytics_workspace_resource_id"
}

variable "synapse_sql_password" {
  type        = string
  sensitive   = true
  description = "SQL admin password for the Synapse workspace (8+ chars, mixed case, digit, special char)"
}
