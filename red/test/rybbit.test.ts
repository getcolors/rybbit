import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Opts } from "red/workflow";
import * as tools from "../src/tools.ts";
import * as validate from "../src/validate.ts";
import * as workflow from "../src/workflow.ts";

const fixtureFile = join(import.meta.dir, "../../test/fixtures/colors.yml");
const vultrFixtureFile = join(import.meta.dir, "../../test/fixtures/colors-vultr.yml");

function readFixture(path: string, overrides: Opts): Opts {
  const text = readFileSync(path, "utf8").replaceAll("WORKDIR", ".colors");
  return { ...(Bun.YAML.parse(text) as Opts), ...overrides };
}

const fixture = (overrides: Opts = {}) => readFixture(fixtureFile, overrides);
const vultrFixture = (overrides: Opts = {}) => readFixture(vultrFixtureFile, overrides);

const resource = (name: string) =>
  readFileSync(join(import.meta.dir, "../resources", name), "utf8");
const source = readFileSync(join(import.meta.dir, "../src/tools.ts"), "utf8");

// --- validate ----------------------------------------------------------------

describe("validate", () => {
  test("both fixtures are valid", () => {
    expect(validate.stateErrors(fixture())).toEqual([]);
    expect(validate.stateErrors(vultrFixture())).toEqual([]);
  });

  test("compute provider must be one the package has a template for", () => {
    // The registry is the only list; a provider accepted here with no template
    // directory would fail at render time instead of at validation.
    const errors = validate.stateErrors(fixture({ "provider-compute": "hcloud" }));
    expect(errors.some((e) => e.includes("digitalocean, vultr"))).toBe(true);
  });

  test("each provider requires only its own keys", () => {
    // The DigitalOcean keys are not required of a Vultr deployment, and vice
    // versa -- a flat required list made every deployment carry both.
    expect(validate.stateErrors(vultrFixture({ "vultr-plan": null }))
      .some((e) => e.includes("vultr-plan"))).toBe(true);
    expect(validate.stateErrors(vultrFixture())
      .filter((e) => e.includes("digitalocean"))).toEqual([]);
    expect(validate.stateErrors(fixture({ "digitalocean-size": null }))
      .some((e) => e.includes("digitalocean-size"))).toBe(true);
    expect(validate.stateErrors(fixture())
      .filter((e) => e.includes("vultr"))).toEqual([]);
  });

  test("compute credentials follow the provider", () => {
    expect(validate.tofuEnv(fixture(), "provider-compute"))
      .toEqual({ "do-token": "DIGITALOCEAN_TOKEN" });
    expect(validate.tofuEnv(vultrFixture(), "provider-compute"))
      .toEqual({ "vultr-api-key": "VULTR_API_KEY" });
    // And the credential checked is the credential exported: a Vultr deployment
    // must not be asked for a DigitalOcean token it never uses.
    const errors = validate.secretErrors(vultrFixture()).join("\n");
    expect(errors).toContain("COLORS_PAR_VULTR_API_KEY");
    expect(errors).not.toContain("COLORS_PAR_DO_TOKEN");
  });

  test("reports all errors", () => {
    const errors = validate.stateErrors(fixture({
      "rybbit-host": "bad", "postgres-image": "floating",
      "rybbit-backup-retention-days": -1,
      "provider-dns": "other", "digitalocean-vpc-uuid": "forbidden",
    }));
    expect(errors.length).toBeGreaterThanOrEqual(5);
    for (const part of ["host", "image", "retention", "provider-dns", "vpc-uuid"]) {
      expect(errors.some((e) => e.includes(part))).toBe(true);
    }
  });

  test("forbids vpc configuration", () => {
    expect(validate.stateErrors(fixture({ "digitalocean-vpc-cidr": "10.0.0.0/16" }))
      .some((e) => e.includes("must be absent"))).toBe(true);
  });

  test("profile overlay is refused", () => {
    expect(validate.envErrors({ COLORS_PAR_PROFILE: "other" }).length).toBe(1);
    expect(validate.envErrors({})).toEqual([]);
  });

  test("names all package secrets", () => {
    const errors = validate.secretErrors(fixture()).join("\n");
    for (const name of ["COLORS_PAR_DO_TOKEN", "COLORS_PAR_CLOUDFLARE_API_TOKEN",
                        "COLORS_PAR_R2_ACCESS_KEY_ID", "COLORS_PAR_R2_SECRET_ACCESS_KEY",
                        "COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID",
                        "COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"]) {
      expect(errors).toContain(name);
    }
  });

  test("validation accepts a digest pin", () => {
    expect(validate.stateErrors(fixture())).toEqual([]);
    expect(validate.stateErrors(fixture({ "rybbit-backend-image": "no-tag-at-all" }))
      .length).toBeGreaterThan(0);
  });
});

// --- tools -------------------------------------------------------------------

describe("tools", () => {
  test("infrastructure discovers the default vpc", () => {
    const data = tools.infrastructureData(fixture());
    expect(tools.cidrs(data, "digitalocean-http-sources")).toEqual(["0.0.0.0/0", "::/0"]);
  });

  test("compute keys follow the selected provider", () => {
    // Firewall sources are named after the provider, so a step reaching them
    // through a fixed digitalocean- prefix silently renders an empty list on
    // any other provider -- a firewall with no rules rather than an error.
    expect(tools.cidrs(tools.infrastructureData(vultrFixture()), "vultr-http-sources"))
      .toEqual(["0.0.0.0/0", "::/0"]);
    expect(String(tools.infrastructureData(vultrFixture())["ssh-sources-hcl"]))
      .toContain("0.0.0.0/0");
    expect(String(tools.infrastructureData(fixture())["http-sources-hcl"]))
      .toContain("0.0.0.0/0");
  });

  test("hostname is provider-neutral", () => {
    // The playbook used digitalocean-name, which renders empty on Vultr.
    expect(tools.computeName(fixture())).toBe("rybbit-fixture");
    expect(tools.computeName(vultrFixture())).toBe("rybbit-vultr-fixture");
    // Build and dry-run render without a provider name at all.
    expect(tools.computeName(fixture({ "digitalocean-name": null }))).toBe("rybbit-fixture");
    expect(resource("tools/ansible/main.yml")).toContain("<{ compute-name }>");
  });

  test("vultr cidrs split into address and prefix", () => {
    // Vultr takes subnet and subnet_size as separate fields, per address family.
    expect(tools.cidrParts("0.0.0.0/0"))
      .toEqual({ subnet: "0.0.0.0", "subnet-size": 0, "ip-type": "v4" });
    expect(tools.cidrParts("::/0"))
      .toEqual({ subnet: "::", "subnet-size": 0, "ip-type": "v6" });
    expect(tools.cidrParts("203.0.113.4"))
      .toEqual({ subnet: "203.0.113.4", "subnet-size": 32, "ip-type": "v4" });
    expect(tools.cidrParts("2001:db8::1"))
      .toEqual({ subnet: "2001:db8::1", "subnet-size": 128, "ip-type": "v6" });
  });

  test("vultr firewall opens ssh, http and http/3", () => {
    const json = tools.vultrFirewallJson(vultrFixture());
    // HTTP/3 rides UDP 443. Caddy advertises it through alt-svc whether or not
    // the port is reachable, so leaving it closed degrades every visitor to TCP
    // without erroring anywhere.
    expect(json).toContain('"protocol" : "udp"');
    expect(json).toContain('"subnet_size" : 0');
    expect(json).toContain('"subnet" : "::"');
    // Four services across two address families, and nothing else open.
    expect(json.match(/"firewall_group_id"/g)?.length).toBe(8);
    const ports = new Set([...json.matchAll(/"port" : ("\d+")/g)].map((m) => m[1]));
    expect(ports).toEqual(new Set(['"22"', '"80"', '"443"']));
  });

  test("dns is apex and proxied", () => {
    const json = tools.dnsJson(tools.dnsData(fixture({ ip: "192.0.2.10" })));
    expect(json).toContain("rybbit.example.com");
    expect(json).toContain("192.0.2.10");
    // Assert the value, not the key: "proxied" appears in the rendered record
    // either way, so a bare includes check passes on an unproxied record and
    // would not have caught the default being false.
    expect(json).toContain('"proxied" : true');
  });

  test("dns proxying defaults on and can be declined", () => {
    expect(tools.dnsData(fixture())["cloudflare-proxied"]).toBe(true);
    expect(tools.dnsData(fixture({ "cloudflare-proxied": false }))["cloudflare-proxied"])
      .toBe(false);
    expect(tools.dnsJson(tools.dnsData(fixture({ ip: "192.0.2.10",
                                                 "cloudflare-proxied": false }))))
      .toContain('"proxied" : false');
  });

  test("inventory keeps one private target", () => {
    const inventory = tools.inventory(fixture({ ip: "192.0.2.10" }));
    expect(inventory).toContain("192.0.2.10");
    expect(inventory).toContain("rybbit-fixture");
  });

  test("delete cleanup skips when state has no compute", async () => {
    // With the instance already gone the inventory would render 192.0.2.10;
    // there is no host to reach, so the step must not run the playbook and the
    // teardown must continue past it.
    const result = await tools.ansibleStep(fixture({ "red/event": "delete" }),
      () => { throw new Error("playbook must not run"); });
    expect(result["red/exit"]).toBe(0);
    expect(result["rybbit/cleanup"]).toBe("skipped-no-compute");
  });

  test("delete cleanup targets the adopted address", async () => {
    // When the start step recovered the instance address from state, the
    // cleanup playbook runs against it, never the documentation fallback.
    const result = await tools.ansibleStep(
      fixture({ "red/event": "delete", ip: "203.0.113.7" }),
      async (opts) => ({ ...opts, "red/exit": 0, "ran-against": opts.ip }));
    expect(result["ran-against"]).toBe("203.0.113.7");
  });

  test("ingestion is judged by the stored row, not the status", () => {
    expect(tools.ingestionVerdict("200", 4, 5)).toBe("ingested");
    // The failure this gate exists for: the endpoint accepts and nothing lands.
    expect(tools.ingestionVerdict("200", 4, 4)).toBe("dropped");
    expect(tools.ingestionVerdict("202", 4, undefined)).toBe("dropped");
    expect(tools.ingestionVerdict("400", 4, 4)).toBe("rejected");
    expect(tools.ingestionVerdict(undefined, 4, 4)).toBe("unreachable");
  });

  test("backup must be fresh and non-empty", () => {
    const since = Date.parse("2026-08-17T02:30:00Z");
    const entry = (size: number, modTime: string) => ({ Size: size, ModTime: modTime });
    expect(tools.freshBackup([entry(1024, "2026-08-17T02:30:05Z")], since)).toBe(true);
    expect(tools.freshBackup([entry(1024, "2026-08-17T04:30:05+02:00")], since)).toBe(true);
    expect(tools.freshBackup([entry(1024, "2026-08-16T02:30:05Z")], since)).toBe(false);
    expect(tools.freshBackup([entry(0, "2026-08-17T02:30:05Z")], since)).toBe(false);
    expect(tools.freshBackup([], since)).toBe(false);
    expect(tools.freshBackup(undefined, since)).toBe(false);
  });

  test("clickhouse backup is native and has no torn fallback", () => {
    // A hot tar of the data directory races running merges: parts vanish
    // mid-read, tar exits non-zero and set -e aborts before the upload, which is
    // how this deployment ran for hours with nothing reaching R2.
    const backup = resource("tools/ansible/backup");
    expect(backup).toContain("BACKUP DATABASE");
    expect(backup).toContain("/var/lib/clickhouse/backups/");
    expect(/\|\|\s*\{?\s*\n?\s*tar -czf/.test(backup)).toBe(false);
  });

  test("backup proves it restores and prunes the bucket", () => {
    const backup = resource("tools/ansible/backup");
    expect(backup).toContain("CREATE DATABASE");
    expect(backup).toContain("information_schema.tables");
    expect(backup).toContain("rclone delete --min-age");
    const restore = backup.indexOf("restore check restored no tables");
    const upload = backup.indexOf("rclone copyto");
    expect(restore).toBeGreaterThanOrEqual(0);
    expect(restore).toBeLessThan(upload);
  });

  test("signup is desired state", () => {
    // Open registration on a public analytics instance should be a decision in
    // colors.yml, not a constant in the playbook.
    const playbook = resource("tools/ansible/main.yml");
    expect(playbook).not.toContain("DISABLE_SIGNUP=false");
    expect(playbook).toContain("DISABLE_SIGNUP=<{ rybbit-disable-signup }>");
  });

  test("images are pinned, not floating", () => {
    // A moving tag is how the PostHog arm ended up running an application and a
    // plugin server built from different commits, querying a column that did not
    // exist. Digests cannot move under a deployment.
    const text = readFileSync(fixtureFile, "utf8");
    expect(/image:\s*\S+:(latest|master)\s*$/m.test(text)).toBe(false);
    expect(/rybbit-backend-image: \S+@sha256:[0-9a-f]{64}/.test(text)).toBe(true);
    expect(/rybbit-client-image: \S+@sha256:[0-9a-f]{64}/.test(text)).toBe(true);
  });

  test("track payload uses the api discriminator", () => {
    // Rybbit validates a `type` discriminator and rejects the request outright
    // without it; `name` produced a 400 that only surfaced once a site existed.
    expect(source).toContain('type: "pageview"');
    expect(source).toContain("pathname");
    expect(source).not.toContain('name: "pageview"');
  });

  test("acceptance uses a throwaway site", () => {
    // Sending to whichever site was first wrote a synthetic pageview into the
    // operator's real analytics on every converge.
    expect(source).toContain("acceptanceSiteId");
    expect(source).toContain("rybbit-acceptance-site-domain");
    // No falling back to an arbitrary existing site.
    expect(source).not.toContain("select site_id from sites limit 1");
    // Created only when absent, so a converge is idempotent.
    expect(source).toContain("where not exists");
  });

  test("the site id is taken from the last line", () => {
    // psql prints "INSERT 0 1" before the selected id; the whole output is not
    // a site id.
    expect(source).toContain('split("\\n")');
    expect(source).toContain("/^\\d+$/");
  });

  test("a missing compute output fails loudly", () => {
    // The documentation address belongs to build and dry-run. Merging it into a
    // real converge would point Ansible at TEST-NET instead of failing.
    expect(tools.resolvedCompute({}, { ip: "192.0.2.10" }, { ip: "1.2.3.4" }).ip)
      .toBe("1.2.3.4");
    expect(tools.resolvedCompute({}, { ip: "192.0.2.10" }, undefined)["red/exit"]).toBe(1);
    expect(tools.resolvedCompute({}, { ip: "192.0.2.10" }, {})["red/exit"]).toBe(1);
    expect(tools.resolvedCompute({}, { ip: "192.0.2.10" }, { ip: "5.6.7.8" })["red/exit"])
      .toBeUndefined();
  });

  test("signup policy is reapplied on every converge", () => {
    // stack.env is written once to keep its generated secrets, which also froze
    // the signup policy: changing the key afterwards silently did nothing.
    const playbook = resource("tools/ansible/main.yml");
    expect(playbook).toContain("lineinfile");
    expect(playbook).toContain("DISABLE_SIGNUP=<{ rybbit-disable-signup }>");
    // An env_file is read when a container is created, not while it runs.
    expect(playbook).toContain("--force-recreate backend client");
  });

  test("caddy access logging is on and bounded", () => {
    // Access logging is off by default in Caddy, so a successful request left no
    // trace and ingestion had no request-level evidence to debug from.
    expect(resource("tools/ansible/Caddyfile")).toContain("log {");
    expect(resource("tools/ansible/Caddyfile")).toContain("output stdout");
    // On, but bounded: json-file never rotates on its own and this endpoint
    // writes a line per request.
    expect(resource("tools/ansible/compose.yml")).toContain("max-size");
    expect(resource("tools/ansible/compose.yml")).toContain("max-file");
  });

  test("caddy reload is convergent, not change-triggered", () => {
    // The Caddyfile is a single-file bind mount, so copy-by-rename leaves the
    // container on the old inode and `up -d` will not recreate an unchanged
    // service: the host file looked right while Caddy served the old config.
    const playbook = resource("tools/ansible/main.yml");
    expect(playbook).toContain("--force-recreate caddy");
    expect(playbook).toContain("sha256sum /etc/caddy/Caddyfile");
    // And it must run once the stack is up, or it recreates against a compose
    // file that has not been rendered yet.
    const converge = playbook.indexOf("Start Rybbit stack");
    const reload = playbook.indexOf("--force-recreate caddy");
    const health = playbook.indexOf("Wait for backend health endpoint");
    expect(converge).toBeGreaterThanOrEqual(0);
    expect(converge).toBeLessThan(reload);
    expect(reload).toBeLessThan(health);
  });

  test("the access log records the visitor, not the proxy", () => {
    // Behind the Cloudflare proxy every connection arrives from an edge address,
    // so without trusted_proxies Caddy attributes each request to Cloudflare and
    // the access log answers "who sent this?" with the proxy.
    const caddyfile = resource("tools/ansible/Caddyfile");
    expect(caddyfile).toContain("trusted_proxies static");
    expect(caddyfile).toContain("162.158.0.0/15");
    expect(caddyfile).toContain("2400:cb00::/32");
  });
});

// --- workflow ----------------------------------------------------------------

describe("workflow", () => {
  test("build and dry-run need no credentials", async () => {
    expect((await workflow.startStep(fixture({ "red/event": "build" }), {}))["red/exit"])
      .toBe(0);
    expect((await workflow.startStep(
      fixture({ "red/event": "create", "red/dry-run": true }), {}))["red/exit"]).toBe(0);
  });

  test("a real create requires credentials", async () => {
    const result = await workflow.startStep(fixture({ "red/event": "create" }), {});
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).toContain("COLORS_PAR_DO_TOKEN");
    expect(String(result["red/err"]))
      .toContain("COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY");
  });

  test("delete is protected", async () => {
    const result = await workflow.startStep(fixture({ "red/event": "delete" }), {});
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).toContain("COMPUTE_PREVENT_DESTROY");
  });

  // A fixture that passes real-delete preflight: guard lifted, secrets present.
  const deletableFixture = (overrides: Opts = {}) => fixture({
    "compute-prevent-destroy": false,
    "do-token": "t", "cloudflare-api-token": "t",
    "r2-access-key-id": "k", "r2-secret-access-key": "s",
    "rybbit-backup-r2-access-key-id": "k",
    "rybbit-backup-r2-secret-access-key": "s",
    ...overrides,
  });

  test("delete fails loudly when state is unreadable", async () => {
    // Swallowing a failed state read is how a live teardown ended up pointing
    // the cleanup playbook at 192.0.2.10: stale backend credentials made
    // `tofu output` fail, nil was merged, and the inventory fell back to
    // TEST-NET. The failure must surface here, before any playbook runs.
    const result = await workflow.startStep(
      deletableFixture({ "red/event": "delete" }), {},
      async () => { throw new Error("Unauthorized"); });
    expect(result["red/exit"]).toBe(1);
    expect(String(result["red/err"])).toContain("Unauthorized");
    expect(String(result["red/err"])).toContain("COLORS_PAR_IP");
  });

  test("delete with an explicit ip skips the state read", async () => {
    // COLORS_PAR_IP is the operator's escape hatch when the state backend is
    // unreachable; it must not require the read it exists to replace.
    const result = await workflow.startStep(
      deletableFixture({ "red/event": "delete", ip: "203.0.113.7" }), {},
      async () => { throw new Error("must not be called"); });
    expect(result["red/exit"]).toBe(0);
    expect(result.ip).toBe("203.0.113.7");
  });

  test("delete with empty state proceeds without an address", async () => {
    // State readable, no compute recorded: the instance is already gone, the
    // cleanup step skips itself, and the rest of the teardown still runs.
    const result = await workflow.startStep(
      deletableFixture({ "red/event": "delete" }), {}, async () => undefined);
    expect(result["red/exit"]).toBe(0);
    expect(result.ip).toBeUndefined();
  });

  test("the graph orders the private stack", () => {
    const next = (step: string, event: string) =>
      (workflow.wireFn(step, { "red/event": event }) ?? []).slice(1);
    expect(next("rybbit/start", "create")).toEqual(["rybbit/infrastructure"]);
    expect(next("rybbit/infrastructure", "create")).toEqual(["rybbit/dns"]);
    expect(next("rybbit/start", "delete")).toEqual(["rybbit/ansible"]);
  });
});
