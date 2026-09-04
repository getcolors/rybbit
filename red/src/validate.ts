// Desired-state and credential validation, the port of
// io.github.getcolors.rybbit.validate.
//
// Green renders its keys as Clojure keywords, so every message here carries the
// same leading colon — the three colours must report identical errors for one
// colors.yml.

import { parName } from "red/cli";
import type { Opts } from "red/workflow";
import { compute, providers as onceProviders } from "package-once-red";
import { onceSsh } from "./once.ts";

export const profilePar = parName("profile");

// provider-compute -> what that choice implies.
//
// `required` are the non-secret keys that provider's template interpolates,
// `secrets` the credentials it needs through COLORS_PAR_*, and `tofuEnv` the
// subset OpenTofu reads from the process environment itself. Keeping the three
// together is what stops a provider being validated against one set of keys and
// run with another -- a stage exporting a credential nobody checked for, or a
// check demanding a key no template uses. The keys of this map are the
// advertised providers; a provider without a template directory and a golden
// is not advertised.
//
// Both providers need firewall sources because this package puts a provider
// firewall in front of the host; ONCE's compute templates have none, so its
// registry entries are shorter.
//
// Two keys the templates read are deliberately not required. `<provider>-name`
// is an optional override of the profile (Compute Name Standard), and
// `<provider>-ssh-keys` is meaningful by its absence (SSH Keypair Standard).
// Keys of the unselected provider are accepted and ignored, so one colors.yml
// stays portable between providers.
export const computeProviders: compute.Registry = {
  digitalocean: {
    required: ["digitalocean-region", "digitalocean-size", "digitalocean-image",
               "digitalocean-ssh-sources", "digitalocean-http-sources"],
    secrets: ["do-token"],
    tofuEnv: { "do-token": "DIGITALOCEAN_TOKEN" },
  },
  vultr: {
    required: ["vultr-region", "vultr-plan", "vultr-os-id",
               "vultr-ssh-sources", "vultr-http-sources"],
    secrets: ["vultr-api-key"],
    tofuEnv: { "vultr-api-key": "VULTR_API_KEY" },
  },
};

// The provider a deployment created before this package recorded one in its
// compute output must be running. A legacy state -- `params` without
// `provider` -- is whatever this value says it is, and the only legacy state
// this package has is the live Vultr deployment (`rybbit-vultr`, which serves
// rybbit.getcolors.ai). A DigitalOcean default would make the Compute Provider
// Standard's legacy rule refuse every real create and delete on it until it was
// rebuilt, so the default is Vultr even though DigitalOcean was the package's
// first provider. Every fixture and deployment selects its provider explicitly,
// so nothing renders differently for it.
export const defaultComputeProvider = "vultr";

// How this package describes itself to ONCE's `compute`, the Compute Provider
// Standard's operations over a package-owned registry. The registry and the
// default are the data above; `sources` names the firewall lists the templates
// read -- SSH must list at least one CIDR, an empty HTTP list means no public
// HTTP. The name rules are ONCE's.
export const spec: compute.ComputeSpec = {
  registry: computeProviders,
  default: defaultComputeProvider,
  sources: { nonEmpty: ["ssh-sources"], mayBeEmpty: ["http-sources"] },
};

// Every key desired state must carry whichever provider is selected. The
// provider-scoped keys come from `computeProviders`.
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

// `<provider>-<suffix>`: desired state names compute keys after the provider,
// so the shared steps reach them through the selected provider rather than a
// fixed prefix. ONCE's; named here so `tools` reads the same.
export const computeKey = compute.computeKey;

// What this deployment's machine is called: `<provider>-name` when present,
// else the profile (Compute Name Standard). ONCE's; the templates, the
// firewall and the playbook derive every label from this one answer.
export const computeName = compute.computeName;

// Whether this deployment owns its machine keypair. Delegates to ONCE, the
// standard's reference implementation, so one rule decides it everywhere.
export function keygen(opts: Opts): boolean {
  return onceSsh.keygen(opts);
}

// A source list as desired state or an overlay string carries it. ONCE's, so
// the validator and the templates can never disagree about what an entry is.
export const cidrs = compute.cidrs;

function positiveInt(x: unknown): boolean {
  return typeof x === "number" && Number.isInteger(x) && x > 0;
}

// Every problem with desired state at once: the missing keys (this package's
// and the selected provider's), the package's own checks, then the Compute
// Provider Standard's -- selection, the network contract and the provider
// rules, DigitalOcean's VPC refusal among them -- which are ONCE's over `spec`.
export function stateErrors(opts: Opts): string[] {
  const errors: string[] = [];
  for (const key of [...required, ...compute.requiredKeys(spec, opts)]) {
    if (missing(opts[key])) errors.push(`:${key} is required`);
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
  errors.push(...compute.stateErrors(spec, opts));
  return errors;
}

export function backendSecrets(opts: Opts): string[] {
  return onceProviders["provider-backend"]?.[String(opts["provider-backend"])]?.secrets ?? [];
}

// Credentials a real create or delete needs: the selected compute provider's,
// Cloudflare's, the backup bucket's, and the backend's.
export function secretErrors(opts: Opts): string[] {
  const keys = [
    ...compute.secrets(spec, opts),
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
      return compute.tofuEnv(spec, opts);
    case "provider-dns":
      return { "cloudflare-api-token": "CLOUDFLARE_API_TOKEN" };
    case "provider-backend":
      return onceProviders["provider-backend"]?.[String(opts["provider-backend"])]?.tofuEnv ?? {};
    default:
      return {};
  }
}
