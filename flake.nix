{
  description = "Cloudflare Zero Trust SMB lab";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/f68407657ad294471eb4b2e65d193df26edf729f";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      topology = builtins.fromJSON (builtins.readFile ./topology.json);
    in {
      nixosConfigurations = nixpkgs.lib.mapAttrs (name: _: nixpkgs.lib.nixosSystem {
        inherit system;
        specialArgs = { inherit name topology; };
        modules = [ ./nix/guest.nix ];
      }) topology.machines;

      packages.${system}.cfztna-console = pkgs.callPackage ./nix/console.nix {};

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          opentofu ansible libvirt qemu just python3 jq curl openssh cdrtools libxslt shellcheck nixfmt go
        ];
      };
    };
}
