#!/usr/bin/env python3
"""Create an offline-only deployment review bundle from public inputs.

This stdlib tool never runs processes, performs network I/O, connects to a
database, extracts a wheel, or loads environment/credential configuration.
"""
from dataclasses import dataclass, fields
import argparse
import hashlib
from email import policy
from email.parser import BytesParser
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
from urllib.parse import urlsplit
import zipfile

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLATFORM_ROOT / "src/platform_service"
STATIC_ROOT = PLATFORM_ROOT / "frontend/dist"
MAX_CONFIG_BYTES = 65536
MAX_WHEEL_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4096
STATIC_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg",
                   ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".map", ".txt"}


class PreparationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(value):
    if not value:
        raise ValueError()


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        value = source.read(limit + 1)
        _require(len(value) <= limit)
        return value


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _dns(host):
    _require(isinstance(host, str) and host.isascii() and len(host) <= 253 and "." in host
             and not host.rsplit(".", 1)[-1].isdigit()
             and not re.fullmatch(r"0x[0-9a-f]+", host.rsplit(".", 1)[-1], re.IGNORECASE))
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError()
    _require(all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                 for label in host.split(".")))
    return host


def _url(value, *, issuer=False, source=False):
    _require(isinstance(value, str) and 1 <= len(value) <= 2048 and value.isascii()
             and all(33 <= ord(char) <= 126 for char in value)
             and not any(char in value for char in "\\%?#@"))
    parsed = urlsplit(value)
    _require(parsed.hostname and parsed.username is None and parsed.password is None
             and not parsed.query and not parsed.fragment)
    port = parsed.port
    _require(port is None or 1 <= port <= 65535)
    _require(parsed.path == "/oidc" if issuer else parsed.path in {"", "/"})
    if source and parsed.scheme == "http":
        _require(ipaddress.ip_address(parsed.hostname).is_loopback)
    else:
        _require(parsed.scheme == "https" and port in {None, 443})
        if source:
            try:
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                _dns(parsed.hostname)
        else:
            _dns(parsed.hostname)
    # Exact issuer spelling, including :443, is an identity key. Origins alone
    # are canonicalized for the fixed DNS Host expected by the reverse proxy.
    return (value if issuer else "https://" + parsed.hostname), parsed.hostname


def _path(value):
    _require(isinstance(value, str) and len(value) <= 512
             and re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*", value)
             and PurePosixPath(value).as_posix() == value
             and not any(part in {".", ".."} for part in PurePosixPath(value).parts))
    return value


@dataclass(frozen=True)
class DeploymentConfig:
    version: int
    public_url: str
    console_public_url: str
    oidc_issuer: str
    source_oidc_issuer: str
    api_port: int
    console_port: int
    release_dir: str
    config_dir: str
    certificate_dir: str

    def __post_init__(self):
        _require(type(self.version) is int and self.version == 1)
        public, api_host = _url(self.public_url)
        console, console_host = _url(self.console_public_url)
        _, issuer_host = _url(self.oidc_issuer, issuer=True)
        _url(self.source_oidc_issuer, issuer=True, source=True)
        _require(len({api_host, console_host, issuer_host}) == 3)
        _require(all(type(port) is int and 1024 <= port <= 65535
                     for port in (self.api_port, self.console_port))
                 and self.api_port != self.console_port)
        for value in (self.release_dir, self.config_dir, self.certificate_dir):
            _path(value)
        object.__setattr__(self, "public_url", public)
        object.__setattr__(self, "console_public_url", console)


def load_config(path):
    try:
        value = json.loads(_read(path, MAX_CONFIG_BYTES), object_pairs_hook=_unique)
        _require(isinstance(value, dict) and set(value) == {field.name for field in fields(DeploymentConfig)})
        return DeploymentConfig(**value)
    except (OSError, ValueError, TypeError, KeyError):
        raise PreparationError("invalid_public_configuration") from None


def _name(name):
    path = PurePosixPath(name)
    return (isinstance(name, str) and name.isascii() and bool(name)
            and "\\" not in name and all(33 <= ord(c) <= 126 for c in name)
            and not path.is_absolute() and path.as_posix() == name
            and all(part not in {".", ".."} for part in path.parts))


def _trusted_files():
    result = {}
    for root, static in ((SOURCE_ROOT, False), (STATIC_ROOT, True)):
        _require(root.is_dir() and not root.is_symlink())
        for path in sorted(root.rglob("*")):
            _require(not path.is_symlink())
            if path.is_dir():
                continue
            if not static and path.suffix not in {".py", ".sql"}:
                continue
            relative = path.relative_to(root).as_posix()
            _require(_name(relative) and not any(part.startswith(".") for part in path.relative_to(root).parts))
            if static:
                _require(path.suffix.lower() in STATIC_SUFFIXES)
            name = "platform_service/" + ("console_static/" if static else "") + relative
            _require(name not in result)
            result[name] = _read(path, MAX_FILE_BYTES)
    _require("platform_service/__init__.py" in result
             and "platform_service/console_static/index.html" in result
             and any(name.endswith(".sql") for name in result))
    _require(sum(map(len, result.values())) <= MAX_TOTAL_BYTES)
    return result


def verify_wheel(path):
    try:
        raw = _read(path, MAX_WHEEL_BYTES)
        expected = _trusted_files()
        project = tomllib.loads(_read(PLATFORM_ROOT / "pyproject.toml", MAX_CONFIG_BYTES).decode())["project"]
        expected_dist = re.sub(r"[-_.]+", "_", project["name"]).lower() + "-" + project["version"] + ".dist-info"
        metadata_value = None
        service = {}
        seen = set()
        metadata = set()
        total = 0
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            _require(0 < len(members) <= MAX_MEMBERS)
            for member in members:
                name = member.filename
                _require(name not in seen and _name(name.rstrip("/") if member.is_dir() else name))
                seen.add(name)
                mode = stat.S_IFMT(member.external_attr >> 16)
                _require(mode in ({0, stat.S_IFDIR} if member.is_dir() else {0, stat.S_IFREG})
                         and not member.flag_bits & 1
                         and member.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                         and 0 <= member.file_size <= MAX_FILE_BYTES)
                total += member.file_size
                _require(total <= MAX_TOTAL_BYTES)
                if name.startswith("platform_service/"):
                    if not member.is_dir():
                        _require(name in expected)
                        with archive.open(member) as stream:
                            value = stream.read(MAX_FILE_BYTES + 1)
                        _require(len(value) == member.file_size and value == expected[name])
                        service[name] = _sha(value)
                else:
                    parts = PurePosixPath(name).parts
                    _require(len(parts) >= 2 and re.fullmatch(r"easel_central_platform-[A-Za-z0-9_.+-]+\.dist-info", parts[0]))
                    _require(parts[1] in {"METADATA", "WHEEL", "RECORD", "licenses"})
                    metadata.add(parts[0])
                    if not member.is_dir():
                        with archive.open(member) as stream:
                            value = stream.read(MAX_FILE_BYTES + 1)
                        _require(len(value) == member.file_size)
                        if name == parts[0] + "/METADATA":
                            metadata_value = value
        _require(set(service) == set(expected) and len(metadata) == 1)
        root = next(iter(metadata))
        _require(root == expected_dist and {root + "/METADATA", root + "/WHEEL", root + "/RECORD"} <= seen)
        _require(metadata_value is not None)
        headers = BytesParser(policy=policy.default).parsebytes(metadata_value)
        _require(len(headers.get_all("Name", [])) == 1 and len(headers.get_all("Version", [])) == 1
                 and re.sub(r"[-_.]+", "-", str(headers["Name"])).lower() == re.sub(r"[-_.]+", "-", project["name"]).lower()
                 and str(headers["Version"]) == project["version"])
        return {"wheel_sha256": _sha(raw), "source_files": dict(sorted(service.items())),
                "metadata_identity_verified": True, "dependency_metadata_authenticated": False}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, zipfile.BadZipFile, EOFError):
        raise PreparationError("invalid_release_wheel") from None


COMMON_UNSET = ("CENTRAL_MIGRATION_DATABASE_URL CENTRAL_CONSOLE_MIGRATION_DATABASE_URL "
    "CENTRAL_INTERNAL_GRANT_POLICY_FILE PYTHONHOME PYTHONPATH PYTHONUSERBASE "
    "LD_PRELOAD LD_LIBRARY_PATH HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy "
    "PGPASSWORD PGSERVICE PGSERVICEFILE PGPASSFILE PGHOST PGPORT PGDATABASE PGUSER PGOPTIONS "
    "WEB_CONCURRENCY UVICORN_WORKERS UVICORN_RELOAD GUNICORN_CMD_ARGS")


def _unit(config, *, console):
    service = "console" if console else "central"
    user = "easel-console" if console else "easel-central"
    port = config.console_port if console else config.api_port
    command = "serve-console" if console else "serve"
    unset = ("CENTRAL_DATABASE_URL CENTRAL_OIDC_CLIENTS CENTRAL_EXECUTION_CONFIG_FILE "
             "CENTRAL_PAYMENTS_CONFIG_FILE CENTRAL_PAYMENTS_MODE CENTRAL_PAYMENTS_WORKER" if console else
             "CENTRAL_CONSOLE_DATABASE_URL CENTRAL_CONSOLE_OIDC_CLIENT_SECRET CENTRAL_CONSOLE_PROVIDER_REGISTRY")
    return f"""# OFFLINE TEMPLATE: complete deployment checks; not an installation or readiness proof.
[Unit]
Description=Easel production {service}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={config.release_dir}
EnvironmentFile={config.config_dir}/{service}.env
Environment=PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment={COMMON_UNSET} {unset}
# CLI uses one process; inherited worker/reload overrides are removed above.
ExecStart={config.release_dir}/.venv/bin/python -I -B -m platform_service {command} --host 127.0.0.1 --port {port}
Restart=on-failure
RestartSec=5s
TimeoutStopSec=150s
KillSignal=SIGTERM
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths={config.release_dir} {config.config_dir}
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=
MemoryMax={"256M" if console else "512M"}
TasksMax=128
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _nginx(config):
    blocks = ["# OFFLINE TEMPLATE: certificates, DNS and proxy operation are NOT verified.\n"]
    for origin, port, size, stream in ((config.public_url, config.api_port, "1m", True),
                                      (config.console_public_url, config.console_port, "64k", False)):
        host = urlsplit(origin).hostname
        role = "api" if stream else "console"
        blocks.append(f"""server {{
    listen 443 ssl;
    server_name {host};
    if ($host != {host}) {{ return 421; }}
    ssl_certificate {config.certificate_dir}/{role}/fullchain.pem;
    ssl_certificate_key {config.certificate_dir}/{role}/privkey.pem;
    client_max_body_size {size};
    # No request line, callback query, Cookie, Authorization or body logs.
    access_log off;
    error_log /dev/null;
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host {host};
        proxy_set_header Origin $http_origin;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        send_timeout 180s;
{"        proxy_buffering off;" if stream else "        # Console remains on its own canonical origin."}
    }}
}}
""")
    return "\n".join(blocks)


def _env(config, *, console):
    header = "# DO NOT START: OFFLINE TEMPLATE ONLY. Every REPLACE value needs server-private configuration.\n"
    header += "# Do not load this .template file; create the separate 0600 runtime env after review.\n"
    if console:
        return header + f"""CENTRAL_CONSOLE_ENABLED=1
CENTRAL_CONSOLE_DATABASE_URL=REPLACE_WITH_LOW_PRIVILEGE_CONSOLE_POSTGRES_URL
CENTRAL_CONSOLE_PUBLIC_URL={config.console_public_url}
CENTRAL_CONSOLE_DB_SCHEMA=REPLACE_WITH_VERIFIED_SCHEMA
CENTRAL_CONSOLE_OIDC_ISSUER={config.oidc_issuer}
CENTRAL_CONSOLE_OIDC_CLIENT_ID=REPLACE_WITH_DEDICATED_CONSOLE_CLIENT_ID
CENTRAL_CONSOLE_OIDC_CLIENT_SECRET=REPLACE_WITH_SERVER_ONLY_CONSOLE_SECRET
CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP=0
CENTRAL_CONSOLE_PAYMENTS_MODE=disabled
CENTRAL_CONSOLE_PROVIDER_REGISTRY=[]
# Console pool is fixed by its Settings defaults: min1/max2. No owner or supplier secret.
"""
    return header + f"""CENTRAL_DATABASE_URL=REPLACE_WITH_LOW_PRIVILEGE_PRODUCT_POSTGRES_URL
CENTRAL_PUBLIC_URL={config.public_url}
CENTRAL_DB_SCHEMA=REPLACE_WITH_VERIFIED_SCHEMA
CENTRAL_OIDC_ISSUER={config.oidc_issuer}
CENTRAL_OIDC_CLIENTS=REPLACE_WITH_PRODUCT_CLIENT_JSON_AND_SERVER_ONLY_SECRET
CENTRAL_ALLOW_LOOPBACK_HTTP=0
CENTRAL_POOL_MIN=1
CENTRAL_POOL_MAX=5
CENTRAL_PAYMENTS_MODE=disabled
CENTRAL_PAYMENTS_WORKER=0
# Execution is disabled until a separately reviewed server-private config is supplied.
CENTRAL_EXECUTION_CONFIG_FILE=REPLACE_WITH_REVIEWED_SERVER_PRIVATE_EXECUTION_CONFIG_PATH
"""


def prepare(config_path, wheel_path, output):
    config = load_config(config_path)
    verified = verify_wheel(wheel_path)
    hosts = [urlsplit(value).hostname for value in
             (config.public_url, config.console_public_url, config.oidc_issuer)]
    reserved = sorted(host for host in hosts if host.endswith((".invalid", ".example", ".test", ".localhost")))
    migration = config.source_oidc_issuer != config.oidc_issuer
    outstanding = ["dns_tls_not_verified", "logto_relocation_and_real_login_not_verified",
        "database_roles_and_migrations_not_verified", "backup_restore_not_verified",
        "pending_reconciliation_not_verified", "real_model_and_billing_not_verified",
        "commercial_payments_disabled_not_verified", "media_and_cloud_not_enabled",
        "capacity_monitoring_and_service_installation_not_verified", "locked_dependencies_not_verified"]
    if migration:
        outstanding.append("identity_migration_required")
    if reserved:
        outstanding.append("reserved_domain_placeholders_must_be_replaced")
    report = {"version": 1, "verification": "offline_only", "production_ready": False,
        "public_url": config.public_url, "console_public_url": config.console_public_url,
        "origin_normalization": "HTTPS default port 443; fixed DNS Host without port",
        "oidc_issuer": config.oidc_issuer, "source_oidc_issuer": config.source_oidc_issuer,
        "identity_migration_required": migration, "reserved_domain_placeholders": bool(reserved),
        "reserved_hosts": reserved, "outstanding_checks": outstanding, **verified}
    values = {"deployment-report.json": json.dumps(report, sort_keys=True, indent=2) + "\n",
        "nginx-central.conf": _nginx(config),
        "easel-central-production.service": _unit(config, console=False),
        "easel-console-production.service": _unit(config, console=True),
        "central.env.template": _env(config, console=False),
        "console.env.template": _env(config, console=True)}
    output = Path(output)
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise PreparationError("output_already_exists") from None
    except OSError:
        raise PreparationError("output_creation_failed") from None
    try:
        output.chmod(0o700)
        for name, value in values.items():
            fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                os.fchmod(target.fileno(), 0o600)
                target.write(value)
        return report
    except OSError:
        raise PreparationError("output_creation_failed") from None


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise PreparationError("invalid_arguments")


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        for argument in ("config", "wheel", "output"):
            parser.add_argument("--" + argument, required=True, type=Path)
        args = parser.parse_args(argv)
        report = prepare(args.config, args.wheel, args.output)
        print(json.dumps(report, sort_keys=True))
        return 0
    except PreparationError as error:
        print(json.dumps({"error": {"code": error.code}}, sort_keys=True))
        return 1
    except Exception:
        print(json.dumps({"error": {"code": "deployment_preparation_failed"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
