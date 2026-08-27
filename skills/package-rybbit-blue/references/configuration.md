# Configuration

Required non-secret keys are demonstrated in the package `colors.yml`.

The enabled deployment requires these private environment variables:

```text
COLORS_PAR_DO_TOKEN
COLORS_PAR_CLOUDFLARE_API_TOKEN
COLORS_PAR_R2_ACCESS_KEY_ID
COLORS_PAR_R2_SECRET_ACCESS_KEY
COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID
COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY
```

Never set `COLORS_PAR_PROFILE`. No VPC UUID or CIDR is accepted: the package
looks up `default-<digitalocean-region>` at runtime and never creates a VPC.

`postgres-image`, `clickhouse-image`, `redis-image`, `rybbit-backend-image`,
`rybbit-client-image` and `caddy-image` are exact pins and must each carry a
tag. The two Rybbit images track `:latest` upstream; pin them by digest if you
need a reproducible deployment.

## Secrets the stack generates for itself

The database, ClickHouse, Redis and auth secrets are generated on the droplet
into `/opt/rybbit/stack.env` on first converge and retained after that. They
never appear in `colors.yml`, in `.colors/`, or in this repository.

That file is written once. `rybbit-disable-signup` is rendered into it, so
changing that key later does not rewrite an existing `stack.env` — close
registration through Rybbit itself, or remove the file to have it regenerated.

## Registration

Rybbit has no first-run bootstrap. With signup disabled nobody can create the
first account, so `rybbit-disable-signup: false` is the shipped default. Set it
`true` once you have registered.

## Backups

A systemd timer runs `rybbit-backup` on `rybbit-backup-oncalendar`. Each run
dumps PostgreSQL, takes a native ClickHouse `BACKUP` — never a hot copy of the
data directory, which races running merges and cannot be restored — and
restores the dump into a scratch database before uploading, so an unrestorable
archive fails the unit instead of reaching the bucket.

Retention applies to both sides: `rybbit-backup-retention-days` prunes the local
directory and the `r2:<bucket>/<profile>` prefix.
