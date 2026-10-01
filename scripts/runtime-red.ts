/** Pure v2 behavior parity: no provider calls, state or operator files. */
import { readFileSync } from "node:fs";
import * as compute from "../red/src/compute.ts";
import * as access from "../red/src/access.ts";
import * as tools from "../red/src/tools.ts";
import * as workflow from "../red/src/workflow.ts";
import type { Opts } from "red/workflow";
const opts: Opts = {
  ...(Bun.YAML.parse(readFileSync(process.argv[2]!, "utf8")) as Opts),
  workdir: "/tmp/rybbit-runtime-parity",
  "red/event": "build",
};
function graph(event: string) {
  const path: string[] = [];
  let step: string | undefined = "rybbit/start";
  while (step) {
    path.push(step);
    step = workflow.wireFn(step, { ...opts, "red/event": event })?.[1] as
      string | undefined;
  }
  return path;
}
function sourceErrors(suffix: string, value: unknown) {
  const state = { ...opts };
  for (const prefix of ["compute", "rybbit", String(opts["provider-compute"])])
    delete state[prefix + "-" + suffix];
  if (value !== undefined) state["compute-" + suffix] = value;
  return compute.errors(state);
}
const req = compute.request(opts);
const output = {
  backupChildEnv: Object.fromEntries(["create", "build", "delete"].map(event => [event, tools.ansibleSecretEnv({...opts, "red/event": event, "rybbit-backup-r2-access-key-id": "dummy-access", "rybbit-backup-r2-secret-access-key": "dummy-secret", "rybbit-ssh-passphrase": "never-forward"})])),
  legacyReferences: [
    "digitalocean-ssh-keys",
    "vultr-ssh-keys",
    "ssh-key-id",
  ].map((key) => compute.errors({ ...opts, [key]: null })),
  failures: [
    {},
    { error: null },
    { error: { message: null } },
    { error: { message: "refused", stderr: "provider detail" } },
  ].map((result) => compute.failedResult(opts, result)["red/err"]),
  sourceErrors: Object.fromEntries(
    ["ssh-sources", "http-sources"].map((suffix) => [
      suffix,
      Object.fromEntries(
        [
          ["missing", undefined],
          ["false", false],
          ["map", {}],
          ["null", null],
        ].map(([name, value]) => [String(name), sourceErrors(suffix, value)]),
      ),
    ]),
  ),
  errors: {
    missingV2: compute.errors({ ...opts, "compute-api-version": undefined }),
    legacyKey: compute.errors({
      ...opts,
      "ssh-private-key-path": "/tmp/operator-key",
    }),
  },
  requirements: compute.requirements(opts),
  closedHttp: compute.requirements({ ...opts, "compute-http-sources": [] }),
  sourcePrecedence: compute.requirements({
    ...opts,
    "compute-ssh-sources": ["192.0.2.1/32"],
    "rybbit-ssh-sources": ["192.0.2.2/32"],
    [String(opts["provider-compute"]) + "-ssh-sources"]: ["192.0.2.3/32"],
  }).ingress[0]!.sources,
  identity: {
    node: req.node_id,
    state: req.state_filename,
    passphrase: compute.sshRequest(opts).passphrase_env,
    registration: compute.registration(opts),
  },
  ssh: access.sshArgs({
    ...opts,
    ip: "203.0.113.1",
    user: "ubuntu",
    "ssh-private-key-path": "/tmp/public.pub",
    "rybbit/agent-socket": "/tmp/agent.sock",
  }),
  graphs: Object.fromEntries(
    ["create", "delete", "ssh"].map((event) => [event, graph(event)]),
  ),
};
function sorted(value: any): any {
  return Array.isArray(value)
    ? value.map(sorted)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, sorted(value[key])]),
        )
      : value;
}
console.log(JSON.stringify(sorted(output)));
