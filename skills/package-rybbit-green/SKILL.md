---
name: package-rybbit-green
description: Provisions and operates a production-oriented single-node Rybbit analytics service with PostgreSQL, ClickHouse, Redis and Caddy on one DigitalOcean Droplet or one Vultr instance.
license: MIT
---

# Rybbit with Green

Operate one Rybbit analytics deployment from non-secret `colors.yml`. Read
[references/configuration.md](references/configuration.md) before changing
configuration or running a lifecycle operation.

## Providers

`provider-compute` selects the machine: `digitalocean` (one Droplet, the
region's default VPC discovered at runtime) or `vultr` (one instance, a
generated per-CIDR firewall group). Each provider reads its own keys and its
own credential:

| Provider | Credential | Keys |
|---|---|---|
| `digitalocean` | `COLORS_PAR_DO_TOKEN` | `digitalocean-region`, `digitalocean-size`, `digitalocean-image`, `digitalocean-ssh-sources`, `digitalocean-http-sources`; optional `digitalocean-name`, `digitalocean-ssh-keys` |
| `vultr` | `COLORS_PAR_VULTR_API_KEY` | `vultr-region`, `vultr-plan`, `vultr-os-id`, `vultr-ssh-sources`, `vultr-http-sources`; optional `vultr-name`, `vultr-ssh-keys` |

- `<provider>-name` is optional and defaults to the profile.
- `<provider>-ssh-keys` is optional. Leave it out and the package generates
  and owns the machine keypair at `~/.ssh/<profile>` on the first real create
  (keygen mode, the default); set it to an existing account key id to use that
  key instead.
- A real create also writes a managed `Host <profile>` block into
  `~/.ssh/config`, between `# BEGIN <profile> ANSIBLE MANAGED BLOCK` and
  `# END …` markers, so `ssh <profile>` reaches the machine; `delete` removes
  it before the machine is destroyed. The alias is the profile — there is no
  separate key for it. A `Host <profile>` stanza that already exists outside
  those markers, or an option standing above the first `Host` line of the
  file, refuses the create with the file and line named; the package never
  overwrites either. Remove or rename the stanza, move the global options
  below the block or into a `Host *` stanza at the end, or change `profile`.
- `<provider>-ssh-sources` must list at least one CIDR; every entry of both
  source keys must be a valid IPv4 or IPv6 CIDR. An empty
  `<provider>-http-sources` means no public HTTP.
- Switching providers is a rebuild, never an apply: `delete` on the recorded
  provider first, then `create` on the new one. A changed `provider-compute`
  on a profile that holds a machine is refused.

## Safety

- Keep credentials in gitignored `.envrc.private` as `COLORS_PAR_*` variables.
- Never set `COLORS_PAR_PROFILE` or edit/commit `.colors/`.
- Keep `compute-prevent-destroy: true`; deletion requires separate explicit
  authorization and a one-run environment override.
- Build and dry-run before a real create.
- Only Caddy's 80/443 are public. PostgreSQL, ClickHouse, Redis and the Rybbit
  backend and client ports stay on the private Compose network.
- `rybbit-disable-signup` is desired state. Rybbit has no first-run bootstrap,
  so it must stay `false` until you have registered the first account, then be
  set `true` to close public registration.

```sh
./green build
./green create --dry-run
./green create
```

A real create ends in acceptance: HTTPS health with a verified certificate, a
synthetic event read back out of ClickHouse, and a backup drill confirmed by a
fresh object in R2.
