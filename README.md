# Rybbit Package Skill

A tri-colour Package Skill (green, red, blue) for deploying a
production-oriented single-node [Rybbit](https://github.com/rybbit-io/rybbit)
privacy-friendly analytics platform on DigitalOcean or Vultr.

The canonical implementation is [Green](https://github.com/getcolors/green)
(Clojure); the same deployment can run through the TypeScript
(`package-rybbit-red`) or Python (`package-rybbit-blue`) implementation — all
three render byte-identical artifacts, which `scripts/parity.sh` proves for
both compute providers.

## Architecture

- **Compute**: A dedicated DigitalOcean Droplet (with dynamic account-default
  VPC discovery) or Vultr instance (with a generated per-CIDR firewall group),
  selected by `provider-compute`. The provider operations — selection, the
  CIDR checks, the rebuild-only switch rule — are ONCE's `compute` namespace
  over this package's two-entry registry (the workspace Compute Provider
  Standard).
- **Access**: The machine keypair is generated and owned by the deployment at
  `~/.ssh/<profile>` (the SSH Keypair Standard); set `<provider>-ssh-keys` to
  an existing account key to opt out.
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
