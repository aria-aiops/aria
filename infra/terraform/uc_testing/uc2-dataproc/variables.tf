variable "project_id"             { type = string; default = "aria-uc2-dataproc" }
variable "billing_account"         { type = string }
variable "region"                  { type = string; default = "europe-west1" }
variable "zone"                    { type = string; default = "europe-west1-b" }
variable "aria_gke_node_sa_email"  { type = string; description = "GKE node SA email from aria-platform output — needed for cross-project BQ/GCS access grants" }
