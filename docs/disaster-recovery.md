# Restore into a new deployment profile

The Python controller supports staged preparation and explicit archive
restoration. Use a fresh owned target; do not import legacy infrastructure state.
Current inventory belongs in WikiContext and dated execution evidence belongs
in [verification](verification.md). Credentials and recovery files remain private.

## Source and target

Production moved to `rybbit-hcloud` (server `169932721`, `178.105.137.48`)
on 12 October 2026, serving `rybbit.getcolors.ai`. The former migration source
`rybbit-hetzner` was retired on 13 October after its legacy redirect moved to the
target. Its server, dedicated firewall and primary IPv4 no longer exist. Final
archives, legacy state and controller recovery material remain retained; do not
restart the legacy controller. Recovery now requires fresh compute and a verified
archive. The target was rehearsed at `rybbit-hcloud.getcolors.ai`. Vultr is also
retired, not a recovery origin.

Each deployment has its own provider identities, credentials, authority and
state. Target `.colors.sqlite` and `.ssh/` replace the legacy remote Terraform
state and encrypted machine-access convention only for this new profile.
Names alone cannot authorize adoption. Do not copy legacy state into the target
or rename the source configuration to point at the new server.

## Recovery inputs

Before restoration, identify an exact source archive, its timestamp, byte size
and SHA-256. Keep its PostgreSQL dump and ClickHouse native backup together.
Capture compatible image versions and source aggregate counts. Keep the source
`stack.env` in a protected local file; retain `BETTER_AUTH_SECRET` and compatible
database credentials. Never print that file or commit it.

Recover protected files using the VaultContext skill where applicable. Its
file-recovery authorization does not automatically authorize inspecting or
executing recovered contents. A controller checkpoint contains no application
database archive. Retain independent recovery access if Vault depends on the
infrastructure being restored.

Configure a dedicated target backup bucket or explicitly reviewed isolated
prefix and credential. The new controller writes beneath its profile prefix;
never let its backup retention prune the old profile's archives. Naming a bucket
in YAML does not provision it. Existing source backups remain independent.

## Rehearsal

1. Initialize the new profile and review its plan. Keep production DNS out of
   the target's managed hostname configuration during rehearsal.
2. Run `prepare --stack-env-file /protected/path/stack.env`. Preparation creates
   a database-only target and leaves application writers and the backup timer
   fenced. Supply retained secrets before target databases initialize.
3. Select a complete source archive and download/transfer it to a protected local
   path. Record its digest. A live-source archive is for rehearsal; PostgreSQL
   and ClickHouse need drained writers for final cross-database consistency.
4. Run `restore --archive /protected/path/archive.tar.gz --sha256 SHA256`.
   The controller verifies the transferred archive and restores on the fenced
   target. It records restoration progress. A failed partial restore requires
   investigation and a fresh disposable target, not blind replay.
5. Compare target aggregate counts with the captured snapshot, then converge
   the staging application. Verify public health, authentication as authorized,
   historical queries/replays and a unique ingestion event.
6. Create and retrieve a target backup and demonstrate restoration. Verify both
   PostgreSQL and ClickHouse data. Routine backup's PostgreSQL scratch test and
   uploaded-byte verification do not establish complete replacement recovery.

Keep source production operating during rehearsal. Tests and synthetic events
on the staging target do not authorize final DNS transfer or source retirement.
Use a fresh final target/database state rather than merging source data into
an already exercised target. The restore command does not merge databases.

## Return the rehearsed target to clean databases

After independently retaining the rehearsal archive and validation evidence,
use `reset-target` to quarantine the exercised target instead of deleting
receipts or merging databases. Supply all four exact identifiers:

```sh
uv run rybbit reset-target --reset-id RESET_UUID \
  --expected-machine-id MACHINE_ID --expected-instance-id PROVIDER_ID \
  --expected-deployment-id DEPLOYMENT_UUID
```

The command requires the owned `rybbit-hcloud` deployment, its exact live
compute and machine identities, and the staging hostname. Any DNS adoption
receipt or non-staging DNS resource blocks reset, including after a production
hostname transition. Reset is not a production rollback command.

Reset fences the application and backup timer, verifies/removes stopped
application containers, then quarantines the PostgreSQL, ClickHouse and Redis
directories plus `stack.env`, `application-started` and `restore.json` beneath
`/var/lib/rybbit/reset-quarantine/RESET_UUID`. It retains controller state,
SSH authority, archives and Caddy certificate state. A durable manifest tracks
interruption recovery; repeat with the same reset UUID. Preparation, convergence,
restoration, backup and acceptance refuse pending or malformed reset receipts
before mutating application state. Never remove receipts
or quarantined data to force progress. Keep sufficient disk space for both the
quarantine and the next restore.

Run `prepare --stack-env-file /protected/path/stack.env` again with retained
source secrets before initializing clean target databases. Keep the target
fenced until the final archive is restored and verified. Recheck application
hostname/TLS readiness after the final restore. Retain quarantine independently
until an explicit cleanup decision.

## Final transfer

Follow [explicit DNS cutover](dns-cutover.md). Complete recovery checkpoints,
full rehearsal, authenticated login/replay checks, TLS preparation and a
measured downtime estimate before the final production approval gate.

After approval, disable and drain legacy DNS automation and privately snapshot
its exact backend. Fence source ingress and application writers and stop/drain
scheduled backup jobs. Verify no second writer remains, allow in-flight work
to finish, then capture the final consistent archive, digest and aggregate
counts. The `freeze` receipt reports observed count stability but explicitly
does not prove queue depth; establish ingestion drain independently.

Restore into the clean fenced target using the rehearsed procedure; verify
all aggregate counts. Configure the production hostname and prepared TLS with
`application-converge`. This starts target writers with backups disabled, so
keep source writers fenced. Run direct origin `readiness`, adopt the exact
existing Cloudflare record while preserving its address/proxy/TTL, then run the
separate explicit `dns-switch`. Ordinary convergence is not the initial switch.

Verify public URLs, strict TLS, authenticated login, historical analytics,
replay playback and fresh ingestion. Enable normal target backups and establish
complete recovery evidence. Remove only the old DNS state binding, keep legacy
DNS automation disabled, explicitly retire staging DNS and checkpoint the
final target controller. Preserve source snapshots and source compute; their
retirement requires a separate retention and authorization decision.

## Rollback and retirement

Before target production writes, the intact fenced source and final snapshot
provide a fallback after explicitly restoring source routing and writer
ownership. After target writes begin, merely reversing DNS loses target-only
events or account changes. Lossless rollback needs a separately validated data
transfer with deduplication. Never point older software at upgraded schemas or
assume ClickHouse data files can be downgraded.

Keep destruction protection enabled during migration. Retire the old server
only when the chosen retention and recovery checks are satisfied within the
user's authorization. Preserve its archives and application secrets independently
of compute deletion. A successful new deployment does not itself authorize
removal of all old storage or credentials.
