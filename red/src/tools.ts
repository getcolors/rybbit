// OpenTofu and Ansible stages plus acceptance, the port of
// io.github.getcolors.rybbit.tools.

import * as ansible from "red/ansible";
import { stageDir } from "red/cli";
import { PRESERVE_JINJA_DELIMITERS, contentSpec, scaffold, type Spec, type Template } from "red/scaffold";
import * as tofu from "red/tofu";
import { runtime } from "red/runtime";
import type { Opts } from "red/workflow";
import { failed } from "red/workflow";
import { compute } from "package-once-red";
import * as ssh from "./ssh.ts";
import * as sshConfig from "./ssh-config.ts";
import * as validate from "./validate.ts";

import ansibleLocalCfg from "../resources/tools/ansible-local/ansible.cfg" with { type: "text" };
import ansibleLocalInventory from "../resources/tools/ansible-local/inventory.ini" with { type: "text" };
import ansibleLocalMain from "../resources/tools/ansible-local/main.yml" with { type: "text" };
import ansibleCaddyfile from "../resources/tools/ansible/Caddyfile" with { type: "text" };
import ansibleCfg from "../resources/tools/ansible/ansible.cfg" with { type: "text" };
import ansibleBackup from "../resources/tools/ansible/backup" with { type: "text" };
import ansibleCleanup from "../resources/tools/ansible/cleanup.yml" with { type: "text" };
import ansibleCompose from "../resources/tools/ansible/compose.yml" with { type: "text" };
import ansibleMain from "../resources/tools/ansible/main.yml" with { type: "text" };
import dnsMainTf from "../resources/tools/dns/main.tf" with { type: "text" };
import infrastructureDigitaloceanTf from "../resources/tools/infrastructure/digitalocean/main.tf" with { type: "text" };
import infrastructureVultrTf from "../resources/tools/infrastructure/vultr/main.tf" with { type: "text" };

export const infrastructureTool = "rybbit-infrastructure";
export const dnsTool = "rybbit-dns";
export const ansibleTool = "rybbit-ansible";
export const ansibleLocalTool = "rybbit-ansible-local";
export const templateOpts = PRESERVE_JINJA_DELIMITERS;

export function toolDir(opts: Opts, tool: string): string {
  return stageDir(opts, tool, { defaultProfile: "rybbit" });
}

// The template tree this colour carries, keyed the way green names its
// classpath resources: "<path>/<file>" with dots as directories.
const templates: Record<string, string> = {
  "ansible-local/ansible.cfg": ansibleLocalCfg,
  "ansible-local/inventory.ini": ansibleLocalInventory,
  "ansible-local/main.yml": ansibleLocalMain,
  "ansible/Caddyfile": ansibleCaddyfile,
  "ansible/ansible.cfg": ansibleCfg,
  "ansible/backup": ansibleBackup,
  "ansible/cleanup.yml": ansibleCleanup,
  "ansible/compose.yml": ansibleCompose,
  "ansible/main.yml": ansibleMain,
  "dns/main.tf": dnsMainTf,
  "infrastructure/digitalocean/main.tf": infrastructureDigitaloceanTf,
  "infrastructure/vultr/main.tf": infrastructureVultrTf,
};

export function template(path: string, file: string): Template {
  const name = `${path.replaceAll(".", "/")}/${file}`;
  const content = templates[name];
  if (content === undefined) throw new Error(`template not found: ${name}`);
  return { name, content };
}

function spec(source: Template, target: string, data: Opts): Spec {
  return { template: source, target, data, opts: templateOpts };
}

const rawSpec = (target: string, content: string): Spec => contentSpec(target, content);

// The source lists as validate parses them, so the template and the
// validator can never disagree about what an entry is. ONCE's.
export const cidrs = validate.cidrs;

export function credentialEnv(opts: Opts, ...slots: string[]): Record<string, string> | undefined {
  const mapping: Record<string, string> = Object.assign(
    {},
    ...[...slots, "provider-backend"].map((slot) => validate.tofuEnv(opts, slot)),
  );
  const env: Record<string, string> = {};
  for (const [key, envVar] of Object.entries(mapping)) {
    const value = String(opts[key] ?? "");
    if (value.length > 0) env[envVar] = value;
  }
  return Object.keys(env).length > 0 ? env : undefined;
}

export const backendCredentialEnv = (opts: Opts) => credentialEnv(opts);

// What `build` and `--dry-run` render in place of a compute output: the
// documentation address, shaped like the selected provider's real `params` so
// every later stage sees the same keys either way. ONCE's.
export const fallbackParams = compute.fallbackParams;

// Refuse to hand 192.0.2.10 to Ansible on a real converge whose compute output
// carries no `ip`. ONCE's; `infrastructureStep` is what wires it.
export const resolvedCompute = compute.resolvedCompute;

// `<provider>-<suffix>`, the selected provider's key. ONCE's, via validate.
export const computeKey = validate.computeKey;

// The machine's name: `<provider>-name` when present, else the profile. ONCE's,
// via validate; the templates and the playbook derive every label from it.
export const computeName = validate.computeName;

// Template values for the compute stage. The name, the keypair mode and the
// source lists are resolved here once, so a template interpolates values and
// never branches on which provider it belongs to.
export function infrastructureData(opts: Opts): Opts {
  return {
    ...opts,
    "ssh-keygen": validate.keygen(opts),
    "compute-name": computeName(opts),
    "ssh-sources-hcl": tofu.hclList(cidrs(opts, computeKey(opts, "ssh-sources"))),
    "http-sources-hcl": tofu.hclList(cidrs(opts, computeKey(opts, "http-sources"))),
  };
}

// Vultr takes an address and a prefix length as separate fields, per address
// family, rather than a CIDR string. `0.0.0.0/0` is subnet 0.0.0.0 size 0.
export function cidrParts(cidr: unknown): { subnet: string; "subnet-size": number; "ip-type": string } {
  const s = String(cidr ?? "").trim();
  const [addr = "", size] = s.split("/");
  const v6 = addr.includes(":");
  return {
    subnet: addr,
    "subnet-size": Number.parseInt(size ?? (v6 ? "128" : "32"), 10),
    "ip-type": v6 ? "v6" : "v4",
  };
}

// One rule per protocol, address family and port. UDP 443 carries HTTP/3, which
// Caddy advertises through alt-svc whether or not the port is reachable, so
// omitting it degrades every visitor to TCP silently rather than erroring. An
// empty `vultr-http-sources` lists nothing to open, so no http, https or quic
// rule is emitted: no public HTTP, and the same rule names otherwise.
export function vultrFirewallJson(opts: Opts): string {
  const group = "${vultr_firewall_group.rybbit.id}";
  const entries: Array<[string, string, string, string]> = [
    ...cidrs(opts, "vultr-ssh-sources").map((c): [string, string, string, string] => ["ssh", "tcp", "22", c]),
    ...cidrs(opts, "vultr-http-sources").map((c): [string, string, string, string] => ["http", "tcp", "80", c]),
    ...cidrs(opts, "vultr-http-sources").map((c): [string, string, string, string] => ["https", "tcp", "443", c]),
    ...cidrs(opts, "vultr-http-sources").map((c): [string, string, string, string] => ["quic", "udp", "443", c]),
  ];
  return tofu.constructsJson(entries.map(([tag, proto, port, cidr], i) => {
    const parts = cidrParts(cidr);
    return tofu.construct("resource", "vultr_firewall_rule",
      `${tag}_${parts["ip-type"]}_${i}`,
      {
        firewall_group_id: group, protocol: proto, ip_type: parts["ip-type"],
        subnet: parts.subnet, subnet_size: parts["subnet-size"], port,
        notes: `${tag} ${parts["ip-type"]}`,
      });
  }));
}

// Providers are selected by template directory, not by conditionals inside one
// file. Vultr additionally needs its firewall rules generated, because their
// number depends on how many source CIDRs desired state lists.
export function infrastructureSpecs(opts: Opts): Spec[] {
  const dir = toolDir(opts, infrastructureTool);
  const data = infrastructureData(opts);
  const specs = [spec(template(`infrastructure.${opts["provider-compute"]}`, "main.tf"),
                      `${dir}/main.tf`, data)];
  if (opts["provider-compute"] === "vultr") {
    specs.push(rawSpec(`${dir}/firewall.tf.json`, vultrFirewallJson(data)));
  }
  return specs;
}

export async function infrastructureStep(opts: Opts): Promise<Opts> {
  const dir = toolDir(opts, infrastructureTool);
  const result = await tofu.tofuWithSpec(opts, infrastructureSpecs(opts),
    { dir, env: credentialEnv(opts, "provider-compute") });
  if (failed(result)) return result;
  if (opts["red/event"] === "build") return { ...result, ...fallbackParams(opts) };
  if (opts["red/event"] === "delete") return result;
  return resolvedCompute(result, fallbackParams(opts), compute.outputParams(result));
}

export function dnsData(opts: Opts): Opts {
  const host = String(opts["rybbit-host"]);
  const parts = host.split(".");
  const zone = opts["cloudflare-zone"] ??
    (parts.length > 2 ? parts.slice(1).join(".") : host);
  return {
    ...opts,
    ip: opts.ip ?? fallbackParams(opts).ip,
    "cloudflare-zone": zone,
    // Proxied by default: an unproxied record publishes the droplet's
    // address, leaving the firewall as the only thing in front of the
    // origin. The Caddyfile already trusts Cloudflare's ranges, so
    // client addresses still come from X-Forwarded-For and geo and ASN
    // attribution are unaffected. Set the key to false to opt out --
    // note that doing so is also what keeps ssh to the host name
    // working, which a converge never needs but an operator may.
    "cloudflare-proxied": opts["cloudflare-proxied"] !== null &&
      opts["cloudflare-proxied"] !== undefined
      ? opts["cloudflare-proxied"]
      : true,
  };
}

export function dnsJson(opts: Opts): string {
  return tofu.constructsJson([
    tofu.construct("resource", "cloudflare_dns_record", "rybbit", {
      zone_id: "${data.cloudflare_zone.zone.id}",
      name: opts["rybbit-host"], content: opts.ip, type: "A",
      proxied: Boolean(opts["cloudflare-proxied"]), ttl: 1,
    }),
  ]);
}

export async function dnsStep(opts: Opts): Promise<Opts> {
  const dir = toolDir(opts, dnsTool);
  const data = dnsData(opts);
  const specs = [
    spec(template("dns", "main.tf"), `${dir}/main.tf`, data),
    rawSpec(`${dir}/record.tf.json`, dnsJson(data)),
  ];
  return tofu.tofuWithSpec(opts, specs, { dir, env: credentialEnv(opts, "provider-dns") });
}

// Cheshire's pretty printer, byte for byte: spaces around colons, arrays
// inline, nested objects newline-indented.
function pretty(value: unknown, indent = 0): string {
  if (Array.isArray(value)) {
    if (value.length === 0) return "[ ]";
    return `[ ${value.map((item) => pretty(item, indent)).join(", ")} ]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) return "{ }";
    const pad = " ".repeat(indent + 2);
    return `{\n${entries
      .map(([key, nested]) => `${pad}${JSON.stringify(key)} : ${pretty(nested, indent + 2)}`)
      .join(",\n")}\n${" ".repeat(indent)}}`;
  }
  return JSON.stringify(value ?? null);
}

// ---------------------------------------------------------- ansible (local)

// Only what a `build` genuinely knows. The address, the user and the alias are
// run-time facts and reach the play as extra-vars instead, so the rendered
// playbook carries no IP and is identical on every workstation (SSH Config
// Standard §6).
export function ansibleLocalData(opts: Opts): Opts {
  return {
    ...opts,
    "ssh-keygen": validate.keygen(opts),
    "ssh-config-identity-file": sshConfig.identityFile(opts),
  };
}

export function ansibleLocalSpecs(opts: Opts): Spec[] {
  const dir = toolDir(opts, ansibleLocalTool);
  const data = ansibleLocalData(opts);
  return [
    spec(template("ansible-local", "ansible.cfg"), `${dir}/ansible.cfg`, data),
    spec(template("ansible-local", "inventory.ini"), `${dir}/inventory.ini`, data),
    spec(template("ansible-local", "main.yml"), `${dir}/main.yml`, data),
  ];
}

// Write or remove the `~/.ssh/config` block. The same playbook serves both
// events; `block_state` is what distinguishes them.
export async function ansibleLocalStep(opts: Opts): Promise<Opts> {
  const dir = toolDir(opts, ansibleLocalTool);
  const isDelete = opts["red/event"] === "delete";
  return ansible.ansibleWithSpec(opts, {
    dir,
    inventory: "inventory.ini",
    playbooks: { create: "main.yml", delete: "main.yml" },
    extraVars: {
      host_alias: sshConfig.hostAlias(opts),
      ip: opts.ip ?? fallbackParams(opts).ip,
      user: opts.user ?? "root",
      block_state: isDelete ? "absent" : "present",
    },
  }, ansibleLocalSpecs(opts));
}

// ---------------------------------------------------------------- ansible

export function inventory(opts: Opts): string {
  return pretty({
    all: {
      children: {
        rybbit: {
          hosts: {
            [String(opts.profile)]: {
              ansible_host: opts.ip ?? "192.0.2.10",
              ansible_user: "root",
            },
          },
        },
      },
    },
  });
}

// Template values for the Ansible stage. `ssh-private-key-path` reaches
// ansible.cfg so convergence uses the deployment's own key in keygen mode,
// where nothing guarantees an agent holds it.
export function ansibleData(opts: Opts): Opts {
  return {
    ...opts,
    ip: opts.ip ?? "192.0.2.10",
    "ssh-keygen": validate.keygen(opts),
    "compute-name": computeName(opts),
    "rybbit-backup-access-key": "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID') }}",
    "rybbit-backup-secret-key": "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY') }}",
  };
}

export function ansibleSpecs(opts: Opts): Spec[] {
  const dir = toolDir(opts, ansibleTool);
  const data = ansibleData(opts);
  return [
    spec(template("ansible", "ansible.cfg"), `${dir}/ansible.cfg`, data),
    spec(template("ansible", "main.yml"), `${dir}/main.yml`, data),
    spec(template("ansible", "cleanup.yml"), `${dir}/cleanup.yml`, data),
    spec(template("ansible", "compose.yml"), `${dir}/compose.yml`, data),
    spec(template("ansible", "Caddyfile"), `${dir}/Caddyfile`, data),
    spec(template("ansible", "backup"), `${dir}/backup`, data),
    rawSpec(`${dir}/inventory.json`, inventory(data)),
  ];
}

// `runner` is dependency-injected the way green's tests use with-redefs on
// ansible/ansible-with-spec: ES module exports cannot be rebound from a test.
export async function ansibleStep(
  opts: Opts,
  runner: typeof ansible.ansibleWithSpec = ansible.ansibleWithSpec,
): Promise<Opts> {
  const dir = toolDir(opts, ansibleTool);
  if (opts["red/event"] === "delete" && !opts.ip) {
    // No compute in state: there is no host to clean up, and the rendered
    // inventory would fall back to 192.0.2.10. Remove the rendered tree the
    // way a completed cleanup would and let the teardown continue.
    return {
      ...scaffold(opts, ansibleSpecs(opts)),
      "red/exit": 0, "rybbit/cleanup": "skipped-no-compute",
    };
  }
  return runner(opts, {
    dir,
    inventory: "inventory.json",
    playbooks: { create: "main.yml", delete: "cleanup.yml" },
    hostKeyChecking: false,
  }, ansibleSpecs(opts));
}

// --- Acceptance --------------------------------------------------------------
//
// Every claim this step reports must be one it checked. TLS is verified (never
// `curl -k`), an ingested event is read back out of ClickHouse rather than
// inferred from a status code, and the backup drill is confirmed by a fresh
// object in R2 rather than by systemd reporting that it started something.

export async function httpStatus(args: string[]): Promise<string | undefined> {
  const r = await runtime.exec(
    ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", ...args],
    { timeoutMs: 20000 });
  return r.exit === 0 ? String(r.out ?? "").trim() : undefined;
}

// Run `command` on the host over ssh. The deployment's own key is selected in
// keygen mode (`ssh.identityArgs`), because nothing guarantees an agent holds
// it; opt-out mode adds nothing and relies on the operator's identities.
export async function sshOut(opts: Opts, ip: unknown, command: string, timeout: number): Promise<string | undefined> {
  const r = await runtime.exec(
    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
     ...ssh.identityArgs(opts), `root@${ip}`, command],
    { timeoutMs: timeout });
  return r.exit === 0 ? String(r.out ?? "").trim() : undefined;
}

export const stackEnv = "cd /opt/rybbit && set -a && . ./stack.env && set +a && ";

export async function psql(opts: Opts, ip: unknown, query: string): Promise<string | undefined> {
  const out = String(await sshOut(opts, ip, stackEnv +
    'docker compose exec -T postgres psql -U "$POSTGRES_USER"' +
    ` -d "$POSTGRES_DB" -tAc '${query}'`, 30000) ?? "");
  return out.length > 0 ? out : undefined;
}

// Resolve the events table from system.tables so the check does not hardcode a
// database name Rybbit's migrations own, then run `query` against it.
export async function clickhouse(opts: Opts, ip: unknown, query: string): Promise<string | undefined> {
  const out = String(await sshOut(opts, ip, stackEnv +
    "t=$(docker compose exec -T clickhouse clickhouse-client" +
    ' --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"' +
    " --query \"SELECT database || '.' || name FROM system.tables" +
    " WHERE name = 'events' AND database NOT IN ('system')" +
    " ORDER BY database LIMIT 1\" | tr -d '\\r'); " +
    '[ -n "$t" ] && docker compose exec -T clickhouse clickhouse-client' +
    ' --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"' +
    ` --query "${query}"`, 30000) ?? "");
  return out.length > 0 ? out : undefined;
}

export async function eventCount(opts: Opts, ip: unknown): Promise<number | undefined> {
  const out = await clickhouse(opts, ip, "SELECT count() FROM $t");
  if (out === undefined) return undefined;
  const parsed = Number.parseInt(out, 10);
  return Number.isInteger(parsed) && /^[+-]?\d+$/.test(out.trim()) ? parsed : undefined;
}

// A dedicated throwaway site, created on demand. Sending the synthetic event to
// whichever site happened to be first wrote a /colors-acceptance pageview into
// the operator's real analytics on every converge. The site is attached to the
// existing organization so it stays visible and deletable in the UI.
export async function acceptanceSiteId(opts: Opts, ip: unknown): Promise<string | undefined> {
  const configured = String(opts["rybbit-acceptance-site-domain"] ?? "");
  const domain = configured.length > 0 ? configured : "colors-acceptance.invalid";
  // Dollar-quoted literals: the query travels inside single quotes in a
  // remote shell, where an escaped quote would arrive at psql verbatim.
  // psql prints the INSERT tag before the SELECT result, so take the id off
  // the last line rather than the whole output.
  const out = await psql(opts, ip,
    "insert into sites (name, domain, organization_id) " +
    `select $$colors-acceptance$$, $$${domain}$$, ` +
    "(select id from organization limit 1) " +
    `where not exists (select 1 from sites where domain = $$${domain}$$); ` +
    `select site_id from sites where domain = $$${domain}$$ limit 1`);
  const last = out?.split("\n").at(-1)?.trim();
  return last !== undefined && /^\d+$/.test(last) ? last : undefined;
}

export async function waitHealth(url: string, attempts: number): Promise<boolean> {
  for (let n = attempts; ; n -= 1) {
    const r = await runtime.exec(["curl", "-fsS", `${url}/api/health`], { timeoutMs: 10000 });
    if (r.exit === 0) return true;
    if (n <= 0) return false;
    await Bun.sleep(5000);
  }
}

// Rybbit discriminates on `type`, not `name`: the API answers 400 with
// "Invalid discriminator value" for anything else. This went unnoticed while
// no site existed, because the step reports "not-configured" and sends nothing.
export async function sendEvent(base: string, site: string): Promise<string | undefined> {
  return httpStatus(["-X", "POST", "-H", "content-type: application/json",
    "-H", "User-Agent: Mozilla/5.0 (Colors acceptance)",
    "--data", JSON.stringify({ type: "pageview", site_id: site,
                               pathname: "/colors-acceptance" }),
    `${base}/api/track`]);
}

export function ingestionVerdict(
  status: string | undefined,
  before: number | undefined,
  after: number | undefined,
): string {
  if (status === undefined || status === null) return "unreachable";
  if (Number.isInteger(before) && Number.isInteger(after) && (after as number) > (before as number)) {
    return "ingested";
  }
  if (/^2\d\d$/.test(String(status))) return "dropped";
  return "rejected";
}

export async function waitIngested(
  opts: Opts,
  ip: unknown,
  baseline: number,
  attempts: number,
): Promise<number | undefined> {
  for (let n = attempts; ; n -= 1) {
    const after = await eventCount(opts, ip);
    if (Number.isInteger(after) && (after as number) > baseline) return after;
    if (n <= 0) return after;
    await Bun.sleep(3000);
  }
}

export const rcloneEnv =
  "RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare " +
  "RCLONE_CONFIG_R2_REGION=auto RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true";

interface BackupEntry {
  Size?: number;
  ModTime?: string;
}

export async function backupListing(opts: Opts, ip: unknown): Promise<BackupEntry[] | undefined> {
  const out = await sshOut(opts, ip,
    `set -a; . /etc/rybbit-backup.env; set +a; ${rcloneEnv}` +
    ' RCLONE_CONFIG_R2_ACCESS_KEY_ID="$RYBBIT_BACKUP_R2_ACCESS_KEY_ID"' +
    ' RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"' +
    ` RCLONE_CONFIG_R2_ENDPOINT="${opts["rybbit-backup-r2-endpoint"]}"` +
    ` rclone lsjson --files-only r2:${opts["rybbit-backup-r2-bucket"]}` +
    `/${opts.profile}`,
    120000);
  if (out === undefined || out.length === 0) return undefined;
  try {
    return JSON.parse(out) as BackupEntry[];
  } catch {
    return undefined;
  }
}

// The instant `s` names, or undefined. Green parses through
// java.time.OffsetDateTime, which demands an explicit offset.
export function parseInstant(s: unknown): number | undefined {
  const text = String(s ?? "");
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(text)) return undefined;
  const t = Date.parse(text);
  return Number.isFinite(t) ? t : undefined;
}

export function freshBackup(entries: BackupEntry[] | undefined, since: number): boolean {
  return Boolean(entries?.some(({ Size, ModTime }) => {
    if (!((Size ?? 0) > 0)) return false;
    const t = parseInstant(ModTime);
    return t !== undefined && t >= since;
  }));
}

export async function runBackup(opts: Opts, ip: unknown): Promise<string | undefined> {
  return sshOut(opts, ip,
    "systemctl start rybbit-backup.service && systemctl is-active rybbit-backup.timer",
    300000);
}

export async function acceptanceStep(opts: Opts): Promise<Opts> {
  if (opts["red/event"] !== "create") return { ...opts, "red/exit": 0 };
  const base = `https://${opts["rybbit-host"]}`;
  const ip = opts.ip;
  const since = Date.now() - 120000;
  if (!(await waitHealth(base, 60))) {
    return { ...opts, "red/exit": 1,
      "red/err": "HTTPS health did not become ready with a valid certificate" };
  }
  const site = await acceptanceSiteId(opts, ip);
  const before = await eventCount(opts, ip);
  if (!Number.isInteger(before)) {
    return { ...opts, "red/exit": 1,
      "red/err": "could not read the ClickHouse events table to verify ingestion" };
  }
  const verdict = site === undefined
    ? "not-configured"
    : ingestionVerdict(await sendEvent(base, site),
                       before, await waitIngested(opts, ip, before as number, 10));
  if (["dropped", "rejected", "unreachable"].includes(verdict)) {
    return { ...opts, "red/exit": 1,
      "red/err": `synthetic event was not ingested: ${verdict}` };
  }
  if ((await runBackup(opts, ip)) === undefined) {
    return { ...opts, "red/exit": 1, "red/err": "backup unit or timer is not healthy" };
  }
  if (!freshBackup(await backupListing(opts, ip), since)) {
    return { ...opts, "red/exit": 1,
      "red/err": "no backup object newer than this run under r2:" +
        `${opts["rybbit-backup-r2-bucket"]}/${opts.profile}` };
  }
  return { ...opts, "red/exit": 0,
    "rybbit/acceptance": { health: "ok", event: verdict, backup: "verified-in-r2" } };
}
