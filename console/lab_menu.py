"""Role-specific controls for the Linux lab guests."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys


TITLES = {
    "office": "Office workstation",
    "remote": "Home workstation",
    "connector": "Cloudflare Tunnel connector",
    "origin": "Samba and print server",
    "iot-gateway": "IoT gateway",
}


@dataclass(frozen=True)
class Action:
    label: str
    commands: tuple[tuple[str, ...], ...]
    confirm: str | None = None


def status(*units):
    return ("systemctl", "status", "--no-pager", "--lines=0", *units)


def logs(unit):
    return ("sudo", "-n", "journalctl", "--no-pager", "-n", "25", "-u", unit)


def restart(label, unit, impact):
    return Action(f"Restart {label}", (("sudo", "-n", "systemctl", "restart", unit), status(unit)), impact)


def port_check(address, port):
    return ("nc", "-4", "-v", "-z", "-w", "4", address, str(port))


def actions_for(role, topology):
    origin = topology["machines"]["origin"]["ip"]
    ports = topology["service_ports"]
    private_ports = tuple(port_check(origin, port) for port in ports.values())
    warp_status = ("warp-cli", "--accept-tos", "status")
    warp_restart = restart("WARP", "cloudflare-warp.service", "Traffic through WARP will pause briefly.")
    if role in ("office", "remote"):
        return (
            Action("Show WARP status and the private route", (warp_status, ("ip", "-4", "route", "get", origin))),
            Action("Check Samba and IPP TCP ports", private_ports),
            Action("Check HTTPS using the lab Gateway CA", ((
                "curl", "--noproxy", "*", "-4", "--max-time", "10", "--silent", "--show-error", "--fail",
                "--cacert", "/etc/cfztna/gateway-ca.pem", "-o", "/dev/null", "-w", "HTTPS %{http_code}\\n", "https://example.com/",
            ),)),
            Action("Show Linux egress guard status", (status("lab-egress.service"),)),
            Action("Show recent WARP logs", (logs("cloudflare-warp.service"),)),
            warp_restart,
        )
    if role == "connector":
        return (
            Action("Check Tunnel service and edge readiness", (status("lab-tunnel.service"), (
                "curl", "--fail", "--silent", "--show-error", "--max-time", "5", "http://127.0.0.1:20241/ready",
            ))),
            Action("Check Samba and IPP ports on the origin", private_ports),
            Action("Check the Windows Server SMB port", (port_check(topology["windows_server_ip"], ports["smb"]),)),
            Action("Show recent Tunnel logs", (logs("lab-tunnel.service"),)),
            restart("the Tunnel connector", "lab-tunnel.service", "Private SMB and printing will pause briefly."),
        )
    if role == "origin":
        return (
            Action("Show Samba and printer status", (status("samba-smbd.service", "lab-printer.service"),)),
            Action("List retained print jobs, newest first", (("sudo", "-n", "ls", "-lht", "/var/lib/cfztna-print/"),)),
            Action("Show recent Samba logs", (logs("samba-smbd.service"),)),
            Action("Show recent printer logs", (logs("lab-printer.service"),)),
            restart("Samba", "samba-smbd.service", "Active SMB sessions may be interrupted."),
            restart("the printer", "lab-printer.service", "Active print jobs may be interrupted."),
        )
    return (
        Action("Show WARP and the IoT routing table", (warp_status, ("ip", "-4", "route", "show", "table", "149"))),
        Action("Show DHCP and connected-device leases", (status("dnsmasq.service"), (
            "sudo", "-n", "cat", "/var/lib/dnsmasq/dnsmasq.leases",
        ))),
        Action("Test the IoT gateway's DNS resolver", ((
            "dig", f"@{topology['iot']['gateway']}", "example.com", "A", "+tries=1", "+time=4",
            "+noall", "+comments", "+answer",
        ),)),
        Action("Show isolation rules and traffic counters", (("sudo", "-n", "nft", "list", "table", "inet", "iot_gateway"),)),
        Action("Show recent DHCP and DNS service logs", (logs("dnsmasq.service"),)),
        warp_restart,
    )


def run_action(action):
    print(f"\n{action.label}\n", flush=True)
    try:
        for command in action.commands:
            try:
                result = subprocess.run(command, timeout=20, check=False)
                if result.returncode:
                    print(f"\n{command[0]} returned status {result.returncode}.", flush=True)
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"\nCould not complete this check: {error}", flush=True)
    except KeyboardInterrupt:
        print("\nAction interrupted.")


def menu(role, actions):
    choices = {str(number): action for number, action in enumerate(actions, 1)}
    while True:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        print(f"Cloudflare Zero Trust Lab / {TITLES[role]}\n")
        for key, action in choices.items():
            print(f"  {key}  {action.label}")
        print("\n  S  Open a shell\n  Q  Disconnect")
        choice = input("\nChoose an action: ").strip().lower()
        if choice == "q":
            return
        if choice == "s":
            shell = "/run/current-system/sw/bin/bash"
            print("Type exit to return to this menu.", flush=True)
            subprocess.run([shell, "-l"], check=False)
            continue
        action = choices.get(choice)
        if action is None:
            input("Choose a listed number, S, or Q. Press Enter to continue.")
            continue
        if action.confirm and input(f"{action.confirm} Continue? [y/N] ").strip().lower() != "y":
            continue
        run_action(action)
        input("\nPress Enter to return to the menu.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TITLES:
        raise SystemExit("A valid lab guest role is required.")
    topology = json.loads(Path(__file__).with_name("topology.json").read_text())
    role = sys.argv[1]
    try:
        menu(role, actions_for(role, topology))
    except (EOFError, KeyboardInterrupt):
        print()
