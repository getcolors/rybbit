// Desired-state and credential validation, the port of
// io.github.getcolors.rybbit.validate.
//
// Green renders its keys as Clojure keywords, so every message here carries the
// same leading colon — the three colours must report identical errors for one
// colors.yml.

import { parName } from "red/cli";
import type { Opts } from "red/workflow";
import { providers as onceProviders } from "package-once-red";

export const profilePar = parName("profile");

interface ProviderEntry {
  required?: string[];
  secrets?: string[];
  tofuEnv?: Record<string, string>;
}

// provider-compute -> what that choice implies.
//
// `required` are the non-secret keys that provider's template interpolates,
// `secrets` the credentials it needs through COLORS_PAR_*, and `tofuEnv` the
// subset OpenTofu reads from the process environment itself. Keeping the three
// together is what stops a provider being validated against one set of keys and
// run with another -- a stage exporting a credential nobody checked for, or a
// check demanding a key no template uses.
//
// Both providers need firewall sources because this package puts a provider
// firewall in front of the host; ONCE's compute templates have none, so its
// registry entries are shorter.
export const computeProviders: Record<string, ProviderEntry> = {
  digitalocean: {
    required: ["digitalocean-name", "digitalocean-region", "digitalocean-size",
               "digitalocean-image", "digitalocean-ssh-keys",
               "digitalocean-ssh-sources", "digitalocean-http-sources"],
    secrets: ["do-token"],
    tofuEnv: { "do-token": "DIGITALOCEAN_TOKEN" },
  },
  vultr: {
    required: ["vultr-name", "vultr-region", "vultr-plan", "vultr-os-id",
               "vultr-ssh-keys", "vultr-ssh-sources", "vultr-http-sources"],
    secrets: ["vultr-api-key"],
    tofuEnv: { "vultr-api-key": "VULTR_API_KEY" },
  },
};

export const required = [
  "profile", "workdir", "provider-compute", "provider-dns", "provider-backend",
  "compute-prevent-destroy", "rybbit-host", "rybbit-disable-signup",
  "postgres-image", "clickhouse-image", "redis-image",
  "rybbit-backend-image", "rybbit-client-image", "caddy-image",
  "postgres-data-dir", "clickhouse-data-dir", "redis-data-dir", "rybbit-backup-dir",
  "rybbit-backup-r2-bucket", "rybbit-backup-r2-endpoint",
  "rybbit-backup-r2-region", "rybbit-backup-oncalendar",
  "rybbit-backup-retention-days",
  "r2-bucket", "r2-endpoint",
];

const hostRe = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/;
// name:tag, name@sha256:..., or name:tag@sha256:... A digest is the only
// pin that cannot move under the deployment, so validation must accept it.
const imageRe = /^[^\s:@]+(?:\/[^\s:@]+)*(?::[^\s:@]+|(?::[^\s:@]+)?@sha256:[0-9a-f]{64})$/;

export const imageKeys = [
  "postgres-image", "clickhouse-image", "redis-image",
  "rybbit-backend-image", "rybbit-client-image", "caddy-image",
];

export function missing(value: unknown): boolean {
  return value === null || value === undefined ||
    (typeof value === "string" && value.trim() === "");
}

export function envErrors(env: Record<string, string | undefined>): string[] {
  return String(env[profilePar] ?? "").length
    ? [`${profilePar} is set; profile must come from colors.yml only`]
    : [];
}

export function computeProvider(opts: Opts): ProviderEntry | undefined {
  return computeProviders[String(opts["provider-compute"])];
}

function positiveInt(x: unknown): boolean {
  return typeof x === "number" && Number.isInteger(x) && x > 0;
}

export function stateErrors(opts: Opts): string[] {
  const errors: string[] = [];
  for (const key of [...required, ...(computeProvider(opts)?.required ?? [])]) {
    if (missing(opts[key])) errors.push(`:${key} is required`);
  }
  if (!computeProvider(opts)) {
    errors.push(`:provider-compute must be one of ${Object.keys(computeProviders).sort().join(", ")}`);
  }
  if (opts["provider-dns"] !== "cloudflare") {
    errors.push(":provider-dns must be cloudflare");
  }
  if (!["local", "s3", "r2"].includes(String(opts["provider-backend"]))) {
    errors.push(":provider-backend must be local, s3, or r2");
  }
  if (typeof opts["compute-prevent-destroy"] !== "boolean") {
    errors.push(":compute-prevent-destroy must be true or false");
  }
  if (!(missing(opts["rybbit-host"]) || hostRe.test(String(opts["rybbit-host"])))) {
    errors.push(":rybbit-host must be a fully qualified hostname");
  }
  for (const key of imageKeys) {
    const value = opts[key];
    if (!missing(value) && !imageRe.test(String(value))) {
      errors.push(`:${key} must carry an explicit image tag`);
    }
  }
  for (const key of ["rybbit-backup-retention-days"]) {
    const value = opts[key];
    if (!missing(value) && !positiveInt(value)) {
      errors.push(`:${key} must be a positive integer`);
    }
  }
  if ("digitalocean-vpc-uuid" in opts) {
    errors.push(":digitalocean-vpc-uuid must be absent; the default regional VPC is discovered at runtime");
  }
  if ("digitalocean-vpc-cidr" in opts) {
    errors.push(":digitalocean-vpc-cidr must be absent; this package must not create a VPC");
  }
  return errors;
}

export function backendSecrets(opts: Opts): string[] {
  return (onceProviders as Record<string, Record<string, ProviderEntry>>)
    ["provider-backend"]?.[String(opts["provider-backend"])]?.secrets ?? [];
}

export function secretErrors(opts: Opts): string[] {
  const keys = [
    ...(computeProvider(opts)?.secrets ?? []),
    "cloudflare-api-token",
    "rybbit-backup-r2-access-key-id",
    "rybbit-backup-r2-secret-access-key",
    ...backendSecrets(opts),
  ];
  return [...new Set(keys)].filter((key) => missing(opts[key]))
    .map((key) => `required credential is not set: ${parName(key)}`);
}

export function tofuEnv(opts: Opts, slot: string): Record<string, string> {
  switch (slot) {
    case "provider-compute":
      return computeProvider(opts)?.tofuEnv ?? {};
    case "provider-dns":
      return { "cloudflare-api-token": "CLOUDFLARE_API_TOKEN" };
    case "provider-backend":
      return (onceProviders as Record<string, Record<string, ProviderEntry>>)
        ["provider-backend"]?.[String(opts["provider-backend"])]?.tofuEnv ?? {};
    default:
      return {};
  }
}
