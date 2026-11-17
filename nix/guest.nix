{ config, lib, pkgs, modulesPath, name, topology, ... }:
let
  machine = topology.machines.${name};
  isClient = builtins.elem name [ "office" "remote" ];
  isConnector = name == "connector";
  isOrigin = name == "origin";
  origin = topology.machines.origin.ip;
  segment = topology.${machine.segment};
  officeGw = topology.office.gateway;
  connectorIP = topology.resources.gateway;
  net = mac: address: gateway: dns: {
    matchConfig.MACAddress = mac;
    address = [ "${address}/24" ];
    gateway = lib.optional (gateway != null) gateway;
    dns = lib.optional (dns != null) dns;
    networkConfig = { DHCP = "no"; IPv6AcceptRA = false; LinkLocalAddressing = "no"; };
  };
in {
  imports = [
    (modulesPath + "/profiles/qemu-guest.nix")
    (modulesPath + "/virtualisation/disk-image.nix")
  ];
  image.efiSupport = false;
  virtualisation.diskSize = 6144;
  system.stateVersion = "26.05";
  boot.kernelParams = [ "console=ttyS0,115200" ];
  boot.loader.timeout = 1;

  networking = {
    hostName = name;
    useDHCP = false;
    useNetworkd = true;
    enableIPv6 = false;
    firewall.enable = false;
    nftables = {
      enable = true;
      flushRuleset = false;
      ruleset = ''
        table inet lab
        flush table inet lab
        table inet lab {
          chain input {
            type filter hook input priority 0; policy drop;
            iifname "lo" accept
            ct state established,related accept
            ${if isOrigin then ''
              ip saddr ${connectorIP} tcp dport { 22, 445, 631 } accept
            '' else ''
              ip saddr ${segment.gateway} tcp dport 22 accept
            ''}
          }
          chain forward {
            type filter hook forward priority 0; policy drop;
            ${lib.optionalString isConnector ''
              ct state established,related accept
              iifname "resource0" oifname "uplink0" ip saddr { ${origin}, ${topology.windows_server_ip} } ip daddr ${officeGw} meta l4proto { tcp, udp } th dport 53 accept
              iifname "resource0" oifname "uplink0" ip saddr { ${origin}, ${topology.windows_server_ip} } ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10 } tcp dport { 80, 443 } accept
            ''}
          }
        }
        ${lib.optionalString isConnector ''
          table ip lab_nat
          flush table ip lab_nat
          table ip lab_nat {
            chain postrouting {
              type nat hook postrouting priority srcnat; policy accept;
              oifname "uplink0" ip saddr ${topology.resources.cidr} masquerade
            }
          }
        ''}
      '';
    };
  };

  # Cloud-init only installs credentials; all physical settings are static.
  systemd.network.wait-online.enable = false;
  systemd.network.links = {
    "10-primary" = {
      matchConfig.MACAddress = machine.mac;
      linkConfig.Name = if isOrigin then "resource0" else "uplink0";
    };
  } // lib.optionalAttrs isConnector {
    "10-resource" = {
      matchConfig.MACAddress = topology.connector_resource_mac;
      linkConfig.Name = "resource0";
    };
  };
  systemd.network.networks = {
    "10-primary" = net machine.mac machine.ip segment.gateway
      (if isOrigin then officeGw else segment.gateway);
  } // lib.optionalAttrs isConnector {
    "10-resource" = net topology.connector_resource_mac connectorIP null null;
  };

  boot.kernel.sysctl = {
    "net.ipv4.ip_forward" = if isConnector then 1 else 0;
  } // lib.optionalAttrs isOrigin {
    # The CUPS IPP emulator insists on creating an IPv6 listening socket.
    # Only loopback has IPv6; the resource NIC remains IPv4-only.
    "net.ipv6.conf.lo.disable_ipv6" = 0;
  };

  services.openssh = {
    enable = true;
    openFirewall = false;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "no";
      AllowTcpForwarding = if isConnector then "local" else "no";
      PermitOpen = if isConnector then "${origin}:22" else "none";
    };
  };

  services.cloud-init = {
    enable = true;
    network.enable = false;
    settings.datasource_list = [ "NoCloud" ];
    settings.network.config = "disabled";
  };
  users.users.lab = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    hashedPassword = "!";
  };
  security.sudo.wheelNeedsPassword = false;

  environment.systemPackages = with pkgs; [
    python3 curl openssl iproute2 nftables samba cups bind netcat-openbsd
  ] ++ lib.optional isConnector cloudflared;

  services.samba = lib.mkIf isOrigin {
    enable = true;
    nmbd.enable = false;
    settings = {
      global = {
        "server min protocol" = "SMB2_10";
        "server smb encrypt" = "required";
        "smb ports" = "445";
        "load printers" = "no";
      };
      Work = {
        path = "/srv/work";
        "read only" = "no";
        "guest ok" = "no";
        "valid users" = "labshare";
      };
    };
  };
  users.users.labshare = lib.mkIf isOrigin {
    isSystemUser = true;
    group = "labshare";
  };
  users.groups.labshare = lib.mkIf isOrigin {};
  systemd.tmpfiles.rules = lib.optionals isOrigin [
    "d /srv/work 0770 labshare labshare -"

  ];

  # CUPS' IPP Everywhere emulator stores each submitted document for verification.
  # Avahi supplies the DNS-SD library connection; the printer itself is not advertised.
  services.avahi.enable = isOrigin;
  systemd.services.lab-printer = lib.mkIf isOrigin {
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    requires = [ "avahi-daemon.service" ];
    after = [ "network-online.target" "avahi-daemon.service" ];
    serviceConfig = {
      DynamicUser = true;
      StateDirectory = "cfztna-print";
      StateDirectoryMode = "0700";
      AmbientCapabilities = [ "CAP_NET_BIND_SERVICE" ];
      ExecStart = "${pkgs.cups}/bin/ippeveprinter -r off -p 631 -d /var/lib/cfztna-print -k -f application/pdf,image/pwg-raster,image/urf LabPrinter";
      Restart = "on-failure";
      RestartSec = 1;
    };
  };

  systemd.services.lab-tunnel = lib.mkIf isConnector {
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    unitConfig.ConditionPathExists = "/etc/cfztna/tunnel-token";
    serviceConfig = {
      DynamicUser = true;
      LoadCredential = "tunnel-token:/etc/cfztna/tunnel-token";
      ExecStart = "${pkgs.cloudflared}/bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 run --token-file %d/tunnel-token";
      Restart = "on-failure";
      RestartSec = 5;
    };
  };

  systemd.services.lab-egress = lib.mkIf isClient {
    wantedBy = [ "multi-user.target" ];
    before = [ "network-pre.target" "cloudflare-warp.service" ];
    after = [ "nftables.service" ];
    unitConfig.ConditionPathExists = "/etc/cfztna/egress-guard";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.nftables}/bin/nft -f /etc/cfztna-egress.nft";
      ExecStop = "${pkgs.nftables}/bin/nft delete table inet lab_egress";
    };
  };
  environment.etc."cfztna-egress.nft" = lib.mkIf isClient {
    text = ''
      table inet lab_egress
      flush table inet lab_egress
      table inet lab_egress {
        chain output {
          type filter hook output priority -5; policy drop;
          oifname { "lo", "CloudflareWARP" } accept
          oifname "uplink0" ip daddr ${segment.gateway} tcp sport 22 ct state established accept
          oifname "uplink0" meta skuid 0 ip daddr 162.159.197.0/24 udp dport { 443, 500, 1701, 4500, 4443, 8443, 8095 } accept
          oifname "uplink0" meta skuid 0 ip daddr { 162.159.137.105, 162.159.138.105, 162.159.197.0/24 } tcp dport 443 accept
          counter drop
        }
      }
    '';
  };

  services.resolved.enable = true;
  services.cloudflare-warp = lib.mkIf isClient {
    enable = true;
    openFirewall = false;
  };
  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "cloudflare-warp";
}
