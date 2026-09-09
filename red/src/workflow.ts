import { readPars, parName } from "red/cli";
import * as dryRun from "red/dry-run";
import { preflight, type PreflightContext } from "red/lifecycle";
import * as progress from "red/progress";
import * as tofu from "red/tofu";
import { adviceAdd, failed, workflow, type Opts, type WireDecl } from "red/workflow";
import * as compute from "./compute.ts";
import * as ssh from "./ssh.ts";
import * as sshConfig from "./ssh-config.ts";
import * as tools from "./tools.ts";
import * as validate from "./validate.ts";

export const defaults: Opts = {
  "provider-compute": validate.defaultComputeProvider, "provider-dns": "cloudflare",
  "provider-backend": "r2", "compute-prevent-destroy": true,
  workdir: ".colors",
};

export async function startStep(opts:Opts,env:Record<string,string|undefined>=process.env):Promise<Opts>{return preflight(opts,{defaults,overlay:readPars,validators:[(_o,e)=>validate.envErrors(e),o=>validate.stateErrors(o),(o,_e,c)=>c.real&&['create','delete'].includes(c.event??'')?validate.secretErrors(o):[],(o,_e,c)=>c.real&&c.event==='delete'&&o['compute-prevent-destroy']?['compute destruction is protected; set COLORS_PAR_COMPUTE_PREVENT_DESTROY=false to delete']:[]],afterValidate:async(o,e,c)=>{if(c.real&&c.event==='delete'){const result=await compute.load(o,e);return !failed(result)&&o.ip&&!result['rybbit/already-destroyed']?{...result,ip:o.ip}:result;}if(c.real&&c.event==='create')return sshConfig.preflight(o);return {...ssh.withMachineKey(o),'red/exit':0};}},env);}

export function wireFn(step: string, runOpts: Opts): WireDecl | undefined {
  if (runOpts["red/event"] === "delete") {
    const graph: Record<string, WireDecl> = {
      "rybbit/start": [startStep, "rybbit/ansible"],
      "rybbit/ansible": [tools.ansibleStep, "rybbit/dns"],
      // The `~/.ssh/config` block goes before the destroy, the opposite of the
      // keypair below. A block that outlives its host is stale but harmless; a
      // key that predeceases its host locks the operator out of a machine that
      // still exists. Both orders are deliberate; see standards/ssh-config.md.
      "rybbit/dns": [tools.dnsStep, "rybbit/ssh-config"],
      "rybbit/ssh-config": [tools.ansibleLocalStep, "rybbit/infrastructure"],
      "rybbit/infrastructure": [tools.infrastructureStep],
    };
    return graph[step];
  }
  const graph: Record<string, WireDecl> = {
    "rybbit/start": [startStep, "rybbit/infrastructure"],
    // After compute, which is where the address first exists, and before the
    // stage that converges the machine.
    "rybbit/infrastructure": [tools.infrastructureStep, "rybbit/ssh-config"],
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
  "rybbit/infrastructure", "rybbit/dns", "rybbit/ssh-config",
  "rybbit/ansible", "rybbit/acceptance",
];

function create() {
  let wf = workflow({ start: "rybbit/start", wireFn, nextFn:(_step, successors, opts)=>opts["rybbit/already-destroyed"]||failed(opts)?[]:(successors??[]).map(step=>[step,opts]) });
  wf = adviceAdd(wf, "rybbit/dns", "before", "rybbit.workflow/backend",
    backendAdvice(tools.dnsTool));
  return dryRun.advise(progress.advise(wf), sideEffecting);
}

export const rybbitWorkflow = create();
