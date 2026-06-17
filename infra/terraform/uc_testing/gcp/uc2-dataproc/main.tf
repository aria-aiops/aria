terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google"; version = "~> 5.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project" "uc2" {
  name            = "ARIA UC2 Dataproc"
  project_id      = var.project_id
  billing_account = var.billing_account
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "dataproc.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  project            = google_project.uc2.project_id
  service            = each.value
  disable_on_destroy = false
  depends_on         = [google_project.uc2]
}

# ── Service Account for Dataproc workers ──────────────────────────────────────
resource "google_service_account" "dataproc_sa" {
  project      = google_project.uc2.project_id
  account_id   = "aria-uc2-dataproc-sa"
  display_name = "ARIA UC2 Dataproc Worker SA"
}

resource "google_project_iam_member" "dataproc_sa_roles" {
  for_each = toset([
    "roles/dataproc.worker",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
  ])
  project = google_project.uc2.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.dataproc_sa.email}"
}

# Grant ARIA GKE SA read access to UC2 log stores (cross-project)
# logging.viewer is required — GCPLogConnector reads Cloud Logging, not BQ directly
resource "google_project_iam_member" "aria_gke_access" {
  for_each = toset([
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/storage.objectViewer",
    "roles/logging.viewer",
  ])
  project = google_project.uc2.project_id
  role    = each.value
  member  = "serviceAccount:${var.aria_gke_node_sa_email}"
}

# ── GCS Buckets ───────────────────────────────────────────────────────────────
resource "google_storage_bucket" "logs_raw" {
  project       = google_project.uc2.project_id
  name          = "${var.project_id}-logs-raw"
  location      = var.region
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
  lifecycle_rule { condition { age = 30 }; action { type = "Delete" } }
}

resource "google_storage_bucket" "dataproc_staging" {
  project       = google_project.uc2.project_id
  name          = "${var.project_id}-dataproc-staging"
  location      = var.region
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
}

# ── BigQuery ──────────────────────────────────────────────────────────────────
resource "google_bigquery_dataset" "aria_logs" {
  project    = google_project.uc2.project_id
  dataset_id = "aria_logs"
  location   = var.region
}

resource "google_bigquery_table" "platform_logs" {
  project    = google_project.uc2.project_id
  dataset_id = google_bigquery_dataset.aria_logs.dataset_id
  table_id   = "platform_logs"
  deletion_protection = false

  time_partitioning { type = "DAY"; field = "timestamp" }
  clustering = ["platform_tag", "host", "level"]

  schema = jsonencode([
    { name = "timestamp",    type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "level",        type = "STRING",    mode = "REQUIRED" },
    { name = "component",    type = "STRING",    mode = "REQUIRED" },
    { name = "host",         type = "STRING",    mode = "REQUIRED" },
    { name = "message",      type = "STRING",    mode = "REQUIRED" },
    { name = "platform_tag", type = "STRING",    mode = "REQUIRED" },
    { name = "job_id",       type = "STRING",    mode = "NULLABLE" },
    { name = "cluster_id",   type = "STRING",    mode = "NULLABLE" },
    { name = "severity",     type = "INTEGER",   mode = "NULLABLE" },
  ])
}

# ── Dataproc Cluster ──────────────────────────────────────────────────────────
resource "google_dataproc_cluster" "aria_cluster" {
  project = google_project.uc2.project_id
  name    = "aria-uc2-cluster"
  region  = var.region

  cluster_config {
    staging_bucket = google_storage_bucket.dataproc_staging.name

    gce_cluster_config {
      zone            = var.zone
      service_account = google_service_account.dataproc_sa.email
      service_account_scopes = ["cloud-platform"]
      tags            = ["aria-uc2-node"]
    }

    master_config {
      num_instances = 1
      machine_type  = "n2-standard-2"
      disk_config   { boot_disk_type = "pd-ssd"; boot_disk_size_gb = 50 }
    }

    worker_config {
      num_instances = 2
      machine_type  = "n2-standard-2"
      disk_config   { boot_disk_type = "pd-ssd"; boot_disk_size_gb = 50 }
    }

    software_config {
      image_version = "2.1-debian12"
      override_properties = {
        "yarn:yarn.log-aggregation-enable"         = "true"
        "yarn:yarn.nodemanager.remote-app-log-dir" = "gs://${google_storage_bucket.logs_raw.name}/yarn-logs"
      }
    }

    lifecycle_config {
      idle_delete_ttl = "3600s"   # auto-delete if idle 1h — cost safety net
    }
  }

  depends_on = [
    google_project_service.apis,
    google_storage_bucket.dataproc_staging,
    google_service_account.dataproc_sa,
  ]
}
