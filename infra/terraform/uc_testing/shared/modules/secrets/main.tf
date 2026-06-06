variable "project_id"        { type = string }
variable "snow_instance"     { type = string }
variable "snow_user"         { type = string }
variable "snow_password"     { type = string; sensitive = true }
variable "slack_bot_token"   { type = string; sensitive = true }
variable "slack_channel_id"  { type = string }
variable "anthropic_api_key" { type = string; sensitive = true }

locals {
  secrets = {
    "aria-snow-instance"     = var.snow_instance
    "aria-snow-user"         = var.snow_user
    "aria-snow-password"     = var.snow_password
    "aria-slack-bot-token"   = var.slack_bot_token
    "aria-slack-channel-id"  = var.slack_channel_id
    "aria-anthropic-api-key" = var.anthropic_api_key
  }
}

resource "google_secret_manager_secret" "aria_secrets" {
  for_each  = local.secrets
  project   = var.project_id
  secret_id = each.key

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "aria_secret_versions" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.aria_secrets[each.key].id
  secret_data = each.value
}

output "secret_ids" {
  value = { for k, v in google_secret_manager_secret.aria_secrets : k => v.id }
}
