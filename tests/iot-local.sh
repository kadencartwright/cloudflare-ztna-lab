#!/usr/bin/env bash
# Run on the lab host with the gateway unenrolled/disconnected.
set -euo pipefail
if [[ $EUID != 0 ]]; then
    echo "Run with sudo on the lab host." >&2
    exit 1
fi

export LC_ALL=C
bridge=$(virsh -c qemu:///system net-info cfztna-iot | awk '$1 == "Bridge:" { print $2 }')
test -n "$bridge"
ns="cfztna-iot-test-$$"
link="iotv$$"
work=$(mktemp -d)
cleanup() {
    ip netns del "$ns"
    unlink "$work/lease"
    rmdir "$work"
}
ip netns add "$ns"
trap cleanup EXIT
ip link add "$link" type veth peer name iot-test netns "$ns"
ip link set "$link" master "$bridge" up
ip -n "$ns" link set lo up
ip -n "$ns" link set iot-test up
cat > "$work/lease" <<'SCRIPT'
#!/bin/sh
set -eu
[ "$1" = bound ] || exit 0
case "$ip" in
    192.168.249.1[0-9][0-9]) ;;
    *) exit 1 ;;
esac
[ "$router" = 192.168.249.1 ]
[ "$dns" = 192.168.249.1 ]
ip addr add "$ip/24" dev "$interface"
ip route add default via "$router"
SCRIPT
chmod 700 "$work/lease"
ip netns exec "$ns" busybox udhcpc -i iot-test -s "$work/lease" -n -q -B -t 10 -T 2
ip -n "$ns" addr show dev iot-test
ip -n "$ns" route show | grep -F 'default via 192.168.249.1'
for endpoint in 1.1.1.1:443 192.168.246.1:22 192.168.250.10:445; do
    if ip netns exec "$ns" nc -z -w 3 "${endpoint%:*}" "${endpoint##*:}"; then
        echo "Unexpected connection to $endpoint while WARP is disconnected." >&2
        exit 1
    fi
done
if ip netns exec "$ns" dig @192.168.249.1 example.com A +short +time=2 +tries=1 | grep -Eq '^[0-9]+\.'; then
    echo "Unexpected public DNS answer while WARP is disconnected." >&2
    exit 1
fi
echo "PASS: IoT DHCP, blocked public and private IPv4, and no fallback DNS"
