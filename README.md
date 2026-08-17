# Rybbit Package Skill

A reproducible [Green](https://github.com/getcolors/green) Package Skill for deploying a production-oriented single-node [Rybbit](https://github.com/rybbit-io/rybbit) privacy-friendly analytics platform on DigitalOcean.

## Architecture

- **Compute**: Dedicated DigitalOcean Droplet in `ams3` (or configured region) with dynamic account-default VPC discovery.
- **Ingress**: Caddy terminating origin TLS on ports 80/443, reverse-proxying `/api/*` to Rybbit backend (Fastify) and the rest to Rybbit client (Next.js).
- **Databases**:
  - **PostgreSQL 17** (`postgres:17-alpine`) for auth/metadata (Better-Auth, users, projects).
  - **ClickHouse 24.8** (`clickhouse/clickhouse-server:24.8-alpine`) for high-throughput columnar analytics events.
  - **Redis** (`redis:8.6.4-alpine`) for session state and tracking queues.
- **Disaster Recovery**: Automated systemd timer `rybbit-backup.timer` executing `/usr/local/sbin/rybbit-backup` for consistent PostgreSQL dumps and ClickHouse snapshots, uploaded to Cloudflare R2 via `rclone`.

## Quick Start

```sh
npx skills add getcolors/rybbit
cp .agents/skills/package-rybbit-green/green ./green
chmod +x green
./green build
./green create --dry-run
```

## Lifecycle Commands

```sh
./green build              # render .colors/<profile>/ — no provider calls, no credentials
./green create --dry-run   # walk the DAG without making changes
./green create             # provision infrastructure and converge application
./green delete             # guarded deletion
```

## License

MIT License. Copyright (c) 2026 getcolors.
