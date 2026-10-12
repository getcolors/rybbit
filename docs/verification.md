# Verification — 12 October 2026

## Initial Python deployment: completed rehearsal (subsequently deleted)

Profile `rybbit-hcloud`, Hetzner server `169927907` at `178.105.137.48`,
CAX21 in nbg1. Owned firewall `11788632`; explicit owned primary IPv4 `155049690`.
This initial server served `https://rybbit-hcloud.getcolors.ai` until the
authorized deletion/recreation test below.
Production `https://rybbit.getcolors.ai` remains on the original
`rybbit-hetzner` server. This is a rehearsal, not completed production cutover.

The controller uses Python and PocketDeploy with local SQLite state and freshly
generated operator/host keys. Bootstrap host identity was rotated and verified
before application secrets were sent. hcloud 1.70.1 ARM64 was downloaded from its
release and checksum-verified. The new server has no private network or IPv6.

A current PostgreSQL logical dump and ClickHouse native archive were captured
from the running old server using the new Python backup helper. The transfer's
SHA256 matched. Source traffic was not stopped, so this rehearsal snapshot is
not a final consistent cross-database migration point. Retained database and
Better Auth secrets transferred privately before target database initialization.
No source database, provider resource or production DNS record was replaced.

## Completed live checks

- Provisioned and reconciled owned IPv4/firewall/server using hcloud; initial
  server creation and SSH worked. Destruction/rebuild protection remains enabled.
- `prepare` installed databases with application writers and backup timer fenced.
- `restore` recovered 39 PostgreSQL tables and the complete ClickHouse database.
- `converge --no-backups` started the restored application on separate DNS.
- Public HTTPS health and login returned 200 using curl; old production health
  also returned 200. Python urllib requests from the controller host received
  Cloudflare 403; no TLS or WAF protections were disabled.
- The unique acceptance event reached ClickHouse exactly once after correcting
  browser headers that the old synthetic request lacked. Ordinary bot filtering
  remained enabled. The initial dropped probe was reported as failure.
- Rebooted the staging server: pinned SSH, all six containers and the backup
  timer returned; public health returned 200 and another unique event arrived
  exactly once. A transient SSH refusal and HTTP 521 occurred during reboot.
- A new target backup uploaded to the retained R2 bucket under `rybbit-hcloud/`,
  with download-based verification before prefix-scoped retention.
- Independently downloaded that R2 object, verified its SHA256 and restored it
  into temporary PostgreSQL and ClickHouse databases on the target. Both scratch
  databases were removed after comparison. Restore contained 39 PostgreSQL
  tables, seven ClickHouse tables and the aggregates below.

| Restored aggregate | Value |
| --- | ---: |
| Users | 2 |
| Organizations | 1 |
| Sites | 3 |
| Analytics events | 7,854 |
| Replay event rows | 63,493 |
| Replay sessions (`FINAL`) | 1,037 |
| Replay metadata events (`FINAL`) | 63,493 |
| Replay compressed bytes (`FINAL`) | 430,464,360 |

These values describe the rehearsal archive. Subsequent synthetic events and
TTL expiry can change live counts. They do not establish authenticated historical
replay playback or zero-loss migration.

Repeated convergence passed with the published PocketDeploy dependency, retaining
the same server and enabling scheduled backups. The shared SSH updater now uses
`config.d`; only this deployment's generated inline alias was explicitly migrated,
with a private backup of the prior local configuration. Alias configuration is
now checked before provisioning.

## Compatibility issue found live

Ubuntu cloud-init returned status `done`, no errors and only a DEPRECATED notice,
with exit code 2. PocketDeploy previously refused host-key rotation on any nonzero
code. The shared fix accepts only that parsed narrow case; all other warnings,
errors or malformed output still block rotation. Rotation then succeeded.

## Storage and recovery boundaries

Existing bucket `rybbit-hetzner-backup` is retained as application-lifetime storage;
new writes/pruning use only `rybbit-hcloud/`. Old `rybbit-hetzner/` archives are
preserved. The existing credential is bucket-scoped: separate prefixes do not
provide credential isolation. No new bucket/token was provisioned or renamed.

The old source checkout is preserved at
`/home/ubuntu/code/getcolors/rybbit-hetzner-legacy`, pinned to `b5b63ca`.
Old encrypted authority/state remain unchanged. The new controller has local
private keys; this is intentionally a fresh authority model, not import of
legacy encrypted keys. A complete new-controller Vault checkpoint has not yet
been made. Database backup verification is separate from controller recovery.

## Automated validation

- PocketDeploy: 909 tests and four subtests after merging the concurrently
  published SSH config.d changes; package build and 36 copied-launcher checks.
- Alice against the shared changes: 42 tests.
- Walter against the shared changes: 162 tests, 47 subtests; one skipped.
- Rybbit: 78 tests and four subtests; package build passed and wheel templates verified.
- Copied portable launcher pinned to published implementation `1e9980dc750adfa62b7f9d99a49b1931ce5e2c29`: help, usage, synthetic configuration and missing-state checks passed from an unrelated directory, with no provider calls.
- Shared PocketDeploy implementation `52b016fb249068a3f3d3afa5b52042198bf7b3e7` and launcher release `b6eb347e13ca025b2a8bbd2c80fa28694c86009b` passed GitHub CI.

## Remaining release evidence

Final production freeze/snapshot, authenticated application/replay checks,
production DNS ownership transfer and old-server retirement are not claimed by
this rehearsal. See `migration-write-pause.md` for queue-drain limitations.

## Deletion and fresh recreation test

The user deferred production migration and authorized deletion/recreation of
`rybbit-hcloud`. Production and its DNS remain untouched.

- The pinned launcher refused deletion with destruction protection enabled.
- After explicitly disabling protection for this test, deletion fenced the
  application and removed its staging DNS record, server `169927907`, firewall
  `11788632` and primary IPv4 `155049690`. A second deletion succeeded idempotently.
- Independent provider-list checks confirmed those three IDs were absent.
  Cloudflare confirmed the staging record was absent. Controller state had zero
  resources and zero pending steps; owned SSH files and alias were removed.
- Archived the complete finished SQLite receipt privately, without editing its
  rows. Retained R2 archives and source controller/state were not deleted.
- Re-enabled destruction protection and initialized fresh ownership UUID
  `40bddaf7-f261-4e19-9c8b-02a935edc7b3`, with new SSH authority. Fresh convergence
  uses no source secrets or database restoration inputs.

Fresh server `169932721`, firewall `11788843`, primary IPv4 `155053924`. Hetzner
reassigned `178.105.137.48`; the provider identities and controller authority
are new. Fresh convergence and repeated convergence both passed. All eight
aggregate counts were zero: users, organizations, sites, events and replay
aggregates. HTTPS health/login returned 200. Acceptance verified health and
reported ingestion `not-configured` because this empty installation has no
organization; no source data or secrets were restored. Existing-state enforcement
and destruction protection are both enabled again, leaving `colors.yml` unchanged.

The DNS assessment found a partial-deletion hazard after changing the managed
hostname. A new preflight guard refuses unexpected recorded DNS/service resources
before any fencing or provider mutation. Regression suite: 82 tests and four
subtests passed; package build passed. See [DNS cutover assessment](dns-cutover.md)
for the remaining transition work. Production still resolves through its
existing proxied A record to `2.31.12.220`; public health returned 200.

A backup of the fresh empty installation also passed PostgreSQL scratch recovery,
R2 upload and download verification. Historical R2 archives were retained;
normal profile-prefix retention remains enabled for future scheduled backups.

The portable launcher is now pinned to published guard implementation `6cf30d3`;
its copied help/configuration/missing-state checks passed without provider calls.

## Cutover preparation and second rehearsal — 12 October 2026

The user authorized migration preparation with subagents, retaining a final
production write-pause/routing approval gate. Production routing and writers
remain on server `169322464`; target identity remains server `169932721`,
deployment `40bddaf7-f261-4e19-9c8b-02a935edc7b3` at `178.105.137.48`.

- Saved the target controller's six recovery files and SQLite checkpoint to
  the default Vault. Separately saved retained source application secrets,
  source node/DNS backend snapshots, encrypted machine authority and the exact
  required source credential bindings. File contents were not exposed.
- Implemented explicit application-only reconciliation, origin readiness,
  DNS evidence/adoption/switch/retirement and staging-only reset. Reset verifies
  deployment/provider/machine identities, removes stopped containers, and
  quarantines database directories and receipts without deleting them. It
  preserves Caddy storage and backups. Pending or malformed reset receipts block
  application mutations; reset is forbidden once DNS adoption begins.
- Live reset, database-only preparation with retained source secrets, and
  restoration succeeded. Reset took 3.7 seconds, preparation 17.3 seconds and
  archive transfer/restoration 5.1 seconds. These are individual rehearsal
  timings, not a measured production interruption.
- The fresh live-source rehearsal archive restored 39 PostgreSQL tables and
  seven ClickHouse tables: two users, one organization, three sites, 7,855
  events, 63,493 replay rows/events, 1,037 logical replay sessions and
  430,600,572 compressed replay bytes. Source writers continued during capture;
  this is not a final cross-database-consistent snapshot.
- All six source/target image digests matched. Privately transferred the existing
  production Caddy-managed certificate/key/metadata to target storage, retaining
  automatic certificate management. The certificate SAN covers production and
  expires 22 November 2026. Configured production hostname on the target without
  DNS mutation: running application settings, direct origin TLS/SNI, backend
  health and frontend passed. An external direct-origin HTTPS probe also returned
  200 with normal certificate verification. Restored the staging hostname.
- Cloudflare TLS-setting read returned HTTP 403 with the existing credential;
  the setting was not changed. Public production health remained HTTP 200.
- The user verified staging login, historical analytics and replay playback in
  their browser. A unique synthetic ingestion event arrived exactly once.
- A new target backup uploaded and verified successfully. Independently
  downloaded it and restored scratch PostgreSQL and ClickHouse databases:
  39 and seven tables respectively, matching the above aggregates plus the
  single synthetic event (7,856 total). Scratch databases were removed.
- Source and target rehearsal archives were independently copied and verified
  under `migration-retained/2026-10-12/` in the existing backup bucket, outside
  normal profile-prefix retention. No bucket was created.
- Source/controller read-only inventory found no scheduled DNS manager; legacy
  explicit create/delete entrypoints still require locking and disabling before
  ownership transfer. Production DNS has not been adopted or switched, source
  writes have not been fenced, and source retirement is not authorized.

Rybbit validation: 135 tests and 85 subtests, package build. Pinned PocketDeploy
implementation `35529af2b3642f530e8bc2a27ecefecff28f4797` fixes stale DNS write
journals after lost-response recovery; its 914 tests/four subtests, build and
36 copied-launcher checks passed. Alice's 42 tests passed. Walter's unchanged
inline-SSH migration test fails with both the previous and new dependency; its
other 161 tests/47 subtests passed with one skipped. Walter's pin was unchanged.

After browser acceptance, the published portable controller successfully reset
the exercised staging databases a second time, preserving them in quarantine,
and prepared clean database-only state with retained source secrets. Target
application ingress/writers and backups are now fenced for the final archive;
staging is intentionally unavailable. A repeated preparation passed with the
final service-state verification: timer disabled, timer/service inactive, and
unexpected systemctl inspection failures refused. Production remains online.

## Production cutover — 12 October 2026

The user approved the final write interruption and routing transfer. Production
now runs on `rybbit-hcloud`, server `169932721`, `178.105.137.48`, deployment
`40bddaf7-f261-4e19-9c8b-02a935edc7b3`, at `rybbit.getcolors.ai`.

- Source ingress was fenced, counts remained quiet for 60 seconds, then backend
  and client stopped. Source backups were disabled. Queue depth was not directly
  verified, so this does not establish a zero-loss drain. Freeze began 18:14:49
  UTC; independent public health passed at 18:22:59 UTC, an approximately
  eight-minute-ten-second freeze-to-verification interval.
- Final source archive `rybbit-20261012T181655Z-1c8dcaf6.tar.gz`, SHA-256
  `bd862589961a6d508618d16fe71d5ebd56bead046ac022adf5a0c89a046d42ff`, restored
  successfully. It remains on source, protected local storage and target, with
  an independently downloaded/verified R2 copy under
  `migration-retained/2026-10-12/`, outside scheduled retention.
- Users (2), organizations (1), sites (3) and analytics events (7,855) matched.
  The target had 1,037 replay sessions, 63,493 replay rows/events and 430,600,572
  compressed bytes. The source difference of two sessions, six rows/events and
  143,341 bytes exactly matched source records expired under the 30-day TTL;
  target expired counts were zero and latest timestamps matched. This is
  aggregate evidence of retention expiry, not a row-by-row equivalence proof.
  The user had explicitly accepted a small loss while this was investigated.
- Production origin TLS/SNI, backend and frontend readiness passed before
  switching. DNS adoption preserved the source address and record identity;
  the separate switch retained record `638a21dd81d2e8f02995de6cc23cf67d`, proxy
  enabled and TTL 1, changing only the origin to the target.
- The legacy default configuration was disabled and its managers locked during
  transfer. Supported OpenTofu state removal forgot only
  `cloudflare_dns_record.rybbit` (backend serial 4 to 5); private before/after
  snapshots were retained. Independent provider evidence confirmed the target
  record remained unchanged. Never run the disabled legacy DNS manager again.
- Independent public and direct-origin health, login and tracking script checks
  passed with valid TLS. A unique synthetic event arrived exactly once. The
  user verified production login, historical analytics and replay playback.
- Target convergence enabled normal backups. Backup
  `rybbit-20261012T182344Z-85a428fa.tar.gz` uploaded and verified, then an
  independent R2 download restored 39 PostgreSQL and seven ClickHouse tables
  into temporary databases. All aggregates matched, including 7,856 events
  after acceptance; scratch databases were removed.
- Source server `169322464` remains retained with backend/client stopped and
  backups disabled. Only its existing Caddy container was restarted to preserve
  the verified HTTPS 301 redirect from `rybbit.bigconfig.online` to production.
  Source retirement is not authorized. DNS-only rollback would discard new
  target writes; any reverse migration requires a verified data transfer.
- Retired the exact owned staging DNS record. Final independent checks confirmed
  all six target services running, all configured health checks healthy, backup
  timer enabled/active, and public HTTPS health 200.
