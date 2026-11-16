# Reviewed syntax only. The executor BLOCKS this provider's always-powered-on
# bare-create workflow; power_state is computed, not a supported argument.
terraform {
  required_version = "= 1.13.9"
  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = "= 2.18.0"
    }
  }
}

provider "vsphere" {
  vsphere_server       = var.server
  allow_unverified_ssl = false
  persist_session      = false
  client_debug         = false
  api_timeout          = 5
  # Credentials come only from VSPHERE_USER / VSPHERE_PASSWORD.
}

variable "server" {
  type = string
}
variable "name" {
  type = string
}
variable "datastore_id" {
  type = string
}
variable "network_ids" {
  type = list(string)
}
variable "resource_pool_id" {
  type = string
}
variable "host_id" {
  type = string
}
variable "cpu" {
  type = number
}
variable "memory_mb" {
  type = number
}
variable "disk_gb" {
  type = number
}

resource "vsphere_virtual_machine" "vm" {
  name             = var.name
  resource_pool_id = var.resource_pool_id
  datastore_id     = var.datastore_id
  host_system_id   = var.host_id
  num_cpus         = var.cpu
  memory           = var.memory_mb
  guest_id         = "otherLinux64Guest"

  wait_for_guest_net_timeout = 0
  wait_for_guest_ip_timeout  = 0
  force_power_off           = false

  dynamic "network_interface" {
    for_each = var.network_ids
    content {
      network_id   = network_interface.value
      adapter_type = "vmxnet3"
    }
  }
  disk {
    label            = "Hard Disk 1"
    size             = var.disk_gb
    thin_provisioned = true
  }
  lifecycle {
    prevent_destroy = true
  }
}
