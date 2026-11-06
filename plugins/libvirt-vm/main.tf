terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "= 0.9.9"
    }
  }
}
variable "uri" { type = string }
variable "name" { type = string }
variable "cpu" { type = number }
variable "memory_mb" { type = number }
variable "disk_gb" { type = number }
provider "libvirt" { uri = var.uri }

# Additive blank disk: NEVER clones or modifies the running nested-smoke disk.
resource "libvirt_volume" "disk" {
  name       = "${var.name}.qcow2"
  pool       = "infra-lab"
  capacity   = var.disk_gb * 1073741824
  allocation = 0
  target = { format = { type = "qcow2" } }
}
resource "libvirt_domain" "vm" {
  name        = var.name
  description = "KuberWeaver owned ${var.name}; stopped blank-disk lab definition"
  type        = "kvm"
  memory      = var.memory_mb
  memory_unit = "MiB"
  vcpu        = var.cpu
  running     = false
  autostart   = false
  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = "q35"
  }
  devices = {
    disks = [{
      device = "disk"
      driver = { name = "qemu", type = "qcow2" }
      source = { file = { file = libvirt_volume.disk.path } }
      target = { dev = "vda", bus = "virtio" }
    }]
    interfaces = [{
      model  = { type = "virtio" }
      source = { network = { network = "lab-nested" } }
    }]
  }
}
