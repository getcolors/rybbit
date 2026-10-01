import { readPars } from "red/cli";
import * as dryRun from "red/dry-run";
import { preflight } from "red/lifecycle";
import * as progress from "red/progress";
import * as tofu from "red/tofu";
import {
  adviceAdd,
  failed,
  workflow,
  type Opts,
  type WireDecl,
} from "red/workflow";
import * as compute from "./compute.ts";
import * as access from "./access.ts";
import * as sshConfig from "./ssh-config.ts";
import * as tools from "./tools.ts";
import * as validate from "./validate.ts";
export const defaults: Opts = {
  "provider-compute": validate.defaultComputeProvider,
  "provider-dns": "cloudflare",
  "provider-backend": "r2",
  "compute-prevent-destroy": true,
  workdir: ".colors",
};
export async function startStep(
  opts: Opts,
  env: Record<string, string | undefined> = process.env,
): Promise<Opts> {
  return preflight(
    opts,
    {
      defaults,
      overlay: readPars,
      validators: [
        (_o, e) => validate.envErrors(e),
        (o) => validate.stateErrors(o),
        (o, _e, c) =>
          c.real && ["create", "delete"].includes(c.event ?? "")
            ? validate.secretErrors(o)
            : [],
        (o, _e, c) =>
          c.real && c.event === "delete" && o["compute-prevent-destroy"]
            ? [
                "compute destruction is protected; set COLORS_PAR_COMPUTE_PREVENT_DESTROY=false to delete",
              ]
            : [],
      ],
      afterValidate: async (o, e, c) => {
        if (c.event === "build")
          o = { ...o, workdir: String(o.workdir) + "/build" };
        if (c.real && c.event === "create") {
          o = sshConfig.preflight(o);
          if (failed(o)) return o;
        }
        o = await access.resourceStep(o);
        if (failed(o)) return o;
        o = await access.registrationStep(o);
        if (failed(o)) return o;
        if (
          c.real &&
          (c.event === "delete" ||
            c.event === "ssh" ||
            (c.event === "create" && o["compute-require-existing-state"]))
        ) {
          const originalIp = o.ip;
          o = await compute.load(o, e);
          if (failed(o)) return o;
          if (
            c.event === "delete" &&
            originalIp &&
            !o["colors-compute/already-destroyed"]
          )
            o = { ...o, ip: originalIp };
        }
        if (!o["colors-compute/already-destroyed"])
          o = await access.agentStep(o);
        return o;
      },
    },
    env,
  );
}
export function wireFn(step: string, opts: Opts): WireDecl | undefined {
  const event = opts["red/event"];
  const graph: Record<string, WireDecl> =
    event === "ssh"
      ? {
          "rybbit/start": [startStep, "rybbit/ssh"],
          "rybbit/ssh": [access.sshStep],
        }
      : event === "delete"
        ? {
            "rybbit/start": [startStep, "rybbit/ssh-config"],
            "rybbit/ssh-config": [tools.ansibleLocalStep, "rybbit/ansible"],
            "rybbit/ansible": [tools.ansibleStep, "rybbit/dns"],
            "rybbit/dns": [tools.dnsStep, "rybbit/infrastructure"],
            "rybbit/infrastructure": [
              tools.infrastructureStep,
              "rybbit/registration-delete",
            ],
            "rybbit/registration-delete": [access.registrationDeleteStep],
          }
        : {
            "rybbit/start": [startStep, "rybbit/infrastructure"],
            "rybbit/infrastructure": [
              tools.infrastructureStep,
              "rybbit/ssh-config",
            ],
            "rybbit/ssh-config": [tools.ansibleLocalStep, "rybbit/dns"],
            "rybbit/dns": [tools.dnsStep, "rybbit/ansible"],
            "rybbit/ansible": [tools.ansibleStep, "rybbit/acceptance"],
            "rybbit/acceptance": [tools.acceptanceStep],
          };
  return graph[step];
}
export function backendAdvice(tool: string) {
  return tofu.conventionalBackendAdvice({
    dir: (opts) => tools.toolDir(opts, tool),
    key: (opts) => `${opts.profile ?? ""}/${tool}.tfstate`,
  });
}
export const sideEffecting = [
  "rybbit/infrastructure",
  "rybbit/dns",
  "rybbit/ssh-config",
  "rybbit/ansible",
  "rybbit/acceptance",
  "rybbit/registration-delete",
  "rybbit/ssh",
];
function create() {
  let wf = workflow({
    start: "rybbit/start",
    wireFn,
    nextFn: (_step, successors, opts) =>
      failed(opts) ? [] : (successors ?? []).map((step) => [step, opts]),
  });
  wf = adviceAdd(
    wf,
    "rybbit/dns",
    "before",
    "rybbit.workflow/backend",
    backendAdvice(tools.dnsTool),
  );
  return dryRun.advise(progress.advise(wf), sideEffecting);
}
export const rybbitWorkflow = create();
