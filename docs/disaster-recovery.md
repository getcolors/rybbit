# Recover into a new deployment profile

This is the authoritative recovery procedure for this repository. The deployment
wiki holds the current resource inventory and protected secret references:
[Rybbit Hetzner deployment](https://wiki.pocketcontext.com/#/page/deployment-profile-rybbit-hetzner).
Secret values belong in VaultContext, never in this document or the wiki.

## Scope and current limitation

Recover a destroyed Hetzner server by creating an independent deployment profile,
restoring an explicitly selected old-profile backup, validating it on a temporary
hostname, and transferring production routing only after validation. The same
procedure can rehearse recovery while the original deployment remains running.

**This is a staged operator/agent runbook, not an implemented recovery command.**
Ordinary `green/green create` provisions infrastructure, updates DNS, starts the
application, and runs acceptance including a backup. It does not restore data.
There is currently no supported recovery flag that pauses those stages. Before
executing recovery, implement and review that staging or prepare a concrete
equivalent procedure; do not invent CLI flags or run ordinary create against the
production hostname. A documentation request does not authorize provisioning,
DNS cutover, secret use, or retirement.

Database restoration from R2 and exact-version Vault file recovery were previously
demonstrated. A complete new-profile, replacement-server recovery drill remains
unproven. Track the drill and remaining operational work in
[ENG-30](https://tasks.pocketcontext.com/#/issues/79ahug6zlon1vmt).

## Recovery inputs

Record these before provisioning. Obtain current values from the deployment
inventory and actual backup metadata rather than assuming this dated baseline
remains current.

| Input | Recorded baseline, 2026-10-08 |
| --- | --- |
| Source profile | `rybbit-hetzner` |
| Source backup location | Bucket `rybbit-hetzner-backup`, prefix `rybbit-hetzner/` |
| Backup object | Select an explicit `rybbit-<UTC timestamp>.tar.gz`; record its size, timestamp and downloaded SHA-256 |
| Source state | Bucket `rybbit-hetzner-state`, prefix `rybbit-hetzner/` |
| Production hostname | `rybbit.getcolors.ai` |
| Externally managed redirect hostname | `rybbit.bigconfig.online` |
| Recovery profile | Choose an unused name, for example `rybbit-hetzner-recovery` |
| Recovery hostname | Choose a separate hostname in a zone the recovery DNS credential can manage |
| Versions | Preserve the source backup's compatible pins; current baseline is Rybbit 2.9.0, PostgreSQL 17.11 and ClickHouse 26.3.17.4 |

Use a separate checkout/configuration for recovery. Set the new profile in its
`colors.yml`; do not export `COLORS_PAR_PROFILE`, change the production checkout's
identity, hand-edit `.colors/`, or copy old compute state into the new profile.
Keep `compute-prevent-destroy: true`. An unused profile needs
`compute-require-existing-state: false` for its first authorized creation; enable
it after successful provisioning. This exception applies only to the new profile.
Let the library verify remote absence and establish new ownership and SSH authority.

Use separate state and backup buckets with names matching the new profile, or
explicitly reviewed isolated prefixes and credentials. Provision storage and
credentials before compute; naming them in configuration does not create buckets.
Prefer bucket separation because the source backup credential is bucket-scoped.
The restoration process needs read access to the old backup; the new backup job
must write and prune only the new backup location. Do not give it the old archive
prefix as its normal backup destination.

## Protected recovery material

The following are inventory references, not instructions to display secret values.
Load the VaultContext skill for retrieval and follow its metadata-only handling
rules. Restoring a protected file is distinct from inspecting or executing it;
arrange any required deployment consumption under the applicable instructions.

| Saved file | Vault document | Purpose |
| --- | --- | --- |
| `/home/ubuntu/code/getcolors/rybbit/.envrc.private` | `b0sovn8phjqiu87` | Old deployment's provider, storage, DNS and SSH-passphrase bindings |
| `/home/ubuntu/.local/share/rybbit-hetzner-cutover/dns-token.json` | `f23dj6hsu83c8rx` | Production DNS credential and issuance metadata |
| `/home/ubuntu/.local/share/rybbit-hetzner-handoff/recovery-20261008.tar.gz` | `vr9cvxzeagvq4mg` | Dated application configuration/secrets and state/handoff snapshots |

The recorded recovery bundle includes `/opt/rybbit/stack.env`, Compose and Caddy
configuration, and backup configuration. It contains **no database archive**.
Select appropriate Vault versions and verify their currency. Preserve the old
`BETTER_AUTH_SECRET`; a new ordinary create would generate a different one.
Reusing the old database passwords is simplest; new passwords are acceptable only
when database initialization and every client are configured consistently.
Replace old-profile backup credentials/settings with the new deployment's bindings.
Do not restore old state, SSH authority or old DNS ownership into the new profile.

## Execution sequence

### 1. Preserve the recovery point

Select a completed archive from the source backup prefix and copy it to protected
recovery storage outside automated retention. Record the exact object, timestamp,
size and checksum. Confirm the archive contains `postgres.sql` and
`clickhouse.zip` before relying on it. An ETag alone is not a content checksum.
Do not expose archive contents in logs or commit them to Git.

Select compatible pinned images and dependencies. Do not combine disaster recovery
with an application/database upgrade; recover first and upgrade separately.
Record the accepted data-loss interval relative to the selected backup.

### 2. Provision an isolated target

Review the rendered configuration and infrastructure plan for the new profile.
Confirm all resource/state/SSH identities are new, and no source resource or
production DNS record will change. Use the launcher and scoped SSH authority for
access, not copied private keys or assumed server addresses.

The staged procedure must keep the backend, client, Caddy, backup timer and any
pending/running backup service stopped while secrets and data are restored.
Only the database services needed for restoration should run. Block public
ingestion until validation, including access through the temporary hostname.
If a disposable target was already bootstrapped, stop these services first and
remove only that target's disposable database contents before restoring.

### 3. Restore configuration and databases

Install the retained application secrets before database initialization. Render
configuration for the new profile and temporary hostname; do not blindly replace
it with the old Compose/Caddy configuration. Keep restored private files root-owned
and mode `0600`. Start compatible PostgreSQL and ClickHouse services.

Restore `postgres.sql` into a clean application database with SQL errors treated
as failures. Preserve the entire schema, data and migration history. Do not merge
the dump into an application-initialized schema or selectively copy migration rows.

Place `clickhouse.zip` in the configured ClickHouse native-backup directory and
restore the archived application database into a clean target database using
ClickHouse's native restore facility. Verify the actual archive/database name
before preparing the command. Wait for restoration to finish successfully.

A backup from the current 2.9.0 deployment already contains its replay migration.
**Do not rerun the historical 2.8-to-2.9 replay backfill:** it is not idempotent.
An older pre-upgrade archive requires a separate version-specific migration plan.

Initialize Redis fresh: these archives do not preserve Redis sessions or queued
tracking work. Start the application only after both database restores succeed;
its entrypoint can run migrations. Keep public ingestion and scheduled backups
fenced until validation is complete. On a failed attempt, stop the application
and retry from clean target databases rather than layering restores.

### 4. Validate before routing production

Verify the API version and health, expected users/sites, login, historical event
counts/time ranges, representative historical replay playback, and a tagged new
tracking event reaching ClickHouse. Check logs for migration or authentication
errors. Use the snapshot's expected counts, not fixed counts from an older drill.

Run a controlled backup to the **new** bucket/prefix and download it for a scratch
database restore check. Confirm PostgreSQL and ClickHouse recovery, not merely
object existence. Keep automatic backup scheduling disabled until its destination
and retention behavior are verified. Record results and elapsed recovery time.

### 5. Transfer production routing and ownership

For a drill, stop here unless production cutover is authorized. For an authorized
cutover, freeze convergence of the old production profile so it cannot reclaim DNS.
Take protected source/target DNS state snapshots and record the current production
record ID, address, proxy setting and TTL.

Prepare the new deployment's production URL, authentication and Caddy settings.
Transfer ownership of the existing production DNS record to the new state with a
reviewed state removal/import procedure, preserving the record rather than
deleting and recreating it. Do not let two states manage the same record or run
ordinary create with production desired state before the handoff is ready.
Review the resulting plan, then route production to the verified replacement IP.

Handle the temporary validation record explicitly: preserve it externally or in
separate managed state as appropriate before replacing a single-record DNS state.
Also update the separately managed `rybbit.bigconfig.online` record and, if retained,
Vultr's Caddy forwarding destination, which previously used the old Hetzner IP.
The redirect-host setting does not manage that hostname's DNS.

Verify public production login, historical replay and ingestion after cutover.
Enable the new backup timer and verify the next scheduled run. Enable the new
profile's existing-state guard and confirm normal convergence is safe.

### 6. Record completion and retain rollback material

Update the wiki inventory with the replacement server, profile, state/backup
locations, credential references, DNS owner, recovery point, validation results,
and remaining gaps. Save the new private environment and application recovery
configuration through VaultContext. Keep old archives and state snapshots until
recovery is accepted; retirement requires separate authorization.

If the original server still runs, routing rollback also requires restoring its
serving configuration and freezing the new profile's DNS convergence. Writes made
only on the new deployment will not appear on the old one. If the original server
was destroyed, DNS rollback cannot resurrect it: retain maintenance routing and
repair or recreate the recovery target instead.

## Recovery limits and evidence

The recorded policy is daily backup at 02:30 with seven-day retention. With
successful daily runs, the recovery point can be nearly 24 hours old; failures can
make it older. Verify server timer timezone and actual successful object times.
Events not collected during the outage are also lost. There is no point-in-time
recovery in this workflow. PostgreSQL and ClickHouse backups are sequential, not
an atomic cross-database snapshot. Redis is excluded. No recovery-time guarantee
exists until a complete drill is timed.

Implementation references:

- [Create ordering and preflight](../green/src/clj/io/github/getcolors/rybbit/workflow.clj)
- [Secret generation and service setup](../green/src/resources/io/github/getcolors/rybbit/tools/ansible/main.yml)
- [Backup format and retention](../green/src/resources/io/github/getcolors/rybbit/tools/ansible/backup)
- [Pinned deployment configuration](../colors.yml)
- [Migration and restoration evidence](../README.md#upgrade-and-restore-rehearsal)

Before considering recovery automated, implement staged provisioning/restore and
delayed DNS/backup activation across all three colors, satisfy repository parity
checks, and complete an isolated new-profile recovery drill.
