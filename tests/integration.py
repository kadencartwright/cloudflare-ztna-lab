#!/usr/bin/env python3
"""Real network probes. Use 'local' without Cloudflare or select an enrolled scenario."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".lab"
TOPOLOGY = json.loads((ROOT / "topology.json").read_text())
ORIGIN = TOPOLOGY["machines"]["origin"]["ip"]
CLIENTS = ("office", "remote")
PORTS = TOPOLOGY["service_ports"].values()
RUN = uuid.uuid4().hex[:12]
CURL = "CURL_CA_BUNDLE=/etc/cfztna/cabundle.pem curl --noproxy '*' -4 --http1.1 --max-time 12 --silent --show-error"
ARTIFACTS = LAB / "proof" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + RUN)
ARTIFACTS.mkdir(parents=True, mode=0o700)


def ssh(role, label, command, *, stdin=None, allowed=(0,)):
    rendered = command if isinstance(command, str) else shlex.join(command)
    result = subprocess.run(
        ["ssh", "-F", str(LAB / "ssh_config"), role, rendered],
        input=stdin, text=True, capture_output=True, timeout=70,
    )
    target = ARTIFACTS / f"{role}-{label}.txt"
    target.write_text(f"$ {rendered}\nexit={result.returncode}\n{result.stdout}\n{result.stderr}")
    print(f"{role} {label} exit={result.returncode}", flush=True)
    if result.returncode not in allowed:
        raise RuntimeError(f"{role} {label}: expected {allowed}, see {target}")
    return result


def private_socket(role, port, should_connect, label):
    # An SSH failure or missing probe binary is a harness error, never a passing denial.
    ssh(role, f"{label}-{port}", ["nc", "-4", "-z", "-v", "-w", "4", ORIGIN, str(port)],
        allowed=(0,) if should_connect else (1,))


def smb(role):
    password = (LAB / "smb-password").read_text().strip()
    nonce = f"cfztna-{role}-{RUN}"
    script = f"""set -eu
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
umask 077
cat > "$d/auth"
printf '%s\\n' '{nonce}' > "$d/upload"
cd "$d"
smbclient //{ORIGIN}/Work -p 445 -A auth --client-protection=encrypt \
  -c 'put upload {nonce}.txt; get {nonce}.txt download; del {nonce}.txt'
cmp upload download
sha256sum download"""
    ssh(role, "smb-rw", ["bash", "-c", script], stdin=f"username = labshare\npassword = {password}\n")


def pdf_document(text):
    content = f"BT /F1 12 Tf 50 100 Td ({text}) Tj ET\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 220 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets)
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def printer(role):
    nonce = f"cfztna-{role}-{RUN}"
    pdf = pdf_document(nonce)
    # Ship the ASCII PDF in a heredoc and use an actual IPP Print-Job request.
    script = f"""set -eu
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
cd "$d"
cat > document.pdf
cat > print.test <<'IPP'
{{
  NAME "Submit CFZTNA PDF"
  OPERATION Print-Job
  GROUP operation-attributes-tag
  ATTR charset attributes-charset utf-8
  ATTR language attributes-natural-language en
  ATTR uri printer-uri $uri
  ATTR name requesting-user-name lab
  ATTR name job-name {nonce}
  ATTR mimeMediaType document-format application/pdf
  FILE document.pdf
  STATUS successful-ok
  EXPECT job-id OF-TYPE integer
}}
IPP
ipptool -tv ipp://{ORIGIN}:631/ipp/print print.test"""
    ssh(role, "ipp-submit", ["bash", "-c", script], stdin=pdf.decode())
    expected = hashlib.sha256(pdf).hexdigest()
    for _ in range(10):
        evidence = ssh("origin", f"ipp-artifact-{role}", [
            "sudo", "find", "-L", "/var/lib/cfztna-print", "-name", f"*{nonce}*",
            "-type", "f", "-exec", "sha256sum", "{}", "+",
        ]).stdout
        if expected in evidence:
            return
        time.sleep(1)
    raise RuntimeError(f"IPP accepted {nonce} but its exact bytes were not found in the print spool.")


def local():
    ssh("connector", "firewall", "sudo nft list ruleset")
    for port in PORTS:
        private_socket("connector", port, True, "origin-listener")
    smb("connector")
    printer("connector")
    for role in CLIENTS:
        ssh(role, "local-route", ["ip", "-4", "route", "get", ORIGIN])
        for port in PORTS:
            private_socket(role, port, False, "no-direct")
    # The office has a plausible direct next hop. Even with that route, the connector
    # must refuse to forward employee traffic into the resource network.
    ip = TOPOLOGY["machines"]["connector"]["ip"]
    ssh("office", "force-direct", ["sudo", "ip", "route", "add", f"{ORIGIN}/32", "via", ip])
    try:
        for port in PORTS:
            private_socket("office", port, False, "connector-forward-denied")
    finally:
        ssh("office", "remove-direct", ["sudo", "ip", "route", "del", f"{ORIGIN}/32", "via", ip])


def warp_ready(role):
    status = ssh(role, "warp-status", "warp-cli --accept-tos status").stdout
    if not re.search(r"\bConnected\b", status, re.IGNORECASE):
        raise RuntimeError(f"{role} needs a connected organizational WARP registration.")
    ssh(role, "warp-settings", "warp-cli --accept-tos settings")


def https(role):
    normal = ssh(role, "https-control", f"{CURL} --fail -o /dev/null -w '%{{http_code}}\n' https://example.com/")
    if normal.stdout.strip() != "200":
        raise RuntimeError(f"{role} HTTPS control did not return 200.")
    blocked = ssh(role, "https-block", f"{CURL} -i https://example.com/cfztna-lab-block")
    if not re.search(r"^HTTP/1\.1 403\b", blocked.stdout, re.MULTILINE):
        raise RuntimeError(f"{role} HTTPS path policy did not return 403.")
    # Verify the intercepted peer against only the supplied Gateway root.
    ssh(role, "gateway-ca", ["bash", "-o", "pipefail", "-c", f"{CURL} --cacert /etc/cfztna/gateway-ca.pem --fail -o /dev/null -w '%{{certs}}' https://example.com/ | grep -E '^(Subject|Issuer):'"])
    dns = ssh(role, "dns-canary", "dig +tries=1 +time=5 +noall +comments +answer example.net A")
    if re.search(r"\sIN\s+A\s+(?!0\.0\.0\.0(?:\s|$))\d", dns.stdout):
        raise RuntimeError(f"{role} DNS canary unexpectedly resolved to an Internet address.")


def check_route(role, label):
    route = ssh(role, label, ["ip", "-4", "route", "get", ORIGIN]).stdout
    if "dev CloudflareWARP" not in route:
        raise RuntimeError(f"{role} did not select the CloudflareWARP interface; see route artifact.")
    gateway = TOPOLOGY[TOPOLOGY["machines"][role]["segment"]]["gateway"]
    route = ssh(role, "management-route", ["ip", "-4", "route", "get", gateway]).stdout
    if "dev uplink0" not in route:
        raise RuntimeError(f"{role} hypervisor management address was not excluded.")


def identity(scenario):
    ssh("connector", "tunnel-ready", "curl --fail --silent --show-error http://127.0.0.1:20241/ready")
    for port in PORTS:
        private_socket("connector", port, True, "origin-listener")
    for role in CLIENTS:
        warp_ready(role)
        https(role)
        check_route(role, "private-route")
        for port in PORTS:
            private_socket(role, port, scenario == "allow", scenario)
        if scenario == "allow":
            smb(role)
            printer(role)


def overlap():
    role = "remote"
    warp_ready(role)
    network = TOPOLOGY["resources"]["cidr"]
    private_socket(role, 445, True, "overlap-before")
    ssh(role, "add-overlap", ["sudo", "ip", "route", "add", network, "dev", "uplink0"])
    try:
        check_route(role, "overlap-private-route")
        smb(role)
    finally:
        ssh(role, "remove-overlap", ["sudo", "ip", "route", "del", network, "dev", "uplink0"])


def outage():
    for role in CLIENTS:
        warp_ready(role)
        private_socket(role, 445, True, "outage-before")
    ssh("connector", "stop-tunnel", "sudo systemctl stop lab-tunnel")
    try:
        for role in CLIENTS:
            private_socket(role, 445, False, "tunnel-outage")
            https(role)
    finally:
        ssh("connector", "restart-tunnel", "sudo systemctl start lab-tunnel")
    wait_for_tcp("office")


def wait_for_tcp(role):
    for attempt in range(15):
        result = ssh(role, f"recovery-{attempt}", ["nc", "-4", "-z", "-w", "3", ORIGIN, "445"], allowed=(0, 1))
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"{role}: private SMB did not recover.")


def fail_closed():
    for role in CLIENTS:
        warp_ready(role)
        https(role)
        rules = ssh(role, "egress-guard-before", "sudo nft list table inet lab_egress").stdout
        if "policy drop" not in rules:
            raise RuntimeError(f"{role}: enable the lab egress guard first.")
        address = ssh(role, "public-ip", f"{CURL} --fail -o /dev/null -w '%{{remote_ip}}' https://example.com/").stdout.strip()
        ssh(role, "stop-warp", "sudo systemctl stop cloudflare-warp")
        try:
            ssh(role, "public-physical-route", ["ip", "-4", "route", "get", address])
            ssh(role, "no-direct-https", f"{CURL} --resolve example.com:443:{shlex.quote(address)} https://example.com/", allowed=(7, 28))
            ssh(role, "no-direct-dns", "dig +tries=1 +time=3 @1.1.1.1 example.com A", allowed=(9,))
            private_socket(role, 445, False, "warp-outage")
            after = ssh(role, "egress-guard-after", "sudo nft list table inet lab_egress").stdout
            before_count = re.search(r"counter packets (\d+)", rules)
            after_count = re.search(r"counter packets (\d+)", after)
            if not before_count or not after_count or int(after_count[1]) <= int(before_count[1]):
                raise RuntimeError(f"{role}: nftables did not record dropped physical egress.")
        finally:
            ssh(role, "restart-warp", "sudo systemctl start cloudflare-warp")
        wait_for_tcp(role)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("local", "allow", "deny-identity", "deny-posture", "overlap", "outage", "fail-closed"))
    args = parser.parse_args()
    if args.scenario != "local" and os.environ.get("CFZTNA_LIVE") != "1":
        print("SKIP: dedicated Cloudflare lab and prepared registrations required; set CFZTNA_LIVE=1.")
        return 77
    print(f"Proof directory: {ARTIFACTS}", flush=True)
    if args.scenario in {"allow", "deny-identity", "deny-posture"}:
        identity(args.scenario)
    else:
        {"local": local, "overlap": overlap, "outage": outage, "fail-closed": fail_closed}[args.scenario]()
    print(f"PASS: {args.scenario}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
