# Configuration

Required non-secret keys are demonstrated in the package `colors.yml`. The
package supports two compute providers; `provider-compute` selects one, and
only the selected provider's keys and credential are required. Keys of the
other provider are accepted and ignored, so one `colors.yml` can carry both
blocks.

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

## Compute providers

### DigitalOcean (`provider-compute: digitalocean`)

| Key | Required | Meaning |
|---|---|---|
| `digitalocean-region` | yes | Droplet region, e.g. `ams3` |
| `digitalocean-size` | yes | Droplet size, e.g. `s-4vcpu-8gb` |
| `digitalocean-image` | yes | Image slug, `ubuntu-24-04-x64` |
| `digitalocean-ssh-sources` | yes | CIDRs admitted to TCP 22 |
| `digitalocean-http-sources` | yes | CIDRs admitted to TCP 80/443 and UDP 443 |
| `digitalocean-name` | no | Droplet name; the profile by default |
| `digitalocean-ssh-keys` | no | An existing account key id; absent means keygen mode |

No VPC UUID or CIDR is accepted: the package looks up
`default-<digitalocean-region>` at runtime and never creates a VPC.

### Vultr (`provider-compute: vultr`)

| Key | Required | Meaning |
|---|---|---|
| `vultr-region` | yes | Instance region, e.g. `ams` |
| `vultr-plan` | yes | Instance plan, e.g. `vc2-2c-4gb` |
| `vultr-os-id` | yes | Vultr's numeric OS id, `2284` (Ubuntu 24.04) |
| `vultr-ssh-sources` | yes | CIDRs admitted to TCP 22 |
| `vultr-http-sources` | yes | CIDRs admitted to TCP 80/443 and UDP 443 |
| `vultr-name` | no | Instance label; the profile by default |
| `vultr-ssh-keys` | no | An existing account key id; absent means keygen mode |

Vultr's `hostname` is deliberately not a key: a hostname change is an OS
reinstall. The playbook sets the hostname from the resolved name instead.

### Firewall sources

`<provider>-ssh-sources` must list at least one CIDR, and every entry of both
source keys must be a syntactically valid IPv4 or IPv6 CIDR; both are checked
before any provider call. An empty `<provider>-http-sources` is allowed and
means no public HTTP. The provider firewall admits 22, 80 and 443 (plus UDP 443
for HTTP/3) from those sources and nothing else.

### The machine keypair

When `<provider>-ssh-keys` is absent (keygen mode, the default), the first real
`create` generates an ed25519 keypair at `~/.ssh/<profile>` and registers it as
an account key named after the profile; `delete` removes the local keypair
after the machine is destroyed. The key is not generated output: it survives
regeneration of `.colors/`, and a fresh clone on another workstation does not
carry it. A key on disk with no matching state, or an account key of that name
this deployment does not own, refuses the create rather than being overwritten
or adopted. Set `<provider>-ssh-keys` to an existing account key id to opt out;
the package then creates and deletes no key material.

### Switching providers

Every provider shares one state key per profile, so switching is a rebuild:
`delete` on the provider recorded in state, then `create` on the new one. A
changed `provider-compute` on a profile whose state holds a machine is refused
on both `create` and `delete`; a state recorded before the package wrote the
provider is treated as Vultr's.

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
