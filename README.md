# Rybbit Package Skill

A tri-colour Package Skill (green, red, blue) for deploying a
production-oriented single-node [Rybbit](https://github.com/rybbit-io/rybbit)
privacy-friendly analytics platform on a VM provisioned by colors-compute.

The canonical implementation is [Green](https://github.com/getcolors/green)
(Clojure); the same deployment can run through the TypeScript
(`package-rybbit-red`) or Python (`package-rybbit-blue`) implementation — all
three render byte-identical artifacts, which `scripts/parity.sh` proves for
the v2 integration fixtures.

## Compute ownership

This version requires `compute-api-version: 2` and creates fresh deployments
only. Existing deployments keep their pinned launchers, configuration, keys and
state. There is no migration, adoption or compatibility layer.

The pinned [colors-compute v2 contract](https://github.com/getcolors/colors-compute/blob/b4421d0478ced33fc5b97cdd4a8a93cd34949f18/contracts/node.md)
owns provider validation, templates, backend access and guarded node operations.
Rybbit owns a singleton `rybbit-compute`, workflow ordering, a profile lock, and
application convergence. Provider support comes from that library pin; package
code does not maintain a provider matrix.

State uses `<profile>/rybbit-node-0.tfstate`; providers requiring public-key
registration use a separate `<profile>/rybbit-ssh-registration.tfstate`. DNS
retains `<profile>/rybbit-dns.tfstate`. R2 and S3 are supported; S3 uses ambient
AWS credentials. `compute-require-existing-state: true` guards subsequent
creates against missing ownership, but cannot create or import initial state.
Use fresh identities and state roots, never redirect an existing deployment.

Encrypted SSH authority is `ssh/machine-access/resource.json` beneath the
profile in the backend. Supply `COLORS_PAR_RYBBIT_SSH_PASSPHRASE` at runtime and
retain it for recovery. No decrypted private key is persisted. A temporary agent
and public identity cache provide access; use the launcher's `ssh` command.
External private-key paths and legacy provider key references are refused.

Use `compute-ssh-sources` and `compute-http-sources` for CIDR allowlists.
Rybbit also accepts its `rybbit-*` source aliases and selected-provider source
settings. TCP 22 is required; HTTP sources govern TCP 80/443 and UDP 443.
An empty HTTP list closes both HTTP and HTTP/3 ingress. Optional
`compute-network-mode` selects a library-supported network mode; when omitted,
the provider recipe supplies its default. A singleton needing no private
network can request `none` where the provider supports it.

Create writes the owned local SSH alias before DNS and remote convergence.
Delete removes aliases, performs application cleanup, destroys DNS and compute,
then removes any provider registration. It retains encrypted SSH authority.
The public cache alone cannot authenticate after the scoped agent stops.
Build renders under `.colors/build/<profile>/` without credentials; dry-run
performs no state reads or writes.

### Fresh deployment verification

Create first inspects encrypted SSH authority and reuses an existing ready
identity. If authority is confirmed missing and `compute-require-existing-state`
is false, Rybbit asks the compute library to inspect its node and registration
state and check the provider for matching resources. Only a verified absence
result authorizes creating the identity. Unreadable or nonempty state, existing
provider resources, incomplete inventories, and unsupported authentication
overrides stop creation. Restore the original authority when consumers survive;
never force the verification flag.

The check uses the configured provider account/project and stable resource names;
it does not discover renamed resources or other deployments sharing an identity.
Keep the profile and provider scope stable and serialize deployment operations.
Build and dry-run perform no verification calls. The library contract documents
required read permissions, command-line tools, and supported credential sources.

## Architecture

- **Compute**: One v2 node, with separate public-key registration where required.
- **Access**: Encrypted SSH authority and a temporary scoped agent. `./green ssh`
  opens an administrative session with the observed address and login user.
- **Ingress**: Caddy terminating origin TLS on ports 80/443 (plus UDP 443 for
  HTTP/3), reverse-proxying `/api/*` to Rybbit backend (Fastify) and the rest
  to Rybbit client (Next.js).
- **Databases**:
  - **PostgreSQL 17** (`postgres:17-alpine`) for auth/metadata (Better-Auth, users, projects).
  - **ClickHouse 26.3** (`clickhouse/clickhouse-server:26.3.17.4`) for high-throughput columnar analytics events.
  - **Redis** (`redis:8.6.4-alpine`) for session state and tracking queues.
- **Disaster Recovery**: Automated systemd timer `rybbit-backup.timer` executing
  `/usr/local/sbin/rybbit-backup` for consistent PostgreSQL dumps and ClickHouse
  snapshots, uploaded to Cloudflare R2 via `rclone`.

## Quick Start

```sh
npx skills add getcolors/rybbit
cp .agents/skills/package-rybbit-green/green ./green
chmod +x green
./green build
./green create --dry-run
```

The red and blue payloads (`package-rybbit-red`, `package-rybbit-blue`) install
and run the same way with `./red` and `./blue`.

## Lifecycle Commands

```sh
./green build              # render .colors/build/<profile>/ — no provider calls, no credentials
./green create --dry-run   # walk the DAG without making changes
./green create             # provision infrastructure and converge application
./green delete             # guarded deletion; retains encrypted SSH authority
./green ssh                # scoped administrative SSH session
```

## Hetzner migration target

The root `colors.yml` configures a fresh `rybbit-hetzner` deployment on Hetzner
CAX21 in Nuremberg (`nbg1`): 4 ARM64 vCPUs, 8 GB RAM and 80 GB local NVMe.
It pins Ubuntu 24.04 ARM image ID `161547270` and selects no private network.
All six container images are pinned to multi-architecture digests supporting
ARM64. The Rybbit backend and client target release **2.9.0**; the existing
Vultr deployment stays on **2.8.0**. PostgreSQL stays on the supported 17 major
line. Redis stays on 8.6.4, matching upstream's 2.9.0 Compose configuration,
rather than adding an independent Redis upgrade to the database migration.

Supply `COLORS_PAR_HCLOUD_TOKEN` at runtime, alongside the existing Cloudflare,
R2, backup and SSH-passphrase bindings. Use dedicated `rybbit-hetzner-state`
and `rybbit-hetzner-backup` buckets; state, SSH authority and backups use the
`rybbit-hetzner` profile prefix. No old state or SSH identity is adopted.
Both private EU buckets and separate bucket-scoped credentials were provisioned
and verified on 8 October 2026. Configuration itself does not create buckets
or move existing state and backups. See the
[deployment resource catalog](https://wiki.pocketcontext.com/#/page/deployment-profile-rybbit-hetzner)
for bootstrap ownership and credential references.

The initial Hetzner create completed on 8 October 2026;
`compute-require-existing-state: true` now guards subsequent operations.
Destroy protection stays enabled. Build and dry-run are offline checks;
real provisioning is a separate authorized operation.

Keep `rybbit.bigconfig.online` as the rehearsal hostname. Restore and validate
PostgreSQL, ClickHouse, application secrets and Redis workload handling before
the separately coordinated final backup and cutover of `rybbit.getcolors.ai`.
Vultr retains production DNS ownership until that cutover. Keep signup disabled
when restoring the existing users and organizations.

### Upgrade and restore rehearsal

Production was switched to Hetzner on 8 October 2026 at 06:18 UTC, after the
operator verified login, historical data and session replay on the rehearsal
site and accepted the gap since the restored snapshot. No final restore or
Redis migration was performed. `rybbit.getcolors.ai` now points to `2.31.12.220`;
Cloudflare proxying, automatic TTL and strict origin TLS were preserved.
The rehearsal hostname redirects to production, preserving the request path.
Vultr Caddy forwards late production requests to Hetzner using verified HTTPS;
its original application and databases remain intact for rollback.

The DNS state handoff completed at 06:38 UTC on 8 October 2026. Protected state
snapshots were captured first; `tofu state rm` detached the rehearsal record
from Hetzner and production from Vultr without deleting either live record.
Production record `638a21dd81d2e8f02995de6cc23cf67d` in zone
`19c2f7a3c6651751a43d9700640fd9fd` was imported at
`cloudflare_dns_record.rybbit` into
`rybbit-hetzner-state/rybbit-hetzner/rybbit-dns.tfstate`. The normal DNS plan
returned exit 0 with no changes, using the existing Cloudflare provider 5.27.0.
The dedicated production DNS credential is installed in the normal
`COLORS_PAR_CLOUDFLARE_API_TOKEN` runtime binding. The temporary source-state
credential was revoked after the handoff.

The first full convergence exposed an obsolete Hetzner 1.54 provider reader:
Hetzner had removed `datacenter.location`, so refresh returned an empty location
and proposed replacing the correctly located server. Destroy protection blocked
that plan. The compute library now pins compatible provider 1.58.0, following
the [upstream API compatibility release](https://github.com/hetznercloud/terraform-provider-hcloud/releases/tag/v1.58.0).
Provider locks were upgraded through OpenTofu initialization; no state values
or destruction guards were changed to suppress the mismatch.

The subsequent normal `create` completed all stages. A plan guard verified no
managed resource changes for compute and DNS before applying those plans;
server ID `169322464` remained unchanged and refreshed location was `nbg1`.
HTTPS, fresh public tracking and a new R2 backup passed. The redirect preserved
both path and query. The published compute dependency is
`b4421d0478ced33fc5b97cdd4a8a93cd34949f18`, package implementation `073e91c`
and launcher-pin commit `9d04622`. Local Rybbit validation passed 280 tests,
typecheck, golden, parity and published-launcher checks.

The current private environment, production DNS credential and a dated recovery
bundle are saved in VaultContext. The bundle contains application secret/config
files, current target state and encrypted machine-access authority, and the DNS
handoff snapshots. All three saved files passed exact-version restore checks
into protected paths without inspecting or executing the restored contents.
Database archives remain in R2; the earlier database restore rehearsal is
separate evidence. This is not a full replacement-server disaster-recovery drill.

Hetzner's DNS automation freeze is lifted with the redirect-support release.
Optional `rybbit-redirect-host: rybbit.bigconfig.online` renders a 301 redirect
to the primary hostname, preserving path and query during normal convergence.
It does not create or manage DNS for the redirect hostname; the existing
rehearsal record `747c4d8aa3bd8f7667ba59200b32b834` remains externally managed.
**Vultr `create`/`delete` remain frozen**: its state has no managed DNS record,
but its retained desired configuration still names production. Never converge
that retired profile against production. Destruction remains separately guarded.

For rollback, first suspend Hetzner convergence: its desired DNS state still
targets Hetzner and would undo a manual DNS rollback. Then restore Vultr's
`/opt/rybbit/Caddyfile` **in place** from its
protected `/var/backups/rybbit-cutover/Caddyfile.before` (write into the existing
file, preserving its inode), validate and reload Caddy, and
verify the old application directly over HTTPS. Then update only the existing
production A record's content to `78.141.212.24`, preserving proxy and TTL.
Restoring source Caddy first prevents it from continuing to forward to Hetzner.
The dedicated production DNS credential and before/after record metadata are
protected under `/home/ubuntu/.local/share/rybbit-hetzner-cutover/` on the
operator machine; never print or commit the credential. No rollback was run.
Target-only writes will not appear in the retained Vultr databases.

The following records the earlier rehearsal, before production cutover.

The 8 October 2026 rehearsal ran at
[rybbit.bigconfig.online](https://rybbit.bigconfig.online) on CAX21 in Nuremberg
(`2.31.12.220`, ARM64). Production remained on Vultr during that rehearsal;
the later cutover is recorded above. Published implementation `01f681dcfbe32082807badf0660b689169d6502d`
and launcher-pin commit `c352127203c286dff98f539d491aedfc3d484eec` were published.
Local validation passed 277 tests plus type, parity, golden and launcher checks;
the successful GitHub workflow published the repository's Pages site.

The source PostgreSQL dump restored with three sites and two users, then
upgraded to 19 recorded migrations. The ClickHouse 24.8 native archive restored
directly into 26.3.17.4. Historical events numbered 7,614; the isolated tracking
probe added one event. The one-time replay backfill matched 1,056 sessions,
64,096 replay events and 450,705,643 compressed bytes. A fresh target R2 archive
was downloaded and restored into scratch PostgreSQL and ClickHouse databases;
site/user/migration counts, event counts and replay totals matched. The scratch
databases were removed and the backup timer is active. Both hostnames returned
HTTP 200 for `/api/health` and `/login`.

Authenticated login, dashboard/replay checks, load testing and Redis workload
handling remain cutover prerequisites. `BETTER_AUTH_SECRET` was preserved;
Redis was not migrated. The rehearsal snapshot was taken while source ingestion
continued, so final cutover still requires a fenced, consistent snapshot. The
source's scheduled R2 backup was observed failing with HTTP 401; the rehearsal
used a separately captured database archive transferred over SSH. This source
backup failure remains unresolved. DNS rollback alone cannot recover writes
accepted only on the target. See the deployment resource catalog for dated
verification evidence and remaining ownership/recovery gaps.

The target follows [Rybbit 2.9.0](https://github.com/rybbit-io/rybbit/releases/tag/v2.9.0)
and its [Compose configuration](https://github.com/rybbit-io/rybbit/blob/v2.9.0/docker-compose.yml).
The image and template update does not prove database migration compatibility.
The source ClickHouse 24.8.14.39 to target 26.3.17.4 upgrade must pass a full
native-backup restore rehearsal, including JSON columns, historical counts,
replays, queries and ingestion. If direct restore fails, establish a supported
intermediate upgrade or logical transfer on disposable copies; do not alter
the source database to discover the path. Use a PostgreSQL logical dump, not
a copy of the x86 data directory, for the ARM64 target.

Normal `create` starts the application and performs acceptance, including a
backup. It is not a restore command or an automatic migration workflow. Before
restoring onto a rehearsal server, stop its backend, client, Caddy and backup
timer and any pending backup service. Discard only the target's disposable
bootstrap databases, recreate clean target databases, and restore the source
snapshot before starting the new backend. Do not merge the source dump into
the already-initialized 2.9.0 schema or copy source database migration markers
selectively. Keep the complete restored migration history with its data.

The backend entrypoint applies PostgreSQL migrations at startup. Version 2.9.0
adds migrations 0014 through 0018 beyond 2.8.0; the Better Auth migration checks
duplicate provider/account identities and invalid OAuth metadata. Rehearse and
verify login, organization/site IDs, API keys and OAuth behavior after migration.
Migration [0014](https://github.com/rybbit-io/rybbit/blob/v2.9.0/server/drizzle/0014_huge_dagger.sql)
drops the old uptime-monitoring tables, including monitors, incidents and alerts.
Check whether those features contain data you need before production cutover;
preserve the original dump and arrange a separate export or replacement if used.
Transfer required application secrets, especially `BETTER_AUTH_SECRET`, through
a protected channel without displaying them. The current archive omits those
secrets and Redis; explicitly preserve or drain relevant Redis state and verify
session continuity. Reconcile the target hostname settings after secret transfer.

Session replay needs a separate, one-time
[metadata v2 backfill](https://github.com/rybbit-io/rybbit/blob/v2.9.0/clickhouse/REPLAY_METADATA_V2.md).
The upgraded backend creates `session_replay_metadata_v2` but does not transfer
the old table's rows. After isolated startup initializes the new tables, keep
ingestion fenced and stop writers before executing the upstream backfill against
the restored database. It uses `FINAL`, preserves duration precision, and copies
the retained 30-day window. Verify session counts and event/size totals before
accepting writes. The insertion is **not idempotent**: rerunning doubles sums.
Record completion per restored snapshot; recover an interrupted rehearsal on a
fresh disposable target, never blindly repeat or truncate after production writes.
Retain the old replay table and source snapshot throughout the rollback window.

The target uses modern ClickHouse JSON settings and enables access management
for upstream's restricted query-user provisioning. Shared-host memory/thread
limits leave room for PostgreSQL, Redis and the application. Upstream's custom
query-user profile has its own larger per-query limit; the server-wide cap still
applies. Load testing and measured migration duration remain deployment checks.

Keep Vultr serving production throughout rehearsal. For cutover, fence writes
and drain pending work, capture a final consistent snapshot, repeat the proven
restore/migration, verify production TLS and URLs, and transfer DNS ownership
exactly once. Preserve Cloudflare proxy behavior; route late requests reaching
the old origin to the selected active writer to avoid split ingestion. Both
servers may remain running, but they must not independently accept production
writes during the transition.

Before target writes begin, the original Vultr snapshot provides a straightforward
fallback. Afterward, DNS rollback alone omits Hetzner-only events and account
changes. Lossless rollback requires a separately tested capture/replay or reverse
migration mechanism with deduplication. Do not run the old application against
upgraded schemas or assume ClickHouse data files can be downgraded.

When changing source templates, the standalone launchers continue to resolve
their existing release pins; a root image change does not upgrade the templates
fetched by those pins. Publish the reviewed source and regenerate launcher pins
with `bb pin` before a normal launcher deployment. For local
validation use the repository's `RYBBIT_LIB_ROOT` paths as in `scripts/parity.sh`.

For an empty installation, create the first account while signup is enabled, then set
`rybbit-disable-signup: true` and converge again. Until an organization exists,
acceptance may report ingestion as `not-configured`; rerun after bootstrap to
verify a synthetic event. Backup checks verify PostgreSQL restoration and a
fresh uploaded object, not a ClickHouse restore rehearsal.

## Development

```sh
cd green && bb test && bb golden          # canonical implementation and goldens
cd red && bun test && bun run typecheck   # TypeScript implementation
cd blue && uv run pytest                  # Python implementation
./scripts/parity.sh                       # three colours, v2 integration fixtures, byte for byte
./scripts/launcher.sh
```

## License

MIT License. Copyright (c) 2026 getcolors.

## Google SSH reauthentication

`./green ssh` (and the Red/Blue launchers) checks the current machine address
before opening its scoped SSH agent. When Google reports an expired user session,
an interactive terminal displays the reason and runs
`gcloud auth application-default login`, showing Google's browser link and login
progress directly in that terminal. A successful login retries the address lookup
once, then connects. Cancellation stops the operation; repeated authentication
failure never loops or falls back to a cached address.

For a terminal without a local browser, set `rybbit-ssh-login-browser: false` in
`colors.yml`, or run:

```sh
COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER=false ./green ssh
```

This uses `gcloud auth application-default login --no-launch-browser`; open its
link and paste the verification code into the terminal. Google sign-in still
requires user interaction. No login runs during build/dry-run or create/delete.

Automatic recovery requires local `authorized_user` ADC and no explicit Google
credential overrides. Service accounts, federation, overridden credentials, and
noninteractive sessions receive recovery instructions instead. The workflow does
not print tokens or capture the login interaction in its result. Google session
policies still apply; this handles expiry rather than extending its lifetime.

The pinned `colors-compute` dependency supplies the structured reauthentication
hint. Existing deployments keep their installed launcher pins until explicitly
updated; publishing this package does not refresh those copies.
