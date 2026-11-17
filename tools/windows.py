"""Define reproducible Windows guests; Windows itself is installed from user media."""
import argparse
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

POOL = "cfztna-lab"
ROLES = {"client": (6144, 4, 80), "server": (4096, 2, 64)}
NETWORKS = {"office": "cfztna-office", "home": "cfztna-home", "server": "cfztna-resources"}
NAMESPACE = "https://github.com/kadencartwright/cloudflare-ztna-lab"
ET.register_namespace("lab", NAMESPACE)


def virsh(*args, **kwargs):
    return subprocess.run(["virsh", "-c", "qemu:///system", *args], check=True, text=True, **kwargs)


def domain_name(role):
    return f"cfztna-win-{role}"


def domain_xml(role, iso, network, disk_path):
    name = domain_name(role)
    ram, cpus, _ = ROLES[role]
    # The media path is added as an XML attribute so special characters are escaped.
    domain = ET.fromstring(f"""<domain type="kvm">
      <name>{name}</name>
      <memory unit="MiB">{ram}</memory>
      <vcpu>{cpus}</vcpu>
      <metadata><lab:managed xmlns:lab="{NAMESPACE}" /></metadata>
      <os firmware="efi">
        <type arch="x86_64" machine="q35">hvm</type>
        <firmware>
          <feature enabled="yes" name="secure-boot"/>
          <feature enabled="yes" name="enrolled-keys"/>
        </firmware>
        <boot dev="cdrom"/>
        <boot dev="hd"/>
      </os>
      <features><acpi/><apic/><smm state="on"/></features>
      <cpu mode="host-passthrough"/>
      <clock offset="localtime"/>
      <devices>
        <disk type="file" device="disk">
          <driver name="qemu" type="qcow2"/>
          <source/>
          <target dev="sda" bus="sata"/>
        </disk>
        <disk type="file" device="cdrom">
          <driver name="qemu" type="raw"/>
          <source/>
          <target dev="sdb" bus="sata"/>
          <readonly/>
        </disk>
        <interface type="network">
          <source network="{NETWORKS[network]}"/>
          <model type="e1000e"/>
        </interface>
        <graphics type="vnc" autoport="yes" listen="127.0.0.1"/>
        <video><model type="vga"/></video>
        <tpm model="tpm-crb"><backend type="emulator" version="2.0"/></tpm>
      </devices>
    </domain>""")
    domain.find("devices/disk[@device='cdrom']/source").set("file", str(iso))
    domain.find("devices/disk[@device='disk']/source").set("file", str(disk_path))
    return domain


def define(domain):
    virsh("define", "--validate", "/dev/stdin", input=ET.tostring(domain, encoding="unicode"))


def list_domains():
    return set(virsh("list", "--all", "--name", capture_output=True).stdout.splitlines())


def owned_domain(role):
    name = domain_name(role)
    domain = ET.fromstring(virsh("dumpxml", "--inactive", name, capture_output=True).stdout)
    if domain.find(f"metadata/{{{NAMESPACE}}}managed") is None:
        raise SystemExit(f"Refusing to modify unmanaged VM {name}.")
    return domain


def up(role, iso, location):
    name = domain_name(role)
    if name in list_domains():
        owned_domain(role)
        virsh("start", name)
        return
    if iso is None:
        raise SystemExit("Provide the path to a Windows installation ISO for the first boot.")
    iso = iso.resolve(strict=True)
    if not iso.is_file() or not os.access(iso, os.R_OK):
        raise SystemExit(f"Cannot read {iso}.")
    network = "server" if role == "server" else location
    virsh("net-info", NETWORKS[network], capture_output=True)
    volume = f"{name}.qcow2"
    virsh("vol-create-as", "--pool", POOL, volume, f"{ROLES[role][2]}G", "--format", "qcow2")
    try:
        disk_path = virsh("vol-path", "--pool", POOL, volume, capture_output=True).stdout.strip()
        define(domain_xml(role, iso, network, disk_path))
    except subprocess.CalledProcessError:
        virsh("vol-delete", "--pool", POOL, volume)
        raise
    virsh("start", name)


def require_stopped(name):
    if name in virsh("list", "--name", capture_output=True).stdout.splitlines():
        raise SystemExit(f"Shut down {name} in Windows before continuing.")


def move(location):
    name = domain_name("client")
    require_stopped(name)
    domain = owned_domain("client")
    virsh("net-info", NETWORKS[location], capture_output=True)
    interface = domain.find("devices/interface")
    interface.find("source").set("network", NETWORKS[location])
    port = interface.find("target")
    if port is not None:
        interface.remove(port)
    define(domain)
    virsh("start", name)


def down(role):
    name = domain_name(role)
    require_stopped(name)
    owned_domain(role)
    if input(f"Type {name} to delete its Windows disk and TPM: ") != name:
        raise SystemExit("Cancelled.")
    virsh("undefine", name, "--nvram", "--tpm", "--storage", "sda")


def check_down():
    if {domain_name(role) for role in ROLES}.intersection(list_domains()):
        raise SystemExit("Remove the Windows guests with windows-down before deleting the local lab.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    create = subs.add_parser("up")
    create.add_argument("role", choices=ROLES)
    create.add_argument("iso", nargs="?", type=Path)
    create.add_argument("--location", choices=("office", "home"), default="office")
    relocate = subs.add_parser("move")
    relocate.add_argument("location", choices=("office", "home"))
    delete = subs.add_parser("down")
    delete.add_argument("role", choices=ROLES)
    subs.add_parser("check-down")
    args = parser.parse_args()
    if args.command == "up":
        up(args.role, args.iso, args.location)
    elif args.command == "move":
        move(args.location)
    elif args.command == "down":
        down(args.role)
    else:
        check_down()
