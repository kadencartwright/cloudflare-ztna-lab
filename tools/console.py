"""Prepare Guacamole connections and relay the isolated guest consoles on loopback."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from .windows import NAMESPACE, domain_name


ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".lab"
STATE = LAB / "console"
MACHINES = json.loads((ROOT / "topology.json").read_text())["machines"]
LOOPBACK = "127.0.0.1"
ORIGIN_RELAY = 24823
WINDOWS_RELAYS = {"client": 24824, "server": 24825}
SSH = ["ssh", "-F", str(LAB / "ssh_config")]


def key_for(role, address):
    def known_key():
        result = subprocess.run(
            ["ssh-keygen", "-F", address, "-f", str(LAB / "known_hosts")],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "ssh-ed25519":
                return f"{fields[1]} {fields[2]}"
        return None

    key = known_key()
    if key is None:
        subprocess.run([*SSH, role, "true"], check=True, timeout=15)
        key = known_key()
    if not key:
        raise ValueError(f"No Ed25519 host key for {role} in {LAB / 'known_hosts'}.")
    return key


def add_connection(user, name, protocol, **parameters):
    connection = ET.SubElement(user, "connection", name=name)
    ET.SubElement(connection, "protocol").text = protocol
    for key, value in parameters.items():
        ET.SubElement(connection, "param", name=key.replace("_", "-")).text = str(value)


def menu_command(role):
    menu = "/etc/cfztna-console/lab_menu.py"
    launch = shlex.join(["/run/current-system/sw/bin/python3", "-u", menu, role])
    return (
        f"if [ -r {menu} ]; then exec {launch}; fi; "
        "printf '%s\\n' 'Menu unavailable. Run just console-up on the lab host while this guest is reachable.'; "
        "exec /run/current-system/sw/bin/bash -l"
    )


def prepare():
    key_path = LAB / "id_ed25519"
    if not key_path.is_file() or not (LAB / "ssh_config").is_file():
        raise SystemExit("Run just up and just configure before starting the console.")
    private_key = key_path.read_text()
    home = STATE / "home"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    password_path = STATE / "password"
    try:
        with password_path.open("x") as stream:
            stream.write(secrets.token_urlsafe(24) + "\n")
    except FileExistsError:
        pass
    password = password_path.read_text().strip()
    if len(password) < 32:
        raise ValueError(f"The generated password file is incomplete: {password_path}")
    credentials = ET.Element("user-mapping")
    # The built-in Guacamole file provider expects MD5 for its hashed format.
    digest = hashlib.md5(password.encode(), usedforsecurity=False).hexdigest()
    user = ET.SubElement(credentials, "authorize", username="lab-console", password=digest, encoding="md5")
    for role, machine in MACHINES.items():
        address = machine["ip"]
        host = LOOPBACK if role == "origin" else address
        port = ORIGIN_RELAY if role == "origin" else 22
        known_host = f"[{host}]:{port}" if port != 22 else host
        add_connection(user, f"{role} (SSH)", "ssh", hostname=host, port=port, username="lab",
                       private_key=private_key, host_key=f"{known_host} {key_for(role, address)}",
                       command=menu_command(role))
    for role, port in WINDOWS_RELAYS.items():
        add_connection(user, f"Windows {role} (VNC)", "vnc", hostname=LOOPBACK, port=port)

    ET.indent(credentials)
    with tempfile.NamedTemporaryFile(dir=home, delete=False) as staged:
        ET.ElementTree(credentials).write(staged, encoding="utf-8", xml_declaration=True)
    Path(staged.name).replace(home / "user-mapping.xml")


def windows_port(xml):
    domain = ET.fromstring(xml)
    graphics = domain.find("devices/graphics[@type='vnc']")
    if (not domain.get("id") or domain.find(f"metadata/{{{NAMESPACE}}}managed") is None
            or graphics is None or graphics.get("listen") != LOOPBACK):
        raise ValueError("Expected a running lab guest with loopback VNC.")
    port = int(graphics.get("port", "0"))
    if not 5900 <= port <= 65535:
        raise ValueError("The guest has no active VNC port.")
    return port


def active_vnc_port(role):
    result = subprocess.run(
        ["virsh", "-c", "qemu:///system", "dumpxml", domain_name(role)],
        check=True, capture_output=True, text=True, timeout=5,
    )
    return windows_port(result.stdout)


async def copy(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def relay(reader, writer, role):
    process = None
    backend = None
    copies = []
    try:
        if role == "origin":
            process = await asyncio.create_subprocess_exec(
                *SSH, "-o", "StrictHostKeyChecking=yes", "-W", f"{MACHINES['origin']['ip']}:22", "connector",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            )
            upstream, backend = process.stdout, process.stdin
        else:
            port = await asyncio.to_thread(active_vnc_port, role)
            upstream, backend = await asyncio.wait_for(asyncio.open_connection(LOOPBACK, port), 5)
        copies = [asyncio.create_task(copy(reader, backend)), asyncio.create_task(copy(upstream, writer))]
        done, _ = await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
        print(f"{role} console unavailable: {error}", flush=True)
    finally:
        for task in copies:
            task.cancel()
        await asyncio.gather(*copies, return_exceptions=True)
        if backend is not None:
            backend.close()
        writer.close()
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            await process.wait()


async def serve():
    listeners = []
    try:
        for role, port in {"origin": ORIGIN_RELAY, **WINDOWS_RELAYS}.items():
            listeners.append(await asyncio.start_server(
                lambda reader, writer, role=role: relay(reader, writer, role), LOOPBACK, port,
            ))
        await asyncio.gather(*(listener.serve_forever() for listener in listeners))
    finally:
        for listener in listeners:
            listener.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "relay"))
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "prepare":
        prepare()
    else:
        asyncio.run(serve())
