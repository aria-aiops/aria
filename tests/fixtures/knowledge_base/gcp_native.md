# GCP Native Services Runbook
This runbook covers native GCP service incidents: BigQuery, Cloud Functions, Pub/Sub, GCS.
Full connector support is Phase 1.5 S6 scope. In round 2 testing (S5), UC3 validates the
graceful degradation path only — Agent 2 returns an empty LogQueryResult with LOW confidence,
and Agent 4 notifies with "no logs available — connector not yet implemented for this platform".

Platforms: BigQuery, Cloud Functions, Pub/Sub, GCS (GCP native services).

## Log Paths
No connector implemented in S4. GCPLogConnector returns empty result for these resource types:
* bigquery_dataset
* cloud_function
* pubsub_subscription
* gcs_bucket

## Target Error Keywords
These keywords are used for future S6 connector implementation and round 3 testing:
* FATAL
* timeout
* ERROR
* WARN
