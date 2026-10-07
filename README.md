# Rybbit Package Skill

A tri-colour Package Skill (green, red, blue) for deploying a
production-oriented single-node [Rybbit](https://github.com/rybbit-io/rybbit)
privacy-friendly analytics platform on a VM provisioned by colors-compute.

The canonical implementation is [Green](https://github.com/getcolors/green)
(Clojure); the same deployment can run through the TypeScript
(`package-rybbit-red`) or Python (`package-rybbit-blue`) implementation — all
three render byte-identical artifacts, which `scripts/parity.sh` proves for
the v2 integration fixtures.

## Compute ownership

This version requires `compute-api-version: 2` and creates fresh deployments
only. Existing deployments keep their pinned launchers, configuration, keys and
state. There is no migration, adoption or compatibility layer.

The pinned [colors-compute v2 contract](https://github.com/getcolors/colors-compute/blob/ed39df40ac0bd30f4014c263ec70ae7d09ab3984/contracts/node.md)
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

### Fresh deployment verification

Create first inspects encrypted SSH authority and reuses an existing ready
identity. If authority is confirmed missing and `compute-require-existing-state`
is false, Rybbit asks the compute library to inspect its node and registration
state and check the provider for matching resources. Only a verified absence
result authorizes creating the identity. Unreadable or nonempty state, existing
provider resources, incomplete inventories, and unsupported authentication
overrides stop creation. Restore the original authority when consumers survive;
never force the verification flag.

The check uses the configured provider account/project and stable resource names;
it does not discover renamed resources or other deployments sharing an identity.
Keep the profile and provider scope stable and serialize deployment operations.
Build and dry-run perform no verification calls. The library contract documents
required read permissions, command-line tools, and supported credential sources.

## Architecture

- **Compute**: One v2 node, with separate public-key registration where required.
- **Access**: Encrypted SSH authority and a temporary scoped agent. `./green ssh`
  opens an administrative session with the observed address and login user.
- **Ingress**: Caddy terminating origin TLS on ports 80/443 (plus UDP 443 for
  HTTP/3), reverse-proxying `/api/*` to Rybbit backend (Fastify) and the rest
  to Rybbit client (Next.js).
- **Databases**:
  - **PostgreSQL 17** (`postgres:17-alpine`) for auth/metadata (Better-Auth, users, projects).
  - **ClickHouse 24.8** (`clickhouse/clickhouse-server:24.8-alpine`) for high-throughput columnar analytics events.
  - **Redis** (`redis:8.6.4-alpine`) for session state and tracking queues.
- **Disaster Recovery**: Automated systemd timer `rybbit-backup.timer` executing
  `/usr/local/sbin/rybbit-backup` for consistent PostgreSQL dumps and ClickHouse
  snapshots, uploaded to Cloudflare R2 via `rclone`.

## Quick Start

```sh
npx skills add getcolors/rybbit
cp .agents/skills/package-rybbit-green/green ./green
chmod +x green
./green build
./green create --dry-run
```

The red and blue payloads (`package-rybbit-red`, `package-rybbit-blue`) install
and run the same way with `./red` and `./blue`.

## Lifecycle Commands

```sh
./green build              # render .colors/build/<profile>/ — no provider calls, no credentials
./green create --dry-run   # walk the DAG without making changes
./green create             # provision infrastructure and converge application
./green delete             # guarded deletion; retains encrypted SSH authority
./green ssh                # scoped administrative SSH session
```

## Google example

The root `colors.yml` describes a fresh Google deployment with an ARM64 Ubuntu
image, `n4a-highmem-1`, Hyperdisk Balanced, GVNIC and a 100 GB boot disk. Google
uses Application Default Credentials. Check project permissions, quota, image
availability and metadata-key/OS Login policy before a real create. Cloudflare
DNS, R2 state and R2 backups remain independent services.

Create the first account while signup is enabled, then set
`rybbit-disable-signup: true` and converge again. Until an organization exists,
acceptance may report ingestion as `not-configured`; rerun after bootstrap to
verify a synthetic event. Backup checks verify PostgreSQL restoration and a
fresh uploaded object, not a ClickHouse restore rehearsal.

## Development

```sh
cd green && bb test && bb golden          # canonical implementation and goldens
cd red && bun test && bun run typecheck   # TypeScript implementation
cd blue && uv run pytest                  # Python implementation
./scripts/parity.sh                       # three colours, v2 integration fixtures, byte for byte
./scripts/launcher.sh
```

## License

MIT License. Copyright (c) 2026 getcolors.

## Google SSH reauthentication

`./green ssh` (and the Red/Blue launchers) checks the current machine address
before opening its scoped SSH agent. When Google reports an expired user session,
an interactive terminal displays the reason and runs
`gcloud auth application-default login`, showing Google's browser link and login
progress directly in that terminal. A successful login retries the address lookup
once, then connects. Cancellation stops the operation; repeated authentication
failure never loops or falls back to a cached address.

For a terminal without a local browser, set `rybbit-ssh-login-browser: false` in
`colors.yml`, or run:

```sh
COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER=false ./green ssh
```

This uses `gcloud auth application-default login --no-launch-browser`; open its
link and paste the verification code into the terminal. Google sign-in still
requires user interaction. No login runs during build/dry-run or create/delete.

Automatic recovery requires local `authorized_user` ADC and no explicit Google
credential overrides. Service accounts, federation, overridden credentials, and
noninteractive sessions receive recovery instructions instead. The workflow does
not print tokens or capture the login interaction in its result. Google session
policies still apply; this handles expiry rather than extending its lifetime.

The pinned `colors-compute` dependency supplies the structured reauthentication
hint. Existing deployments keep their installed launcher pins until explicitly
updated; publishing this package does not refresh those copies.
