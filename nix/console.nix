{ buildGoModule }:
buildGoModule {
  pname = "cfztna-console";
  version = "0.1.0";
  src = ../console/tui;
  vendorHash = "sha256-5bL+XMZ7Z0dCIEo9tJY3hBRHPu1+hwFwZ2RMTedRfNE=";
  env.CGO_ENABLED = "0";

  meta.mainProgram = "cfztna-console";
}
