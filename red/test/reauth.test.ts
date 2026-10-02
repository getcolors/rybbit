import { expect, test, spyOn } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { credentialOverrides, localUserAdc, loginRuntime, resolveWithLogin } from "../src/reauth.ts";
import { failedResult } from "../src/compute.ts";
const expired = {status: "error", error: {code: "command_failed", stage: "plan", command: ["tofu", "plan"], infrastructure_changes: "none", auth_reason: "google_reauth_required", message: "Required command failed.", stderr: "[structured output suppressed]"}};
const opts = {"red/event": "ssh", "provider-compute": "google"};
for (const [exit, second, count] of [[0, {status: "ready"}, 2], [0, expired, 2], [130, expired, 1], [127, expired, 1]] as const) test(`retry/cancel ${exit} ${second.status}`, async () => {
  let calls = 0;
  const spies = [spyOn(loginRuntime, "localUserAdc").mockReturnValue(true), spyOn(loginRuntime, "interactive").mockReturnValue(true), spyOn(loginRuntime, "announce").mockImplementation(() => {}), spyOn(loginRuntime, "login").mockImplementation(async argv => {
    expect(argv).toEqual(["gcloud", "auth", "application-default", "login", "--no-launch-browser"]);
    return {exit, out: "", err: ""};
  })];
  try {
    const result = await resolveWithLogin({...opts, "rybbit-ssh-login-browser": false}, {}, async () => ++calls === 1 ? expired : second);
    expect(calls).toBe(count);
    if (exit) expect(failedResult({}, result)["red/exit"]).toBe(exit);
    else expect(result.status).toBe(second.status);
    if (second === expired) expect(result.error.stderr).toBeUndefined();
  } finally { spies.forEach(spy => spy.mockRestore()); }
});
const scenarios = [
  [opts, expired, true, false], [opts, expired, false, true],
  ...["create", "delete", "build"].map(event => [{...opts, "red/event": event}, expired, true, true]),
  [{...opts, "red/dry-run": true}, expired, true, true],
  [{...opts, "provider-compute": "aws"}, expired, true, true],
  [opts, {...expired, error: {...expired.error, infrastructure_changes: "possible"}}, true, true],
  [opts, {...expired, error: {...expired.error, auth_reason: undefined}}, true, true],
  [opts, {status: "ready"}, true, true],
];
for (const [index, [options, result, local, tty]] of scenarios.entries()) test(`no unexpected login ${index}`, async () => {
  let calls = 0;
  const spies = [spyOn(loginRuntime, "localUserAdc").mockReturnValue(local as boolean), spyOn(loginRuntime, "interactive").mockReturnValue(tty as boolean), spyOn(loginRuntime, "login").mockImplementation(() => {throw Error("unexpected login");})];
  try { await resolveWithLogin(options as any, {}, async () => {calls++; return result;}); expect(calls).toBe(1); }
  finally { spies.forEach(spy => spy.mockRestore()); }
});
test("only local user ADC", () => {
  const dir = mkdtempSync(join(tmpdir(), "rybbit-adc-")), env = {CLOUDSDK_CONFIG: dir};
  try {
    expect(localUserAdc(env)).toBe(false);
    for (const type of ["authorized_user", "service_account", "external_account", "impersonated_service_account"]) {
      writeFileSync(join(dir, "application_default_credentials.json"), JSON.stringify({type, refresh_token: "PRIVATE-CANARY"}));
      expect(localUserAdc(env)).toBe(type === "authorized_user");
      for (const key of credentialOverrides) expect(localUserAdc({...env, [key]: "override"})).toBe(false);
    }
    writeFileSync(join(dir, "application_default_credentials.json"), "malformed");
    expect(localUserAdc(env)).toBe(false);
  } finally {rmSync(dir, {recursive: true, force: true});}
});

test("login uses SDK inherited process", async () => {
  const { chmodSync, readFileSync } = await import("node:fs");
  const dir = mkdtempSync(join(tmpdir(), "rybbit-login-")), marker = join(dir, "args");
  const oldPath = process.env.PATH, oldMarker = process.env.LOGIN_MARKER;
  const spies = [spyOn(loginRuntime, "localUserAdc").mockReturnValue(true), spyOn(loginRuntime, "interactive").mockReturnValue(true), spyOn(loginRuntime, "announce").mockImplementation(() => {})];
  try {
    writeFileSync(join(dir, "gcloud"), '#!/bin/sh\nprintf "%s\\n" "$@" > "$LOGIN_MARKER"\nexit 0\n');
    chmodSync(join(dir, "gcloud"), 0o700);
    process.env.PATH = dir + ":" + oldPath;
    process.env.LOGIN_MARKER = marker;
    let calls = 0;
    const result = await resolveWithLogin(opts, {}, async () => ++calls === 1 ? expired : {status: "ready"});
    expect(result.status).toBe("ready"); expect(calls).toBe(2);
    expect(readFileSync(marker, "utf8")).toBe("auth\napplication-default\nlogin\n");
  } finally {
    process.env.PATH = oldPath;
    if (oldMarker === undefined) delete process.env.LOGIN_MARKER; else process.env.LOGIN_MARKER = oldMarker;
    spies.forEach(spy => spy.mockRestore()); rmSync(dir, {recursive: true, force: true});
  }
});

test("browser environment option is typed after initial CLI overlay", async () => {
  const { readLoginPars } = await import("../src/reauth.ts");
  for (const [value, expected] of [["false", false], ["true", true], ["FALSE", false], ["invalid", "invalid"]] as const)
    expect(readLoginPars({"rybbit-ssh-login-browser": value}, {COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER: value})["rybbit-ssh-login-browser"]).toBe(expected);
});
