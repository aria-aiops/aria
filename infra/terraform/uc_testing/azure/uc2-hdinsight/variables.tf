variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — get from: az account show --query id"
}

variable "resource_group_name" {
  type        = string
  default     = "aria-uc2-rg"
  description = "Name of the Azure resource group for UC2"
}

variable "location" {
  type        = string
  default     = "West Europe"
  description = "Azure region for all UC2 resources"
}

variable "storage_account_name" {
  type        = string
  default     = "ariauc2logs"
  description = "Storage account name for HDInsight (must be globally unique, 3-24 chars, lowercase)"
}

variable "aria_runner_object_id" {
  type        = string
  description = "Object ID of the identity running ARIA locally — grants Log Analytics Reader. Get from: az ad signed-in-user show --query id -o tsv"
}

variable "aria_ssh_public_key" {
  type        = string
  description = "ED25519 public key for SSH access to cluster nodes — can reuse the UC1 key"
}

variable "hdinsight_gateway_password" {
  type        = string
  sensitive   = true
  description = "Password for the HDInsight Ambari gateway UI (must be 10+ chars with mixed case, digit, and special char)"
}
