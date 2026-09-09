# Rybbit Package Skill

A tri-colour Package Skill (green, red, blue) for deploying a
production-oriented single-node [Rybbit](https://github.com/rybbit-io/rybbit)
privacy-friendly analytics platform on a VM provisioned by colors-compute.

The canonical implementation is [Green](https://github.com/getcolors/green)
(Clojure); the same deployment can run through the TypeScript
(`package-rybbit-red`) or Python (`package-rybbit-blue`) implementation — all
three render byte-identical artifacts, which `scripts/parity.sh` proves for
the provider and key-mode fixtures.

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

## Architecture

- **Compute**: The shared colors-compute library owns provider adapters, remote
  backend state, deployment locking and machine-key lifecycle. Rybbit supplies
  one node and its ingress requirements.
- **Access**: The machine keypair is generated and owned by the deployment at
  `~/.ssh/<profile>` (the SSH Keypair Standard); set `<provider>-ssh-keys` to
  an existing account key to opt out.
- **Reach**: `ssh <profile>` works: the package writes a managed block in
  `~/.ssh/config` (the SSH Config Standard) on create and removes it on delete.
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
./green build              # render .colors/<profile>/ — no provider calls, no credentials
./green create --dry-run   # walk the DAG without making changes
./green create             # provision infrastructure and converge application
./green delete             # guarded deletion
```

## Development

```sh
cd green && bb test && bb golden          # canonical implementation and goldens
cd red && bun test && bun run typecheck   # TypeScript implementation
cd blue && uv run pytest                  # Python implementation
./scripts/parity.sh                       # three colours, two providers, two keypair modes, byte for byte
./scripts/launcher.sh
```

## License

MIT License. Copyright (c) 2026 getcolors.
