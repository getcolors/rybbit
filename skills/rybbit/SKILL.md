---
name: rybbit
description: Deploy and operate the Python Rybbit controller on Hetzner, including staged database restoration, backups, DNS ownership and deployment recovery. Use for this Rybbit deployment, not general analytics queries.
---

# Rybbit

Keep it simple. Use the bundled `rybbit` executable from the deployment directory
containing `colors.yml`, or select that file explicitly with `-f`. The launcher
uses a pinned package; it does not run arbitrary adjacent source code. Start
with `rybbit --help` and the relevant command's help for exact options.

## Ownership and privacy

The new Python deployment is `rybbit-hcloud`. Its private SQLite state and local
SSH authority belong to that profile. `rybbit-hetzner` retains the legacy source
controller until a verified transfer. Never copy its Terraform state into
SQLite or operate the same resources through both controllers. Vultr is retired.

Load credentials through the trusted shell. Do not print `.envrc.private`,
application secret files, raw cloud responses, Docker metadata or environment
dumps. Filter exact non-secret fields inside the inspecting process. Preserve
pending-operation evidence and exact provider identities when reconciling a
failed command; missing state never authorizes recreating production.

Use existing user authorization for the task. Provisioning, final production
routing changes and retirement must fall within that scope. Commands called
`acceptance` and `backup` mutate data or storage; they are not read-only checks.

## Workflows

For a fresh deployment: `init`, review `plan`, then `prepare` or `converge` as
appropriate. `prepare` keeps application writers and backups fenced while
running databases. `converge` starts normal application service and backups.
Use `status`, `counts` and `acceptance` to assess the result. `acceptance` submits
a synthetic event when an organization exists; an empty deployment may require
account bootstrap first.

For data migration, use a distinct staging hostname and target backup
bucket/prefix. Preserve source application secrets with `--stack-env-file`
during preparation, before target databases initialize. Restore an explicitly
selected archive with `restore --archive PATH --sha256 DIGEST`. These inputs
must be protected local files. Validate aggregate counts, ingestion and a new
target backup/restore before final transfer. An archive captured while source
writers continue is rehearsal evidence only.

Final migration needs source writers and backup jobs drained, a final consistent
snapshot, successful target restoration and exact DNS identity/settings evidence.
DNS ownership adoption preserves the old address, proxy and TTL; switching to
the target is a separate action. Keep the former DNS manager disabled after
transfer. After target-only writes begin, DNS rollback alone cannot recover them.
Read the deployed package's README and `docs/disaster-recovery.md` for the full
procedure; do not invent an automatic migration or downgrade command.

Use `backup` for an application database archive. PostgreSQL scratch restoration
and uploaded-byte checks are not complete ClickHouse recovery evidence.
`vault-save` / `vault-restore` protect controller state and SSH authority and
require the VaultContext user's unlocked session; they do not restore databases.

Use `ssh` for the owned host, and `ssh-install` / `ssh-uninstall` for local alias
management. `delete` requires deliberately disabled destruction protection.
Keep backup resources and recovery material until retirement is verified.

Report the profile, concrete checks and remaining limitations. Never claim a
production migration from staging deployment or mocked tests alone.

### Explicit hostname and DNS transfer

Use `application-converge` to reconcile existing compute without DNS changes;
it starts application writers with backups disabled. `readiness` checks direct
origin HTTPS, configured hostname, certificate and health without ingestion.
Prepare production certificates separately and keep source writers fenced for
final transfer. `dns-evidence` emits exact record evidence; save its result
object in a private owned JSON file. After disabling/draining the previous DNS
manager, run `dns-adopt --evidence-file FILE --previous-dns-manager-disabled`.
Adoption changes ownership only; repeat with original evidence on interruption.
`dns-switch` checks fresh origin readiness and preserves adopted proxy/TTL while
routing to the owned target. Retire old staging with
`dns-retire --hostname OLD_HOST --record-id EXACT_RECORDED_ID`.
See README.md and docs/dns-cutover.md for migration scope and independent data,
TLS, authentication and replay acceptance requirements.

For an explicitly authorized rehearsal-target reset, `reset-target` requires
`--reset-id UUID`, `--expected-machine-id MACHINE_ID`,
`--expected-instance-id PROVIDER_ID` and `--expected-deployment-id DEPLOYMENT_UUID`. It accepts only the exact owned
`rybbit-hcloud` staging target before DNS adoption, fences it, removes stopped
containers, and quarantines databases,
settings and initialization/restore receipts. Reuse the same reset ID after an
interruption; do not delete receipts or quarantine to force progress. Prepare
retained source secrets again before final restoration.
