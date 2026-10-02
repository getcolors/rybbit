# Configuration

`colors.yml` contains non-secret desired state. The root example targets a fresh
Google deployment. Keep `compute-prevent-destroy: true` and never export
`COLORS_PAR_PROFILE`. Start every `.envrc*` file with `# -*- mode: sh; -*-`.

## Credentials

Keep these in gitignored `.envrc.private`:

```text
COLORS_PAR_CLOUDFLARE_API_TOKEN
COLORS_PAR_R2_ACCESS_KEY_ID
COLORS_PAR_R2_SECRET_ACCESS_KEY
COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID
COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY
COLORS_PAR_RYBBIT_SSH_PASSPHRASE
```

The state R2 credentials are required only with `provider-backend: r2`.
Backup credentials are independent. Google compute uses Application Default
Credentials; other providers use the authentication documented by the pinned
compute library. Build and dry-run require none of these credentials.

## Compute ownership

This version requires `compute-api-version: 2` and creates fresh deployments
only. Existing deployments keep their pinned launchers, configuration, keys and
state. There is no migration, adoption or compatibility layer.

The pinned [colors-compute v2 contract](https://github.com/getcolors/colors-compute/blob/bc8658ded0e78bd7b212e3e89a2d87184fa64561/contracts/node.md)
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

## Images and data

The six image settings must carry an explicit tag or digest. Tags, including
`:latest`, can move; use digests for reproducibility. Datastore and auth secrets
are generated into `/opt/rybbit/stack.env` on the machine and retained across
converges. Signup and other managed non-secret settings are updated without
regenerating those secrets. Do not delete `stack.env` to change signup policy.

## Registration and acceptance

Leave `rybbit-disable-signup: false` until the first account is registered,
then set it to `true` and converge. A new installation can report ingestion as
`not-configured` before its first organization exists. Repeat acceptance after
bootstrap to verify an event actually reaches ClickHouse.

## Backups

The systemd timer runs on `rybbit-backup-oncalendar`. It dumps PostgreSQL,
restores that dump into a scratch database, creates a native ClickHouse backup,
and uploads the combined archive under `r2:<bucket>/<profile>`. It does not
rehearse ClickHouse restoration. Retention applies to the local backup directory
and the R2 profile prefix for `rybbit-backup-retention-days`.
