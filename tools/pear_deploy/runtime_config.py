#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

AUTH_ENV_PATH = Path("/etc/pear/caddy-basic-auth.env")
RUNTIME_FILES = {
    Path("tools/pear_deploy/runtime/Caddyfile"): (Path("/etc/caddy/Caddyfile"), 0o644),
    Path("tools/pear_deploy/runtime/caddy-pear-basic-auth.conf"): (
        Path("/etc/systemd/system/caddy.service.d/pear-basic-auth.conf"),
        0o644,
    ),
    Path("tools/pear_deploy/runtime/pear-run-zulip-dev"): (
        Path("/usr/local/bin/pear-run-zulip-dev"),
        0o755,
    ),
    Path("tools/pear_deploy/runtime/pear-zulip-dev.service"): (
        Path("/etc/systemd/system/pear-zulip-dev.service"),
        0o644,
    ),
}
PEAR_ENV_PATH = Path("/home/zulipdev/pear.env")
PRODUCTION_ASSET_ENV = "ZULIP_DEV_PRODUCTION_ASSETS"

FQDN_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
USERNAME_RE = re.compile(r"[A-Za-z0-9._@-]{1,128}\Z")
BCRYPT_RE = re.compile(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}\Z")


class RuntimeConfigError(RuntimeError):
    pass


def rooted(root_prefix: Path, absolute_path: Path) -> Path:
    if not absolute_path.is_absolute():
        raise RuntimeConfigError(f"runtime destination must be absolute: {absolute_path}")
    return root_prefix / absolute_path.relative_to("/")


def validate_fqdn(value: str) -> str:
    value = value.lower()
    if FQDN_RE.fullmatch(value) is None:
        raise RuntimeConfigError("invalid Caddy FQDN")
    return value


def validate_username(value: str) -> str:
    if USERNAME_RE.fullmatch(value) is None:
        raise RuntimeConfigError("invalid Caddy basic-auth username")
    return value


def validate_bcrypt(value: str) -> str:
    if BCRYPT_RE.fullmatch(value) is None:
        raise RuntimeConfigError("Caddy password hash must be one bcrypt hash")
    return value


def parse_auth_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeConfigError(f"missing required Caddy auth environment: {path}") from exc

    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise RuntimeConfigError("invalid Caddy auth environment")
        values[key] = value

    required = {
        "PEAR_CADDY_FQDN",
        "PEAR_CADDY_BASICAUTH_USER",
        "PEAR_CADDY_BASICAUTH_HASH",
    }
    if set(values) != required:
        raise RuntimeConfigError("Caddy auth environment must contain exactly the required keys")
    values["PEAR_CADDY_FQDN"] = validate_fqdn(values["PEAR_CADDY_FQDN"])
    values["PEAR_CADDY_BASICAUTH_USER"] = validate_username(values["PEAR_CADDY_BASICAUTH_USER"])
    values["PEAR_CADDY_BASICAUTH_HASH"] = validate_bcrypt(values["PEAR_CADDY_BASICAUTH_HASH"])
    path_stat = path.stat()
    mode = stat.S_IMODE(path_stat.st_mode)
    if mode != 0o600:
        raise RuntimeConfigError(f"Caddy auth environment must have mode 0600, not {mode:04o}")
    if os.geteuid() == 0 and (path_stat.st_uid != 0 or path_stat.st_gid != 0):
        raise RuntimeConfigError("Caddy auth environment must be owned by root:root")
    return values


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_auth_env(path: Path, fqdn: str, username: str, password_hash: str) -> None:
    values = {
        "PEAR_CADDY_FQDN": validate_fqdn(fqdn),
        "PEAR_CADDY_BASICAUTH_USER": validate_username(username),
        "PEAR_CADDY_BASICAUTH_HASH": validate_bcrypt(password_hash),
    }
    content = "".join(f"{key}={value}\n" for key, value in values.items()).encode()
    atomic_write(path, content, 0o600)


def read_hash_from_stdin() -> str:
    lines = sys.stdin.read().splitlines()
    if len(lines) != 1:
        raise RuntimeConfigError("Caddy hash input must contain exactly one line")
    return validate_bcrypt(lines[0])


def upsert_pear_env(path: Path) -> None:
    try:
        old_stat = path.stat()
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeConfigError(f"missing required Pear environment: {path}") from exc

    prefix = f"{PRODUCTION_ASSET_ENV}="
    lines = [line for line in lines if not line.startswith(prefix)]
    lines.append(f"{PRODUCTION_ASSET_ENV}=1")
    atomic_write(path, ("\n".join(lines) + "\n").encode(), 0o600)
    os.chown(path, old_stat.st_uid, old_stat.st_gid)


def copy_runtime_files(repo_root: Path, root_prefix: Path) -> None:
    for source_relative, (destination_absolute, mode) in RUNTIME_FILES.items():
        source = repo_root / source_relative
        if not source.is_file():
            raise RuntimeConfigError(f"missing runtime template: {source}")
        destination = rooted(root_prefix, destination_absolute)
        atomic_write(destination, source.read_bytes(), mode)


def verify_runtime_files(repo_root: Path, root_prefix: Path) -> None:
    for source_relative, (destination_absolute, mode) in RUNTIME_FILES.items():
        source = repo_root / source_relative
        destination = rooted(root_prefix, destination_absolute)
        if not destination.is_file() or destination.read_bytes() != source.read_bytes():
            raise RuntimeConfigError(f"runtime file does not match source: {destination}")
        actual_mode = stat.S_IMODE(destination.stat().st_mode)
        if actual_mode != mode:
            raise RuntimeConfigError(
                f"runtime file {destination} must have mode {mode:04o}, not {actual_mode:04o}"
            )


def verify_pear_env(path: Path) -> None:
    matches = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{PRODUCTION_ASSET_ENV}=")
    ]
    if matches != [f"{PRODUCTION_ASSET_ENV}=1"]:
        raise RuntimeConfigError(
            f"{path} must contain exactly one {PRODUCTION_ASSET_ENV}=1 setting"
        )


def validate_caddy(caddyfile: Path, auth: dict[str, str]) -> None:
    environment = os.environ.copy()
    environment.update(auth)
    try:
        result = subprocess.run(
            ["caddy", "validate", "--config", str(caddyfile), "--adapter", "caddyfile"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise RuntimeConfigError("caddy executable is missing") from exc
    if result.returncode != 0:
        output = (result.stdout + result.stderr).replace(
            auth["PEAR_CADDY_BASICAUTH_HASH"], "<redacted>"
        )
        raise RuntimeConfigError(f"Caddy config validation failed: {output.strip()}")


def install(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    root_prefix = args.root_prefix.resolve()
    auth_path = rooted(root_prefix, AUTH_ENV_PATH)
    if auth_path.exists():
        auth = parse_auth_env(auth_path)
        if auth["PEAR_CADDY_FQDN"] != validate_fqdn(args.fqdn):
            write_auth_env(
                auth_path,
                args.fqdn,
                auth["PEAR_CADDY_BASICAUTH_USER"],
                auth["PEAR_CADDY_BASICAUTH_HASH"],
            )
    else:
        if not args.bootstrap_auth_user or not args.bootstrap_auth_stdin:
            raise RuntimeConfigError(
                "Caddy auth is absent; bootstrap requires a username and bcrypt hash on stdin"
            )
        write_auth_env(
            auth_path,
            args.fqdn,
            args.bootstrap_auth_user,
            read_hash_from_stdin(),
        )

    copy_runtime_files(repo_root, root_prefix)
    pear_env_path = rooted(root_prefix, PEAR_ENV_PATH)
    upsert_pear_env(pear_env_path)
    verify(args)


def verify(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    root_prefix = args.root_prefix.resolve()
    verify_runtime_files(repo_root, root_prefix)
    auth = parse_auth_env(rooted(root_prefix, AUTH_ENV_PATH))
    if auth["PEAR_CADDY_FQDN"] != validate_fqdn(args.fqdn):
        raise RuntimeConfigError("Caddy auth environment FQDN does not match deployment target")
    verify_pear_env(rooted(root_prefix, PEAR_ENV_PATH))
    if not args.skip_caddy_validate:
        validate_caddy(rooted(root_prefix, Path("/etc/caddy/Caddyfile")), auth)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "verify"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--repo-root", type=Path, required=True)
        command_parser.add_argument("--root-prefix", type=Path, default=Path("/"))
        command_parser.add_argument("--fqdn", required=True)
        command_parser.add_argument("--skip-caddy-validate", action="store_true")
        if command == "install":
            command_parser.add_argument("--bootstrap-auth-user")
            command_parser.add_argument("--bootstrap-auth-stdin", action="store_true")
            command_parser.set_defaults(handler=install)
        else:
            command_parser.set_defaults(handler=verify)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (OSError, RuntimeConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
