# CDP Operational Utility Runbook
This runbook maps operational metadata and runtime environments for the cdp utility worker node.

## Log Paths
Analytics and workflow orchestration execution engines map log targets here:
* Apache Hive Metastore Instance: /var/log/hive/hive.log
* Apache Spark Cluster Engine: /var/log/spark/spark.log
* Oozie Coordinator Engine: /var/log/oozie/oozie.log
* Apache NiFi Flow Manager: /var/log/nifi/nifi.log

## Target Error Keywords
Isolate runtime engine query and execution exceptions using these tracking strings:
* Hive
* Spark
* Oozie