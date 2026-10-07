import { expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as access from "../src/access.ts";
import * as compute from "../src/compute.ts";
import type { Opts } from "red/workflow";

const missing = { status: "error", error: { code: "ssh_authority_missing", message: "authority missing" } };
const verified = { status: "verified", verified_absent: true };
async function fixture(body: (opts: Opts) => Promise<void>) {
  const workdir = mkdtempSync(join(tmpdir(), "rybbit-access-"));
  try {
    await body({ ...(Bun.YAML.parse(readFileSync(join(import.meta.dir, "../../test/fixtures/keygen.yml"), "utf8")) as Opts), workdir, "red/event": "create" });
  } finally {
    rmSync(workdir, { recursive: true, force: true });
  }
}
for (const provider of ["digitalocean", "google"]) {
  test(`fresh ${provider} authority verifies all consumers before creation`, () => fixture(async (base) => {
    const opts: Opts = { ...base, "provider-compute": provider };
    const directory = join(String(opts.workdir), String(opts.profile), compute.nodeId);
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, "compute.tf.json"), "{}");
    const calls: string[] = [];
    const run: any = async (_options: any, request: any, operation: string) => {
      calls.push(operation);
      if (operation === "inspect") {
        expect(request.verified_absent).toBeUndefined();
        return missing;
      }
      expect(request.verified_absent).toBe(true);
      return compute.placeholderResource;
    };
    const verify: any = async (_options: any, request: any) => {
      calls.push("verify");
      expect(request).toEqual({
        workdir: compute.sdkWorkdir(opts),
        consumers: [{ node_id: "rybbit-compute", state_filename: "rybbit-node-0.tfstate" }],
        registrations: provider === "digitalocean" ? [{ name: "machine-access", state_filename: "rybbit-ssh-registration.tfstate" }] : [],
      });
      return verified;
    };
    const result = await access.scoped(() => access.resourceStep(opts, run, verify));
    expect(result["red/exit"]).toBe(0);
    expect(calls).toEqual(["inspect", "verify", "create"]);
  }));
}
for (const [label, inspection, overrides, exit] of [
  ["ready authority", compute.placeholderResource, {}, 0],
  ["required existing state", missing, { "compute-require-existing-state": true }, 1],
  ["delete", missing, { "red/event": "delete" }, 1],
  ["ssh", missing, { "red/event": "ssh" }, 1],
  ["failed read", { status: "error", error: { code: "backend_unreadable", message: "read failed" } }, {}, 1],
] as const) {
  test(`${label} never creates an identity`, () => fixture(async (opts) => {
    const calls: string[] = [];
    const run: any = async (_options: any, _request: any, operation: string) => { calls.push(operation); return inspection; };
    const verify: any = async () => { throw Error("must not verify"); };
    const result = await access.scoped(() => access.resourceStep({ ...opts, ...overrides }, run, verify));
    expect(result["red/exit"]).toBe(exit);
    expect(calls).toEqual(["inspect"]);
  }));
}
for (const verification of [
  { status: "error", error: { message: "consumer survives", stderr: "provider detail" } },
  { status: "verified", verified_absent: false },
  { status: "verified", verified_absent: "true" },
  { status: "unknown", verified_absent: true },
]) {
  test(`incomplete verification ${JSON.stringify(verification)} refuses creation`, () => fixture(async (opts) => {
    const run: any = async (_options: any, _request: any, operation: string) => { expect(operation).toBe("inspect"); return missing; };
    const result = await access.scoped(() => access.resourceStep(opts, run, (async () => verification) as any));
    expect(result["red/exit"]).toBe(1);
    if (verification.error) expect(result["red/err"]).toBe("consumer survives\nprovider detail");
  }));
}
for (const overrides of [{ "red/event": "build" }, { "red/event": "create", "red/dry-run": true }]) {
  test(`planning ${JSON.stringify(overrides)} never queries authority or consumers`, () => fixture(async (opts) => {
    const forbidden: any = async () => { throw Error("planning must not query authority or consumers"); };
    const result = await access.scoped(() => access.resourceStep({ ...opts, ...overrides }, forbidden, forbidden));
    expect(result["red/exit"]).toBe(0);
  }));
}

test("creation failure is reported", () => fixture(async (opts) => {
  const run: any = async (_options: any, _request: any, operation: string) => operation === "inspect"
    ? missing : { status: "error", error: { message: "authority reservation lost" } };
  const result = await access.scoped(() => access.resourceStep(opts, run, (async () => verified) as any));
  expect(result["red/exit"]).toBe(1);
  expect(result["red/err"]).toBe("authority reservation lost");
}));
