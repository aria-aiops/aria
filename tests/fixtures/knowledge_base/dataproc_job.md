# GCP Dataproc Job Runbook
This runbook covers Dataproc job execution failures: Spark driver crashes, executor OOM,
quota exceeded, SA key expiry, driver node timeouts, and init action failures.
GCP resource type: cloud_dataproc_job. UC2 job runner: aria-uc2-dataproc.

## Log Paths
Cloud Logging API — no local SSH paths. Filter by resource.type = "cloud_dataproc_job".

## Target Error Keywords
Match these patterns for job-level triage on Dataproc:
* FATAL
* OutOfMemory
* Spark
* timeout
* DiskOutOfSpaceException
* AuthenticationException
* WARN
* ERROR
