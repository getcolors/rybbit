---
name: package-rybbit-blue
description: Provisions and operates a production-oriented single-node Rybbit analytics service with PostgreSQL, ClickHouse, Redis and Caddy on one VM through the shared colors-compute library.
license: MIT
---

# Rybbit with Blue

Operate one Rybbit analytics deployment from non-secret `colors.yml`. Read
[references/configuration.md](references/configuration.md) before changing
configuration or running a lifecycle operation.

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

## Safety

- Keep credentials in gitignored `.envrc.private` as `COLORS_PAR_*` variables.
- Never set `COLORS_PAR_PROFILE` or edit/commit `.colors/`.
- Keep `compute-prevent-destroy: true`; deletion requires separate explicit
  authorization and a one-run environment override.
- Build and dry-run before a real create.
- Only SSH and Caddy's configured ingress are public. PostgreSQL, ClickHouse, Redis and the Rybbit
  backend and client ports stay on the private Compose network.
- `rybbit-disable-signup` is desired state. Rybbit has no first-run bootstrap,
  so it must stay `false` until you have registered the first account, then be
  set `true` to close public registration.

```sh
./blue build
./blue create --dry-run
./blue create
```

A real create ends in acceptance: HTTPS health with a verified certificate, a
synthetic event read back out of ClickHouse once an organization exists, and a
backup drill confirmed by a fresh object in R2. Before first-account bootstrap,
ingestion may report `not-configured`; rerun acceptance after registration.
