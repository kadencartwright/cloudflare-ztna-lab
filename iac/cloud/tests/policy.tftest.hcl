mock_provider "cloudflare" {}

variables {
  account_id                         = "00000000000000000000000000000000"
  dedicated_lab_account_confirmation = "dedicated-lab:00000000000000000000000000000000"
  allow_email                        = "employee@example.org"
  deny_email                         = "visitor@example.org"
}

run "scoped_default" {
  command = plan

  assert {
    condition     = length(cloudflare_zero_trust_tunnel_cloudflared_route.lab) == 1 && contains(keys(cloudflare_zero_trust_tunnel_cloudflared_route.lab), "nixos")
    error_message = "The Windows file server route must be opt-in."
  }

  assert {
    condition     = cloudflare_zero_trust_gateway_policy.private_allow.precedence < cloudflare_zero_trust_gateway_policy.private_deny.precedence
    error_message = "Only the specific authorized match should precede the private deny."
  }

  assert {
    condition     = cloudflare_zero_trust_device_custom_profile.lab.switch_locked && !cloudflare_zero_trust_device_custom_profile.lab.allowed_to_leave
    error_message = "The lab profile must lock the client controls."
  }
}

run "windows_route" {
  command = plan

  variables {
    expose_windows_server = true
  }

  assert {
    condition     = cloudflare_zero_trust_tunnel_cloudflared_route.lab["windows"].network == "192.168.250.30/32"
    error_message = "The Windows opt-in must advertise only the Windows Server host route."
  }
}

run "reject_missing_account_confirmation" {
  command = plan

  variables {
    dedicated_lab_account_confirmation = "yes"
  }

  expect_failures = [var.dedicated_lab_account_confirmation]
}
