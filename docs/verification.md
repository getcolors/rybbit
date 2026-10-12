# Verification — 12 October 2026

## New Python deployment: rehearsal

Profile `rybbit-hcloud`, Hetzner server `169927907` at `178.105.137.48`,
CAX21 in nbg1. Owned firewall `11788632`; explicit owned primary IPv4 `155049690`.
The application is served at `https://rybbit-hcloud.getcolors.ai`.
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
