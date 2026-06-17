variable "project_id"   { type = string }
variable "account_id"   { type = string; default = "aria-pipeline-sa" }
variable "display_name" { type = string; default = "ARIA Pipeline Service Account" }
variable "roles"        { type = list(string) }

resource "google_service_account" "aria_sa" {
  project      = var.project_id
  account_id   = var.account_id
  display_name = var.display_name
}

resource "google_project_iam_member" "aria_sa_roles" {
  for_each = toset(var.roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.aria_sa.email}"
}

output "email"     { value = google_service_account.aria_sa.email }
output "unique_id" { value = google_service_account.aria_sa.unique_id }
