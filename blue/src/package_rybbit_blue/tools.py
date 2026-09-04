"""OpenTofu and Ansible stages plus acceptance, the port of
io.github.getcolors.rybbit.tools."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blue import tofu
from blue.ansible import ansible_with_spec
from blue.cli import stage_dir
from blue.runtime import runtime
from blue.scaffold import PRESERVE_JINJA_DELIMITERS, content_spec, scaffold
from package_once_blue import compute as once_compute

from . import ssh, validate

infrastructure_tool = "rybbit-infrastructure"
dns_tool = "rybbit-dns"
ansible_tool = "rybbit-ansible"
ROOT = Path(__file__).parent / "resources"
template_opts = PRESERVE_JINJA_DELIMITERS


def tool_dir(opts: dict, tool: str) -> str:
    return stage_dir(opts, tool, default_profile="rybbit")


def template(path: str, file: str) -> dict:
    name = f"tools/{path.replace('.', '/')}/{file}"
    return {"name": name, "content": (ROOT / name).read_text()}


def spec(source: dict, target: str, data: dict) -> dict:
    return {"template": source, "target": target, "data": data, "opts": template_opts}


def raw_spec(target: str, content: str) -> dict:
    return content_spec(target, content)


# The source lists as validate parses them, so the template and the
# validator can never disagree about what an entry is. ONCE's.
cidrs = validate.cidrs


def credential_env(opts: dict, *slots: str) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for slot in [*slots, "provider-backend"]:
        merged.update(validate.tofu_env(opts, slot))
    result = {}
    for key, env_var in merged.items():
        value = "" if opts.get(key) is None else str(opts.get(key))
        if value:
            result[env_var] = value
    return result or None


def backend_credential_env(opts: dict) -> dict[str, str] | None:
    return credential_env(opts)


# What `build` and `--dry-run` render in place of a compute output: the
# documentation address, shaped like the selected provider's real `params` so
# every later stage sees the same keys either way. ONCE's.
fallback_params = once_compute.fallback_params

# Refuse to hand 192.0.2.10 to Ansible on a real converge whose compute output
# carries no `ip`. ONCE's; `infrastructure_step` is what wires it.
resolved_compute = once_compute.resolved_compute

# `<provider>-<suffix>`, the selected provider's key. ONCE's, via validate.
compute_key = validate.compute_key

# The machine's name: `<provider>-name` when present, else the profile. ONCE's,
# via validate; the templates and the playbook derive every label from it.
compute_name = validate.compute_name


def infrastructure_data(opts: dict) -> dict:
    """Template values for the compute stage. The name, the keypair mode and
    the source lists are resolved here once, so a template interpolates values
    and never branches on which provider it belongs to."""
    return {**opts,
            "ssh-keygen": validate.keygen(opts),
            "compute-name": compute_name(opts),
            "ssh-sources-hcl": tofu.hcl_list(cidrs(opts, compute_key(opts, "ssh-sources"))),
            "http-sources-hcl": tofu.hcl_list(cidrs(opts, compute_key(opts, "http-sources")))}


def cidr_parts(cidr) -> dict:
    """Vultr takes an address and a prefix length as separate fields, per
    address family, rather than a CIDR string. `0.0.0.0/0` is subnet 0.0.0.0
    size 0."""
    s = str("" if cidr is None else cidr).strip()
    addr, _, size = s.partition("/")
    v6 = ":" in addr
    return {"subnet": addr,
            "subnet-size": int(size or ("128" if v6 else "32")),
            "ip-type": "v6" if v6 else "v4"}


def vultr_firewall_json(opts: dict) -> str:
    """One rule per protocol, address family and port. UDP 443 carries HTTP/3,
    which Caddy advertises through alt-svc whether or not the port is
    reachable, so omitting it degrades every visitor to TCP silently rather
    than erroring. An empty `vultr-http-sources` lists nothing to open, so no
    http, https or quic rule is emitted: no public HTTP, and the same rule
    names otherwise."""
    group = "${vultr_firewall_group.rybbit.id}"
    entries = [*[("ssh", "tcp", "22", c) for c in cidrs(opts, "vultr-ssh-sources")],
               *[("http", "tcp", "80", c) for c in cidrs(opts, "vultr-http-sources")],
               *[("https", "tcp", "443", c) for c in cidrs(opts, "vultr-http-sources")],
               *[("quic", "udp", "443", c) for c in cidrs(opts, "vultr-http-sources")]]
    constructs = []
    for i, (tag, proto, port, cidr) in enumerate(entries):
        parts = cidr_parts(cidr)
        constructs.append(tofu.construct(
            "resource", "vultr_firewall_rule", f"{tag}_{parts['ip-type']}_{i}",
            {"firewall_group_id": group, "protocol": proto,
             "ip_type": parts["ip-type"], "subnet": parts["subnet"],
             "subnet_size": parts["subnet-size"], "port": port,
             "notes": f"{tag} {parts['ip-type']}"}))
    return tofu.constructs_json(constructs)


def infrastructure_specs(opts: dict) -> list[dict]:
    """Providers are selected by template directory, not by conditionals inside
    one file. Vultr additionally needs its firewall rules generated, because
    their number depends on how many source CIDRs desired state lists."""
    dir = tool_dir(opts, infrastructure_tool)
    data = infrastructure_data(opts)
    specs = [spec(template(f"infrastructure.{opts.get('provider-compute')}", "main.tf"),
                  f"{dir}/main.tf", data)]
    if opts.get("provider-compute") == "vultr":
        specs.append(raw_spec(f"{dir}/firewall.tf.json", vultr_firewall_json(data)))
    return specs


async def infrastructure_step(opts: dict) -> dict:
    dir = tool_dir(opts, infrastructure_tool)
    result = await tofu.tofu_with_spec(
        opts, infrastructure_specs(opts),
        dir=dir, env=credential_env(opts, "provider-compute"))
    if (result.get("blue/exit") or 0) > 0:
        return result
    if opts.get("blue/event") == "build":
        return {**result, **fallback_params(opts)}
    if opts.get("blue/event") == "delete":
        return result
    return resolved_compute(result, fallback_params(opts), once_compute.output_params(result))


def dns_data(opts: dict) -> dict:
    host = str(opts.get("rybbit-host"))
    parts = host.split(".")
    zone = opts.get("cloudflare-zone") or (
        ".".join(parts[1:]) if len(parts) > 2 else host)
    # Proxied by default: an unproxied record publishes the droplet's
    # address, leaving the firewall as the only thing in front of the
    # origin. The Caddyfile already trusts Cloudflare's ranges, so
    # client addresses still come from X-Forwarded-For and geo and ASN
    # attribution are unaffected. Set the key to false to opt out --
    # note that doing so is also what keeps ssh to the host name
    # working, which a converge never needs but an operator may.
    return {**opts,
            "ip": opts.get("ip") or fallback_params(opts)["ip"],
            "cloudflare-zone": zone,
            "cloudflare-proxied": (opts.get("cloudflare-proxied")
                                   if opts.get("cloudflare-proxied") is not None
                                   else True)}


def dns_json(opts: dict) -> str:
    return tofu.constructs_json([
        tofu.construct("resource", "cloudflare_dns_record", "rybbit",
                       {"zone_id": "${data.cloudflare_zone.zone.id}",
                        "name": opts.get("rybbit-host"),
                        "content": opts.get("ip"), "type": "A",
                        "proxied": bool(opts.get("cloudflare-proxied")),
                        "ttl": 1})])


async def dns_step(opts: dict) -> dict:
    dir = tool_dir(opts, dns_tool)
    data = dns_data(opts)
    specs = [spec(template("dns", "main.tf"), f"{dir}/main.tf", data),
             raw_spec(f"{dir}/record.tf.json", dns_json(data))]
    return await tofu.tofu_with_spec(
        opts, specs, dir=dir, env=credential_env(opts, "provider-dns"))


def _pretty(value, indent=0):
    """Cheshire's pretty JSON, byte for byte — Green's artifact contract."""
    if isinstance(value, list):
        if not value:
            return "[ ]"
        return "[ " + ", ".join(_pretty(item, indent) for item in value) + " ]"
    if isinstance(value, dict):
        if not value:
            return "{ }"
        pad = " " * (indent + 2)
        body = ",\n".join(f"{pad}{json.dumps(str(k))} : {_pretty(v, indent + 2)}"
                          for k, v in value.items())
        return "{\n" + body + "\n" + " " * indent + "}"
    return json.dumps(value)


def inventory(opts: dict) -> str:
    return _pretty(
        {"all": {"children": {"rybbit": {"hosts": {
            opts.get("profile"): {"ansible_host": opts.get("ip") or "192.0.2.10",
                                  "ansible_user": "root"}}}}}})


def ansible_data(opts: dict) -> dict:
    """Template values for the Ansible stage. `ssh-private-key-path` reaches
    ansible.cfg so convergence uses the deployment's own key in keygen mode,
    where nothing guarantees an agent holds it."""
    return {**opts,
            "ip": opts.get("ip") or "192.0.2.10",
            "ssh-keygen": validate.keygen(opts),
            "compute-name": compute_name(opts),
            "rybbit-backup-access-key":
                "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID') }}",
            "rybbit-backup-secret-key":
                "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY') }}"}


def ansible_specs(opts: dict) -> list[dict]:
    dir = tool_dir(opts, ansible_tool)
    data = ansible_data(opts)
    return [spec(template("ansible", "ansible.cfg"), f"{dir}/ansible.cfg", data),
            spec(template("ansible", "main.yml"), f"{dir}/main.yml", data),
            spec(template("ansible", "cleanup.yml"), f"{dir}/cleanup.yml", data),
            spec(template("ansible", "compose.yml"), f"{dir}/compose.yml", data),
            spec(template("ansible", "Caddyfile"), f"{dir}/Caddyfile", data),
            spec(template("ansible", "backup"), f"{dir}/backup", data),
            raw_spec(f"{dir}/inventory.json", inventory(data))]


async def ansible_step(opts: dict) -> dict:
    dir = tool_dir(opts, ansible_tool)
    if opts.get("blue/event") == "delete" and not opts.get("ip"):
        # No compute in state: there is no host to clean up, and the rendered
        # inventory would fall back to 192.0.2.10. Remove the rendered tree the
        # way a completed cleanup would and let the teardown continue.
        return {**scaffold(opts, ansible_specs(opts)),
                "blue/exit": 0, "rybbit/cleanup": "skipped-no-compute"}
    return await ansible_with_spec(
        opts, ansible_specs(opts),
        dir=dir, inventory="inventory.json",
        playbooks={"create": "main.yml", "delete": "cleanup.yml"},
        host_key_checking=False)


# --- Acceptance --------------------------------------------------------------
#
# Every claim this step reports must be one it checked. TLS is verified (never
# `curl -k`), an ingested event is read back out of ClickHouse rather than
# inferred from a status code, and the backup drill is confirmed by a fresh
# object in R2 rather than by systemd reporting that it started something.


async def http_status(args: list[str]) -> str | None:
    r = await runtime.exec(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", *args],
        timeout_ms=20000)
    return str(r.out or "").strip() if r.exit == 0 else None


async def ssh_out(opts: dict, ip, command: str, timeout: int) -> str | None:
    """Run `command` on the host over ssh. The deployment's own key is selected
    in keygen mode (`ssh.identity_args`), because nothing guarantees an agent
    holds it; opt-out mode adds nothing and relies on the operator's
    identities."""
    r = await runtime.exec(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         *ssh.identity_args(opts), f"root@{ip}", command],
        timeout_ms=timeout)
    return str(r.out or "").strip() if r.exit == 0 else None


stack_env = "cd /opt/rybbit && set -a && . ./stack.env && set +a && "


async def psql(opts: dict, ip, query: str) -> str | None:
    out = str(await ssh_out(
        opts, ip, stack_env
        + 'docker compose exec -T postgres psql -U "$POSTGRES_USER"'
        + f" -d \"$POSTGRES_DB\" -tAc '{query}'", 30000) or "")
    return out or None


async def clickhouse(opts: dict, ip, query: str) -> str | None:
    """Resolve the events table from system.tables so the check does not
    hardcode a database name Rybbit's migrations own, then run `query` against
    it."""
    out = str(await ssh_out(
        opts, ip, stack_env
        + "t=$(docker compose exec -T clickhouse clickhouse-client"
        + ' --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"'
        + " --query \"SELECT database || '.' || name FROM system.tables"
        + " WHERE name = 'events' AND database NOT IN ('system')"
        + " ORDER BY database LIMIT 1\" | tr -d '\\r'); "
        + '[ -n "$t" ] && docker compose exec -T clickhouse clickhouse-client'
        + ' --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"'
        + f' --query "{query}"', 30000) or "")
    return out or None


async def event_count(opts: dict, ip) -> int | None:
    out = await clickhouse(opts, ip, "SELECT count() FROM $t")
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


async def acceptance_site_id(opts: dict, ip) -> str | None:
    """A dedicated throwaway site, created on demand. Sending the synthetic
    event to whichever site happened to be first wrote a /colors-acceptance
    pageview into the operator's real analytics on every converge. The site is
    attached to the existing organization so it stays visible and deletable in
    the UI."""
    domain = str(opts.get("rybbit-acceptance-site-domain") or "") \
        or "colors-acceptance.invalid"
    # Dollar-quoted literals: the query travels inside single quotes in a
    # remote shell, where an escaped quote would arrive at psql verbatim.
    # psql prints the INSERT tag before the SELECT result, so take the id off
    # the last line rather than the whole output.
    out = await psql(opts, ip, (
        "insert into sites (name, domain, organization_id) "
        f"select $$colors-acceptance$$, $${domain}$$, "
        "(select id from organization limit 1) "
        f"where not exists (select 1 from sites where domain = $${domain}$$); "
        f"select site_id from sites where domain = $${domain}$$ limit 1"))
    if out is None:
        return None
    last = out.splitlines()[-1].strip() if out.splitlines() else ""
    return last if re.fullmatch(r"\d+", last) else None


async def wait_health(url: str, attempts: int) -> bool:
    n = attempts
    while True:
        r = await runtime.exec(["curl", "-fsS", f"{url}/api/health"], timeout_ms=10000)
        if r.exit == 0:
            return True
        if n > 0:
            await asyncio.sleep(5)
            n -= 1
        else:
            return False


async def send_event(base: str, site: str) -> str | None:
    """Rybbit discriminates on `type`, not `name`: the API answers 400 with
    "Invalid discriminator value" for anything else. This went unnoticed while
    no site existed, because the step reports "not-configured" and sends
    nothing."""
    return await http_status(
        ["-X", "POST", "-H", "content-type: application/json",
         "-H", "User-Agent: Mozilla/5.0 (Colors acceptance)",
         "--data", json.dumps({"type": "pageview", "site_id": site,
                               "pathname": "/colors-acceptance"}),
         f"{base}/api/track"])


def ingestion_verdict(status, before, after) -> str:
    if status is None:
        return "unreachable"
    if isinstance(before, int) and isinstance(after, int) and after > before:
        return "ingested"
    if re.fullmatch(r"2\d\d", str(status)):
        return "dropped"
    return "rejected"


async def wait_ingested(opts: dict, ip, baseline: int, attempts: int) -> int | None:
    n = attempts
    while True:
        after = await event_count(opts, ip)
        if isinstance(after, int) and after > baseline:
            return after
        if n > 0:
            await asyncio.sleep(3)
            n -= 1
        else:
            return after


rclone_env = ("RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare "
              "RCLONE_CONFIG_R2_REGION=auto RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true")


async def backup_listing(opts: dict, ip) -> list[dict] | None:
    out = await ssh_out(
        opts, ip,
        "set -a; . /etc/rybbit-backup.env; set +a; " + rclone_env
        + ' RCLONE_CONFIG_R2_ACCESS_KEY_ID="$RYBBIT_BACKUP_R2_ACCESS_KEY_ID"'
        + ' RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"'
        + f" RCLONE_CONFIG_R2_ENDPOINT=\"{opts.get('rybbit-backup-r2-endpoint')}\""
        + f" rclone lsjson --files-only r2:{opts.get('rybbit-backup-r2-bucket')}"
        + f"/{opts.get('profile')}",
        120000)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def parse_instant(s) -> datetime | None:
    """The instant `s` names, or None. Green parses through
    java.time.OffsetDateTime, which demands an explicit offset."""
    try:
        t = datetime.fromisoformat(str(s))
        return t if t.tzinfo is not None else None
    except ValueError:
        return None


def fresh_backup(entries, since: datetime) -> bool:
    for entry in entries or []:
        if (entry.get("Size") or 0) > 0:
            t = parse_instant(entry.get("ModTime"))
            if t is not None and t >= since:
                return True
    return False


async def run_backup(opts: dict, ip) -> str | None:
    return await ssh_out(
        opts, ip, "systemctl start rybbit-backup.service"
        " && systemctl is-active rybbit-backup.timer", 300000)


async def acceptance_step(opts: dict) -> dict:
    if opts.get("blue/event") != "create":
        return {**opts, "blue/exit": 0}
    base = f"https://{opts.get('rybbit-host')}"
    ip = opts.get("ip")
    since = datetime.now(timezone.utc) - timedelta(seconds=120)
    if not await wait_health(base, 60):
        return {**opts, "blue/exit": 1,
                "blue/err": "HTTPS health did not become ready with a valid certificate"}
    site = await acceptance_site_id(opts, ip)
    before = await event_count(opts, ip)
    if not isinstance(before, int):
        return {**opts, "blue/exit": 1,
                "blue/err": "could not read the ClickHouse events table to verify ingestion"}
    if site is None:
        verdict = "not-configured"
    else:
        status = await send_event(base, site)
        after = await wait_ingested(opts, ip, before, 10)
        verdict = ingestion_verdict(status, before, after)
    if verdict in ("dropped", "rejected", "unreachable"):
        return {**opts, "blue/exit": 1,
                "blue/err": f"synthetic event was not ingested: {verdict}"}
    if await run_backup(opts, ip) is None:
        return {**opts, "blue/exit": 1,
                "blue/err": "backup unit or timer is not healthy"}
    if not fresh_backup(await backup_listing(opts, ip), since):
        return {**opts, "blue/exit": 1,
                "blue/err": ("no backup object newer than this run under r2:"
                             f"{opts.get('rybbit-backup-r2-bucket')}/{opts.get('profile')}")}
    return {**opts, "blue/exit": 0,
            "rybbit/acceptance": {"health": "ok", "event": verdict,
                                  "backup": "verified-in-r2"}}
