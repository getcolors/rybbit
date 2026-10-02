/** Recover only an interactive SSH lookup using local Google user ADC. */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { readPars } from "red/cli";
import { runtime } from "red/runtime";
import type { Opts } from "red/workflow";

export const credentialOverrides = [
  "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CREDENTIALS", "GOOGLE_CLOUD_KEYFILE_JSON",
  "GCLOUD_KEYFILE_JSON", "GOOGLE_OAUTH_ACCESS_TOKEN", "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT",
  "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", "CLOUDSDK_AUTH_ACCESS_TOKEN",
  "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
];
export function readLoginPars(opts: Opts, env: Record<string, string | undefined>): Opts {
  // The first CLI overlay lacks a type for this optional boolean.
  return readPars({ ...opts, ...(Object.hasOwn(env, "COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER") ? {"rybbit-ssh-login-browser": true} : {}) }, env);
}
export function localUserAdc(env: Record<string, string | undefined>): boolean {
  if (credentialOverrides.some(key => env[key]?.trim())) return false;
  try {
    const dir = env.CLOUDSDK_CONFIG || join(env.HOME || homedir(), ".config", "gcloud");
    return JSON.parse(readFileSync(join(dir, "application_default_credentials.json"), "utf8")).type === "authorized_user";
  } catch { return false; }
}
export function recoverable(opts: Opts, result: any): boolean {
  const error = result.error;
  return opts["red/event"] === "ssh" && !opts["red/dry-run"] && opts["provider-compute"] === "google" && result.status === "error" &&
    error?.auth_reason === "google_reauth_required" && error.stage === "plan" &&
    JSON.stringify(error.command) === JSON.stringify(["tofu", "plan"]) && error.infrastructure_changes === "none";
}
function explain(result: any, message: string): any {
  const { stderr: _stderr, ...error } = result.error;
  return { ...result, error: { ...error, message } };
}
export const loginRuntime = {
  interactive: () => !!process.stdin.isTTY && !!process.stderr.isTTY,
  localUserAdc,
  announce: (message: string) => console.error(message),
  login: (command: string[]) => runtime.execInherit(command),
};
export async function resolveWithLogin(opts: Opts, env: Record<string, string | undefined>, resolve: () => Promise<any>): Promise<any> {
  const result = await resolve();
  if (!recoverable(opts, result)) return result;
  const local = loginRuntime.localUserAdc(env);
  const command = ["gcloud", "auth", "application-default", "login", ...(opts["rybbit-ssh-login-browser"] === false ? ["--no-launch-browser"] : [])];
  const hint = local ? `Run \`${command.join(" ")}\`, then retry SSH.` : "Renew the configured Google credentials, then retry SSH; automatic login requires local user ADC without credential overrides.";
  if (!local || !loginRuntime.interactive()) return explain(result, "Google authentication expired. " + hint);
  loginRuntime.announce("Google authentication expired. Starting Google sign-in…");
  loginRuntime.announce("$ " + command.join(" "));
  const login = await loginRuntime.login(command);
  if (login.exit !== 0) return { ...explain(result, [126, 127].includes(login.exit) ? "Google sign-in could not start. Check that `gcloud` is installed and available in this terminal, then retry SSH." : "Google sign-in did not complete. SSH was not started; retry SSH to try again."), "rybbit/login-exit": login.exit || 1 };
  loginRuntime.announce("Google authentication complete. Retrying address lookup…");
  const retried = await resolve();
  return recoverable(opts, retried) ? explain(retried, "Google authentication is still expired after login. Check the configured credential source; SSH was not started.") : retried;
}
