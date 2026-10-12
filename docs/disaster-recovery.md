# Restore into a new deployment profile

The Python controller supports staged preparation and explicit archive
restoration. Use a fresh owned target; do not import legacy infrastructure state.
Current inventory belongs in WikiContext and dated execution evidence belongs
in [verification](verification.md). Credentials and recovery files remain private.

## Source and target

The migration source is `rybbit-hetzner`, serving `rybbit.getcolors.ai`. Its
legacy checkout remains at `/home/ubuntu/code/getcolors/rybbit-hetzner-legacy`
(preserved starting revision `b5b63ca`). Operate it with its established
controller and SSH authority. The new Python target is `rybbit-hcloud`, rehearsed
at `rybbit-hcloud.getcolors.ai`. Vultr is retired, not a recovery origin.

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

## Final transfer

Migration is currently deferred. Read [the DNS cutover assessment](dns-cutover.md)
for required hostname/TLS sequencing and ownership gaps before any transfer.

Within authorized migration scope, schedule the final write interruption and
disable the source controller's DNS automation. Drain application writers,
in-flight ingestion and backup jobs. Verify the fence, capture a final
consistent archive and record its digest and aggregate counts.

Restore the final archive using the proven target procedure and verify the
result before enabling target writers. Preserve the production Cloudflare
record's exact zone ID, record ID, source address, proxy setting and TTL with
its settings fingerprint. PocketDeploy's explicit DNS adoption transfers only
the ownership comment; subsequent convergence changes routing. Keep the old
manager disabled after the transfer, including during retries.

Validate production URLs, TLS, authentication, ingestion and fresh backups.
Prevent requests still reaching the old origin from producing a second writer;
keep it fenced or route it deliberately to the selected active origin. Record
actual outcomes and retain source snapshots and controller recovery evidence
before considering retirement. Production migration is incomplete until these
steps have their own evidence.

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
