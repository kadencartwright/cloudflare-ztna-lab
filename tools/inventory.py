"""Render checkout-local credentials references and verify the libvirt host."""
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".lab"
TOPOLOGY = json.loads((ROOT / "topology.json").read_text())


def preflight():
    state = ROOT / "iac/local/terraform.tfstate"
    if state.is_file() and json.loads(state.read_text()).get("resources"):
        return
    routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "table", "all"]))
    for segment in ("office", "home", "resources"):
        requested = ipaddress.ip_network(TOPOLOGY[segment]["cidr"])
        for route in routes:
            destination = route.get("dst", "default")
            if destination != "default" and requested.overlaps(ipaddress.ip_network(destination, strict=False)):
                sys.exit(f"Host route {destination} overlaps lab {segment} {requested}")
    for command, names in (
        ("list", [f"cfztna-{name}" for name in TOPOLOGY["machines"]] + ["cfztna-win-client", "cfztna-win-server"]),
        ("net-list", ["cfztna-office", "cfztna-home", "cfztna-resources"]),
        ("pool-list", ["cfztna-lab"]),
    ):
        existing = subprocess.check_output(["virsh", "-c", "qemu:///system", command, "--all", "--name"], text=True)
        collisions = set(existing.splitlines()).intersection(names)
        if collisions:
            sys.exit(f"Unmanaged libvirt name collision: {', '.join(sorted(collisions))}")


def render():
    paths = {}
    for role in TOPOLOGY["machines"]:
        images = list((LAB / f"image-{role}").glob("*.qcow2"))
        if len(images) != 1:
            sys.exit(f"Expected one qcow2 for {role}, found {images}")
        paths[role] = str(images[0].resolve())
    data = {"image_paths": paths, "ssh_public_key": (LAB / "id_ed25519.pub").read_text().strip()}
    (LAB / "local.tfvars.json").write_text(json.dumps(data) + "\n")
    ssh = []
    for role, vm in TOPOLOGY["machines"].items():
        ssh += [f"Host {role}", f"  HostName {vm['ip']}"]
        if role == "origin":
            ssh += ["  ProxyJump connector"]
    ssh += [
        "Host *", "  User lab", f'  IdentityFile "{LAB / "id_ed25519"}"', "  IdentitiesOnly yes",
        f'  UserKnownHostsFile "{LAB / "known_hosts"}"', "  GlobalKnownHostsFile /dev/null",
        "  StrictHostKeyChecking accept-new", "  ConnectTimeout 5", "  BatchMode yes",
    ]
    (LAB / "ssh_config").write_text("\n".join(ssh) + "\n")
    config = shlex.quote(f'-F "{LAB / "ssh_config"}"')
    inventory = [
        "[guests]", *TOPOLOGY["machines"], "",
        "[guests:vars]", "ansible_python_interpreter=/run/current-system/sw/bin/python3",
        f"ansible_ssh_common_args={config}",
    ]
    (LAB / "inventory.ini").write_text("\n".join(inventory) + "\n")


def wait():
    for role in ("connector", "origin", "office", "remote"):
        deadline = time.monotonic() + 150
        while True:
            result = subprocess.run(
                ["ssh", "-F", str(LAB / "ssh_config"), role, "systemctl is-active cloud-final.service"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                print(f"{role}: {result.stdout.strip()}", flush=True)
                break
            if time.monotonic() >= deadline:
                sys.exit(f"{role}: {result.stdout}\n{result.stderr}")
            time.sleep(2)


def state_account():
    state = json.loads((ROOT / "iac/cloud/terraform.tfstate").read_text())
    accounts = {
        instance["attributes"]["account_id"]
        for resource in state.get("resources", [])
        for instance in resource.get("instances", [])
        if instance.get("attributes", {}).get("account_id")
    }
    if len(accounts) > 1:
        sys.exit("Cloud state contains multiple account IDs.")
    print(next(iter(accounts), ""))


def check_plan():
    plan = json.load(sys.stdin)
    account = os.environ["TF_VAR_account_id"]
    variables = plan["variables"]
    if variables["account_id"]["value"] != account:
        sys.exit("Saved plan account does not match the confirmed account ID.")
    if variables["dedicated_lab_account_confirmation"]["value"] != f"dedicated-lab:{account}":
        sys.exit("Saved plan lacks the dedicated account confirmation.")


if __name__ == "__main__":
    actions = {
        "preflight": preflight, "render": render, "wait": wait,
        "state-account": state_account, "check-plan": check_plan,
    }
    actions[sys.argv[1]]()
