# CDP Master Node Runbook
This runbook configures metadata and monitoring layouts for the cdp master server layer.

## Log Paths
The automated cluster runtime maps its principal processing logs to these locations:
* HDFS Storage Core Log: /var/log/hadoop/hdfs/hdfs-daemon.log
* YARN Resource Manager Log: /var/log/hadoop/yarn/yarn-daemon.log

## Target Error Keywords
During node metric anomalies, prioritize matching these infrastructure fault patterns:
* OutOfMemory
* FATAL
* Connection refused