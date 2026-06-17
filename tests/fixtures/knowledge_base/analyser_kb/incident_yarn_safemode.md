# YARN / HDFS Safe Mode — Example Log
label: incident | type: pipeline

2024-01-18 22:10:05 FATAL NameNode: HDFS is in safe mode — replica count below minimum threshold
2024-01-18 22:10:05 ERROR NameNode: Only 2 of 3 DataNodes reporting — waiting for data node recovery
2024-01-18 22:10:06 WARN  ResourceManager: YARN cluster degraded — 1 node lost, rescheduling containers
2024-01-18 22:10:07 ERROR JobHistoryServer: Cannot write to /var/log/hadoop/yarn — HDFS safe mode active
