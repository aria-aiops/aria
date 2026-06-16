# CDP Bus Data Pipeline Runbook
This runbook configures messaging brokers and event buses running on the cdp bus tier.

## Log Paths
Message streaming components write operational logs here:
* Kafka Engine Event Stream: /var/log/kafka/server.log
* ZooKeeper Coordination Cluster Log: /var/log/zookeeper/zookeeper.log

## Target Error Keywords
Monitor trace streams for active ingestion or validation failures:
* timeout