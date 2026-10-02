import {
  writeFileSync,
  mkdirSync,
  lstatSync,
  chmodSync,
  openSync,
  closeSync,
  fsyncSync,
  renameSync,
  rmSync,
  constants,
} from "node:fs";
import { randomUUID } from "node:crypto";
/** RYBBIT owns one greenfield v2 node; downstream compute helpers stay separate. */
import { dirname, resolve, join } from "node:path";
import { stageDir } from "red/cli";
import type { Opts } from "red/workflow";
import {
  registry,
  ssh_plan,
  node_plan,
  compute_node,
  resolve_connection,
} from "colors-compute-red";
import { resolveWithLogin } from "./reauth.ts";
export const nodeId = "rybbit-compute";
export const placeholderResource = {
  status: "ready",
  reference: "ssh-resource:build-placeholder",
  public_key:
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  fingerprint: "SHA256:kmYcvdi2GkPeWxB6XLjrZB8JHsy2Hm8luHMFp9GMvqk",
};
export const planning = (opts: Opts) =>
  opts["red/event"] === "build" || Boolean(opts["red/dry-run"]);
export const sdkWorkdir = (opts: Opts) =>
  resolve(dirname(dirname(stageDir(opts, nodeId))));
export const placeholderKey = (opts: Opts) =>
  `/home/build-placeholder/compute/${opts.profile}/ssh/machine-access/identity.pub`;
export function libraryOptions(opts: Opts): Opts {
  return Object.fromEntries(
    Object.entries(opts).filter(
      ([k]) =>
        !k.includes("/") &&
        ![
          "ssh-private-key-path",
          "ssh-public-key-path",
          "rybbit-ssh-passphrase",
          "colors-compute/node",
        ].includes(k),
    ),
  );
}
export function resource(opts: Opts): any {
  const value =
    opts["rybbit/ssh-resource"] ??
    (planning(opts) ? placeholderResource : undefined);
  if (!value) throw Error("SSH resource unavailable");
  return value;
}
export const registration = (opts: Opts) =>
  Boolean(
    (registry.compute as any)[String(opts["provider-compute"])]?.registration,
  );
export const sshRequest = (opts: Opts) => ({
  name: "machine-access",
  workdir: sdkWorkdir(opts),
  passphrase_env: "COLORS_PAR_RYBBIT_SSH_PASSPHRASE",
});
export const registrationRequest = (opts: Opts) => ({
  name: "machine-access",
  workdir: sdkWorkdir(opts),
  state_filename: "rybbit-ssh-registration.tfstate",
  ssh_resource: resource(opts),
});
export function requirements(opts: Opts) {
  const sources = (suffix: string) => {
    const raw =
      opts["compute-" + suffix] ??
      opts["rybbit-" + suffix] ??
      opts[String(opts["provider-compute"]) + "-" + suffix];
    return typeof raw === "string" ? raw.split(/[,\s]+/).filter(Boolean) : raw;
  };
  const ssh = sources("ssh-sources"),
    http = sources("http-sources");
  if (!Array.isArray(ssh) || !ssh.length)
    throw Error("compute-ssh-sources is required");
  if (!Array.isArray(http)) throw Error("compute-http-sources is required");
  return {
    egress: "all",
    private_filter: false,
    ingress: [
      { id: "ssh", protocol: "tcp", from_port: 22, to_port: 22, sources: ssh },
      ...(http.length
        ? [80, 443].map((port) => ({
            id: "http-" + port,
            protocol: "tcp",
            from_port: port,
            to_port: port,
            sources: http,
          }))
        : []),
      ...(http.length
        ? [
            {
              id: "http3",
              protocol: "udp",
              from_port: 443,
              to_port: 443,
              sources: http,
            },
          ]
        : []),
    ],
  };
}
export function request(opts: Opts): any {
  const provider = String(opts["provider-compute"]);
  return {
    node_id: nodeId,
    state_filename: "rybbit-node-0.tfstate",
    workdir: sdkWorkdir(opts),
    ssh_resource: resource(opts),
    security: requirements(opts),
    ...(opts["compute-network-mode"] !== undefined
      ? { network: { mode: opts["compute-network-mode"] } }
      : {}),
    ...(registration(opts)
      ? {
          ssh_registration:
            opts["rybbit/ssh-registration"] ??
            (planning(opts)
              ? {
                  status: "ready",
                  reference: "registration:build-placeholder",
                  provider,
                  ssh_resource_reference: resource(opts).reference,
                  fingerprint: resource(opts).fingerprint,
                  id: "0",
                }
              : undefined),
        }
      : {}),
  };
}
export function errors(opts: Opts): string[] {
  if (opts["compute-api-version"] !== 2)
    return [
      "compute-api-version must be 2; existing deployments must retain their pinned launchers",
    ];
  if (!["r2", "s3"].includes(String(opts["provider-backend"])))
    return ["compute state requires an s3 or r2 backend"];
  const legacyKeys = [
    "ssh-keygen",
    "ssh-key-path",
    "ssh-private-key-path",
    "ssh-public-key-path",
    "ssh-key-id",
    ...Object.values(registry.compute)
      .flatMap((adapter: any) => [
        adapter["ssh-setting"],
        ...Object.keys(adapter["ssh-aliases"] ?? {}),
      ])
      .filter((key): key is string => typeof key === "string"),
  ];
  if (legacyKeys.some((key) => Object.hasOwn(opts, key)))
    return ["external SSH keys are outside the single-node contract"];
  try {
    const p = { ...opts, "red/dry-run": true };
    ssh_plan(libraryOptions(p), sshRequest(p));
    node_plan(libraryOptions(p), request(p));
    return [];
  } catch (e) {
    return [(e as Error).message];
  }
}
export function params(opts: Opts, result: any): Opts {
  const node = result.params;
  return {
    ...node,
    name: node.name ?? `${opts.profile}-${nodeId}`,
    sudoer: node.sudoer ?? node.user,
    "ssh-keygen": true,
    "ssh-private-key-path":
      opts["ssh-private-key-path"] ??
      (planning(opts) ? placeholderKey(opts) : null),
    "rybbit/agent-socket": opts["rybbit/agent-socket"] ?? null,
  };
}
export function fallbackParams(opts: Opts): Opts {
  if (!planning(opts)) throw Error("compute inventory unavailable");
  return {
    node_id: nodeId,
    provider: opts["provider-compute"],
    name: `${opts.profile}-${nodeId}`,
    ip: "192.0.2.10",
    user:
      (registry.compute as any)[String(opts["provider-compute"])]?.user ??
      "root",
    sudoer:
      (registry.compute as any)[String(opts["provider-compute"])]?.user ??
      "root",
    "ssh-keygen": true,
    "ssh-private-key-path": placeholderKey(opts),
    "rybbit/agent-socket": "/home/build-placeholder/agent.sock",
  };
}
export function failedResult(opts: Opts, result: any): Opts {
  return {
    ...opts,
    "red/exit": result["rybbit/login-exit"] ?? 1,
    "red/err":
      (result.error?.message ?? "compute lifecycle refused") +
      (result.error?.stderr ? "\n" + result.error.stderr : ""),
  };
}
function sorted(value: any): any {
  return Array.isArray(value)
    ? value.map(sorted)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((k) => [k, sorted(value[k])]),
        )
      : value;
}
export function writeBuild(plan: any): void {
  const directory = resolve(plan.directory);
  const paths: string[] = [];
  for (let path = directory; path !== dirname(path); path = dirname(path))
    paths.unshift(path);
  for (const path of paths) {
    try {
      mkdirSync(path, { mode: 0o700 });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
    const info = lstatSync(path);
    if (!info.isDirectory() || info.isSymbolicLink())
      throw Error("unsafe compute directory");
    if (paths.slice(-3).includes(path)) {
      if (info.uid !== process.getuid?.())
        throw Error("unsafe compute directory ownership");
      chmodSync(path, 0o700);
    }
  }
  for (const [filename, document] of Object.entries(plan.documents)) {
    if (!["compute.tf.json", "backend.tf.json"].includes(filename))
      throw Error("unsafe compute filename");
    const path = join(directory, filename);
    try {
      const info = lstatSync(path);
      if (
        !info.isFile() ||
        info.isSymbolicLink() ||
        info.nlink !== 1 ||
        info.uid !== process.getuid?.()
      )
        throw Error("unsafe compute file");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const temporary = join(directory, ".build-" + randomUUID());
    const fd = openSync(
      temporary,
      constants.O_WRONLY |
        constants.O_CREAT |
        constants.O_EXCL |
        constants.O_NOFOLLOW,
      0o600,
    );
    try {
      try {
        writeFileSync(fd, JSON.stringify(sorted(document), null, 2) + "\n");
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
      renameSync(temporary, path);
    } finally {
      rmSync(temporary, { force: true });
    }
  }
}
export async function step(opts: Opts): Promise<Opts> {
  if (
    opts["red/event"] === "delete" &&
    opts["colors-compute/already-destroyed"]
  )
    return opts;
  const result: any = planning(opts)
    ? { ...node_plan(libraryOptions(opts), request(opts)), status: "built" }
    : await compute_node(
        libraryOptions(opts),
        request(opts),
        String(opts["red/event"]),
      );
  if (result.status === "built") writeBuild(result);
  if (!["built", "ready", "destroyed"].includes(result.status))
    return failedResult(opts, result);
  if (result.status === "destroyed") return { ...opts, "red/exit": 0 };
  const adopted = planning(opts) ? fallbackParams(opts) : params(opts, result);
  return {
    ...opts,
    ...adopted,
    "rybbit/compute-params": adopted,
    "colors-compute/node": adopted,
    "red/exit": 0,
  };
}
export async function load(
  opts: Opts,
  env: Record<string, string | undefined> = process.env,
): Promise<Opts> {
  const result: any = ["ssh", "describe"].includes(String(opts["red/event"]))
    ? await resolveWithLogin(opts, env, () => resolve_connection(libraryOptions(opts), request(opts), env))
    : await compute_node(libraryOptions(opts), request(opts), "inspect", env);
  if (result.status === "destroyed" && opts["red/event"] === "delete")
    return { ...opts, "red/exit": 0, "colors-compute/already-destroyed": true };
  if (result.status === "destroyed")
    return { ...opts, "red/exit": 1, "red/err": "compute node is destroyed" };
  if (result.status !== "ready") return failedResult(opts, result);
  if (opts["rybbit/registration-destroyed"])
    return {
      ...opts,
      "red/exit": 1,
      "red/err": "SSH registration is destroyed while compute is still present",
    };
  const adopted = params(opts, result);
  return {
    ...opts,
    ...adopted,
    "rybbit/compute-params": adopted,
    "colors-compute/node": adopted,
    "red/exit": 0,
  };
}

export const infrastructureStep = step;
