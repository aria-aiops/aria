variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — get from: az account show --query id"
}

variable "resource_group_name" {
  type        = string
  default     = "aria-uc1-rg"
  description = "Name of the Azure resource group to create for UC1"
}

variable "location" {
  type        = string
  default     = "West Europe"
  description = "Azure region for all UC1 resources"
}

variable "allowed_ssh_cidr" {
  type        = string
  description = "Your workstation IP/32 for SSH access — get from: curl ifconfig.me"
}

variable "aria_ssh_public_key" {
  type        = string
  description = "ED25519 public key for ARIA SSH access — generate with: ssh-keygen -t ed25519 -f ~/.ssh/aria_uc1_key -C aria"
}
