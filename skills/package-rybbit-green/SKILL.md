---
name: package-rybbit-green
description: Provisions and operates a production-oriented single-node Rybbit analytics service with PostgreSQL, ClickHouse, Redis and Caddy on one VM through the shared colors-compute library.
license: MIT
---

# Rybbit with Green

Operate one Rybbit analytics deployment from non-secret `colors.yml`. Read
[references/configuration.md](references/configuration.md) before changing
configuration or running a lifecycle operation.

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
External account key references require `ssh-private-key-path`; external
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
