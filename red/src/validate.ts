// Desired-state and credential validation, the port of
// io.github.getcolors.rybbit.validate.
//
// Green renders its keys as Clojure keywords, so every message here carries the
// same leading colon — the three colours must report identical errors for one
// colors.yml.

import { parName } from "red/cli";
import type { Opts } from "red/workflow";
import { providers as onceProviders } from "package-once-red";
import * as compute from "./compute.ts";
import {keyMode} from "colors-compute-red";

export const profilePar = parName("profile");

export const defaultComputeProvider="vultr";

export const required = [
  "profile", "workdir", "provider-compute", "provider-dns", "provider-backend",
  "compute-prevent-destroy", "rybbit-host", "rybbit-disable-signup",
  "postgres-image", "clickhouse-image", "redis-image",
  "rybbit-backend-image", "rybbit-client-image", "caddy-image",
  "postgres-data-dir", "clickhouse-data-dir", "redis-data-dir", "rybbit-backup-dir",
  "rybbit-backup-r2-bucket", "rybbit-backup-r2-endpoint",
  "rybbit-backup-r2-region", "rybbit-backup-oncalendar",
  "rybbit-backup-retention-days",

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
export function keygen(opts:Opts){try{return keyMode(opts).mode==='managed';}catch{return true;}}

function positiveInt(x: unknown): boolean {
  return typeof x === "number" && Number.isInteger(x) && x > 0;
}

// Every problem with desired state at once: the missing keys (this package's
// and the selected provider's), the package's own checks, then the Compute
// Provider Standard's -- selection, the network contract and the provider
// rules, DigitalOcean's VPC refusal among them -- which are ONCE's over `spec`.
export function stateErrors(opts: Opts): string[] {
  const errors: string[] = [];
  for (const key of required) {
    if (missing(opts[key])) errors.push(`:${key} is required`);
  }
  if (opts["provider-dns"] !== "cloudflare") {
    errors.push(":provider-dns must be cloudflare");
  }
  if (!["s3", "r2"].includes(String(opts["provider-backend"]))) {
    errors.push(":provider-backend must be s3 or r2");
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
  errors.push(...compute.errors(opts));
  return errors;
}

export function backendSecrets(opts: Opts): string[] {
  return onceProviders["provider-backend"]?.[String(opts["provider-backend"])]?.secrets ?? [];
}

// Credentials a real create or delete needs: the selected compute provider's,
// Cloudflare's, the backup bucket's, and the backend's.
export function secretErrors(opts: Opts): string[] {
  const keys = [

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
      return {};
    case "provider-dns":
      return { "cloudflare-api-token": "CLOUDFLARE_API_TOKEN" };
    case "provider-backend":
      return onceProviders["provider-backend"]?.[String(opts["provider-backend"])]?.tofuEnv ?? {};
    default:
      return {};
  }
}
