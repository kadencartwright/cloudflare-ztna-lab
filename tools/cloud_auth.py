"""Bootstrap a scoped Cloudflare account token from one CLI device login."""

import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlencode


LAB = Path(".lab")
CREDENTIALS = LAB / "cloud-auth.json"
CF = ("npx", "--yes", "cf@1.0.0-rc.1")
PROFILE = "cfztna-bootstrap"
DEVICE_URL = "https://dash.cloudflare.com/oauth2/device/verify"
ACCOUNT_SCOPE = "com.cloudflare.api.account"
PERMISSIONS = {
    "Zero Trust": "teams",
    "Cloudflare Tunnel": "argotunnel",
    "Cloudflare One Networks": "teams-networks",
    "Access: Apps and Policies": "access",
    "Access: Service Tokens": "access-service-token",
}
OAUTH_SCOPES = (
    "account:read", "account_api_tokens:create",
    *(f"{scope}.{operation}" for scope in PERMISSIONS.values() for operation in ("read", "write")),
)
PROBES = {
    "Cloudflare Tunnel": "cfd_tunnel?per_page=1",
    "Cloudflare One Networks": "teamnet/routes?per_page=1",
    "WARP profiles": "devices/policies",
    "device posture": "devices/posture",
    "Gateway policies": "gateway/rules",
    "Access applications and enrollment": "access/apps?per_page=1",
    "Access service tokens": "access/service_tokens?per_page=1",
}
ID = re.compile(r"[0-9a-f]{32}")
SECRET = re.compile(r"[A-Za-z0-9_-]{32,}")


class AuthError(Exception):
    pass


def read_credentials(path=CREDENTIALS):
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
        raise AuthError(f"Expected a private regular file with mode 0600 at {path}.")
    data = json.loads(path.read_text())
    account = data["account_id"]
    if (not ID.fullmatch(account) or not ID.fullmatch(data["token_id"])
            or not SECRET.fullmatch(data["token"])
            or data["confirmation"] not in (f"dedicated-lab:{account}", f"approved-poc:{account}")):
        raise AuthError(f"Invalid Cloudflare credential record at {path}.")
    return data


def permission_ids(groups):
    requested = [f"{name} {operation}" for name in PERMISSIONS for operation in ("Read", "Write")]
    by_name = {}
    for group in groups:
        if (group["name"] in requested and ACCOUNT_SCOPE in group["scopes"]
                and group["is_selectable"] and ID.fullmatch(group["id"])):
            by_name.setdefault(group["name"], set()).add(group["id"])
    missing = [name for name in requested if len(by_name.get(name, ())) != 1]
    if missing:
        raise AuthError("Account permission groups unavailable or ambiguous: " + ", ".join(missing))
    return [next(iter(by_name[name])) for name in requested]


def check_token_policy(token, account, expected_ids):
    policies = token.get("policies", [])
    if len(policies) == 1:
        policy = policies[0]
        actual_ids = {group["id"] for group in policy["permission_groups"]}
        if (policy["effect"] == "allow" and actual_ids == set(expected_ids)
                and policy["resources"] == {f"{ACCOUNT_SCOPE}.{account}": "*"}):
            return
    raise AuthError("Cloudflare returned a token policy that differs from the requested account and permissions.")


def api_get(account, token, operation, path):
    connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=20)
    try:
        connection.request("GET", f"/client/v4/accounts/{account}/{path}",
                           headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        if response.status == 200 and payload.get("success"):
            return payload["result"]
        codes = ", ".join(str(item["code"]) for item in payload.get("errors", []) if "code" in item)
        detail = f", Cloudflare code {codes}" if codes else ""
        raise AuthError(f"{operation} failed (HTTP {response.status}{detail}).")
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise AuthError(f"{operation} could not be verified ({type(error).__name__}).") from None
    finally:
        connection.close()


def verify(data):
    account, token = data["account_id"], data["token"]
    result = api_get(account, token, "Account token verification", "tokens/verify")
    if result.get("status") != "active" or result.get("id") != data["token_id"]:
        raise AuthError("Cloudflare did not verify the expected active account token.")
    for operation, path in PROBES.items():
        api_get(account, token, operation, path)


def cf_json(args, environment, directory):
    result = subprocess.run([*CF, *args, "--profile", PROFILE], env=environment,
                            cwd=directory, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Token creation responses can contain credentials. Keep CLI response bodies out of logs.
        raise AuthError(f"Cloudflare CLI failed during {' '.join(args[:3])} (exit {result.returncode}).")
    return json.loads(result.stdout)


def show_device_login(environment, directory):
    command = [*CF, "auth", "create", PROFILE, "--no-browser", "--device", "--scopes", *OAUTH_SCOPES]
    with subprocess.Popen(command, cwd=directory, env=environment, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT) as process:
        try:
            waiting_for_code = False
            for line in process.stdout:
                value = line.strip()
                if value in ("To authorize cf, please visit:", DEVICE_URL):
                    continue
                if value == "and enter the code:":
                    waiting_for_code = True
                    continue
                if waiting_for_code and value:
                    waiting_for_code = False
                    if re.fullmatch(r"[A-Za-z0-9-]{4,32}", value):
                        link = f"{DEVICE_URL}?{urlencode({'user_code': value})}"
                        print(f"Open this link to sign in to Cloudflare\n\n{link}\n"
                              f"Code if prompted: {value}", flush=True)
                        continue
                print(line, end="", flush=True)
            if process.wait():
                raise AuthError("Cloudflare authorization did not complete. Rerun just cloud-auth for a fresh link.")
        finally:
            if process.poll() is None:
                process.terminate()


def store(data):
    output = tempfile.NamedTemporaryFile(mode="w", dir=LAB, delete=False)
    staged = Path(output.name)
    try:
        with output:
            json.dump(data, output)
            output.write("\n")
        staged.replace(CREDENTIALS)
    finally:
        staged.unlink(missing_ok=True)


def authorize(account, confirmation, rotate=False):
    previous_id = None
    if CREDENTIALS.exists():
        existing = read_credentials()
        if (existing["account_id"], existing["confirmation"]) != (account, confirmation):
            raise AuthError("Saved Cloudflare credentials belong to a different account or confirmation.")
        if not rotate:
            verify(existing)
            print("Existing Cloudflare account token and lab API access verified.")
            return
        previous_id = existing["token_id"]

    version = subprocess.check_output(["node", "-p", "process.versions.node"], text=True).strip()
    major, minor, patch = map(int, version.split("."))
    if (minor, patch) < {22: (23, 0), 24: (22, 0), 26: (12, 0)}.get(major, (999, 0)):
        raise AuthError("Use the Nix dev shell or Node.js 22.23+, 24.22+, or 26.12+ on those release lines.")

    with tempfile.TemporaryDirectory(prefix="cloud-oauth-", dir=LAB) as temporary:
        directory = Path(temporary).resolve()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("CLOUDFLARE_")}
        environment.update(XDG_CONFIG_HOME=str(directory), CLOUDFLARE_ACCOUNT_ID=account)
        print(f"Requesting consent to bootstrap Cloudflare account {account}.", flush=True)
        show_device_login(environment, directory)
        print("Checking permissions and creating the account-scoped lab token.", flush=True)
        try:
            groups = cf_json(["accounts", "tokens", "permission-groups", "list"], environment, directory)
            ids = permission_ids(groups)
            policies = [{
                "effect": "allow", "resources": {f"{ACCOUNT_SCOPE}.{account}": "*"},
                "permission_groups": [{"id": identifier} for identifier in ids],
            }]
            policy_path = directory / "policies.json"
            policy_path.write_text(json.dumps(policies))
            created = cf_json(["accounts", "tokens", "create", "--name", "cfztna-lab-oauth",
                               "--policies", f"@{policy_path}"], environment, directory)
        except AuthError as error:
            raise AuthError(f"{error} Check that the signed-in user can provision account API tokens "
                            "and has the requested permissions on this account.") from None

        token_id = created.get("id", "")
        if not ID.fullmatch(token_id):
            raise AuthError("Cloudflare did not return a valid token ID. Check Account API tokens for cfztna-lab-oauth.")
        try:
            token = created.get("value", "")
            if not SECRET.fullmatch(token):
                raise AuthError("Cloudflare did not return the account token secret.")
            check_token_policy(created, account, ids)
            data = {"account_id": account, "confirmation": confirmation, "token_id": token_id, "token": token}
            verify(data)
            store(data)
        except (AuthError, OSError, ValueError, KeyError) as error:
            raise AuthError(f"{error} The new token was not saved. Revoke token {token_id} under Account API tokens.") from None

    print(f"Cloudflare account token and lab API access verified. Saved to {CREDENTIALS} (0600).")
    if previous_id:
        print(f"Revoke the previous token {previous_id} in the Cloudflare dashboard when it is out of use.")


def main():
    os.umask(0o077)
    action, *options = sys.argv[1:] or [""]
    try:
        if action == "load" and not options:
            data = read_credentials()
            print(*[data[key] for key in ("account_id", "confirmation", "token")], sep="\n")
        elif action == "authorize" and options in ([], ["--rotate"]):
            with (LAB / "cloud-oauth.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise AuthError("Cloudflare authentication is already running in this checkout.") from None
                authorize(os.environ["TF_VAR_account_id"],
                          os.environ["TF_VAR_dedicated_lab_account_confirmation"], rotate=bool(options))
        else:
            raise AuthError("Usage: just cloud-auth CONFIRMATION [--rotate]")
    except (AuthError, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        # JSON parsing and subprocess exceptions can refer to response bodies. Do not echo them.
        if isinstance(error, AuthError):
            sys.exit(str(error))
        sys.exit(f"Cloudflare authentication could not complete ({type(error).__name__}).")


if __name__ == "__main__":
    main()
