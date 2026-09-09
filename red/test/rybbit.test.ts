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
import * as compute from "../src/compute.ts";
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







  test("absent machine key selects keygen", () => {
    expect(validate.keygen(keygen())).toBe(true);
    expect(validate.keygen(keygenVultr())).toBe(true);
    expect(validate.keygen(fixture())).toBe(false);
    expect(validate.keygen(vultrFixture())).toBe(false);
    // Absence, not a flag, is the switch.
    expect(validate.keygen(vultrFixture({ "vultr-ssh-keys": null }))).toBe(true);
  });













  test("reports all errors", () => {
    const errors = validate.stateErrors(fixture({
      "rybbit-host": "bad", "postgres-image": "floating",
      "rybbit-backup-retention-days": -1,
      "provider-dns": "other", "digitalocean-vpc-uuid": "forbidden",
    }));
    expect(errors.length).toBeGreaterThanOrEqual(4);
    for (const part of ["host", "image", "retention", "provider-dns"]) {
      expect(errors.some((e) => e.includes(part))).toBe(true);
    }
  });



  test("profile overlay is refused", () => {
    expect(validate.envErrors({ COLORS_PAR_PROFILE: "other" }).length).toBe(1);
    expect(validate.envErrors({})).toEqual([]);
  });



  test("validation accepts a digest pin", () => {
    expect(validate.stateErrors(fixture())).toEqual([]);
    expect(validate.stateErrors(fixture({ "rybbit-backend-image": "no-tag-at-all" }))
      .length).toBeGreaterThan(0);
  });
});

// --- tools -------------------------------------------------------------------

describe("tools", () => {
















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
    expect(result["red/exit"]).toBe(1);
    expect(result["red/err"]).toBe("compute node unavailable");
  });

  test("delete cleanup targets the adopted address", async () => {
    // When the start step recovered the instance address from state, the
    // cleanup playbook runs against it, never the documentation fallback.
    const result = await tools.ansibleStep(
      fixture({ "red/event": "delete", ip: "203.0.113.7", user:"root", name:"rybbit-fixture" }),
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
    const data = tools.ansibleLocalData(fixture({ ip: "203.0.113.7", user:"root", name:"rybbit-fixture" }));
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

  test("local updater gets the selected key ownership mode",()=>{
    const render=(opts:Opts)=>renderTemplate(tools.template('ansible-local','main.yml'),tools.ansibleLocalData(opts),tools.templateOpts);
    expect(render(keygen())).toContain('colors_keygen: true');expect(render(fixture())).toContain('colors_keygen: false');
    expect(render(fixture())).toContain('fcntl.flock');
  });
});

// --- workflow ----------------------------------------------------------------

describe("library compute", () => {
  test("all fixtures validate and use one library node", () => {
    for(const f of [keygen,fixture,keygenVultr,vultrFixture]) expect(validate.stateErrors(f())).toEqual([]);
    expect(compute.topology).toEqual([{role:null,count:1}]);
    expect(compute.requirements(keygen()).legacy_state_keys).toEqual(['rybbit-keygen-fixture/rybbit-infrastructure.tfstate']);
  });
  test("invalid compute inputs fail before execution", () => {
    for(const update of [{'provider-compute':'unsupported'},{'digitalocean-size':null},{'digitalocean-ssh-sources':[]},{'digitalocean-http-sources':['bad']}]) expect(validate.stateErrors(keygen(update)).length).toBeGreaterThan(0);
  });
  test("compute credentials are deferred to library state inspection", () => {
    const errors=validate.secretErrors(keygen()).join('\n');
    expect(errors).toContain('COLORS_PAR_CLOUDFLARE_API_TOKEN');
    expect(errors).not.toContain('COLORS_PAR_VULTR_API_KEY');
    expect(validate.tofuEnv(keygen(),'provider-compute')).toEqual({});
  });
  test("failed lifecycle diagnostics and observed node identity survive", () => {
    expect(compute.attach(keygen(),{status:'error',errors:['legacy compute state requires migration']})['red/err']).toBe('legacy compute state requires migration');
    const result=compute.attach(keygen(),{status:'present',cluster:{nodes:[{ip:'203.0.113.7',user:'ubuntu'}]},key:{private_key_path:'/tmp/explicit'}});
    expect(result.user).toBe('ubuntu');expect(result['ssh-private-key-path']).toBe('/tmp/explicit');
    expect(compute.attach(keygen(),{status:'destroyed'})['rybbit/already-destroyed']).toBe(true);
    expect(()=>compute.node({cluster:{nodes:[]}})).toThrow();
  });
  test("offline start needs no credentials", async()=> {
    for(const f of [keygen,fixture,keygenVultr,vultrFixture]) expect((await workflow.startStep(f({'red/event':'build'}),{}))['red/exit']).toBe(0);
  });
  test("managed build and external SSH identities are deterministic",()=> {
    expect(ssh.withMachineKey(keygen({'red/event':'build'}))['ssh-private-key-path']).toBe('/home/build-placeholder/.ssh/rybbit-keygen-fixture');
    expect(ssh.withMachineKey(fixture({'red/event':'build'}))).toEqual(fixture({'red/event':'build'}));
    expect(ssh.identityArgs(fixture())[1]).toBe('/home/build-placeholder/.ssh/operator-key');
  });
});

test("neutral HTTP3 ingress and observed hostname",()=>{
 expect(compute.requirements(fixture()).security.ingress.map(r=>[r.protocol,r.from_port])).toEqual([['tcp',22],['tcp',80],['tcp',443],['udp',443]]);
 expect(compute.requirements(fixture({'rybbit-http-sources':[]})).security.ingress).toHaveLength(1);
 expect(tools.ansibleData(fixture({ip:'203.0.113.7',user:'ubuntu',name:'observed-node'}))['compute-name']).toBe('observed-node');
});
