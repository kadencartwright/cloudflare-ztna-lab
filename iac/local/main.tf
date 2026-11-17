terraform {
  required_version = ">= 1.10, < 2"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "0.8.3"
    }
  }
}

provider "libvirt" {
  uri = "qemu:///system"
}

variable "image_paths" {
  type = map(string)
}

variable "ssh_public_key" {
  type = string
}

locals {
  topology = jsondecode(file("${path.module}/../../topology.json"))
  machines = local.topology.machines
  segments = {
    office    = local.topology.office
    home      = local.topology.home
    resources = local.topology.resources
  }
}

resource "libvirt_pool" "lab" {
  name = "cfztna-lab"
  type = "dir"
  target {
    path = "/var/lib/libvirt/images/cfztna-lab"
  }
}

resource "libvirt_network" "segment" {
  for_each  = local.segments
  name      = "cfztna-${each.key}"
  mode      = each.key == "resources" ? "none" : "nat"
  addresses = each.key == "resources" ? [] : [each.value.cidr]
  autostart = false
  dns {
    enabled = each.key != "resources"
  }
  dhcp {
    enabled = each.key != "resources"
  }
  xml {
    xslt = templatefile("${path.module}/dhcp.xsl", {
      prefix = join(".", slice(split(".", each.value.gateway), 0, 3))
    })
  }
}

resource "libvirt_volume" "guest" {
  for_each = local.machines
  name     = "${each.key}.qcow2"
  pool     = libvirt_pool.lab.name
  source   = var.image_paths[each.key]
  format   = "qcow2"
}

resource "libvirt_cloudinit_disk" "guest" {
  for_each  = local.machines
  name      = "${each.key}-seed.iso"
  pool      = libvirt_pool.lab.name
  meta_data = yamlencode({ instance-id = "cfztna-${each.key}", local-hostname = each.key })
  user_data = "#cloud-config\n${yamlencode({
    ssh_pwauth   = false
    disable_root = true
    users = [{
      name                = "lab"
      shell               = "/run/current-system/sw/bin/bash"
      lock_passwd         = true
      sudo                = "ALL=(ALL) NOPASSWD:ALL"
      ssh_authorized_keys = [var.ssh_public_key]
    }]
  })}"
}

resource "libvirt_domain" "guest" {
  for_each  = local.machines
  name      = "cfztna-${each.key}"
  memory    = each.value.ram
  vcpu      = each.value.ram == 2048 ? 2 : 1
  cloudinit = libvirt_cloudinit_disk.guest[each.key].id
  autostart = false

  network_interface {
    network_id = libvirt_network.segment[each.value.segment].id
    mac        = each.value.mac
  }

  dynamic "network_interface" {
    for_each = each.key == "connector" ? [true] : []
    content {
      network_id = libvirt_network.segment["resources"].id
      mac        = local.topology.connector_resource_mac
    }
  }

  # A file-backed disk lets libvirt manage DAC labels on Ubuntu hosts.
  disk {
    file = libvirt_volume.guest[each.key].id
  }

  xml {
    xslt = file("${path.module}/qcow2.xsl")
  }

  console {
    type        = "pty"
    target_type = "serial"
    target_port = "0"
  }
}
