terraform {
  required_version = ">= 1.10, < 2"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.24.0"
    }
  }
}

variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "Enter the 32-character dedicated lab account ID."
  }
}

variable "dedicated_lab_account_confirmation" {
  type = string
  validation {
    condition     = contains(["dedicated-lab:${var.account_id}", "approved-poc:${var.account_id}"], var.dedicated_lab_account_confirmation)
    error_message = "Confirm the account with dedicated-lab:<account_id> or approved-poc:<account_id>."
  }
}

variable "team_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.team_name))
    error_message = "Enter the dedicated Zero Trust team name."
  }
}

variable "enrollment_token_uuids" {
  type = set(string)
  validation {
    condition = length(var.enrollment_token_uuids) > 0 && alltrue([
      for id in var.enrollment_token_uuids : can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", id))
    ])
    error_message = "Enter service token resource UUIDs, not client IDs or secrets."
  }
}

variable "expose_windows_server" {
  type    = bool
  default = false
}

variable "gateway_enrollment_token_uuid" {
  type = string
  validation {
    condition     = contains(var.enrollment_token_uuids, var.gateway_enrollment_token_uuid)
    error_message = "Enter the IoT gateway service token resource UUID from enrollment_token_uuids."
  }
}

locals {
  topology = jsondecode(file("${path.module}/../../topology.json"))
  origins = merge(
    { nixos = { ip = local.topology.machines.origin.ip, ports = "445 631" } },
    var.expose_windows_server ? { windows = { ip = local.topology.windows_server_ip, ports = "445" } } : {}
  )
  service_identity = "identity.email == ${jsonencode("non_identity@${var.team_name}.cloudflareaccess.com")}"
  enrollment_match = join(" or ", [
    for id in sort(tolist(var.enrollment_token_uuids)) : "identity.service_token_uuid == ${jsonencode(id)}"
  ])
  destinations  = join(" ", [for origin in local.origins : origin.ip])
  private_ports = join(" or ", [for origin in local.origins : "(net.dst.ip == ${origin.ip} and net.dst.port in {${origin.ports}})"])
  workstation_exclusions = [
    { address = local.topology.iot.cidr, description = "IoT gateway local LAN" },
    { address = "${local.topology.office.gateway}/32", description = "Lab office hypervisor management" },
    { address = "${local.topology.home.gateway}/32", description = "Lab home hypervisor management" },
    { address = "169.254.0.0/16", description = "IPv4 link local" },
    { address = "224.0.0.0/4", description = "IPv4 multicast" },
    { address = "255.255.255.255/32", description = "IPv4 broadcast" },
    { address = "fe80::/10", description = "IPv6 link local" },
    { address = "ff00::/8", description = "IPv6 multicast" },
  ]
}

resource "cloudflare_zero_trust_device_custom_profile" "lab" {
  account_id            = var.account_id
  name                  = "cfztna-lab"
  match                 = local.enrollment_match
  precedence            = 10
  enabled               = true
  service_mode_v2       = { mode = "warp" }
  tunnel_protocol       = "masque"
  switch_locked         = true
  auto_connect          = 30
  allow_mode_switch     = false
  allowed_to_leave      = false
  exclude_office_ips    = false
  disable_auto_fallback = true
  captive_portal        = 180

  # Replace the default broad RFC1918 excludes. Never exclude the resource /32s.
  exclude = local.workstation_exclusions
}

resource "cloudflare_zero_trust_device_custom_profile" "egress" {
  account_id            = var.account_id
  name                  = "cfztna-lab egress gateway"
  match                 = "identity.service_token_uuid == ${jsonencode(var.gateway_enrollment_token_uuid)}"
  precedence            = 5
  enabled               = true
  service_mode_v2       = { mode = "warp" }
  tunnel_protocol       = "masque"
  switch_locked         = true
  auto_connect          = 30
  allow_mode_switch     = false
  allowed_to_leave      = false
  exclude_office_ips    = false
  disable_auto_fallback = true
  captive_portal        = 180
  exclude = concat(local.workstation_exclusions, [
    { address = local.topology.resources.cidr, description = "Local file server network on the egress gateway" },
  ])
}

resource "cloudflare_zero_trust_device_posture_rule" "managed" {
  for_each = {
    linux   = "/etc/cfztna-managed"
    windows = "C:\\ProgramData\\cfztna-managed"
  }
  account_id = var.account_id
  name       = "cfztna-lab marker (${each.key})"
  type       = "file"
  schedule   = "1m"
  expiration = "25m"
  match      = [{ platform = each.key }]
  input = {
    operating_system = each.key
    path             = each.value
    exists           = true
  }
}

resource "cloudflare_zero_trust_gateway_policy" "private_deny" {
  account_id = var.account_id
  name       = "cfztna-lab deny private by default"
  enabled    = true
  precedence = 2000
  action     = "block"
  filters    = ["l4"]
  traffic    = "net.dst.ip in {${local.destinations}}"
}

resource "cloudflare_zero_trust_gateway_policy" "private_allow" {
  account_id     = var.account_id
  name           = "cfztna-lab allow managed device to SMB and IPP"
  enabled        = true
  precedence     = 1000
  action         = "allow"
  filters        = ["l4"]
  traffic        = "net.protocol == \"tcp\" and (${local.private_ports})"
  identity       = local.service_identity
  device_posture = "any(device_posture.checks.passed[*] in {${join(" ", [for rule in cloudflare_zero_trust_device_posture_rule.managed : jsonencode(rule.id)])}})"
  depends_on     = [cloudflare_zero_trust_gateway_policy.private_deny]
}

resource "cloudflare_zero_trust_gateway_policy" "dns_canary" {
  account_id = var.account_id
  name       = "cfztna-lab DNS canary"
  enabled    = true
  precedence = 3000
  action     = "block"
  filters    = ["dns"]
  identity   = local.service_identity
  traffic    = "dns.fqdn == \"example.net\""
}

resource "cloudflare_zero_trust_gateway_policy" "https_canary" {
  account_id = var.account_id
  name       = "cfztna-lab HTTPS path canary"
  enabled    = true
  precedence = 4000
  action     = "block"
  filters    = ["http"]
  identity   = local.service_identity
  traffic    = "http.request.host == \"example.com\" and http.request.uri.path == \"/cfztna-lab-block\""
  rule_settings = {
    block_page_enabled = true
    block_reason       = "cfztna-lab HTTPS path canary"
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "lab" {
  account_id = var.account_id
  name       = "cfztna-lab"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_route" "lab" {
  for_each   = local.origins
  account_id = var.account_id
  network    = "${each.value.ip}/32"
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab.id
  comment    = "cfztna-lab ${each.key} SMB resources"
  depends_on = [cloudflare_zero_trust_gateway_policy.private_allow]
}

output "account_id" {
  value = var.account_id
}

output "tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.lab.id
}
