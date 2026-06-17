# cdp-cluster (UC1 — Hadoop on-prem VMs)
Platform: CDP

## Physical Resources

### cdp-master-01
IP: POPULATE_FROM_TF_OUTPUT
Log paths:
  - /var/log/hadoop/hdfs
  - /var/log/hadoop/yarn

### cdp-data-01
IP: POPULATE_FROM_TF_OUTPUT
Log paths:
  - /var/log/hadoop/hdfs
  - /var/log/hadoop/yarn

### cdp-data-02
IP: POPULATE_FROM_TF_OUTPUT
Log paths:
  - /var/log/hadoop/hdfs
  - /var/log/hadoop/yarn

### cdp-utility-01
IP: POPULATE_FROM_TF_OUTPUT
Log paths:
  - /var/log/hive
  - /var/log/spark
  - /var/log/oozie
  - /var/log/nifi

### cdp-bus-01
IP: POPULATE_FROM_TF_OUTPUT
Log paths:
  - /var/log/kafka/server.log
  - /var/log/zookeeper/zookeeper.log
