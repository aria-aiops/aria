# CDP Master Node Runbook
This runbook covers the cdp master node layer (NameNode, ResourceManager, HiveServer2).
TF node name: cdp-master-01. Log paths use the TF-provisioned subdirectory layout.

## Log Paths
* HDFS NameNode: /var/log/hadoop/hdfs/hdfs-daemon.log
* YARN ResourceManager: /var/log/hadoop/yarn/yarn-daemon.log

## Target Error Keywords
Match these fault patterns during HDFS and YARN incident triage:
* OutOfMemory
* DiskOutOfSpaceException
* FATAL
* WARN
* Connection refused
* AuthenticationException
* GC overhead
* safe mode
