output "project_id"            { value = google_project.uc2.project_id }
output "dataproc_cluster_name" { value = google_dataproc_cluster.aria_cluster.name }
output "gcs_logs_bucket"       { value = google_storage_bucket.logs_raw.name }
output "bq_dataset"            { value = google_bigquery_dataset.aria_logs.dataset_id }
output "bq_table"              { value = google_bigquery_table.platform_logs.table_id }

output "aria_configmap_values" {
  description = "Values for the UC2 Kubernetes ConfigMap"
  value = {
    LOG_CONNECTOR_TYPE  = "gcp"
    PLATFORM_TAG        = "gcp"           # ARIA PlatformTag enum — no "dataproc" value, Dataproc maps to GCP
    GCP_PROJECT_ID      = google_project.uc2.project_id
    GCP_REGION          = var.region
    GCS_LOG_BUCKET      = google_storage_bucket.logs_raw.name
    BQ_LOG_DATASET      = google_bigquery_dataset.aria_logs.dataset_id
    BQ_LOG_TABLE        = google_bigquery_table.platform_logs.table_id
    DATAPROC_CLUSTER    = google_dataproc_cluster.aria_cluster.name
  }
}
