terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  subscription_id             = var.subscription_id
  skip_provider_registration  = true
  features {}
}

# ── Resource Group ─────────────────────────────────────────────────────────────
resource "azurerm_resource_group" "uc1" {
  name     = var.resource_group_name
  location = var.location
}

# ── Virtual Network ────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "uc1" {
  name                = "aria-uc1-vnet"
  location            = azurerm_resource_group.uc1.location
  resource_group_name = azurerm_resource_group.uc1.name
  address_space       = ["10.10.0.0/16"]
}

resource "azurerm_subnet" "uc1" {
  name                 = "aria-uc1-subnet"
  resource_group_name  = azurerm_resource_group.uc1.name
  virtual_network_name = azurerm_virtual_network.uc1.name
  address_prefixes     = ["10.10.0.0/24"]
}

# ── Network Security Group ─────────────────────────────────────────────────────
resource "azurerm_network_security_group" "uc1" {
  name                = "aria-uc1-nsg"
  location            = azurerm_resource_group.uc1.location
  resource_group_name = azurerm_resource_group.uc1.name

  # Allow SSH from operator workstation (needed for key validation and log injection)
  security_rule {
    name                       = "allow-ssh-from-operator"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.allowed_ssh_cidr
    destination_address_prefix = "*"
  }

  # Allow all inbound traffic within the subnet (inter-node Hadoop communication)
  security_rule {
    name                       = "allow-internal-subnet"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "10.10.0.0/24"
    destination_address_prefix = "10.10.0.0/24"
  }
}

resource "azurerm_subnet_network_security_group_association" "uc1" {
  subnet_id                 = azurerm_subnet.uc1.id
  network_security_group_id = azurerm_network_security_group.uc1.id
}

# ── Public IP for master node only ─────────────────────────────────────────────
resource "azurerm_public_ip" "master" {
  name                = "aria-uc1-master-pip"
  location            = azurerm_resource_group.uc1.location
  resource_group_name = azurerm_resource_group.uc1.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

# ── Network Interfaces ─────────────────────────────────────────────────────────
# Azure separates NIC from VM — one NIC per node.
resource "azurerm_network_interface" "nodes" {
  for_each            = local.nodes
  name                = "aria-uc1-${each.key}-nic"
  location            = azurerm_resource_group.uc1.location
  resource_group_name = azurerm_resource_group.uc1.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.uc1.id
    private_ip_address_allocation = "Dynamic"
    # Only the master node gets a public IP
    public_ip_address_id = each.key == "cdp-master-01" ? azurerm_public_ip.master.id : null
  }
}

# ── Node definitions ────────────────────────────────────────────────────────────
locals {
  nodes = {
    "cdp-master-01"  = { role = "hdfs-namenode,yarn-resourcemanager,hiveserver2", disk_gb = 64 }
    "cdp-data-01"    = { role = "hdfs-datanode,yarn-nodemanager",                 disk_gb = 128 }
    "cdp-data-02"    = { role = "hdfs-datanode,yarn-nodemanager",                 disk_gb = 128 }
    "cdp-utility-01" = { role = "hive-metastore,spark-history,oozie,hue",        disk_gb = 64 }
    "cdp-bus-01"     = { role = "kafka,zookeeper,nifi",                           disk_gb = 64 }
  }

  # cloud-init script mirrors the GCP startup script logic:
  # installs Java 11, Hadoop 3.3.6, creates CDP-compatible log directory structure.
  cloud_init = <<-CLOUDINIT
    #cloud-config
    package_update: true
    packages:
      - openjdk-11-jdk
      - python3
      - python3-pip
      - wget
      - curl
      - rsyslog
      - openssh-server

    runcmd:
      - systemctl enable ssh
      - systemctl start ssh

      # Hadoop binaries — for authentic log format
      - HADOOP_VERSION=3.3.6
      - wget -q "https://downloads.apache.org/hadoop/common/hadoop-$HADOOP_VERSION/hadoop-$HADOOP_VERSION.tar.gz" -O /tmp/hadoop.tar.gz
      - tar -xzf /tmp/hadoop.tar.gz -C /opt/
      - ln -s /opt/hadoop-$HADOOP_VERSION /opt/hadoop
      - rm /tmp/hadoop.tar.gz

      # Log directory structure — mirrors real CDP layout exactly
      - mkdir -p /var/log/hadoop/hdfs /var/log/hadoop/yarn
      - mkdir -p /var/log/hive /var/log/spark /var/log/kafka
      - mkdir -p /var/log/zookeeper /var/log/oozie /var/log/nifi
      - chmod -R 755 /var/log/hadoop /var/log/hive /var/log/spark
      - chmod -R 755 /var/log/kafka /var/log/zookeeper /var/log/oozie /var/log/nifi

      - echo "ARIA UC1 Azure node ready $(hostname) at $(date)" >> /var/log/aria-setup.log
  CLOUDINIT
}

# ── Virtual Machines ───────────────────────────────────────────────────────────
resource "azurerm_linux_virtual_machine" "nodes" {
  for_each            = local.nodes
  name                = each.key
  location            = azurerm_resource_group.uc1.location
  resource_group_name = azurerm_resource_group.uc1.name
  size                = "Standard_D2s_v3" # 2 vCPU, 8 GB RAM — B2ms capacity unavailable in westeurope

  admin_username = "aria"
  # SSH key auth only — no password
  disable_password_authentication = true

  admin_ssh_key {
    username   = "aria"
    public_key = var.aria_ssh_public_key
  }

  network_interface_ids = [azurerm_network_interface.nodes[each.key].id]

  os_disk {
    name                 = "aria-uc1-${each.key}-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = each.value.disk_gb
  }

  source_image_reference {
    publisher = "Debian"
    offer     = "debian-11"
    sku       = "11"
    version   = "latest"
  }

  # cloud-init runs on first boot — same Hadoop setup as GCP startup script
  custom_data = base64encode(local.cloud_init)

  tags = {
    aria-role = each.value.role
    aria-uc   = "uc1"
    aria-env  = "testing"
  }

  depends_on = [azurerm_network_interface.nodes]
}
