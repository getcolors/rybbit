import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { renderTemplate } from "red/scaffold";
import { StepError, type Opts } from "red/workflow";
import * as ssh from "../src/ssh.ts";
import * as sshConfig from "../src/ssh-config.ts";
import * as tools from "../src/tools.ts";
import * as validate from "../src/validate.ts";
import * as workflow from "../src/workflow.ts";

const fixtureFile = join(import.meta.dir, "../../test/fixtures/colors.yml");
const vultrFixtureFile = join(import.meta.dir, "../../test/fixtures/colors-vultr.yml");
const keygenFile = join(import.meta.dir, "../../test/fixtures/keygen.yml");
const keygenVultrFile = join(import.meta.dir, "../../test/fixtures/keygen-vultr.yml");

function readFixture(path: string, overrides: Opts): Opts {
  const text = readFileSync(path, "utf8").replaceAll("WORKDIR", ".colors");
  return { ...(Bun.YAML.parse(text) as Opts), ...overrides };
}

// DigitalOcean and Vultr in opt-out mode (an explicit key id, a name equal to
// the profile; the Vultr one is the shape of the live rybbit-vultr deployment),
// and the same two providers in keygen mode (no `<provider>-ssh-keys`, no
// `<provider>-name`).
const fixture = (overrides: Opts = {}) => readFixture(fixtureFile, overrides);
const vultrFixture = (overrides: Opts = {}) => readFixture(vultrFixtureFile, overrides);
const keygen = (overrides: Opts = {}) => readFixture(keygenFile, overrides);
const keygenVultr = (overrides: Opts = {}) => readFixture(keygenVultrFile, overrides);

// ~/.ssh redirection: ONCE's ssh module and this package's ssh-config both
// read $HOME at call time, exactly so tests can point them at a fresh
// temporary home. Nothing here may touch the real one.
let savedHome: string | undefined;
let home: string;
beforeEach(() => {
  savedHome = process.env.HOME;
  home = mkdtempSync(join(tmpdir(), "rybbit-red-test"));
  process.env.HOME = home;
});
afterEach(() => {
  process.env.HOME = savedHome;
  rmSync(home, { recursive: true, force: true });
});

function write(path: string, content: string) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
}

// The compute template for `opts`' provider, rendered as `build` would.
function renderInfrastructure(opts: Opts): string {
  return renderTemplate(tools.template(`infrastructure.${opts["provider-compute"]}`, "main.tf"),
    tools.infrastructureData(opts), tools.templateOpts);
}

const resource = (name: string) =>
  readFileSync(join(import.meta.dir, "../resources", name), "utf8");
const source = readFileSync(join(import.meta.dir, "../src/tools.ts"), "utf8");

// --- validate ----------------------------------------------------------------

describe("validate", () => {
  test("all four fixtures are valid", () => {
    expect(validate.stateErrors(fixture())).toEqual([]);
    expect(validate.stateErrors(vultrFixture())).toEqual([]);
    expect(validate.stateErrors(keygen())).toEqual([]);
    expect(validate.stateErrors(keygenVultr())).toEqual([]);
  });

  test("the spec carries this package's registry, sources and default", () => {
    // The operations are ONCE's; this is the data they run over. A colour
    // whose registry, sources or default drifts fails here, in that colour.
    expect(Object.keys(validate.spec.registry).sort()).toEqual(["digitalocean", "vultr"]);
    expect(validate.spec.registry).toBe(validate.computeProviders);
    expect(validate.spec.registry.digitalocean).toEqual({
      required: ["digitalocean-region", "digitalocean-size", "digitalocean-image",
                 "digitalocean-ssh-sources", "digitalocean-http-sources"],
      secrets: ["do-token"],
      tofuEnv: { "do-token": "DIGITALOCEAN_TOKEN" },
    });
    expect(validate.spec.registry.vultr).toEqual({
      required: ["vultr-region", "vultr-plan", "vultr-os-id",
                 "vultr-ssh-sources", "vultr-http-sources"],
      secrets: ["vultr-api-key"],
      tofuEnv: { "vultr-api-key": "VULTR_API_KEY" },
    });
    expect(validate.spec.sources).toEqual({ nonEmpty: ["ssh-sources"], mayBeEmpty: ["http-sources"] });
    // Vultr, not DigitalOcean: the default is what a legacy state without
    // params.provider is, and the only legacy state is the live Vultr deployment.
    expect(validate.spec.default).toBe("vultr");
    expect(validate.spec.default).toBe(validate.defaultComputeProvider);
    expect("nameRules" in validate.spec).toBe(false);
  });

  test("compute provider must be one the package has a template for", () => {
    // The registry is the only list; a provider accepted here with no template
    // directory would fail at render time instead of at validation.
    const errors = validate.stateErrors(fixture({ "provider-compute": "hcloud" }));
    expect(errors).toContain(":provider-compute must be one of digitalocean, vultr");
  });

  test("name and machine key are never required", () => {
    // `<provider>-name` is an optional override of the profile and
    // `<provider>-ssh-keys` is meaningful by its absence, so neither may be in
    // the registry's required list -- a required machine key would make keygen
    // mode unreachable.
    for (const entry of Object.values(validate.computeProviders)) {
      for (const key of entry.required) {
        expect(key.endsWith("-name")).toBe(false);
        expect(key.endsWith("-ssh-keys")).toBe(false);
      }
    }
    expect(validate.stateErrors(fixture({ "digitalocean-name": null, "digitalocean-ssh-keys": null }))).toEqual([]);
    expect(validate.stateErrors(vultrFixture({ "vultr-name": null, "vultr-ssh-keys": null }))).toEqual([]);
  });

  test("absent machine key selects keygen", () => {
    expect(validate.keygen(keygen())).toBe(true);
    expect(validate.keygen(keygenVultr())).toBe(true);
    expect(validate.keygen(fixture())).toBe(false);
    expect(validate.keygen(vultrFixture())).toBe(false);
    // Absence, not a flag, is the switch.
    expect(validate.keygen(vultrFixture({ "vultr-ssh-keys": null }))).toBe(true);
  });

  test("compute name falls back to the profile", () => {
    expect(validate.computeName(fixture())).toBe("rybbit-fixture");
    expect(validate.computeName(keygen())).toBe("rybbit-keygen-fixture");
    expect(validate.computeName(vultrFixture({ "vultr-name": "custom" }))).toBe("custom");
    expect(validate.computeKey(vultrFixture(), "ssh-sources")).toBe("vultr-ssh-sources");
  });

  test("ssh sources must not be empty; no public HTTP is fine", () => {
    // A machine nobody can reach is not a deployment; an empty HTTP list is
    // simply no public HTTP.
    expect(validate.stateErrors(vultrFixture({ "vultr-ssh-sources": [] })))
      .toContain(":vultr-ssh-sources must list at least one CIDR");
    expect(validate.stateErrors(fixture({ "digitalocean-ssh-sources": [] })))
      .toContain(":digitalocean-ssh-sources must list at least one CIDR");
    expect(validate.stateErrors(vultrFixture({ "vultr-http-sources": [] }))).toEqual([]);
    expect(validate.stateErrors(fixture({ "digitalocean-http-sources": [] }))).toEqual([]);
  });

  test("malformed sources are refused before any provider call", () => {
    expect(validate.stateErrors(vultrFixture({ "vultr-http-sources": ["203.0.113.0"] })))
      .toContain(':vultr-http-sources entry "203.0.113.0" is not an IPv4 or IPv6 CIDR');
    expect(validate.stateErrors(fixture({ "digitalocean-ssh-sources": ["0.0.0.0/0", "nope"] })))
      .toContain(':digitalocean-ssh-sources entry "nope" is not an IPv4 or IPv6 CIDR');
    expect(validate.stateErrors(fixture({ "digitalocean-ssh-sources": ["2001:db8::/32", "203.0.113.4/32"] })))
      .toEqual([]);
  });

  test("provider checks are scoped to the selected provider", () => {
    // DigitalOcean's VPC keys are refused on DigitalOcean and ignored on
    // Vultr, like every other unselected provider's key; Vultr's os-id is
    // numeric, and only checked on Vultr.
    expect(validate.stateErrors(fixture({ "digitalocean-vpc-uuid": "forbidden" }))
      .some((e) => e.includes("vpc-uuid"))).toBe(true);
    expect(validate.stateErrors(vultrFixture({ "digitalocean-vpc-uuid": "ignored",
                                               "digitalocean-vpc-cidr": "10.0.0.0/16" }))).toEqual([]);
    expect(validate.stateErrors(vultrFixture({ "vultr-os-id": "ubuntu" })))
      .toContain(":vultr-os-id must be Vultr's numeric operating-system id");
    expect(validate.stateErrors(fixture({ "vultr-os-id": "ubuntu" }))).toEqual([]);
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

  test("infrastructure data carries the name and the keypair mode", () => {
    // One resolved name and one mode reach every template, so no template
    // branches on the provider or re-derives either.
    const optout = tools.infrastructureData(vultrFixture());
    expect(optout["compute-name"]).toBe("rybbit-vultr-fixture");
    expect(optout["ssh-keygen"]).toBe(false);
    const generated = tools.infrastructureData(keygenVultr());
    expect(generated["compute-name"]).toBe("rybbit-vultr-keygen-fixture");
    expect(generated["ssh-keygen"]).toBe(true);
    expect(tools.ansibleData(keygen())["ssh-keygen"]).toBe(true);
    expect(tools.ansibleData(fixture())["ssh-keygen"]).toBe(false);
  });

  test("templates name the machine from one resolved value", () => {
    // Every label -- droplet name, instance label, firewall group and names,
    // and params.name -- interpolates compute-name, never a provider key or
    // the profile directly, so an override and the fallback land everywhere.
    for (const provider of ["vultr", "digitalocean"]) {
      const template = resource(`tools/infrastructure/${provider}/main.tf`);
      expect(template).not.toContain(`<{ ${provider}-name }>`);
      expect(template).toContain('name = "<{ compute-name }>"');
      expect(template).toContain(`provider = "${provider}"`);
    }
    const rendered = renderInfrastructure(vultrFixture({ "vultr-name": "custom-label" }));
    expect(rendered).toContain('label = "custom-label"');
    expect(rendered).toContain('description = "custom-label"');
    expect(rendered).toContain('name = "custom-label"');
  });

  test("empty http sources render no public HTTP", () => {
    // An empty `<provider>-http-sources` is allowed and means no public HTTP:
    // Vultr's generated rules simply omit http, https and quic, and the
    // DigitalOcean rules are a dynamic block over an empty list. SSH stays.
    const json = tools.vultrFirewallJson(tools.infrastructureData(vultrFixture({ "vultr-http-sources": [] })));
    expect(json.match(/"firewall_group_id"/g)?.length).toBe(2);
    expect(new Set([...json.matchAll(/"port" : ("\d+")/g)].map((m) => m[1]))).toEqual(new Set(['"22"']));
    expect(json).not.toContain("udp");
    const empty = renderInfrastructure(fixture({ "digitalocean-http-sources": [] }));
    expect(empty).toContain("length([]) > 0 ? [");
    expect(empty).toContain("source_addresses = []");
    expect(empty).toContain('port_range       = "22"');
    const full = renderInfrastructure(fixture());
    expect(full).toContain('length(["0.0.0.0/0", "::/0"]) > 0 ? [');
    expect(full).toContain('{ protocol = "udp", port_range = "443" }');
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

describe("ssh", () => {
  // The matrix itself is ONCE's and tested there; these prove the delegation
  // with this package's fixtures: absence of `<provider>-ssh-keys` selects
  // keygen on both providers, a build renders the placeholder path and never
  // names $HOME, opt-out passes through untouched, and the create matrix, the
  // preflight and the cleanup reach ONCE.
  test("build renders a stable placeholder path", () => {
    const opts = ssh.withMachineKey(keygenVultr({ "red/event": "build" }));
    expect(String(opts["ssh-public-key-path"])).toStartWith(ssh.buildPlaceholderDir);
    expect(opts["vultr-ssh-keys"]).toBe(opts["ssh-public-key-path"]);
    expect(String(opts["ssh-private-key-path"])).not.toContain(home);
  });

  test("the build placeholder lands on the selected provider's key", () => {
    // ONCE's table decides which desired-state key carries the machine key,
    // so a second provider needs no second branch here.
    const opts = ssh.withMachineKey(keygen({ "red/event": "build" }));
    expect(opts["digitalocean-ssh-keys"]).toBe(opts["ssh-public-key-path"]);
    expect("vultr-ssh-keys" in opts).toBe(false);
    expect(String(opts["ssh-public-key-path"])).toStartWith(ssh.buildPlaceholderDir);
    const optedOut = ssh.withMachineKey(fixture({ "red/event": "build" }));
    expect(optedOut["digitalocean-ssh-keys"]).toBe("58495393");
    expect(optedOut["ssh-public-key-path"]).toBeUndefined();
  });

  test("a dry-run renders the placeholder too", () => {
    const opts = ssh.withMachineKey(keygenVultr({ "red/event": "create", "red/dry-run": true }));
    expect(String(opts["ssh-public-key-path"])).toStartWith(ssh.buildPlaceholderDir);
  });

  test("real events render the real path", () => {
    const opts = ssh.withMachineKey(keygenVultr({ "red/event": "create" }));
    expect(opts["ssh-private-key-path"]).toBe(join(home, ".ssh", "rybbit-vultr-keygen-fixture"));
    expect(opts["ssh-public-key-path"]).toBe(join(home, ".ssh", "rybbit-vultr-keygen-fixture.pub"));
  });

  test("opt-out passes through untouched", () => {
    for (const event of ["build", "create", "delete"]) {
      const vultr = ssh.withMachineKey(vultrFixture({ "red/event": event }));
      expect(vultr["vultr-ssh-keys"]).toBe("faa53dae-f289-4bba-bf90-8997131ca40a");
      expect(vultr["ssh-public-key-path"]).toBeUndefined();
      expect(vultr["ssh-keygen"]).toBeUndefined();
      const digitalocean = ssh.withMachineKey(fixture({ "red/event": event }));
      expect(digitalocean["digitalocean-ssh-keys"]).toBe("58495393");
      expect(digitalocean["ssh-keygen"]).toBeUndefined();
    }
  });

  test("identity args select the generated key only in keygen mode", () => {
    // The acceptance step's ssh threads these: in keygen mode nothing
    // guarantees an agent holds the key.
    const opts = ssh.withMachineKey(keygenVultr({ "red/event": "create" }));
    expect(ssh.identityArgs(opts)).toEqual(["-o", "IdentitiesOnly=yes", "-i", String(opts["ssh-private-key-path"])]);
    expect(ssh.identityArgs(ssh.withMachineKey(vultrFixture({ "red/event": "create" })))).toEqual([]);
  });

  test("first create generates the keypair", async () => {
    const opts = await ssh.ensureKey(keygenVultr({ "red/event": "create" }), async () => undefined);
    const prv = join(home, ".ssh", "rybbit-vultr-keygen-fixture");
    const pub = `${prv}.pub`;
    expect(opts["red/err"]).toBeUndefined();
    expect(existsSync(prv)).toBe(true);
    expect(existsSync(pub)).toBe(true);
    // ed25519, no passphrase, profile-named comment
    expect(readFileSync(pub, "utf8")).toContain("ssh-ed25519");
    expect(readFileSync(pub, "utf8")).toContain("rybbit-vultr-keygen-fixture managed by Colors");
    // 600 on the private key, 700 on ~/.ssh
    expect(statSync(prv).mode & 0o777).toBe(0o600);
    expect(statSync(join(home, ".ssh")).mode & 0o777).toBe(0o700);
  });

  test("a key without state is never overwritten", async () => {
    const prv = join(home, ".ssh", "rybbit-keygen-fixture");
    write(prv, "irreplaceable");
    write(`${prv}.pub`, "ssh-ed25519 AAAA test");
    const opts = await ssh.ensureKey(keygen({ "red/event": "create" }), async () => undefined);
    expect(opts["red/exit"]).toBe(1);
    expect(String(opts["red/err"])).toContain("no compute state is readable");
    expect(String(opts["red/err"])).toContain("survives");
    expect(readFileSync(prv, "utf8")).toBe("irreplaceable");
  });

  test("state without a key is an error", async () => {
    const opts = await ssh.ensureKey(keygen({ "red/event": "create" }),
      async () => ({ ip: "192.0.2.10" }));
    expect(opts["red/exit"]).toBe(1);
    expect(String(opts["red/err"])).toContain("does not hold the machine key");
  });

  test("opt-out generates nothing", async () => {
    for (const opts of [vultrFixture(), fixture()]) {
      const result = await ssh.ensureKey({ ...opts, "red/event": "create" }, async () => undefined);
      expect(result["red/err"]).toBeUndefined();
    }
    expect(existsSync(join(home, ".ssh"))).toBe(false);
  });

  test("preflight lists keys with the selected provider's token", async () => {
    // ONCE selects the REST API and the token by provider; this proves the
    // delegation hands each provider its own credential.
    const seen: Array<[string, string]> = [];
    const capture = async (provider: string, token: string) => { seen.push([provider, token]); return []; };
    await ssh.preflight(ssh.withMachineKey(keygen({ "red/event": "create",
      "do-token": "do-secret", "vultr-api-key": "wrong" })), capture);
    await ssh.preflight(ssh.withMachineKey(keygenVultr({ "red/event": "create",
      "vultr-api-key": "vultr-secret", "do-token": "wrong" })), capture);
    expect(seen).toEqual([["digitalocean", "do-secret"], ["vultr", "vultr-secret"]]);
  });

  test("preflight refuses a foreign key and says do not delete it", async () => {
    write(join(home, ".ssh", "rybbit-vultr-keygen-fixture.pub"), "ssh-ed25519 OURS comment");
    const opts = await ssh.preflight(ssh.withMachineKey(keygenVultr({ "red/event": "create" })),
      async () => [{ id: "abc", name: "rybbit-vultr-keygen-fixture", public: "ssh-ed25519 THEIRS" }]);
    expect(opts["red/exit"]).toBe(1);
    expect(String(opts["red/err"])).toContain("Do not delete it");
  });

  test("preflight is skipped in opt-out mode", async () => {
    const opts = await ssh.preflight(vultrFixture({ "red/event": "create" }),
      async () => { throw new Error("must not be called"); });
    expect(opts["red/err"]).toBeUndefined();
  });

  test("delete removes the keypair; ~/.ssh itself survives", () => {
    write(join(home, ".ssh", "rybbit-keygen-fixture"), "private");
    write(join(home, ".ssh", "rybbit-keygen-fixture.pub"), "public");
    ssh.cleanupStep(keygen({ "red/event": "delete", "ssh-keygen": true }));
    expect(existsSync(join(home, ".ssh", "rybbit-keygen-fixture"))).toBe(false);
    expect(existsSync(join(home, ".ssh", "rybbit-keygen-fixture.pub"))).toBe(false);
    expect(existsSync(join(home, ".ssh"))).toBe(true);
  });

  test("cleanup is inert on create and in opt-out mode", () => {
    write(join(home, ".ssh", "rybbit-keygen-fixture"), "private");
    ssh.cleanupStep(keygen({ "red/event": "create", "ssh-keygen": true }));
    expect(existsSync(join(home, ".ssh", "rybbit-keygen-fixture"))).toBe(true);
    ssh.cleanupStep(fixture({ "red/event": "delete" }));
    expect(existsSync(join(home, ".ssh", "rybbit-keygen-fixture"))).toBe(true);
  });
});

// --- ssh-config --------------------------------------------------------------

describe("ssh-config", () => {
  const configFile = () => join(home, ".ssh", "config");

  test("the alias is the profile and the identity file keeps the tilde", () => {
    expect(sshConfig.hostAlias(fixture())).toBe("rybbit-fixture");
    expect(sshConfig.identityFile(fixture())).toBe("~/.ssh/rybbit-fixture");
    expect(sshConfig.identityFile(fixture())).not.toContain(home);
  });

  test("the marker is the alias alone, and owned-markers holds only it", () => {
    expect(sshConfig.beginMarker("rybbit-vultr")).toBe("# BEGIN rybbit-vultr ANSIBLE MANAGED BLOCK");
    expect(sshConfig.endMarker("rybbit-vultr")).toBe("# END rybbit-vultr ANSIBLE MANAGED BLOCK");
    // Born conforming: no marker migration is in flight.
    const owned = sshConfig.ownedMarkers("rybbit-vultr");
    expect([...owned.begin]).toEqual(["# BEGIN rybbit-vultr ANSIBLE MANAGED BLOCK"]);
    expect([...owned.end]).toEqual(["# END rybbit-vultr ANSIBLE MANAGED BLOCK"]);
  });

  test("host patterns are read from a Host line", () => {
    expect(sshConfig.hostPatterns("Host rybbit-fixture")).toEqual(["rybbit-fixture"]);
    expect(sshConfig.hostPatterns("  host   web rybbit-fixture  db ")).toEqual(["web", "rybbit-fixture", "db"]);
    expect(sshConfig.hostPatterns("    HostName 192.0.2.1")).toBeUndefined();
    expect(sshConfig.hostPatterns("Match host rybbit-fixture")).toBeUndefined();
  });

  test("a foreign stanza is found; our own block is not foreign", () => {
    expect(sshConfig.foreignStanzaLine(
      ["Host other", "    HostName 192.0.2.1", "", "Host rybbit-fixture"],
      "rybbit-fixture")).toBe(4);
    const alias = "rybbit-fixture";
    expect(sshConfig.foreignStanzaLine(
      [sshConfig.beginMarker(alias), `Host ${alias}`, "    HostName 192.0.2.1",
       sshConfig.endMarker(alias)], alias)).toBeUndefined();
  });

  test("a stanza after our block is still foreign", () => {
    const alias = "rybbit-fixture";
    expect(sshConfig.foreignStanzaLine(
      [sshConfig.beginMarker(alias), `Host ${alias}`, sshConfig.endMarker(alias),
       `Host ${alias}`], alias)).toBe(4);
  });

  test("a block under a package-prefixed marker is foreign", () => {
    // This package never wrote a `# BEGIN rybbit <alias>` marker, so a block
    // carrying one belongs to nobody this package knows.
    const alias = "rybbit-vultr";
    expect(sshConfig.foreignStanzaLine(
      [`# BEGIN rybbit ${alias} ANSIBLE MANAGED BLOCK`, `Host ${alias}`,
       `# END rybbit ${alias} ANSIBLE MANAGED BLOCK`], alias)).toBe(2);
  });

  test("multi-pattern host lines count; unrelated files are left alone", () => {
    expect(sshConfig.foreignStanzaLine(["Host web rybbit-fixture db"], "rybbit-fixture")).toBe(1);
    expect(sshConfig.foreignStanzaLine(["Host build", "Host rybbit-other"], "rybbit-fixture"))
      .toBeUndefined();
  });

  test("an option above the first Host is refused; comments and Host openers are fine", () => {
    expect(sshConfig.leadingOptionLine(["ServerAliveInterval 60", "Host a"])).toBe(1);
    expect(sshConfig.leadingOptionLine(["# comment", "", "IdentitiesOnly yes", "Host a"])).toBe(3);
    expect(sshConfig.leadingOptionLine(["Host a", "    User root"])).toBeUndefined();
    expect(sshConfig.leadingOptionLine(["# lead comment", "", "Host a", "    User root"])).toBeUndefined();
    expect(sshConfig.leadingOptionLine(["Match host b", "    User root"])).toBeUndefined();
    expect(sshConfig.leadingOptionLine(["# nothing here", ""])).toBeUndefined();
  });

  test("preflight refuses rather than overwrites", () => {
    const refused = sshConfig.preflight(fixture(), {
      adoptError: () => "already declares `Host x`",
      placementError: () => undefined,
    });
    expect(refused["red/exit"]).toBe(1);
    expect(String(refused["red/err"])).toContain("already declares");
    const clean = sshConfig.preflight(fixture(), {
      adoptError: () => undefined,
      placementError: () => undefined,
    });
    expect(clean["red/exit"]).toBeUndefined();
  });

  test("adopt error names the file and the line; our own block and a missing file pass", () => {
    expect(sshConfig.adoptError(fixture())).toBeUndefined();
    write(configFile(), "Host other\n    HostName 192.0.2.1\n\nHost rybbit-fixture\n    User root\n");
    const error = String(sshConfig.adoptError(fixture()));
    expect(error).toContain(configFile());
    expect(error).toContain("`Host rybbit-fixture` at line 4");
    expect(error).toContain("will not overwrite it");
    const alias = "rybbit-fixture";
    write(configFile(), `${sshConfig.beginMarker(alias)}\nHost ${alias}\n    HostName 192.0.2.1\n${sshConfig.endMarker(alias)}\n`);
    expect(sshConfig.adoptError(fixture())).toBeUndefined();
  });

  test("placement error names the file and the line and mentions the recovery", () => {
    write(configFile(), "# comment\n\n\nIdentitiesOnly yes\nHost a\n");
    const error = String(sshConfig.placementError(fixture()));
    expect(error).toContain(configFile());
    expect(error).toContain("line 4");
    expect(error).toContain("Host *");
  });

  test("preflight reads the redirected file end to end", () => {
    write(configFile(), "Host rybbit-fixture\n    HostName 192.0.2.1\n");
    const refused = sshConfig.preflight(fixture());
    expect(refused["red/exit"]).toBe(1);
    expect(String(refused["red/err"])).toContain("already declares");
    write(configFile(), "ServerAliveInterval 60\nHost a\n");
    const placed = sshConfig.preflight(fixture());
    expect(placed["red/exit"]).toBe(1);
    expect(String(placed["red/err"])).toContain("line 1");
    write(configFile(), "Host a\n    User root\n");
    expect(sshConfig.preflight(fixture())["red/exit"]).toBeUndefined();
  });

  test("build and dry-run never read the config", async () => {
    // The only readers are adoptError and placementError; a real create is
    // the one event that reaches them, and it stops at the credentials here.
    // A leading-option file that would refuse a real create must not disturb
    // a build or a dry-run.
    write(configFile(), "ServerAliveInterval 60\nHost rybbit-fixture\n");
    for (const opts of [fixture({ "red/event": "build" }),
                        keygen({ "red/event": "build" }),
                        fixture({ "red/event": "create", "red/dry-run": true })]) {
      expect((await workflow.startStep(opts, {}))["red/exit"]).toBe(0);
    }
  });

  test("the local play renders no address and follows keygen mode", () => {
    const data = tools.ansibleLocalData(fixture({ ip: "203.0.113.7" }));
    expect(data["ssh-config-identity-file"]).toBe("~/.ssh/rybbit-fixture");
    expect(data["ssh-keygen"]).toBe(false);
    expect(tools.ansibleLocalData(keygen())["ssh-keygen"]).toBe(true);
  });

  test("the local stage renders three files", () => {
    const targets = tools.ansibleLocalSpecs(fixture()).map((s) => String(s.target));
    for (const file of ["/ansible.cfg", "/inventory.ini", "/main.yml"]) {
      expect(targets.some((t) => t.endsWith(file))).toBe(true);
    }
    expect(targets.every((t) => t.includes("rybbit-ansible-local"))).toBe(true);
  });

  test("the rendered play carries the IdentityFile pair only in keygen mode", () => {
    const render = (opts: Opts) =>
      renderTemplate(tools.template("ansible-local", "main.yml"), tools.ansibleLocalData(opts), tools.templateOpts);
    const keygenPlay = render(keygen());
    expect(keygenPlay).toContain("IdentityFile ~/.ssh/rybbit-keygen-fixture");
    expect(keygenPlay).toContain("IdentitiesOnly yes");
    // The header comment names the pair; the rendered option lines must not.
    const optoutPlay = render(fixture());
    expect(optoutPlay).not.toContain("IdentityFile ~/.ssh/");
    expect(optoutPlay).not.toContain("IdentitiesOnly yes");
    // Address, user and alias are Ansible's, never Selmer's.
    for (const play of [keygenPlay, optoutPlay]) {
      expect(play).toContain("insertbefore: BOF");
      expect(play).toContain("HostName {{ ip }}");
      expect(play).toContain("Host {{ host_alias }}");
      expect(play).not.toMatch(/([0-9]{1,3}\.){3}[0-9]{1,3}/);
    }
  });
});

// --- workflow ----------------------------------------------------------------

describe("workflow", () => {
  // The compute state is read once per run, through the injectable reader,
  // on a real create or delete. Every lifecycle test stubs it: undefined is a
  // readable state holding no compute, a map is a recorded `params`, and a
  // throw is a backend that cannot be read.
  const start = (opts: Opts, state: Record<string, unknown> | undefined) =>
    workflow.startStep(opts, {}, async () => state);
  // The shape `red/tofu` throws: the SDK's StepError. Only that is an
  // unreadable backend; anything else propagates as a defect.
  const startUnreadable = (opts: Opts, message = "tofu output failed: no backend") =>
    workflow.startStep(opts, {}, async () => { throw new StepError(message); });
  const credentials = { "vultr-api-key": "v", "do-token": "d", "cloudflare-api-token": "c",
    "r2-access-key-id": "a", "r2-secret-access-key": "s",
    "rybbit-backup-r2-access-key-id": "k", "rybbit-backup-r2-secret-access-key": "s" };

  test("build and dry-run need no credentials", async () => {
    expect((await workflow.startStep(fixture({ "red/event": "build" }), {}))["red/exit"])
      .toBe(0);
    expect((await workflow.startStep(
      fixture({ "red/event": "create", "red/dry-run": true }), {}))["red/exit"]).toBe(0);
    expect((await workflow.startStep(vultrFixture({ "red/event": "build" }), {}))["red/exit"])
      .toBe(0);
  });

  test("build and dry-run never touch ~/.ssh or the state", async () => {
    // The standard forbids reading, creating, or requiring anything under
    // ~/.ssh on a build or dry-run: they render from desired state alone. Nor
    // do they read the backend: a throwing reader proves nothing on these
    // paths reaches it.
    for (const opts of [keygen({ "red/event": "build" }),
                        keygenVultr({ "red/event": "create", "red/dry-run": true }),
                        keygen({ "red/event": "delete", "red/dry-run": true })]) {
      const result = await startUnreadable(opts);
      expect(result["red/exit"]).toBe(0);
      expect(String(result["ssh-public-key-path"])).toStartWith("/home/build-placeholder");
    }
  });

  test("a real create requires credentials", async () => {
    const result = await start(fixture({ "red/event": "create" }), undefined);
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).toContain("COLORS_PAR_DO_TOKEN");
    expect(String(result["red/err"]))
      .toContain("COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY");
  });

  test("a real create and delete require the selected provider's credentials", async () => {
    const create = await start(vultrFixture({ "red/event": "create" }), undefined);
    expect(create["red/exit"]).toBe(2);
    expect(String(create["red/err"])).toContain("COLORS_PAR_VULTR_API_KEY");
    expect(String(create["red/err"])).not.toContain("COLORS_PAR_DO_TOKEN");
    const del = await start(vultrFixture({ "red/event": "delete", "compute-prevent-destroy": false }), undefined);
    expect(del["red/exit"]).toBe(2);
    expect(String(del["red/err"])).toContain("COLORS_PAR_VULTR_API_KEY");
    expect(String(del["red/err"])).not.toContain("COLORS_PAR_DO_TOKEN");
    const digitalocean = await start(fixture({ "red/event": "delete", "compute-prevent-destroy": false }), undefined);
    expect(String(digitalocean["red/err"])).toContain("COLORS_PAR_DO_TOKEN");
    expect(String(digitalocean["red/err"])).not.toContain("COLORS_PAR_VULTR_API_KEY");
  });

  test("delete is protected", async () => {
    const result = await start(fixture({ "red/event": "delete" }), undefined);
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).toContain("COMPUTE_PREVENT_DESTROY");
  });

  // --- provider switching is a rebuild, never an apply

  test("a provider switch is refused on create and delete", async () => {
    for (const event of ["create", "delete"]) {
      const vultr = await start(vultrFixture({ "red/event": event, "compute-prevent-destroy": false }),
        { provider: "digitalocean", ip: "203.0.113.9" });
      expect(vultr["red/exit"]).toBe(2);
      expect(String(vultr["red/err"]))
        .toContain("state holds a digitalocean machine; set provider-compute back to digitalocean and delete first");
      // The validator order is the thing under test: the actionable error,
      // not a missing token for the provider that was just selected.
      expect(String(vultr["red/err"])).not.toContain("required credential is not set");
      const digitalocean = await start(fixture({ "red/event": event, "compute-prevent-destroy": false }),
        { provider: "vultr", ip: "203.0.113.9" });
      expect(digitalocean["red/exit"]).toBe(2);
      expect(String(digitalocean["red/err"])).toContain("state holds a vultr machine; set provider-compute back to vultr and delete first");
      expect(String(digitalocean["red/err"])).not.toContain("COLORS_PAR_DO_TOKEN");
    }
  });

  test("legacy state accepts only the default provider", async () => {
    // A state recorded before this package wrote params.provider is the live
    // Vultr deployment's: accepted on Vultr, refused on DigitalOcean.
    for (const event of ["create", "delete"]) {
      const vultr = await start(vultrFixture({ "red/event": event, "compute-prevent-destroy": false }),
        { ip: "203.0.113.9" });
      expect(String(vultr["red/err"])).not.toContain("state holds");
      expect(String(vultr["red/err"])).toContain("required credential is not set");
      const digitalocean = await start(fixture({ "red/event": event, "compute-prevent-destroy": false }),
        { ip: "203.0.113.9" });
      expect(digitalocean["red/exit"]).toBe(2);
      expect(String(digitalocean["red/err"])).toContain("no recorded provider");
      expect(String(digitalocean["red/err"])).toContain("set provider-compute back to vultr and delete first");
      expect(String(digitalocean["red/err"])).not.toContain("COLORS_PAR_DO_TOKEN");
    }
  });

  test("a matching provider passes to the credentials", async () => {
    const result = await start(fixture({ "red/event": "create" }), { provider: "digitalocean", ip: "203.0.113.9" });
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).not.toContain("state holds");
    expect(String(result["red/err"])).toContain("COLORS_PAR_DO_TOKEN");
  });

  test("an unreadable backend counts as no state on create", async () => {
    // A fresh clone has no readable state and must still be able to create.
    const result = await startUnreadable(vultrFixture({ "red/event": "create" }));
    expect(result["red/exit"]).toBe(2);
    expect(String(result["red/err"])).not.toContain("could not read");
    expect(String(result["red/err"])).not.toContain("state holds");
    expect(String(result["red/err"])).toContain("COLORS_PAR_VULTR_API_KEY");
  });

  test("a real create on a fresh work directory reports the credentials, not a crash", async () => {
    // No reader stub: the real `stateOutput` runs against a work directory
    // that holds no stage yet, as a fresh clone's does. The SDK's output read
    // throws its StepError there, which ONCE's `readState` counts as an
    // unreadable state, so the create reports its credentials.
    const work = mkdtempSync(join(tmpdir(), "rybbit-red-fresh"));
    try {
      const result = await workflow.startStep(vultrFixture({ workdir: work, "red/event": "create" }), {});
      expect(result["red/exit"]).toBe(2);
      expect(String(result["red/err"])).toContain("COLORS_PAR_VULTR_API_KEY");
      expect(String(result["red/err"])).not.toContain("could not read");
    } finally {
      rmSync(work, { recursive: true, force: true });
    }
  });

  // A fixture that passes real-delete preflight: guard lifted, secrets present.
  const deletableFixture = (overrides: Opts = {}) => fixture({
    "compute-prevent-destroy": false, ...credentials, ...overrides,
  });

  test("delete fails loudly when state is unreadable", async () => {
    // Swallowing a failed state read is how a live teardown ended up pointing
    // the cleanup playbook at 192.0.2.10: stale backend credentials made
    // `tofu output` fail, nil was merged, and the inventory fell back to
    // TEST-NET. The failure must surface here, before any playbook runs, with
    // the standard's wording.
    const result = await startUnreadable(deletableFixture({ "red/event": "delete" }), "Unauthorized");
    expect(result["red/exit"]).toBe(1);
    expect(String(result["red/err"])).toContain("could not read the infrastructure state for the delete cleanup");
    expect(String(result["red/err"])).toContain("Unauthorized");
  });

  test("delete with an explicit ip overrides the adopted address after the read", async () => {
    // COLORS_PAR_IP replaces a stale recorded address; it never skips the read
    // or the provider guard. On a readable state the override wins over the
    // recorded address; an unreadable backend still fails closed with it set.
    const adopted = await start(deletableFixture({ "red/event": "delete", ip: "203.0.113.7" }),
      { provider: "digitalocean", ip: "198.51.100.1", user: "root" });
    expect(adopted["red/exit"]).toBe(0);
    expect(adopted.ip).toBe("203.0.113.7");
    const unreadable = await startUnreadable(deletableFixture({ "red/event": "delete", ip: "203.0.113.7" }));
    expect(unreadable["red/exit"]).toBe(1);
    expect(String(unreadable["red/err"])).toContain("could not read the infrastructure state for the delete cleanup");
  });

  test("delete with empty state proceeds without an address", async () => {
    // State readable, no compute recorded: the instance is already gone, the
    // cleanup step skips itself, and the rest of the teardown still runs.
    const result = await start(deletableFixture({ "red/event": "delete" }), undefined);
    expect(result["red/exit"]).toBe(0);
    expect(result.ip).toBeUndefined();
  });

  test("a real delete adopts the recorded address", async () => {
    const adopted = await start(vultrFixture({ ...credentials, "red/event": "delete", "compute-prevent-destroy": false }),
      { provider: "vultr", ip: "203.0.113.9", user: "root" });
    expect(adopted["red/exit"]).toBe(0);
    expect(adopted.ip).toBe("203.0.113.9");
  });

  test("the graph orders the private stack", () => {
    const next = (step: string, event: string) =>
      (workflow.wireFn(step, { "red/event": event }) ?? []).slice(1);
    expect(next("rybbit/start", "create")).toEqual(["rybbit/infrastructure"]);
    expect(next("rybbit/infrastructure", "create")).toEqual(["rybbit/ssh-config"]);
    expect(next("rybbit/ssh-config", "create")).toEqual(["rybbit/dns"]);
    expect(next("rybbit/dns", "create")).toEqual(["rybbit/ansible"]);
    expect(next("rybbit/ansible", "create")).toEqual(["rybbit/acceptance"]);
    expect(next("rybbit/start", "delete")).toEqual(["rybbit/ansible"]);
  });

  test("delete removes the config block before the destroy", () => {
    // The opposite of the keypair below: a block that outlives its host is
    // stale but harmless, so removing it early costs nothing.
    const next = (step: string) =>
      (workflow.wireFn(step, { "red/event": "delete" }) ?? []).slice(1);
    expect(next("rybbit/ansible")).toEqual(["rybbit/dns"]);
    expect(next("rybbit/dns")).toEqual(["rybbit/ssh-config"]);
    expect(next("rybbit/ssh-config")).toEqual(["rybbit/infrastructure"]);
    expect(workflow.sideEffecting).toContain("rybbit/ssh-config");
  });

  test("delete removes the key after the compute destroy", () => {
    // The ordering is what makes "key present <=> deployment exists" hold: a
    // failed destroy never reaches the cleanup step, and correctly leaves the
    // key that is still the only credential to whatever survived.
    const next = (step: string) =>
      (workflow.wireFn(step, { "red/event": "delete" }) ?? []).slice(1);
    expect(next("rybbit/infrastructure")).toEqual(["rybbit/ssh-cleanup"]);
    expect(next("rybbit/ssh-cleanup")).toEqual([]);
    expect(workflow.sideEffecting).toContain("rybbit/ssh-cleanup");
  });
});
