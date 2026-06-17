output "project_id"          { value = google_project.uc3.project_id }
output "gcs_logs_bucket"     { value = google_storage_bucket.logs_raw.name }
output "gcs_dataflow_temp"   { value = google_storage_bucket.dataflow_temp.name }
output "bq_logs_dataset"     { value = google_bigquery_dataset.aria_logs.dataset_id }
output "bq_logs_table"       { value = google_bigquery_table.platform_logs.table_id }
output "bq_ops_dataset"      { value = google_bigquery_dataset.aria_ops.dataset_id }

output "aria_configmap_values" {
  value = {
    LOG_CONNECTOR_TYPE  = "gcp"
    PLATFORM_TAG        = "gcp"
    GCP_PROJECT_ID      = google_project.uc3.project_id
    GCP_REGION          = var.region
    GCS_LOG_BUCKET      = google_storage_bucket.logs_raw.name
    GCS_DATAFLOW_TEMP   = google_storage_bucket.dataflow_temp.name
    BQ_LOG_DATASET      = google_bigquery_dataset.aria_logs.dataset_id
    BQ_LOG_TABLE        = google_bigquery_table.platform_logs.table_id
  }
}
