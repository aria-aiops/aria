# GCP Dataproc Cluster Runbook
This runbook covers Dataproc cluster-level failures: master/worker node issues, init action
failures, cluster scaling errors, and YARN/HDFS problems on the managed cluster.
GCP resource type: cloud_dataproc_cluster. UC2 cluster name: aria-uc2-cluster.

## Log Paths
Cloud Logging API — no local SSH paths. Filter by:
* resource.type = "cloud_dataproc_cluster"
* resource.labels.cluster_name = <cluster-name>

## Target Error Keywords
Match these patterns for cluster-level triage on Dataproc:
* FATAL
* OutOfMemory
* YARN
* HDFS
* timeout
* AuthenticationException
* DiskOutOfSpaceException
* safe mode
