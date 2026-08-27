"""Desired-state and credential validation, the port of
io.github.getcolors.rybbit.validate.

Green renders its keys as Clojure keywords, so every message here carries the
same leading colon — the three colours must report identical errors for one
colors.yml.
"""

from __future__ import annotations

import re

from blue.cli import par_name
from package_once_blue.validate import providers as once_providers

profile_par = par_name("profile")

# provider-compute -> what that choice implies.
#
# `required` are the non-secret keys that provider's template interpolates,
# `secrets` the credentials it needs through COLORS_PAR_*, and `tofu-env` the
# subset OpenTofu reads from the process environment itself. Keeping the three
# together is what stops a provider being validated against one set of keys and
# run with another -- a stage exporting a credential nobody checked for, or a
# check demanding a key no template uses.
#
# Both providers need firewall sources because this package puts a provider
# firewall in front of the host; ONCE's compute templates have none, so its
# registry entries are shorter.
compute_providers = {
    "digitalocean": {
        "required": ["digitalocean-name", "digitalocean-region", "digitalocean-size",
                     "digitalocean-image", "digitalocean-ssh-keys",
                     "digitalocean-ssh-sources", "digitalocean-http-sources"],
        "secrets": ["do-token"],
        "tofu-env": {"do-token": "DIGITALOCEAN_TOKEN"},
    },
    "vultr": {
        "required": ["vultr-name", "vultr-region", "vultr-plan", "vultr-os-id",
                     "vultr-ssh-keys", "vultr-ssh-sources", "vultr-http-sources"],
        "secrets": ["vultr-api-key"],
        "tofu-env": {"vultr-api-key": "VULTR_API_KEY"},
    },
}

required = [
    "profile", "workdir", "provider-compute", "provider-dns", "provider-backend",
    "compute-prevent-destroy", "rybbit-host", "rybbit-disable-signup",
    "postgres-image", "clickhouse-image", "redis-image",
    "rybbit-backend-image", "rybbit-client-image", "caddy-image",
    "postgres-data-dir", "clickhouse-data-dir", "redis-data-dir", "rybbit-backup-dir",
    "rybbit-backup-r2-bucket", "rybbit-backup-r2-endpoint",
    "rybbit-backup-r2-region", "rybbit-backup-oncalendar",
    "rybbit-backup-retention-days",
    "r2-bucket", "r2-endpoint",
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


def compute_provider(opts: dict) -> dict | None:
    return compute_providers.get(opts.get("provider-compute"))


def _positive_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def state_errors(opts: dict) -> list[str]:
    errors: list[str] = []
    provider = compute_provider(opts) or {}
    for key in [*required, *provider.get("required", [])]:
        if missing(opts.get(key)):
            errors.append(f":{key} is required")
    if not compute_provider(opts):
        errors.append(":provider-compute must be one of "
                      + ", ".join(sorted(compute_providers)))
    if opts.get("provider-dns") != "cloudflare":
        errors.append(":provider-dns must be cloudflare")
    if opts.get("provider-backend") not in ("local", "s3", "r2"):
        errors.append(":provider-backend must be local, s3, or r2")
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
    if "digitalocean-vpc-uuid" in opts:
        errors.append(":digitalocean-vpc-uuid must be absent; "
                      "the default regional VPC is discovered at runtime")
    if "digitalocean-vpc-cidr" in opts:
        errors.append(":digitalocean-vpc-cidr must be absent; "
                      "this package must not create a VPC")
    return errors


def backend_secrets(opts: dict) -> list[str]:
    entry = once_providers.get("provider-backend", {}).get(opts.get("provider-backend"))
    return (entry or {}).get("secrets", [])


def secret_errors(opts: dict) -> list[str]:
    keys = [*(compute_provider(opts) or {}).get("secrets", []),
            "cloudflare-api-token",
            "rybbit-backup-r2-access-key-id",
            "rybbit-backup-r2-secret-access-key",
            *backend_secrets(opts)]
    return [f"required credential is not set: {par_name(key)}"
            for key in dict.fromkeys(keys) if missing(opts.get(key))]


def tofu_env(opts: dict, slot: str) -> dict[str, str]:
    if slot == "provider-compute":
        return (compute_provider(opts) or {}).get("tofu-env", {})
    if slot == "provider-dns":
        return {"cloudflare-api-token": "CLOUDFLARE_API_TOKEN"}
    if slot == "provider-backend":
        entry = once_providers.get("provider-backend", {}).get(
            opts.get("provider-backend"))
        return (entry or {}).get("tofu-env", {})
    return {}
