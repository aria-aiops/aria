terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── Project ───────────────────────────────────────────────────────────────────
resource "google_project" "uc1" {
  name            = "ARIA UC1 Hadoop OnPrem Sim"
  project_id      = var.project_id
  billing_account = var.billing_account
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  project            = google_project.uc1.project_id
  service            = each.value
  disable_on_destroy = false
  depends_on         = [google_project.uc1]
}

# ── VPC ───────────────────────────────────────────────────────────────────────
module "vpc" {
  source           = "../shared/modules/vpc"
  project_id       = google_project.uc1.project_id
  region           = var.region
  network_name     = "aria-uc1-vpc"
  subnet_cidr      = "10.10.0.0/24"
  allowed_ssh_cidr = var.allowed_ssh_cidr
  depends_on       = [google_project_service.apis]
}

# ── Firewall: allow ARIA GKE pod to SSH into cluster nodes ────────────────────
# The GKE node pool SA needs SSH access from the platform project VPC.
# In practice for POC: allow SSH from anywhere within the subnet + the GKE NAT IP.
# For production: use IAP tunnel or VPC peering instead.
resource "google_compute_firewall" "allow_aria_runner_ssh" {
  project = google_project.uc1.project_id
  name    = "aria-uc1-allow-runner-ssh"
  network = module.vpc.network_name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # Allow SSH from GKE cluster egress IP — update after GKE deploy with actual NAT IP
  # For initial setup: use your workstation IP to validate, then restrict
  source_ranges = [var.allowed_ssh_cidr, var.gke_egress_cidr]
  target_tags   = ["aria-node"]
}

# ── Startup script ────────────────────────────────────────────────────────────
locals {
  startup_script = <<-SCRIPT
    #!/bin/bash
    set -euo pipefail

    apt-get update -q
    apt-get install -y openjdk-11-jdk python3 python3-pip wget curl rsyslog openssh-server

    # Ensure SSH is running and configured for key auth
    systemctl enable ssh
    systemctl start ssh

    # Hadoop binaries — for authentic log format
    HADOOP_VERSION="3.3.6"
    wget -q "https://downloads.apache.org/hadoop/common/hadoop-$${HADOOP_VERSION}/hadoop-$${HADOOP_VERSION}.tar.gz" \
      -O /tmp/hadoop.tar.gz
    tar -xzf /tmp/hadoop.tar.gz -C /opt/
    ln -s /opt/hadoop-$${HADOOP_VERSION} /opt/hadoop
    rm /tmp/hadoop.tar.gz

    # Log directory structure — mirrors real CDP layout exactly
    mkdir -p /var/log/hadoop/hdfs /var/log/hadoop/yarn
    mkdir -p /var/log/hive /var/log/spark /var/log/kafka
    mkdir -p /var/log/zookeeper /var/log/oozie /var/log/nifi
    chmod -R 755 /var/log/hadoop /var/log/hive /var/log/spark
    chmod -R 755 /var/log/kafka /var/log/zookeeper /var/log/oozie /var/log/nifi

    echo "ARIA UC1 node ready: $(hostname) at $(date)" >> /var/log/aria-setup.log
  SCRIPT

  nodes = {
    "cdp-master-01"  = { role = "hdfs-namenode,yarn-resourcemanager,hiveserver2", disk_gb = 50,  external_ip = true }
    "cdp-data-01"    = { role = "hdfs-datanode,yarn-nodemanager",                 disk_gb = 100, external_ip = false }
    "cdp-data-02"    = { role = "hdfs-datanode,yarn-nodemanager",                 disk_gb = 100, external_ip = false }
    "cdp-utility-01" = { role = "hive-metastore,spark-history,oozie,hue",        disk_gb = 50,  external_ip = false }
    "cdp-bus-01"     = { role = "kafka,zookeeper,nifi",                           disk_gb = 50,  external_ip = false }
  }
}

# ── Compute Instances ─────────────────────────────────────────────────────────
resource "google_compute_instance" "hadoop_nodes" {
  for_each     = local.nodes
  project      = google_project.uc1.project_id
  name         = each.key
  machine_type = "e2-standard-2"
  zone         = var.zone

  tags = ["aria-node", "aria-uc1"]

  metadata = {
    startup-script    = local.startup_script
    aria-role         = each.value.role
    aria-uc           = "uc1"
    # SSH public key for ARIA container access — set via tfvars
    ssh-keys          = "aria:${var.aria_ssh_public_key}"
  }

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = each.value.disk_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    subnetwork = module.vpc.subnet_self_link
    dynamic "access_config" {
      for_each = each.value.external_ip ? [1] : []
      content {}   # ephemeral external IP
    }
  }

  depends_on = [module.vpc]
}

# ── SSH key secret — ARIA container needs private key to SSH into nodes ────────
resource "google_secret_manager_secret" "aria_ssh_key" {
  project   = google_project.uc1.project_id
  secret_id = "aria-uc1-ssh-private-key"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "aria_ssh_key_value" {
  secret      = google_secret_manager_secret.aria_ssh_key.id
  secret_data = var.aria_ssh_private_key
}
