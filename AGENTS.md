# Rybbit

Keep it simple. Use Python, SQLite controller state and established CLIs.
Reuse PocketDeploy for compute, ownership, locking, SSH, DNS and recovery;
follow Alice and Walter's small application-controller patterns. Keep changes
to `colors.yml` minimal and normalize useful existing field names internally.
Read README.md before changes.

## Boundaries

`src/rybbit/` owns the Docker Compose application, staged restoration, backup
and acceptance logic. PocketDeploy owns generic infrastructure. SQLite stores
deployment identities and operations; Rybbit still uses PostgreSQL, ClickHouse
and Redis. Do not replace application databases as part of controller work.

Production uses `rybbit-hcloud` after the 12 October 2026 transfer. The old
`rybbit-hetzner` server was deleted on 13 October after its legacy redirect moved
to production. Retained archives and legacy state are recovery evidence; never
restart the old controller or copy legacy Terraform state into SQLite. Neither
the old Hetzner server nor Vultr is a rollback target.
See docs/disaster-recovery.md and dated docs/verification.md evidence.

## State and secrets

Keep `.envrc.private`, `.colors.sqlite`, `.ssh/`, archives, plans and recovery
material out of Git. Load bindings through the trusted shell; do not print or
commit their values. Pass application secrets through protected files and SSH
stdin. Inspect only allowlisted provider fields; never print raw provider
responses, Docker metadata, full environments or application secret files.
The new controller intentionally uses PocketDeploy's protected local SSH keys,
not the legacy encrypted machine-access resource.

Keep profile identity in `colors.yml`; never export `COLORS_PAR_PROFILE`.
Never clear SQLite ownership or pending-operation evidence to force progress.
Keep destruction protection enabled except for explicitly authorized retirement.
User authorization persists across the task; routine read-only checks do not
need repeated approval. New resources, routing transfer and destructive work
must stay within the user's authorized scope.

## Migration

Use a distinct staging hostname and separate target backup destination.
`prepare` leaves the application and backup timer fenced while databases run.
Supply retained application secrets before initializing target databases.
Restore a checksummed archive into the new target; inspect counts and run
acceptance before a final production decision. Acceptance writes a synthetic
tracking event and is not a read-only health check.

A rehearsal captured while source writes continue is not a final migration.
Final transfer needs drained source writers, a final consistent archive, tested
restoration and exact DNS evidence. Preserve the DNS record ID, proxy and TTL
while adopting ownership; switching the origin is a separate step. Never run
the old DNS manager after ownership transfer. After target writes begin, DNS
rollback alone loses target-only changes.

## Validation and release

Use `uv sync --extra test`, `uv run pytest -q` and `uv build`. Tests must use
synthetic data and isolated state. Read docs/development.md for package and
copied-launcher checks. Publish dependencies before pinning their full commits;
then publish the application and update its portable launcher's immutable pin.
Test the copied launcher from an unrelated directory. Do not claim mocked tests
as live verification. Record the actual profile, versions, checks, failures and
remaining limitations in docs/verification.md without private records.

`skills/rybbit/` is the maintained portable skill. Keep instructions aligned with
CLI help. Preserve both analytics tags when changing index.html: GA4
`G-4VKP1WY4QJ` with page_title matching the decoded title, and the Rybbit script
at `https://rybbit.getcolors.ai/api/script.js`, site ID `9fb9c41a6d49`.
