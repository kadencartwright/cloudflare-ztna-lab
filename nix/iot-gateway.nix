{ lib, pkgs, topology, ... }:
let
  lan = topology.iot;
  resources = topology.resources;
  servers = "{ ${topology.machines.origin.ip}, ${topology.windows_server_ip} }";
  prefix = lib.concatStringsSep "." (lib.take 3 (lib.splitString "." lan.gateway)) + ".";
  ip = "${pkgs.iproute2}/bin/ip";
in {
  systemd.network.links."10-iot" = {
    matchConfig.MACAddress = topology.iot_gateway_lan_mac;
    linkConfig.Name = "iot0";
  };
  systemd.network.networks."10-iot" = {
    matchConfig.Name = "iot0";
    address = [ "${lan.gateway}/24" ];
    networkConfig = { DHCP = "no"; IPv6AcceptRA = false; LinkLocalAddressing = "no"; };
    routingPolicyRules = [{
      From = lan.cidr;
      IncomingInterface = "iot0";
      Table = 149;
      Priority = 90;
    }];
    routes = [{ Destination = "0.0.0.0/0"; Type = "unreachable"; Table = 149; Metric = 32767; }];
  };
  systemd.network.links."10-resource-gateway" = {
    matchConfig.MACAddress = topology.gateway_resource_mac;
    linkConfig.Name = "resource0";
  };
  systemd.network.networks."10-resource-gateway" = {
    matchConfig.Name = "resource0";
    address = [ "${topology.gateway_resource_ip}/24" ];
    networkConfig = { DHCP = "no"; IPv6AcceptRA = false; LinkLocalAddressing = "no"; };
    routingPolicyRules = [{
      From = resources.cidr;
      IncomingInterface = "resource0";
      Table = 150;
      Priority = 90;
    }];
    routes = [{ Destination = "0.0.0.0/0"; Type = "unreachable"; Table = 150; Metric = 32767; }];
  };

  # The unreachable fallback remains present when the WARP device disappears.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="net", KERNEL=="CloudflareWARP", TAG+="systemd", ENV{SYSTEMD_WANTS}+="iot-warp-route.service"
  '';
  systemd.services.iot-warp-route = {
    bindsTo = [ "sys-subsystem-net-devices-CloudflareWARP.device" ];
    after = [ "sys-subsystem-net-devices-CloudflareWARP.device" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = [
        "${ip} -4 route replace default dev CloudflareWARP table 149 metric 10"
        "${ip} -4 route replace default dev CloudflareWARP table 150 metric 10"
      ];
    };
  };

  services.dnsmasq = {
    enable = true;
    resolveLocalQueries = false;
    settings = {
      interface = [ "iot0" "resource0" ];
      no-dhcp-interface = "resource0";
      except-interface = "lo";
      bind-dynamic = true;
      no-resolv = true;
      no-hosts = true;
      server = [ "127.0.2.2" "127.0.2.3" ];
      cache-size = 0;
      dhcp-authoritative = true;
      dhcp-range = "${prefix}100,${prefix}199,255.255.255.0,12h";
      dhcp-option = [ "option:router,${lan.gateway}" "option:dns-server,${lan.gateway}" "option:mtu,1280" ];
    };
  };

  networking.nftables.ruleset = ''
    table inet iot_gateway
    flush table inet iot_gateway
    table inet iot_gateway {
      set non_public_v4 {
        type ipv4_addr
        flags interval
        elements = { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8,
                     169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16,
                     198.18.0.0/15, 224.0.0.0/3 }
      }
      chain forward {
        type filter hook forward priority -5; policy drop;
        iifname "iot0" ip daddr @non_public_v4 counter drop
        iifname "iot0" meta l4proto { tcp, udp } th dport { 53, 853 } counter drop
        iifname "iot0" oifname "CloudflareWARP" ip saddr ${lan.cidr} tcp flags & (syn | rst) == syn tcp option maxseg size > 1240 tcp option maxseg size set 1240
        iifname "iot0" oifname "CloudflareWARP" ip saddr ${lan.cidr} counter accept
        iifname "CloudflareWARP" oifname "iot0" ip daddr ${lan.cidr} ct state established,related counter accept
        iifname "resource0" ip daddr @non_public_v4 counter drop
        iifname "resource0" oifname "CloudflareWARP" ip saddr ${servers} tcp dport { 80, 443 } tcp flags & (syn | rst) == syn tcp option maxseg size > 1240 tcp option maxseg size set 1240
        iifname "resource0" oifname "CloudflareWARP" ip saddr ${servers} tcp dport { 80, 443 } counter accept
        iifname "CloudflareWARP" oifname "resource0" ip daddr ${servers} ct state established,related counter accept
        counter drop
      }
      chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname "CloudflareWARP" ip saddr ${lan.cidr} masquerade
        oifname "CloudflareWARP" ip saddr ${servers} masquerade
      }
    }
  '';
}
