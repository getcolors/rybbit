import type { Opts } from "red/workflow";
import { placeholderKey, planning } from "./compute.ts";
export { identityArgs } from "./access.ts";
export const renderedOnly = planning;
export const withMachineKey = (opts: Opts): Opts =>
  planning(opts)
    ? {
        ...opts,
        "ssh-private-key-path": placeholderKey(opts),
        "rybbit/agent-socket": "/home/build-placeholder/agent.sock",
      }
    : opts;
export const privateKeyPath = (opts: Opts): string => {
  if (!opts["ssh-private-key-path"])
    throw Error("deployment SSH identity unavailable");
  return opts["ssh-private-key-path"];
};
