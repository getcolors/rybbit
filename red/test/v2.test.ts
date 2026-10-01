import { expect, test } from "bun:test";
import { runtime } from "red/runtime";
import { readFileSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as access from "../src/access.ts";
import * as compute from "../src/compute.ts";
import * as tools from "../src/tools.ts";
import * as workflow from "../src/workflow.ts";
import type { Opts } from "red/workflow";
const fixture = (): Opts => ({
  ...(Bun.YAML.parse(
    readFileSync(
      join(import.meta.dir, "../../test/fixtures/keygen.yml"),
      "utf8",
    ),
  ) as Opts),
  workdir: ".colors",
  "red/event": "build",
});
test("v2 rejects legacy identities and missing API selection", () => {
  for (const legacy of [
    { "compute-api-version": undefined },
    { "ssh-keygen": false },
    { "ssh-private-key-path": "/tmp/key" },
    { "digitalocean-ssh-keys": [123] },
  ])
    expect(compute.errors({ ...fixture(), ...legacy }).length).toBeGreaterThan(
      0,
    );
});
test("planning never starts an agent or writes authority", async () => {
  const dir = mkdtempSync(join(tmpdir(), "rybbit-plan-"));
  try {
    let opts = await access.scoped(() =>
      access.resourceStep({ ...fixture(), workdir: dir }),
    );
    opts = await access.agentStep(opts, () => {
      throw Error("agent must not run");
    });
    expect(opts["rybbit/agent-socket"]).toBe(
      "/home/build-placeholder/agent.sock",
    );
    expect(existsSync(join(dir, String(opts.profile), "ssh"))).toBe(false);
    expect(existsSync(join(dir, String(opts.profile), ".rybbit.lock"))).toBe(
      true,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
test("access scope finalizes after failure", async () => {
  const events: string[] = [];
  await expect(
    access.scoped(async () => {
      access.registerCleanup(() => {
        events.push("closed");
      });
      throw Error("failed");
    }),
  ).rejects.toThrow("failed");
  expect(events).toEqual(["closed"]);
});
test("delete skips remote cleanup only for confirmed absent compute", async () => {
  const opts = {
    ...fixture(),
    "red/event": "delete",
    "colors-compute/already-destroyed": true,
  };
  expect(
    await tools.ansibleStep(opts, () => {
      throw Error("must not contact host");
    }),
  ).toEqual(opts);
});
test("delete graph retains alias removal and registration cleanup after machine deletion", () => {
  const opts = { ...fixture(), "red/event": "delete" };
  const path: string[] = [];
  let step: string | undefined = "rybbit/start";
  while (step) {
    path.push(step);
    step = workflow.wireFn(step, opts)?.[1] as string | undefined;
  }
  expect(path).toEqual([
    "rybbit/start",
    "rybbit/ssh-config",
    "rybbit/ansible",
    "rybbit/dns",
    "rybbit/infrastructure",
    "rybbit/registration-delete",
  ]);
});
test("public cache and scoped agent are explicit in Ansible and SSH", () => {
  const opts: Opts = {
    ...fixture(),
    ip: "203.0.113.1",
    user: "ubuntu",
    "ssh-private-key-path": "/tmp/public.pub",
    "rybbit/agent-socket": "/tmp/scoped.sock",
  };
  const inventory = JSON.parse(tools.inventory(opts));
  const host = inventory.all.children.rybbit.hosts[String(opts.profile)];
  expect(host.ansible_ssh_private_key_file).toBe("/tmp/public.pub");
  expect(host.ansible_ssh_common_args).toContain(
    "IdentityAgent=/tmp/scoped.sock",
  );
  expect(access.sshArgs(opts)).toContain("IdentityAgent=/tmp/scoped.sock");
  expect(access.sshArgs(opts)).toContain("ForwardAgent=no");
});

test("profile lock excludes concurrent operations and releases after failure", async () => {
  const dir = mkdtempSync(join(tmpdir(), "rybbit-lock-"));
  const opts = { ...fixture(), workdir: dir };
  try {
    await expect(
      access.scoped(async () => {
        await access.lock(opts);
        await expect(access.scoped(() => access.lock(opts))).rejects.toThrow(
          "owns this profile",
        );
        throw Error("deliberate operation failure");
      }),
    ).rejects.toThrow("deliberate operation failure");
    await access.scoped(() => access.lock(opts));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("backup credentials survive SDK child scrubbing without entering generated files", async () => {
  const dir = mkdtempSync(join(tmpdir(), "rybbit-backup-env-"));
  const original = runtime.exec;
  const originalSecret = process.env.COLORS_PAR_UNRELATED_SECRET;
  process.env.COLORS_PAR_UNRELATED_SECRET = "unrelated-secret";
  const accessKey = "test-backup-access-key";
  const secretKey = "test-backup-secret-key";
  let invoked = false;
  try {
    runtime.exec = async (args, config) => {
      expect(args[0]).toBe("ansible-playbook");
      expect(config?.env?.RYBBIT_BACKUP_R2_ACCESS_KEY_ID).toBe(accessKey);
      expect(config?.env?.RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY).toBe(secretKey);
      expect(config?.env?.COLORS_PAR_UNRELATED_SECRET).toBeUndefined();
      const opts: Opts = { ...fixture(), workdir: dir };
      for (const spec of tools.ansibleSpecs(opts)) {
        const content = readFileSync(spec.target, "utf8");
        expect(content).not.toContain(accessKey);
        expect(content).not.toContain(secretKey);
      }
      const main = readFileSync(
        join(dir, String(opts.profile), "rybbit-ansible/main.yml"),
        "utf8",
      );
      expect(main).toContain("lookup('env','RYBBIT_BACKUP_R2_ACCESS_KEY_ID')");
      expect(main).toContain(
        "lookup('env','RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY')",
      );
      invoked = true;
      return { exit: 0, out: "", err: "" };
    };
    const result = await tools.ansibleStep({
      ...fixture(),
      workdir: dir,
      "red/event": "create",
      ip: "203.0.113.1",
      user: "ubuntu",
      name: "test-node",
      "rybbit-backup-r2-access-key-id": accessKey,
      "rybbit-backup-r2-secret-access-key": secretKey,
    });
    expect(result["red/exit"]).toBe(0);
    expect(invoked).toBe(true);
  } finally {
    runtime.exec = original;
    if (originalSecret === undefined)
      delete process.env.COLORS_PAR_UNRELATED_SECRET;
    else process.env.COLORS_PAR_UNRELATED_SECRET = originalSecret;
    rmSync(dir, { recursive: true, force: true });
  }
});
