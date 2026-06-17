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

resource "google_project" "uc3" {
  name            = "ARIA UC3 GCP Native"
  project_id      = var.project_id
  billing_account = var.billing_account
}

resource "google_project_service" "apis" {
  for_each = toset([
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "dataflow.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",            # UC3 Pub/Sub incident scenarios
    "cloudfunctions.googleapis.com",    # UC3 Cloud Function incident scenarios
    "monitoring.googleapis.com",        # S6 Cloud Monitoring metrics-based signals
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "logging.googleapis.com",
  ])
  project            = google_project.uc3.project_id
  service            = each.value
  disable_on_destroy = false
  depends_on         = [google_project.uc3]
}

# Grant ARIA GKE SA cross-project access to UC3 log and monitoring stores
# logging.viewer — GCPLogConnector reads Cloud Logging by resource.type (S6)
# monitoring.viewer — S6 Cloud Monitoring metrics-based signals (Pub/Sub backlog, CF error rate)
resource "google_project_iam_member" "aria_gke_access" {
  for_each = toset([
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/storage.objectViewer",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
  ])
  project = google_project.uc3.project_id
  role    = each.value
  member  = "serviceAccount:${var.aria_gke_node_sa_email}"
}

# ── GCS Buckets ───────────────────────────────────────────────────────────────
resource "google_storage_bucket" "logs_raw" {
  project       = google_project.uc3.project_id
  name          = "${var.project_id}-logs-raw"
  location      = var.region
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
  lifecycle_rule { condition { age = 30 }; action { type = "Delete" } }
}

resource "google_storage_bucket" "dataflow_temp" {
  project       = google_project.uc3.project_id
  name          = "${var.project_id}-dataflow-temp"
  location      = var.region
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
}

# GCS folder placeholders for each simulated service type
resource "google_storage_bucket_object" "log_folders" {
  for_each = toset([
    "gcp/pubsub/.keep",
    "gcp/bigquery/.keep",
    "gcp/dataflow/.keep",
    "gcp/cloudrun/.keep",
    "gcp/composer/.keep",
  ])
  bucket  = google_storage_bucket.logs_raw.name
  name    = each.value
  content = "placeholder"
}

# ── BigQuery ──────────────────────────────────────────────────────────────────
resource "google_bigquery_dataset" "aria_logs" {
  project    = google_project.uc3.project_id
  dataset_id = "aria_logs"
  location   = var.region
}

resource "google_bigquery_table" "platform_logs" {
  project    = google_project.uc3.project_id
  dataset_id = google_bigquery_dataset.aria_logs.dataset_id
  table_id   = "platform_logs"
  deletion_protection = false

  time_partitioning { type = "DAY"; field = "timestamp" }
  clustering = ["platform_tag", "service_name", "level"]

  schema = jsonencode([
    { name = "timestamp",    type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "level",        type = "STRING",    mode = "REQUIRED" },
    { name = "service_name", type = "STRING",    mode = "REQUIRED" },
    { name = "resource_id",  type = "STRING",    mode = "REQUIRED" },
    { name = "message",      type = "STRING",    mode = "REQUIRED" },
    { name = "platform_tag", type = "STRING",    mode = "REQUIRED" },
    { name = "project_id",   type = "STRING",    mode = "NULLABLE" },
    { name = "region",       type = "STRING",    mode = "NULLABLE" },
    { name = "error_code",   type = "STRING",    mode = "NULLABLE" },
    { name = "severity",     type = "INTEGER",   mode = "NULLABLE" },
  ])
}

resource "google_bigquery_dataset" "aria_ops" {
  project    = google_project.uc3.project_id
  dataset_id = "aria_ops"
  location   = var.region
}

resource "google_bigquery_table" "job_metrics" {
  project    = google_project.uc3.project_id
  dataset_id = google_bigquery_dataset.aria_ops.dataset_id
  table_id   = "job_metrics"
  deletion_protection = false

  schema = jsonencode([
    { name = "job_id",      type = "STRING",    mode = "REQUIRED" },
    { name = "job_type",    type = "STRING",    mode = "REQUIRED" },
    { name = "status",      type = "STRING",    mode = "REQUIRED" },
    { name = "started_at",  type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "finished_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "error_msg",   type = "STRING",    mode = "NULLABLE" },
    { name = "resource_id", type = "STRING",    mode = "NULLABLE" },
  ])
}
