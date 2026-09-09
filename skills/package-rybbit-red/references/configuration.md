# Configuration

Required non-secret keys are demonstrated in `colors.yml`. Provider options
and capabilities are resolved by the shared colors-compute library.

## Credentials

Every deployment requires these private environment variables:

```text
COLORS_PAR_CLOUDFLARE_API_TOKEN
COLORS_PAR_R2_ACCESS_KEY_ID
COLORS_PAR_R2_SECRET_ACCESS_KEY
COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID
COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY
```

plus the selected compute provider's:

```text
COLORS_PAR_DO_TOKEN          # provider-compute: digitalocean
COLORS_PAR_VULTR_API_KEY     # provider-compute: vultr
```

Never set `COLORS_PAR_PROFILE`.

## Compute ownership

The pinned `colors-compute` library owns provider selection, remote S3/R2
state, deployment coordination, machine keys, network policy and the single
node. This package supplies singleton topology and SSH/HTTP ingress, then
uses the returned address, login user and SSH identity for its application
steps. New provider support belongs in the library; consumers update its pin.
The application needs a supported Ubuntu image and sufficient memory for
Rybbit and its data services. Build first to check adapter capabilities.

Use `rybbit-ssh-sources` and `rybbit-http-sources` for neutral CIDR
allowlists. Existing selected-provider source options remain compatible.
External account key references may use `ssh-private-key-path` or operator/agent SSH configuration; external
private keys are never generated or removed. The local SSH block writes
`IdentityFile` only for a managed deployment key.

Existing `<profile>/rybbit-infrastructure.tfstate` is refused before
compute mutation. Do not remove it to bypass this check: migrate ownership
explicitly or destroy the old deployment through its original version first.
Unreadable state and provider mismatches fail closed.

The default compute provider remains `vultr`. An explicit `COLORS_PAR_IP`
changes only the delete-cleanup target after a successful owned-state read;
it cannot bypass unreadable state or provider identity checks.

Rybbit requests TCP 22 for SSH, TCP 80/443 for HTTP, and UDP 443 for HTTP/3.
Empty HTTP sources close both HTTP and HTTP/3 ingress.

Remote state uses `provider-backend: r2` or `s3`. R2 uses the two R2 backend
credentials above; S3 uses the ambient AWS credential chain. Application backup
credentials remain separate. No private network is requested by default;
explicit supported references, including `digitalocean-vpc-uuid`, are validated
by the library without taking ownership of that network.

A managed key is generated at `~/.ssh/<profile>` only during a real create.
The library records ownership before creating it, refuses unrelated existing
keys, and removes its key only after all compute resources are destroyed.
External provider key references may use `ssh-private-key-path` or operator/agent SSH configuration;
the library never generates or deletes external key material.

The local stage updates `Host <profile>` using the observed IP and login user.
It locks and atomically replaces `~/.ssh/config`, refuses conflicting unmanaged
stanzas or leading global options, and adds `IdentityFile` and `IdentitiesOnly`
only in managed mode. Build and dry-run never inspect or change this file.
Delete removes the managed block before machine destruction.

Changing providers requires deleting the owned deployment first. Missing,
unreadable or mismatched state cannot be replaced by a cleanup IP override.

## Images

`postgres-image`, `clickhouse-image`, `redis-image`, `rybbit-backend-image`,
`rybbit-client-image` and `caddy-image` are exact pins and must each carry a
tag. The two Rybbit images track `:latest` upstream; pin them by digest if you
need a reproducible deployment.

## Secrets the stack generates for itself

The database, ClickHouse, Redis and auth secrets are generated on the machine
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
