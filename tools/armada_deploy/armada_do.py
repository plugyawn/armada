#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "armada-staging"
DEFAULT_ENV_FILES = [
    REPO_ROOT.parent / "env.local",
    REPO_ROOT / ".env.local",
    REPO_ROOT / ".env",
]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]
        os.environ[key] = value


def load_default_env(env_files: list[str]) -> None:
    paths = [Path(path).expanduser() for path in env_files]
    if not paths:
        paths = DEFAULT_ENV_FILES
    for path in paths:
        load_env_file(path)


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def require_env(label: str, *names: str) -> str:
    value = first_env(*names)
    if value:
        return value
    joined = ", ".join(names)
    raise SystemExit(f"Missing {label}. Set one of: {joined}")


def api_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any | None = None,
    ok_statuses: tuple[int, ...] = (200, 201, 202, 204),
) -> Any:
    data = None
    request_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, method=method, data=data, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status not in ok_statuses:
                raise RuntimeError(f"{method} {url} returned {response.status}")
            payload = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} failed with {error.code}: {detail}") from error
    if not payload:
        return None
    return json.loads(payload)


class DigitalOcean:
    def __init__(self, token: str) -> None:
        self.base_url = "https://api.digitalocean.com/v2"
        self.headers = {"Authorization": f"Bearer {token}"}

    def get(self, path: str) -> Any:
        return api_request("GET", self.base_url + path, headers=self.headers)

    def post(self, path: str, body: Any) -> Any:
        return api_request("POST", self.base_url + path, headers=self.headers, body=body)

    def delete(self, path: str) -> None:
        api_request(
            "DELETE",
            self.base_url + path,
            headers=self.headers,
            ok_statuses=(200, 202, 204),
        )

    def account_status(self) -> str:
        return str(self.get("/account").get("account", {}).get("status", "unknown"))

    def ensure_tag(self, tag: str) -> None:
        try:
            self.post("/tags", {"name": tag})
        except RuntimeError as error:
            if "422" not in str(error):
                raise

    def ensure_ssh_key(self, name: str, public_key: str) -> dict[str, Any]:
        keys = self.get("/account/keys?per_page=200").get("ssh_keys", [])
        for key in keys:
            if key.get("public_key") == public_key:
                return key
        return self.post("/account/keys", {"name": name, "public_key": public_key})["ssh_key"]

    def find_droplet(self, name: str) -> dict[str, Any] | None:
        droplets = self.get("/droplets?per_page=200").get("droplets", [])
        for droplet in droplets:
            if droplet.get("name") == name:
                return droplet
        return None

    def create_droplet(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post("/droplets", body)["droplet"]

    def wait_for_droplet(self, droplet_id: int, *, timeout_seconds: int = 900) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            droplet = self.get(f"/droplets/{droplet_id}")["droplet"]
            ipv4 = droplet_ipv4(droplet)
            status = droplet.get("status")
            print(f"DigitalOcean droplet {droplet_id}: status={status} ipv4={ipv4 or '-'}")
            if status == "active" and ipv4:
                return droplet
            time.sleep(10)
        raise TimeoutError(f"Timed out waiting for droplet {droplet_id}")


class GoDaddy:
    def __init__(self, key: str, secret: str) -> None:
        self.base_url = "https://api.godaddy.com"
        self.headers = {
            "Authorization": f"sso-key {key}:{secret}",
            "Accept": "application/json",
        }

    def get(self, path: str) -> Any:
        return api_request("GET", self.base_url + path, headers=self.headers)

    def put(self, path: str, body: Any) -> Any:
        return api_request(
            "PUT",
            self.base_url + path,
            headers=self.headers,
            body=body,
            ok_statuses=(200, 204),
        )

    def replace_a_record(self, domain: str, name: str, ip_address: str, ttl: int) -> None:
        self.put(
            f"/v1/domains/{domain}/records/A/{name}",
            [{"data": ip_address, "ttl": ttl}],
        )

    def clear_a_record(self, domain: str, name: str) -> None:
        self.put(f"/v1/domains/{domain}/records/A/{name}", [])


def droplet_ipv4(droplet: dict[str, Any]) -> str | None:
    for network in droplet.get("networks", {}).get("v4", []):
        if network.get("type") == "public":
            return str(network.get("ip_address"))
    return None


def read_public_key(path: Path) -> str:
    try:
        return path.expanduser().read_text().strip()
    except FileNotFoundError as error:
        raise SystemExit(f"SSH public key not found: {path}") from error


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_ssh_config(output_dir: Path, ip_address: str, ssh_private_key: Path) -> Path:
    path = output_dir / "ssh_config"
    identity = ssh_private_key.expanduser()
    path.write_text(
        f"""\
Host armada-staging
    HostName {ip_address}
    User zulipdev
    IdentityFile {identity}
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30

Host armada-staging-root
    HostName {ip_address}
    User root
    IdentityFile {identity}
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
"""
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def cloud_init(public_key: str) -> str:
    quoted_key = json.dumps(public_key)
    return f"""#cloud-config
users:
  - default
  - name: zulipdev
    gecos: Zulip development user
    groups: sudo
    shell: /bin/bash
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    ssh_authorized_keys:
      - {quoted_key}
package_update: true
packages:
  - ca-certificates
  - curl
  - git
  - rsync
  - python3
  - python3-venv
  - ufw
runcmd:
  - [bash, -lc, 'install -d -m 755 -o zulipdev -g zulipdev /home/zulipdev/zulip']
  - [bash, -lc, 'ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable']
"""


def create(args: argparse.Namespace) -> None:
    load_default_env(args.env_file)
    do_token = require_env("DigitalOcean token", "DIGITALOCEAN_ACCESS_TOKEN", "DO_API_TOKEN", "DIGITAL_OCEAN_API")
    godaddy_key = require_env("GoDaddy API key", "GODADDY_API_KEY", "GODADDY_API_PROD_KEY")
    godaddy_secret = require_env(
        "GoDaddy API secret", "GODADDY_API_SECRET", "GODADDY_API_PROD_SECRET"
    )

    output_dir = Path(args.output_dir).expanduser()
    fqdn = f"{args.subdomain}.{args.domain}"
    public_key_path = Path(args.ssh_public_key).expanduser()
    private_key_path = Path(args.ssh_private_key).expanduser()
    public_key = read_public_key(public_key_path)

    do = DigitalOcean(do_token)
    print(f"DigitalOcean account status: {do.account_status()}")
    godaddy = GoDaddy(godaddy_key, godaddy_secret)

    for tag in args.tag:
        do.ensure_tag(tag)

    key = do.ensure_ssh_key(args.ssh_key_name, public_key)
    key_id = key.get("id") or key.get("fingerprint")
    if key_id is None:
        raise RuntimeError("DigitalOcean SSH key response did not include id or fingerprint")

    existing = do.find_droplet(args.name)
    if existing is not None and args.recreate:
        print(f"Deleting existing droplet {args.name} ({existing['id']})")
        do.delete(f"/droplets/{existing['id']}")
        time.sleep(10)
        existing = None

    if existing is None:
        print(
            f"Creating droplet {args.name}: region={args.region} size={args.size} image={args.image}"
        )
        droplet = do.create_droplet(
            {
                "name": args.name,
                "region": args.region,
                "size": args.size,
                "image": args.image,
                "ssh_keys": [key_id],
                "backups": False,
                "ipv6": True,
                "monitoring": True,
                "tags": args.tag,
                "user_data": cloud_init(public_key),
            }
        )
    else:
        droplet = existing
        print(f"Reusing droplet {args.name} ({droplet['id']})")

    droplet = do.wait_for_droplet(int(droplet["id"]))
    ip_address = droplet_ipv4(droplet)
    if ip_address is None:
        raise RuntimeError("Droplet has no public IPv4 address")

    print(f"Pointing {fqdn} to {ip_address} via GoDaddy")
    godaddy.replace_a_record(args.domain, args.subdomain, ip_address, args.ttl)

    ssh_config = write_ssh_config(output_dir, ip_address, private_key_path)
    state = {
        "domain": args.domain,
        "droplet_id": droplet["id"],
        "fqdn": fqdn,
        "image": args.image,
        "ip_address": ip_address,
        "name": args.name,
        "output_dir": str(output_dir),
        "region": args.region,
        "size": args.size,
        "ssh_config": str(ssh_config),
        "ssh_private_key": str(private_key_path),
        "ssh_public_key": str(public_key_path),
        "ssh_user": "zulipdev",
        "subdomain": args.subdomain,
        "tags": args.tag,
    }
    write_json(output_dir / "server.json", state)
    print()
    print(f"State: {output_dir / 'server.json'}")
    print(f"URL: https://{fqdn}/devlogin/")
    print(f"SSH: ssh -F {shlex.quote(str(ssh_config))} armada-staging")
    print(
        f"Next: tools/armada_deploy/bootstrap_remote.sh {shlex.quote(str(output_dir / 'server.json'))}"
    )


def destroy(args: argparse.Namespace) -> None:
    load_default_env(args.env_file)
    do_token = require_env(
        "DigitalOcean token", "DIGITALOCEAN_ACCESS_TOKEN", "DO_API_TOKEN", "DIGITAL_OCEAN_API"
    )
    do = DigitalOcean(do_token)
    droplet = do.find_droplet(args.name)
    if droplet is None:
        print(f"No droplet named {args.name} found.")
    else:
        if not args.yes:
            raise SystemExit("Pass --yes to destroy the droplet.")
        print(f"Deleting droplet {args.name} ({droplet['id']})")
        do.delete(f"/droplets/{droplet['id']}")

    if args.delete_dns:
        godaddy_key = require_env("GoDaddy API key", "GODADDY_API_KEY", "GODADDY_API_PROD_KEY")
        godaddy_secret = require_env(
            "GoDaddy API secret", "GODADDY_API_SECRET", "GODADDY_API_PROD_SECRET"
        )
        GoDaddy(godaddy_key, godaddy_secret).clear_a_record(args.domain, args.subdomain)
        print(f"Deleted A record {args.subdomain}.{args.domain}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create the Pear Zulip staging server.")
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="Load secrets from this KEY=VALUE file. Defaults include ../env.local.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.set_defaults(func=create)
    create_parser.add_argument("--name", default="armada-backpropaganda")
    create_parser.add_argument("--domain", default="backpropaganda.net")
    create_parser.add_argument("--subdomain", default="armada")
    create_parser.add_argument("--region", default="nyc3")
    create_parser.add_argument("--size", default="s-4vcpu-8gb")
    create_parser.add_argument("--image", default="ubuntu-24-04-x64")
    create_parser.add_argument("--ttl", type=int, default=600)
    create_parser.add_argument("--tag", action="append", default=["armada", "zulip-dev"])
    create_parser.add_argument("--ssh-key-name", default="armada-staging")
    create_parser.add_argument("--ssh-public-key", default="~/.ssh/id_ed25519.pub")
    create_parser.add_argument("--ssh-private-key", default="~/.ssh/id_ed25519")
    create_parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    create_parser.add_argument("--recreate", action="store_true")

    destroy_parser = subparsers.add_parser("destroy")
    destroy_parser.set_defaults(func=destroy)
    destroy_parser.add_argument("--name", default="armada-backpropaganda")
    destroy_parser.add_argument("--domain", default="backpropaganda.net")
    destroy_parser.add_argument("--subdomain", default="armada")
    destroy_parser.add_argument("--delete-dns", action="store_true")
    destroy_parser.add_argument("--yes", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
