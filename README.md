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

The pinned [colors-compute v2 contract](https://github.com/getcolors/colors-compute/blob/59acb202029ea1061c2c68d0a6ad2bb509eccad4/contracts/node.md)
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
