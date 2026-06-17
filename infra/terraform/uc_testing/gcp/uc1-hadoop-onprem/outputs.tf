output "project_id" { value = google_project.uc1.project_id }

output "node_internal_ips" {
  value = { for name, inst in google_compute_instance.hadoop_nodes :
    name => inst.network_interface[0].network_ip }
}

output "master_external_ip" {
  value = google_compute_instance.hadoop_nodes["cdp-master-01"].network_interface[0].access_config[0].nat_ip
}

output "ssh_secret_id" {
  value = google_secret_manager_secret.aria_ssh_key.id
}

output "aria_configmap_values" {
  description = "Values for the UC1 Kubernetes ConfigMap"
  value = {
    LOG_CONNECTOR_TYPE   = "onprem"
    PLATFORM_TAG         = "cdp"
    GCP_PROJECT_ID       = google_project.uc1.project_id
    ONPREM_LOG_BASE_PATH = "/var/log"
    ONPREM_HOST_MAP = jsonencode({
      "cdp-master-01"  = ["hadoop/hdfs", "hadoop/yarn"]
      "cdp-data-01"    = ["hadoop/hdfs", "hadoop/yarn"]
      "cdp-data-02"    = ["hadoop/hdfs", "hadoop/yarn", "spark"]
      "cdp-utility-01" = ["hive", "spark", "oozie"]
      "cdp-bus-01"     = ["kafka", "zookeeper", "nifi"]
    })
    NODE_IPS = jsonencode({
      for name, inst in google_compute_instance.hadoop_nodes :
      name => inst.network_interface[0].network_ip
    })
  }
}
