variable "project_id"        { type = string; default = "aria-uc1-hadoop" }
variable "billing_account"   { type = string }
variable "region"            { type = string; default = "europe-west1" }
variable "zone"              { type = string; default = "europe-west1-b" }
variable "allowed_ssh_cidr"  { type = string; description = "Your workstation IP/32 for SSH access during setup" }
variable "gke_egress_cidr"   { type = string; description = "GKE cluster NAT gateway IP/32 — get from aria-platform after GKE deploy"; default = "0.0.0.0/0" }
variable "aria_ssh_public_key"  { type = string; description = "Public key injected into VM metadata for ARIA container SSH access" }
variable "aria_ssh_private_key" { type = string; sensitive = true; description = "Private key stored in Secret Manager — mounted into ARIA Job pod" }
