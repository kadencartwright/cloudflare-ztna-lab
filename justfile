set positional-arguments

default:
    @just --list

plan:
    ./tools/lab plan

up:
    ./tools/lab up

configure:
    ./tools/lab configure

ssh host *command:
    ./tools/lab ssh "$@"

down:
    ./tools/lab down

test:
    python3 tests/integration.py local

test-cloud scenario="allow":
    python3 tests/integration.py "$1"

cloud-plan:
    ./tools/lab cloud-plan

cloud-apply:
    ./tools/lab cloud-apply

cloud-destroy:
    ./tools/lab cloud-destroy

connector:
    ./tools/lab connector

windows-up role *args:
    python3 tools/windows.py up "$@"

windows-move location:
    python3 tools/windows.py move "$1"

windows-down role:
    python3 tools/windows.py down "$1"

check:
    ./tools/check

trust-ca pem-file:
    CFZTNA_GATEWAY_CA="$(realpath "$1")" ansible-playbook -i .lab/inventory.ini ansible/trust-ca.yml

enroll-warp host:
    ansible-playbook -i .lab/inventory.ini ansible/enroll-warp.yml --limit "$1"

console-up:
    bash tools/console-control up

console-down:
    bash tools/console-control down
