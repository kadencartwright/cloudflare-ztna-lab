{ pkgs, ... }:
{
  services.xserver.enable = true;
  services.displayManager.gdm.enable = true;
  services.desktopManager.gnome.enable = true;

  # Keep the lab's static networkd interface and use the host's VNC console.
  networking.networkmanager.enable = false;
  services.gnome.gnome-initial-setup.enable = false;
  services.gnome.gnome-remote-desktop.enable = false;
  services.gvfs.enable = true;
  services.printing.enable = true;

  users.users.employee = {
    isNormalUser = true;
    description = "Lab Employee";
  };

  environment.systemPackages = [ pkgs.libreoffice ];
  environment.gnome.excludePackages = [ pkgs.epiphany ];
  programs.firefox = {
    enable = true;
    policies.Certificates.Install = [ "/etc/cfztna/gateway-ca.pem" ];
  };
}
