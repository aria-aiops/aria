# CDP Bus Data Pipeline Runbook
This runbook covers the cdp bus node layer (Kafka broker, ZooKeeper ensemble).
TF node name: cdp-bus-01. NiFi is co-located on the bus node in some deployments.

## Log Paths
* Kafka broker: /var/log/kafka/server.log
* ZooKeeper: /var/log/zookeeper/zookeeper.log

## Target Error Keywords
Monitor for messaging and coordination layer failures:
* Kafka
* ZooKeeper
* timeout
* FATAL
* Connection refused
* AuthenticationException
