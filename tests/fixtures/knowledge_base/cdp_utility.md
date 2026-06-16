# CDP Utility Node Runbook
This runbook covers the cdp utility node layer (Hive Metastore, Spark History, Oozie, NiFi).
TF node name: cdp-utility-01.

## Log Paths
* Hive Metastore: /var/log/hive/hive.log
* Spark History Server: /var/log/spark/spark.log
* Oozie Coordinator: /var/log/oozie/oozie.log
* NiFi Flow Manager: /var/log/nifi/nifi.log

## Target Error Keywords
Isolate runtime engine query and execution failures using these patterns:
* Hive
* Spark
* Oozie
* NiFi
* FATAL
* OOM
* OutOfMemory
* timeout
