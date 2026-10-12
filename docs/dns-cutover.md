# Later DNS cutover: assessment only

Migration is deferred at the user's explicit request. Current authorized live
work is deletion/recreation testing of the fresh `rybbit-hcloud` deployment.
This document assesses a later transition from `rybbit-hcloud.getcolors.ai` to
`rybbit.getcolors.ai`; it does not authorize or execute migration, DNS adoption,
source fencing, state removal or production routing changes.

The durable profile stays **`rybbit-hcloud`** after a future cutover. Changing the
application hostname does not require renaming the server, firewall, IPv4,
backup bucket or SQLite deployment identity. The legacy `rybbit-hetzner`
controller remains production's manager until a deliberate ownership transfer.

## Current code and gaps

| Area | Implemented behavior | Required before production cutover |
| --- | --- | --- |
| Record ownership | `CloudflareDNS.evidence(host)` returns exact zone, record ID, address and settings fingerprint. `adopt(...)` journals ownership transfer while retaining address, proxy and TTL. | Expose a small reviewed Rybbit command/workflow; its CLI currently has no DNS evidence/adoption commands. Never use ordinary convergence to claim an existing production record. |
| Routing policy | Ordinary DNS convergence uses a proxied A record with Auto TTL. | Verify this matches production evidence; adoption preserves settings, but the following converge otherwise changes them. Support explicit retained policy if production differs. |
| Desired hostname | `controller.dns()` manages only the current `rybbit-host`. SQLite scope binds compute location/network, not hostname. | Explicitly inventory both staging and production records during transition. Changing YAML alone leaves the staging resource in state. |
| Deletion | A preflight guard now refuses recorded non-compute resources outside the current explicit DNS name/kind before fencing or DNS/cloud deletion. DNS deletion still selects only the current hostname; `delete_compute()` also refuses remaining non-compute resources. | Make an explicit retirement/retention decision for staging and any other recorded services before deletion. No automatic hostname-transition cleanup is implemented. |
| Application readiness | `converge()` updates DNS before configuring the remote application. | Add an application-only staging step and explicit routing switch. Confirm the production virtual host, origin TLS and health before moving production traffic. |
| Redirects | One optional `rybbit-redirect-host` renders a permanent redirect preserving `{uri}`; its DNS is externally managed. | Decide staging retention separately from the legacy `rybbit.bigconfig.online` redirect. One field cannot represent both redirects. Do not silently leave either DNS pointing at a retired origin. |

The deletion preflight guard is implemented with regression tests proving no
fencing, DNS or cloud deletion when a recorded resource lies outside the current
explicit hostname and compute kinds. Handle hostname transition with explicit evidence/adoption and a
recorded staging-retirement step, rather than a general DNS framework or a
second provider implementation. Retiring staging explicitly before final
production operation avoids needing a permanent list of managed hostnames;
retaining it needs explicit lifecycle ownership and cleanup support.

Only the deletion preflight guard has been implemented after this assessment.
The other gaps remain. The normal unchanged-host fresh-server deletion/recreation
test exercises a different path.

## TLS and application hostname

The current renderer updates Caddy's primary virtual host, `DOMAIN_NAME`,
`BASE_URL`, and the client's `NEXT_PUBLIC_BACKEND_URL` from `rybbit-host`.
It retains database passwords and `BETTER_AUTH_SECRET`. These changes require
application reconciliation; switching a Cloudflare A record alone is insufficient.
Restoring an auth secret does not prove browser sessions or OAuth callbacks work
on a different hostname. Verify production login, cookie behavior, callback
URLs and tracking endpoints against the selected active origin.

Production's current certificate belongs to its old origin. The target staging
certificate does not cover production automatically. Caddy's default challenge
flow relies on public DNS and reachable challenge ports; a hosts-file override
only changes the testing client's resolution. It does not redirect the CA's
validation. See [Caddy HTTPS troubleshooting](https://caddyserver.com/docs/troubleshooting).

Choose and rehearse one concrete certificate preparation method before cutover:
a scoped DNS challenge with the necessary Caddy provider module, or an explicitly
authorized transfer of the existing certificate/key with safe renewal behavior.
The stock deployment currently configures neither. DNS challenges require an
additional provider module/credential; they are not enabled by merely providing
the controller's Cloudflare token. See [Caddy DNS challenges](https://caddyserver.com/docs/json/admin/identity/issuers/opportunistic/fallback/opportunistic/primary/acme/challenges/dns).
Keep Cloudflare's existing proxy/TLS policy; do not weaken strict TLS to conceal
an unready origin. Verify direct origin HTTPS with production SNI and certificate
validation, then public HTTPS after the routing switch.

## Later transfer sequence

1. Obtain renewed authorization for migration and confirm the source/target data
   procedure independently of DNS. Finish the code gaps and a synthetic
   interrupted-transfer/deletion regression test first. Record the choice to
   remove or retain staging and both redirect hostnames.
2. Read exact production record evidence through the allowlisting API. Refuse
   multiple A records or AAAA/CNAME conflicts. Record ID/address/proxy/TTL and
   fingerprint without logging raw provider responses or ownership comments.
   Recheck evidence immediately before mutation; this assessment does not cache
   live production settings.
3. Disable and drain the legacy DNS manager and snapshot its exact DNS backend
   state privately. Its historical binding is `cloudflare_dns_record.rybbit`
   in `<profile>/rybbit-dns.tfstate`; verify the actual current binding and backend
   before any action. Keep that manager disabled throughout interrupted retries.
4. After the separately approved final data restore and writer handoff, prepare
   the target production hostname and TLS without publishing production DNS.
   Verify origin health and production authentication behavior as permitted.
5. Adopt the existing production A record into the target controller using exact
   evidence and `previous_dns_manager_disabled: true`. Adoption changes only the
   comment. Record and verify its preserved ID/address/proxy/TTL, then perform a
   separate explicit switch to the new verified target address. Check public
   health, fresh ingestion and the old-origin behavior. Never allow two writers.
6. Make the old DNS state forget only the transferred binding, retaining the
   actual provider record and protected before/after snapshots. Do not run its
   destroy command. OpenTofu's `state rm` forgets a binding without destroying
   the resource, but unchanged old desired configuration would try to recreate
   it on a later plan/apply. Keep the old controller disabled and remove its DNS
   management path before any later source retirement. See [OpenTofu state removal](https://opentofu.org/docs/v1.8/cli/commands/state/rm/).
7. Explicitly retire staging DNS using its recorded ID or retain it with tested
   lifecycle handling. Decide both redirects, verify new backup/recovery
   evidence, checkpoint controller state and document production ownership.
   Source compute retirement remains a separate decision after data retention.

Before target writes, reverting routing may be feasible while preserving a
single active source. After target writes, DNS-only rollback loses target-only
data; reverse transfer requires its own tested process. Ordinary source
controller convergence would also overwrite an intentional rollback or cutover
unless its DNS authority remains disabled.

## Read-only assessment evidence

Reviewed on 12 October 2026: Rybbit controller/CLI, hostname normalization,
Compose/Caddy rendering and retained-secret handling; PocketDeploy DNS adoption,
record-scoped deletion and compute-deletion guards; legacy DNS template and
recorded state conventions. No provider mutation, protected-file inspection,
legacy state change or data migration was performed for this assessment.
A read-only provider check confirmed production has exactly one A record,
pointing at `2.31.12.220`, proxied with Auto TTL (`1`); no A/AAAA/CNAME conflict
was found. This matches the current convergence policy. Its exact record ID and
settings fingerprint were inspected through the allowlisted evidence method.
Re-read them immediately before any later approved adoption. Certificate
availability and Cloudflare TLS mode were not inspected.
