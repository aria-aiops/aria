variable "project_id" { type = string }
variable "region"     { type = string }
variable "network_name" { type = string }
variable "subnet_cidr" { type = string }
variable "allowed_ssh_cidr" { type = string }

resource "google_compute_network" "aria_vpc" {
  project                 = var.project_id
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "aria_subnet" {
  project       = var.project_id
  name          = "${var.network_name}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.aria_vpc.id
}

resource "google_compute_firewall" "allow_internal" {
  project = var.project_id
  name    = "${var.network_name}-allow-internal"
  network = google_compute_network.aria_vpc.name

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = [var.subnet_cidr]
}

resource "google_compute_firewall" "allow_ssh" {
  project = var.project_id
  name    = "${var.network_name}-allow-ssh"
  network = google_compute_network.aria_vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [var.allowed_ssh_cidr]
  target_tags   = ["aria-node"]
}

output "network_name"    { value = google_compute_network.aria_vpc.name }
output "network_self_link" { value = google_compute_network.aria_vpc.self_link }
output "subnet_self_link" { value = google_compute_subnetwork.aria_subnet.self_link }
