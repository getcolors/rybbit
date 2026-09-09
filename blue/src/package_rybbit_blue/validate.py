"""Desired-state and credential validation, the port of
io.github.getcolors.rybbit.validate.

Green renders its keys as Clojure keywords, so every message here carries the
same leading colon — the three colours must report identical errors for one
colors.yml.
"""

from __future__ import annotations

import re

from blue.cli import par_name
from . import compute
from colors_compute.ssh import _mode
from package_once_blue.validate import providers as once_providers

profile_par = par_name("profile")

default_compute_provider = "vultr"

required = [
    "profile", "workdir", "provider-compute", "provider-dns", "provider-backend",
    "compute-prevent-destroy", "rybbit-host", "rybbit-disable-signup",
    "postgres-image", "clickhouse-image", "redis-image",
    "rybbit-backend-image", "rybbit-client-image", "caddy-image",
    "postgres-data-dir", "clickhouse-data-dir", "redis-data-dir", "rybbit-backup-dir",
    "rybbit-backup-r2-bucket", "rybbit-backup-r2-endpoint",
    "rybbit-backup-r2-region", "rybbit-backup-oncalendar",
    "rybbit-backup-retention-days",

]

_host_re = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
# name:tag, name@sha256:..., or name:tag@sha256:... A digest is the only
# pin that cannot move under the deployment, so validation must accept it.
_image_re = re.compile(
    r"[^\s:@]+(?:/[^\s:@]+)*(?::[^\s:@]+|(?::[^\s:@]+)?@sha256:[0-9a-f]{64})")

image_keys = ["postgres-image", "clickhouse-image", "redis-image",
              "rybbit-backend-image", "rybbit-client-image", "caddy-image"]


def missing(x) -> bool:
    return x is None or (isinstance(x, str) and not x.strip())


def env_errors(env: dict) -> list[str]:
    if str(env.get(profile_par) or ""):
        return [f"{profile_par} is set; profile must come from colors.yml only"]
    return []


def keygen(opts):
    try:
        return _mode(opts)['mode'] == 'managed'
    except ValueError:
        return True


def _positive_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def state_errors(opts: dict) -> list[str]:
    """Every problem with desired state at once: the missing keys (this
    package's and the selected provider's), the package's own checks, then the
    Compute Provider Standard's -- selection, the network contract and the
    provider rules, DigitalOcean's VPC refusal among them -- which are ONCE's
    over `spec`."""
    errors: list[str] = []
    for key in required:
        if missing(opts.get(key)):
            errors.append(f":{key} is required")
    if opts.get("provider-dns") != "cloudflare":
        errors.append(":provider-dns must be cloudflare")
    if opts.get("provider-backend") not in ("s3", "r2"):
        errors.append(":provider-backend must be s3 or r2")
    if not isinstance(opts.get("compute-prevent-destroy"), bool):
        errors.append(":compute-prevent-destroy must be true or false")
    if not (missing(opts.get("rybbit-host"))
            or _host_re.fullmatch(str(opts.get("rybbit-host")))):
        errors.append(":rybbit-host must be a fully qualified hostname")
    for key in image_keys:
        value = opts.get(key)
        if not missing(value) and not _image_re.fullmatch(str(value)):
            errors.append(f":{key} must carry an explicit image tag")
    for key in ["rybbit-backup-retention-days"]:
        value = opts.get(key)
        if not missing(value) and not _positive_int(value):
            errors.append(f":{key} must be a positive integer")
    errors += compute.errors(opts)
    return errors


def backend_secrets(opts: dict) -> list[str]:
    entry = once_providers.get("provider-backend", {}).get(opts.get("provider-backend"))
    return (entry or {}).get("secrets", [])


def secret_errors(opts: dict) -> list[str]:
    """Credentials a real create or delete needs: the selected compute
    provider's, Cloudflare's, the backup bucket's, and the backend's."""
    keys = [
            "cloudflare-api-token",
            "rybbit-backup-r2-access-key-id",
            "rybbit-backup-r2-secret-access-key",
            *backend_secrets(opts)]
    return [f"required credential is not set: {par_name(key)}"
            for key in dict.fromkeys(keys) if missing(opts.get(key))]


def tofu_env(opts: dict, slot: str) -> dict[str, str]:
    if slot == "provider-compute":
        return {}
    if slot == "provider-dns":
        return {"cloudflare-api-token": "CLOUDFLARE_API_TOKEN"}
    if slot == "provider-backend":
        entry = once_providers.get("provider-backend", {}).get(
            opts.get("provider-backend"))
        return (entry or {}).get("tofu-env", {})
    return {}
