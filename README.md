# Rybbit

Deploy a single-node [Rybbit](https://github.com/rybbit-io/rybbit) analytics stack
on Hetzner using Python, SQLite deployment state and CLI tools. Keep it simple.
[PocketDeploy](https://github.com/pocketcontext/pocketdeploy) handles server and
firewall ownership, SSH, Cloudflare DNS and controller recovery. Rybbit handles
Docker Compose, database backups, restoration and application acceptance.

SQLite records controller state. Application data stays in PostgreSQL,
ClickHouse and Redis. Caddy exposes HTTPS; database and application ports stay
on the private Compose network. Only SSH, TCP 80/443 and UDP 443 are public,
subject to the configured source allowlists.

The new profile is `rybbit-hcloud`, initially using
`rybbit-hcloud.getcolors.ai`. Production remains `rybbit-hetzner` at
`rybbit.getcolors.ai` until a verified final migration. See
[verification](docs/verification.md) for dated evidence and
[recovery and migration](docs/disaster-recovery.md) for the transfer procedure.
A live staging deployment does not establish that production data is migrated.
Vultr is retired and cannot provide rollback.

## Setup

Install Python 3.11 or later, uv, OpenSSH, Hetzner's `hcloud` CLI and the pinned
Cloudflare `cf` CLI used by PocketDeploy. The deployment host installs Docker
Compose and rclone through its package manager. Use the repository development
environment where available; see [development](docs/development.md).

```sh
uv sync --extra test
uv run rybbit --help
```

`uv run rybbit` runs checkout code. The portable `./rybbit` launcher resolves its
published package commit; editing source does not update that launcher.
`colors.yml` is selected from the working directory or explicitly with `-f`.
Run commands from the deployment directory so configuration and local state
refer to the intended profile.

Keep secrets in ignored `.envrc.private` or an equivalent trusted shell setup.
The controller does not automatically execute private configuration. Hetzner
uses `HCLOUD_TOKEN` or `COLORS_PAR_HCLOUD_TOKEN`; Cloudflare uses
`CLOUDFLARE_API_TOKEN` or `COLORS_PAR_CLOUDFLARE_API_TOKEN`. Colors aliases map
into child environments. Backup credentials use the Rybbit bindings documented
by the configuration and CLI; never put credentials into `colors.yml`.

SSH aliases use an owned `~/.ssh/config.d/00-rybbit-<profile>.conf` snippet.
Before convergence, place `Include config.d/*` early in `~/.ssh/config`, before
applicable SSH options and outside conditional Host/Match blocks. Convergence
checks this requirement without changing local files before provisioning.
Existing inline managed blocks require explicit migration into `config.d`;
the controller does not rewrite your main SSH configuration. Resolve alias
collisions before retrying. Direct controller SSH still uses its pinned transport.

## Configuration and ownership

Keep the flat Colors vocabulary. Existing image pins, data directories,
firewall source lists and backup schedule remain useful. Retired
`compute-api-version`, `provider-backend` and infrastructure-state `r2-*` fields
are removed: private SQLite replaces remote Terraform state. R2 remains the
application backup destination. Defining a bucket in YAML does not create it.

Each profile has independent `.colors.sqlite` state and `.ssh/` authority beside
its configuration. These files are private and ignored. Do not change a
profile's identity to migrate it, reuse another profile's state, or export
`COLORS_PAR_PROFILE`. Names do not establish ownership; provider IDs and
controller ownership markers do. A missing owned resource or uncertain mutation
requires reconciliation, not automatic replacement.

Cloudflare zone discovery accepts one unambiguous zone for the explicit
hostname. Set `cloudflare-zone-id` if the zone is ambiguous. Ordinary convergence
refuses unowned records. The optional redirect hostname remains externally
managed DNS. `compute-prevent-destroy: true` protects the server; enable
`compute-require-existing-state` after establishing the new deployment.

## Commands

```sh
uv run rybbit init
uv run rybbit plan
uv run rybbit prepare
uv run rybbit converge
uv run rybbit status
uv run rybbit counts
uv run rybbit acceptance
uv run rybbit backup
uv run rybbit ssh
```

`init` prepares local state and SSH authority. `plan` checks the intended
changes. `prepare` provisions a database-only target with application ingress
and scheduled backups fenced; use it before restoration. `converge` starts the
application and normal backups. It is not a migration or restore command.

`counts` returns aggregate migration checks. `acceptance` checks application
behavior and submits a uniquely identifiable synthetic event when an
organization exists. It changes application data. An empty installation needs
account/organization bootstrap before ingestion acceptance can pass.

`backup` produces PostgreSQL and ClickHouse archives, checks a PostgreSQL
scratch restore and verifies uploaded bytes before retention. This does not
prove complete ClickHouse recovery; a replacement-server restore is a separate
check. Scheduled backups write beneath the configured bucket and profile prefix.
Keep the target destination separate from the source's retained backups.

`fence` stops application writers and scheduled backups while keeping databases
available. `restore --archive /protected/path/archive.tar.gz --sha256 SHA256`
transfers and verifies a selected local archive before restoration. Supply
`--stack-env-file /protected/path/stack.env` during target preparation when
preserving source secrets. See the [migration procedure](docs/disaster-recovery.md)
before restoring real data.

`ssh-install` and `ssh-uninstall` manage the owned local SSH alias. `delete`
requires destruction protection to be deliberately disabled and removes only
owned resources. Preserve application data and recovery material before retiring
a server. The backup bucket is an independent retained resource.

`vault-save` and `vault-restore` use PocketDeploy's encrypted controller recovery.
They require VaultContext authentication and a user-unlocked session. Controller
recovery preserves state and SSH authority; it does not restore application
databases. Use CLI help for command-specific options.

## Migration and releases

The Python controller creates a fresh server and new ownership under
`rybbit-hcloud`. It does not import the old `rybbit-hetzner` infrastructure.
The retained legacy checkout operates the source until retirement. Rehearse
restoration at the staging hostname, validate counts, ingestion, backups and
recovery, then perform a final fenced snapshot before production routing moves.

After the target accepts production writes, routing traffic back to a stale
source would lose new data. Preserve the source and snapshots; plan any reverse
transfer from verified data, not DNS alone. Do not run the retired Vultr profile.

Development checks and immutable package/launcher release steps are in
[development](docs/development.md). The [assessment](docs/python-sqlite-assessment.md)
records the earlier design decision; current code and verification evidence
establish implemented behavior.

## License

MIT License. Copyright (c) 2026 getcolors.
